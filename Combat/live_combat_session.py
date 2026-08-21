"""LiveCombatSession: Canonical CombatStateSnapshot contract v0.3 Phase 1's RL-side
"ライブ実行経路". Keeps ONE live GameInstance progressing via repeated Step() calls for
the actual committed decision sequence of a single episode - `ResetFromScenario` is
called exactly once, in `start_combat()`, never again mid-episode. Trusts each
StepResult's own Observation/LegalActions directly (no `enumerate_legal_actions()`-style
fresh restore per decision) - this is now backed by an explicit, enforced Quiescent
Decision Boundary check on the Emulator side (`QuiescentBoundaryViolationException`,
commit `ce7ecc2` - see `C:\\STS2_Emulator\\docs\\reports\\combat_state_contract_phase1_
emulator_report_20260726.md`) rather than an unverified assumption.

Scope (per the Phase 1 contract - "Heuristic候補評価／beam search／lookahead／shadow分岐
／中間状態からの完全restoreは対象外"): this class drives ONLY the actually-committed
action sequence. `HeuristicAgent.choose_action_with_detail()`'s own candidate scoring
(and any Policy/Choice-Policy heuristic FALLBACK, which calls that same method) still
restores via `BattleEmulator.apply_action()`/`_restore()` on the SAME shared
GameInstance, entirely unchanged - callers should log this as `legacy_approximate_
restore`, per the Phase 1 instruction.

Critical correctness note (why `step()` is not a bare `Step()` call): HeuristicAgent's
candidate scoring restores the ONE shared GameInstance for its own hypothetical
branches, which necessarily clobbers whatever live progress this session was holding -
"only one GameInstance can be live per OS process" (battle_emulator.py's own module
docstring) cuts both ways. `step()` therefore checks the live GameInstance's own
reported (combat_session_id, step_index) against what this session last left it at,
immediately before committing:

    - still matches  -> nothing else touched the instance; commit via a direct Step()
                        call, no restore (the fast path this class exists for).
    - does not match -> some other restore-based evaluation ran in between (a Heuristic
                        fallback/candidate sweep, most commonly); re-establish this
                        session's own retained engine_state via exactly one
                        ResetFromScenario (the SAME mechanism BattleEmulator._restore()
                        already uses) before calling Step(). Counted via
                        `resynchronize_count`/flagged per-call via
                        `last_step_resynchronized` - a deliberate, DETECTABLE
                        degradation for that one decision, not a silent one.

This interaction was not explicitly anticipated by either side's contract documents -
it is this file's own resolution of an open question raised in
`Outputs/reports/rl_phase1_live_combat_session_status_20260726.md`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable

from game_access import GameAccess, GameAccessError, GameLease, LeaseState, process_game_access
from battle_emulator import (
    BattleEmulator,
    BattleState,
    DecisionFrame,
    build_scenario_from_spec,
    build_scenario_from_state,
    is_action_continuation_pending_choice,
)
from emulator_bridge import (
    ensure_loaded,
    get_restore_capabilities as bridge_get_restore_capabilities,
    legal_actions_to_list,
    restore_snapshot as bridge_restore_snapshot,
    restore_snapshot_json as bridge_restore_snapshot_json,
    to_plain,
    validate_restore_snapshot as bridge_validate_restore_snapshot,
    validate_restore_snapshot_json as bridge_validate_restore_snapshot_json,
)

MAX_CONTINUATION_STEPS = 50


class DecisionFrameMismatchError(RuntimeError):
    """Raised when `LiveCombatSession.step()` is called with a `DecisionFrame` that is
    not the session's current one - e.g. a caller holding a stale frame from an earlier
    decision. Formalizes legal_action_schema.json's existing (previously undetectable)
    rule ("action_id is not stable across states... never cache an action_id across a
    state change") into a runtime check, per this task's DecisionFrame proposal."""


class QuiescentBoundaryViolation(RuntimeError):
    """Python-side wrapper for `Sts2Emulator.Api.QuiescentBoundaryViolationException` -
    see that type's own doc comment. Raised instead of a bare CLR exception so callers
    can catch a stable Python type without importing pythonnet internals."""


def _quiescent_exception_type():
    return ensure_loaded()["QuiescentBoundaryViolationException"]


def _action_faulted_exception_type():
    return ensure_loaded()["ActionFaultedException"]


def _faulted_combat_session_exception_type():
    return ensure_loaded()["FaultedCombatSessionException"]


def _snapshot_restore_rejected_exception_type():
    return ensure_loaded()["SnapshotRestoreRejectedException"]


def _snapshot_restore_failed_exception_type():
    return ensure_loaded()["SnapshotRestoreFailedException"]


# Phase 3A.3 (Canonical CombatStateSnapshot contract - Action fault propagation):
# `Sts2Emulator.Api.ActionFaultedException.Message` (see
# `C:\STS2_Emulator\docs\reports\step_action_exception_propagation_fix_20260727.md` §4-A)
# is the ONLY C#-side surface carrying combatSessionId/stepIndex/actionId/action-description/
# originalExceptionType/originalMessage - neither exception class exposes these as
# structured .NET properties (confirmed by reading `ActionFaultedException.cs`/
# `FaultedCombatSessionException.cs` in full: both are plain `Exception` subclasses with
# only a `message`/`innerException` constructor). This regex extracts them as a documented,
# best-effort SECOND source - never the only source: `_build_fault_context()` below always
# ALSO uses (a) Python's own already-tracked `self._current_frame`/`action`/target
# parameters (authoritative, not parsed from any string) and (b) the CLR
# `InnerException`'s own `.GetType().FullName`/`.Message` accessed as real object
# attributes via pythonnet (not string-parsed) wherever the exception object itself is
# available. The raw message is always preserved verbatim in `raw_message` for audit.
_FAULT_MESSAGE_RE = re.compile(
    r"combatSessionId=(?P<session>\S+)\s+stepIndex=(?P<step>\d+)\s+actionId=(?P<action_id>\S+)\s+"
    r"action=(?P<action_desc>.+?)"
    r"(?:\s+originalExceptionType=(?P<orig_type>\S+)\s+originalMessage=(?P<orig_msg>.*?))?"
    r"\.\s*The combat session is now faulted",
    re.DOTALL,
)


@dataclass(frozen=True)
class ActionFaultContext:
    """Structured fault information for `ActionExecutionError` - deliberately NOT just
    `str(clr_exception)`. `combat_session_id`/`step_index`/`action_id`/`action_type`/
    `card_id`/`target_index`/`target_enemy_index` come from Python's OWN already-tracked
    state (the `DecisionFrame` this session held before the failed `step()` call, and the
    `action`/target parameters the caller passed in) - authoritative, not parsed from any
    exception string. `action_description`/`original_exception_type`/
    `original_exception_message` are extracted from the C# exception (structurally via
    `InnerException` object attributes when available, falling back to `_FAULT_MESSAGE_RE`
    parsing of the C# `Message` only when the object-level access is unavailable)."""

    combat_session_id: "str | None"
    step_index: "int | None"
    action_id: "int | None"
    action_type: "str | None"
    card_id: "str | None"
    target_index: "int | None"
    target_enemy_index: "int | None"
    action_description: "str | None"
    original_exception_type: "str | None"
    original_exception_message: "str | None"
    was_canceled: bool
    raw_message: str


def _build_fault_context(
    clr_exc, frame: "DecisionFrame | None", action: "dict | None",
    target_index: "int | None", target_enemy_index: "int | None",
) -> ActionFaultContext:
    raw_message = str(getattr(clr_exc, "Message", clr_exc))
    m = _FAULT_MESSAGE_RE.search(raw_message)

    inner = getattr(clr_exc, "InnerException", None)
    if inner is not None:
        original_exception_type = str(inner.GetType().FullName)
        original_exception_message = str(inner.Message)
        was_canceled = "OperationCanceledException" in original_exception_type
    elif m is not None:
        was_canceled = m.group("orig_type") is None
        original_exception_type = m.group("orig_type")
        original_exception_message = m.group("orig_msg")
    else:
        was_canceled = False
        original_exception_type = None
        original_exception_message = None

    action = action or {}
    return ActionFaultContext(
        combat_session_id=frame.combat_session_id if frame is not None else (m.group("session") if m else None),
        step_index=frame.step_index if frame is not None else (int(m.group("step")) if m else None),
        action_id=action.get("action_id") if action.get("action_id") is not None else (
            int(m.group("action_id")) if m and m.group("action_id").isdigit() else None
        ),
        action_type=action.get("action_type"),
        card_id=(action.get("parameters") or {}).get("cardId"),
        target_index=target_index,
        target_enemy_index=target_enemy_index,
        action_description=m.group("action_desc") if m else None,
        original_exception_type=original_exception_type,
        original_exception_message=original_exception_message,
        was_canceled=was_canceled,
        raw_message=raw_message,
    )


class ActionExecutionError(RuntimeError):
    """Python-side counterpart to `Sts2Emulator.Api.ActionFaultedException` (Phase 3A.3,
    `step_action_exception_propagation_fix_20260727.md`) - raised when the C# action a
    `LiveCombatSession.step()` call dispatched genuinely faulted or was canceled during
    its own execution (NOT a Quiescent Decision Boundary violation - that remains
    `QuiescentBoundaryViolation`, a structurally different situation: "engine settled into
    an inconsistent-looking state with no explanation" vs. this one, "a specific action's
    own execution genuinely threw"). Never raised for a normal Step failure (illegal
    action, etc.) or a Quiescent Boundary violation - those keep their own existing
    exception types unchanged. `.context` carries the structured `ActionFaultContext`;
    `.__cause__` (via `raise ... from clr_exc`) preserves the original CLR exception
    chain, including its own `InnerException`, for anyone who needs the raw pythonnet
    object."""

    def __init__(self, context: ActionFaultContext):
        self.context = context
        verb = "was canceled" if context.was_canceled else "faulted"
        super().__init__(
            f"Action execution {verb}: combat_session_id={context.combat_session_id} "
            f"step_index={context.step_index} action_id={context.action_id} "
            f"action_type={context.action_type} card_id={context.card_id} "
            f"original_exception_type={context.original_exception_type} "
            f"original_exception_message={context.original_exception_message}"
        )


class FaultedCombatSessionError(RuntimeError):
    """Python-side counterpart to `Sts2Emulator.Api.FaultedCombatSessionException` -
    raised by `LiveCombatSession.step()`/`get_observation()`/`get_legal_actions()`/
    `capture_snapshot()` whenever this session already recorded an `ActionExecutionError`
    and has not since been superseded by a successful `start_combat()`/`resume_from()`.
    Raised BEFORE the underlying CLR call for calls that already know they should fail
    (this session's own `_session_faulted` bookkeeping) - not solely reactive to the CLR
    exception, though a CLR `FaultedCombatSessionException` reaching Python unexpectedly
    (state somehow out of sync) is also translated into this same type, defensively."""

    def __init__(self, message: str, combat_session_id: "str | None" = None):
        self.combat_session_id = combat_session_id
        super().__init__(message)


class SnapshotRestoreMissingMoveError(RuntimeError):
    """Raised by `step()` when asked to execute an End Turn (`action_type == "system"`)
    against a `battle_state` where a living enemy's current Move (`Intent.stateId`) is
    still the Emulator's own `UNSET_MOVE` sentinel - i.e. this state descends from a
    Snapshot Restore whose enemies never got a Move established.

    **The "EVERY restore comes back unset" claim this docstring used to make is no
    longer true.** Measured 2026-08-21 against the current Emulator: capture at a stable
    Combat decision, restore, and the enemies come back with exactly the Intents they had
    (`TACKLE_MOVE`/4, `STICKY_SHOT`, `TACKLE_MOVE`/3 - identical before and after), no
    living enemy with an unset Move, and End Turn on the restored board succeeds and
    advances the turn normally (hp 80 -> 73, turn 1 -> 2, enemies rolling on to their next
    Moves). Whatever gap produced the original finding has since been closed on the
    Emulator side.

    The check is kept as a fail-fast guard for the case it describes, because that case
    is genuinely unrecoverable and expensive: if a living enemy's Move IS the Emulator's
    `UNSET_MOVE` sentinel, ending the turn makes the C# monster-move state machine hang
    inside
    `GameInstance.WaitUntilChoiceOrSettled()` for ~15s before surfacing only as a generic
    `TimeoutException` ("Timed out waiting for the next decision point or settlement") -
    the real cause (`InvalidOperationException("No move has been set for the monster")`,
    thrown from `MonsterMoveStateMachine/MoveState.cs`'s `UnsetMove()`) never reaches
    Python at all. This check fails FAST, before the CLR `Step()` call that would hang,
    with the actual cause and the specific enemies affected - instead of a caller (e.g. a
    Branch Worker) burning a full `request_timeout_s` discovering it the hard way.

    The engine itself is never touched by this check (raised before any CLR call), so
    unlike an actual engine fault this does NOT mark the session faulted - `battle_state`
    remains valid for any non-End-Turn action.

    Do not read this class as "restored boards cannot end their turn". They can; that is
    measured above. Reading the old wording that way is what nearly sank the Whole Run
    combat-branching design, whose leaves are scored by forcing exactly this action.
    """

    fault_kind = "snapshot_restore_missing_monster_move"

    def __init__(self, missing_enemies: "list[dict]") -> None:
        self.missing_enemies = missing_enemies
        super().__init__(
            "Refusing to execute End Turn: living enemies still lack a current Move "
            f"(Intent.stateId == 'UNSET_MOVE'): {missing_enemies!r}. This is a known "
            "Emulator SnapshotRestorer gap (Intent/RollMove is never touched by Restore) "
            "- ending this turn would hang waiting for a Move that will never be set "
            "(see MonsterMoveStateMachine/MoveState.cs's UnsetMove())."
        )


def _enemies_missing_current_move(engine_state: dict) -> "list[dict]":
    missing = []
    for enemy in engine_state.get("enemies") or []:
        alive = enemy.get("isAlive", True) and (enemy.get("hp", 0) or 0) > 0
        if alive and (enemy.get("intent") or {}).get("stateId") == "UNSET_MOVE":
            missing.append({"index": enemy.get("index"), "id": enemy.get("id")})
    return missing


def _str_or_none(value) -> "str | None":
    return None if value is None else str(value)


def _str_list(value) -> list[str]:
    if value is None:
        return []
    return [str(item) for item in value]


@dataclass(frozen=True)
class RestoreValidationResult:
    eligible: bool
    rejection_codes: list[str]
    unsupported_field_paths: list[str]


@dataclass(frozen=True)
class RestoreCapabilities:
    restore_api_version: str
    milestone: str
    contract_version: str
    contract_sha256: str
    snapshot_schema_version: str
    snapshot_schema_sha256: str
    supported_completeness: list[str]
    supports_combat_history: bool
    supports_pets: bool
    supports_pending_choice: bool
    supports_pending_target: bool
    supports_action_continuation: bool
    supported_power_scope: list[str]
    unsupported_power_internal_data_classes: list[str]
    rejection_codes: list[str]
    transaction_model: str
    rollback_after_teardown: bool
    issues_new_combat_session: bool
    preserves_stable_ids: bool


@dataclass(frozen=True)
class SnapshotRestoreRejectedContext:
    rejection_codes: list[str]
    unsupported_field_paths: list[str]
    clr_exception_type: str
    raw_message: str


@dataclass(frozen=True)
class SnapshotRestoreFailedContext:
    restore_phase: "str | None"
    combat_session_id: "str | None"
    schema_version: "str | None"
    contract_version: "str | None"
    snapshot_id: "str | None"
    original_exception_type: "str | None"
    original_exception_message: "str | None"
    clr_exception_type: str
    raw_message: str


class SnapshotRestoreRejectedError(RuntimeError):
    """Python-side counterpart to `SnapshotRestoreRejectedException`.

    Rejections happen before teardown and therefore do not fault or replace the current
    live session. The structured reasons are read directly from CLR properties.
    """

    def __init__(self, context: SnapshotRestoreRejectedContext):
        self.context = context
        self.rejection_codes = context.rejection_codes
        self.unsupported_field_paths = context.unsupported_field_paths
        super().__init__(
            "Snapshot restore rejected: "
            f"rejection_codes={context.rejection_codes} "
            f"unsupported_field_paths={context.unsupported_field_paths}"
        )


class SnapshotRestoreFailedError(RuntimeError):
    """Python-side counterpart to `SnapshotRestoreFailedException`.

    This means validation already passed, teardown already happened, and construction
    failed. The LiveCombatSession is faulted until a successful start/resume/restore.
    """

    def __init__(self, context: SnapshotRestoreFailedContext):
        self.context = context
        super().__init__(
            "Snapshot restore failed after teardown: "
            f"restore_phase={context.restore_phase} "
            f"combat_session_id={context.combat_session_id} "
            f"schema_version={context.schema_version} "
            f"contract_version={context.contract_version} "
            f"snapshot_id={context.snapshot_id} "
            f"original_exception_type={context.original_exception_type} "
            f"original_exception_message={context.original_exception_message}"
        )


def _build_restore_rejected_context(clr_exc) -> SnapshotRestoreRejectedContext:
    return SnapshotRestoreRejectedContext(
        rejection_codes=_str_list(getattr(clr_exc, "Reasons", None)),
        unsupported_field_paths=_str_list(getattr(clr_exc, "UnsupportedFieldPaths", None)),
        clr_exception_type=str(clr_exc.GetType().FullName) if hasattr(clr_exc, "GetType") else type(clr_exc).__name__,
        raw_message=str(getattr(clr_exc, "Message", clr_exc)),
    )


def _build_restore_failed_context(clr_exc) -> SnapshotRestoreFailedContext:
    return SnapshotRestoreFailedContext(
        restore_phase=_str_or_none(getattr(clr_exc, "RestorePhase", None)),
        combat_session_id=_str_or_none(getattr(clr_exc, "CombatSessionId", None)),
        schema_version=_str_or_none(getattr(clr_exc, "SchemaVersion", None)),
        contract_version=_str_or_none(getattr(clr_exc, "ContractVersion", None)),
        snapshot_id=_str_or_none(getattr(clr_exc, "SnapshotId", None)),
        original_exception_type=_str_or_none(getattr(clr_exc, "OriginalExceptionType", None)),
        original_exception_message=_str_or_none(getattr(clr_exc, "OriginalExceptionMessage", None)),
        clr_exception_type=str(clr_exc.GetType().FullName) if hasattr(clr_exc, "GetType") else type(clr_exc).__name__,
        raw_message=str(getattr(clr_exc, "Message", clr_exc)),
    )


def _restore_validation_result_from_clr(result) -> RestoreValidationResult:
    return RestoreValidationResult(
        eligible=bool(result.Eligible),
        rejection_codes=_str_list(result.RejectionCodes),
        unsupported_field_paths=_str_list(result.UnsupportedFieldPaths),
    )


def _restore_capabilities_from_clr(result) -> RestoreCapabilities:
    return RestoreCapabilities(
        restore_api_version=str(result.RestoreApiVersion),
        milestone=str(result.Milestone),
        contract_version=str(result.ContractVersion),
        contract_sha256=str(result.ContractSha256),
        snapshot_schema_version=str(result.SnapshotSchemaVersion),
        snapshot_schema_sha256=str(result.SnapshotSchemaSha256),
        supported_completeness=_str_list(result.SupportedCompleteness),
        supports_combat_history=bool(result.SupportsCombatHistory),
        supports_pets=bool(result.SupportsPets),
        supports_pending_choice=bool(result.SupportsPendingChoice),
        supports_pending_target=bool(result.SupportsPendingTarget),
        supports_action_continuation=bool(result.SupportsActionContinuation),
        supported_power_scope=_str_list(result.SupportedPowerScope),
        unsupported_power_internal_data_classes=_str_list(result.UnsupportedPowerInternalDataClasses),
        rejection_codes=_str_list(result.RejectionCodes),
        transaction_model=str(result.TransactionModel),
        rollback_after_teardown=bool(result.RollbackAfterTeardown),
        issues_new_combat_session=bool(result.IssuesNewCombatSession),
        preserves_stable_ids=bool(result.PreservesStableIds),
    )


class LiveCombatSession:
    """One instance per episode. Holds the shared GameInstance's live progress across
    every real (committed) decision. Construct one per episode (mirrors CombatEnv's own
    per-episode lifetime), not one per process."""

    def __init__(
        self,
        repo_root: "Any | None" = None,
        use_legal_action_cache: bool = True,
        whole_run_mode: bool = False,
    ) -> None:
        ensure_loaded(repo_root)
        self._repo_root = repo_root
        self._emulator = BattleEmulator(
            repo_root=repo_root,
            use_legal_action_cache=use_legal_action_cache,
            whole_run_mode=whole_run_mode,
        )
        self._access: GameAccess | None = None
        self._game = self._initial_game()
        self._lease_holder = object()
        self._lease: GameLease | None = None
        self._current_frame: "DecisionFrame | None" = None
        self.last_step_resynchronized: bool = False
        self.resynchronize_count: int = 0
        self.step_count: int = 0
        # Phase 3A.3 (Action fault contract): Python's own fault-tracking, kept in sync
        # with the Emulator's `_sessionFaulted` - see module-level `ActionFaultContext`
        # docstring. Cleared to False only on a SUCCESSFUL `ResetFromScenario` (mirrors
        # the C# design exactly: "Resetが試みられた"ではなく"Resetが成功した"でのみ解除).
        self._session_faulted: bool = False
        self.last_fault_context: "ActionFaultContext | None" = None

    def _mutating_game(self):
        assert self._access is not None
        if self._lease is None:
            self._lease = self._access.claim(self._lease_holder, LeaseState.COMBAT)
        return self._access.mutating_game(self._lease)

    @property
    def lease_holder(self) -> object:
        """The opaque holder used when a transfer commits this session to COMBAT."""
        return self._lease_holder

    def accept_transferred_lease(self, lease: GameLease) -> None:
        """Install the COMBAT lease made by ``GameAccess.commit`` without claiming.

        Adoption is intentionally possible while the access state is TRANSFERRING.  The
        session receives no mutable capability until this method is given the committed
        COMBAT lease, so no caller can supersede the hand-off by calling ``claim``.
        """
        assert self._access is not None
        if lease.state is not LeaseState.COMBAT or lease.holder is not self._lease_holder:
            raise GameAccessError("LiveCombatSession received a lease for the wrong holder/state")
        self._access.mutating_game(lease)
        self._lease = lease

    def begin_lease_transfer(self) -> GameLease:
        """Withdraw the current COMBAT lease before returning control to Whole Run."""
        assert self._access is not None
        if self._lease is None:
            raise GameAccessError("LiveCombatSession has no COMBAT lease to transfer")
        transfer = self._access.begin(self._lease)
        self._lease = None
        return transfer

    def _initial_game(self):
        self._access = process_game_access(lambda: ensure_loaded(self._repo_root)["GameInstance"]())
        return self._access.observation_game()

    def adopt_current_combat(self) -> BattleState:
        """Observe the live whole-run combat without reset/restore or a lease claim."""
        self._game = self._initial_game()
        obs = self._game.GetObservation()
        state = to_plain(obs.State)
        enemy_max_hps = {
            enemy["index"]: enemy["maxHp"]
            for enemy in state.get("enemies") or []
            if enemy.get("index") is not None and enemy.get("maxHp") is not None
        }
        legal_actions = legal_actions_to_list(self._game.GetLegalActions())
        try:
            turn_number = state["turnNumber"]
        except KeyError as exc:
            raise RuntimeError(
                "cannot adopt current combat: adopted state has no turnNumber "
                f"(state keys: {sorted(state)})"
            ) from exc
        if turn_number is None:
            # Null turnNumber is the schema's marker for a non-combat observation, so this
            # names what was adopted instead of dumping the whole board: engine_state
            # carries the full hand, piles and enemies and runs to several KB.
            raise RuntimeError(
                "cannot adopt current combat: adopted state has a null turnNumber, which "
                "the combat state schema uses for a non-combat observation "
                f"(state keys: {sorted(state)})"
            )
        turn = int(turn_number)
        battle_state = self._emulator._wrap(  # noqa: SLF001 - established live-session boundary
            obs,
            turn=turn,
            enemy_max_hps=enemy_max_hps,
            legal_actions=legal_actions,
        )
        self._current_frame = battle_state.decision_frame
        return battle_state

    def close(self) -> None:
        """Deterministically releases this session's current lease, if it still owns it."""
        lease, self._lease = self._lease, None
        if lease is None:
            return
        try:
            assert self._access is not None
            self._access.release(lease)
        except GameAccessError:
            # A newer instance may have superseded this session.  Its lease must not
            # be released by cleanup of this stale session.
            pass

    def _ensure_session_not_faulted(self) -> None:
        if self._session_faulted:
            raise FaultedCombatSessionError(
                f"Rejected: this LiveCombatSession's combat session "
                f"(combat_session_id={self._current_frame.combat_session_id if self._current_frame else None}) "
                "faulted on a previous action and must not be read from or continued - its state may be partially "
                "updated. Call start_combat()/resume_from()/restore_snapshot()/restore_snapshot_json() "
                "to start a new combat.",
                combat_session_id=self._current_frame.combat_session_id if self._current_frame else None,
            )

    def start_combat(self, scenario_spec: dict) -> BattleState:
        """Calls `ResetFromScenario` exactly once - the contract's "StartCombat". Never
        call this again for the same episode; use `step()` for every subsequent
        decision."""
        scenario = build_scenario_from_spec(scenario_spec)
        self._game = self._mutating_game()
        try:
            reset_result = self._game.ResetFromScenario(scenario)
        except _quiescent_exception_type() as exc:  # noqa: BLE001 - re-raised as our own type below
            raise QuiescentBoundaryViolation(str(exc)) from exc

        obs = reset_result.Observation
        enemy_max_hps = {e["index"]: e["maxHp"] for e in to_plain(obs.State).get("enemies") or []}
        legal_actions = legal_actions_to_list(reset_result.LegalActions)
        battle_state = self._emulator._wrap(  # noqa: SLF001 - same white-box pattern used throughout this package
            obs, turn=1, enemy_max_hps=enemy_max_hps, legal_actions=legal_actions
        )
        self._current_frame = battle_state.decision_frame
        self._session_faulted = False  # only on this success path - see _ensure_session_not_faulted's docstring note
        return battle_state

    def validate_restore_snapshot(self, snapshot) -> RestoreValidationResult:
        """Side-effect-free Restore dry run through the public CLR API."""
        game = self._game or self._observation_game()
        return _restore_validation_result_from_clr(bridge_validate_restore_snapshot(game, snapshot))

    def validate_restore_snapshot_json(self, json_text: str) -> RestoreValidationResult:
        """Side-effect-free Restore dry run through the public CLR JSON API."""
        game = self._game or self._observation_game()
        return _restore_validation_result_from_clr(bridge_validate_restore_snapshot_json(game, json_text))

    def get_restore_capabilities(self) -> RestoreCapabilities:
        """Returns the live CLR Restore capability contract as a Python dataclass."""
        game = self._game or self._observation_game()
        return _restore_capabilities_from_clr(bridge_get_restore_capabilities(game))

    def _observation_game(self):
        assert self._access is not None
        return self._access.observation_game()

    def _wrap_restore_result(self, result) -> BattleState:
        obs = result.Observation
        enemy_max_hps = {e["index"]: e["maxHp"] for e in to_plain(obs.State).get("enemies") or []}
        legal_actions = legal_actions_to_list(result.LegalActions)
        return self._emulator._wrap(  # noqa: SLF001 - established LiveCombatSession integration point
            obs,
            turn=int(getattr(obs, "Turn", 1) or 1),
            enemy_max_hps=enemy_max_hps,
            legal_actions=legal_actions,
        )

    def _handle_restore_failed(self, clr_exc) -> "None":
        context = _build_restore_failed_context(clr_exc)
        self._session_faulted = True
        raise SnapshotRestoreFailedError(context) from clr_exc

    def _finish_restore(self, result) -> BattleState:
        """Shared tail of `restore_snapshot()`/`restore_snapshot_json()`: wraps the CLR
        restore result and commits it as this session's current frame.

        Deliberately does NOT check for `SnapshotRestoreMissingMoveError` here. Restored
        boards come back with their Intents intact (measured; see that exception's own
        docstring), and even if one did not, rejecting at Restore time would reject the
        restores that never end the turn from this exact state - card-play evaluation,
        Branch Worker candidate scoring - along with it. The check instead runs in
        `step()`, gated on the one action that could trigger the hang
        (`action_type == "system"`, i.e. End Turn).
        """
        battle_state = self._wrap_restore_result(result)
        self._current_frame = battle_state.decision_frame
        self._session_faulted = False
        return battle_state

    def restore_snapshot(self, snapshot) -> BattleState:
        """Restores a clean snapshot and establishes a fresh live session identity.

        Raw CLR `CombatStateSnapshot` objects use the object API. Python
        `CombatStateSnapshot` DTOs are converted by the bridge through the existing
        canonical JSON serializer rather than a second parser.
        """
        self._game = self._mutating_game()
        try:
            result = bridge_restore_snapshot(self._game, snapshot)
        except _snapshot_restore_rejected_exception_type() as exc:  # noqa: BLE001
            raise SnapshotRestoreRejectedError(_build_restore_rejected_context(exc)) from exc
        except _snapshot_restore_failed_exception_type() as exc:  # noqa: BLE001
            self._handle_restore_failed(exc)
        except _quiescent_exception_type() as exc:  # noqa: BLE001
            raise QuiescentBoundaryViolation(str(exc)) from exc

        return self._finish_restore(result)

    def restore_snapshot_json(self, json_text: str) -> BattleState:
        """Restores from canonical Snapshot JSON through the public CLR JSON API."""
        self._game = self._mutating_game()
        try:
            result = bridge_restore_snapshot_json(self._game, json_text)
        except _snapshot_restore_rejected_exception_type() as exc:  # noqa: BLE001
            raise SnapshotRestoreRejectedError(_build_restore_rejected_context(exc)) from exc
        except _snapshot_restore_failed_exception_type() as exc:  # noqa: BLE001
            self._handle_restore_failed(exc)
        except _quiescent_exception_type() as exc:  # noqa: BLE001
            raise QuiescentBoundaryViolation(str(exc)) from exc

        return self._finish_restore(result)

    def resume_from(self, battle_state: BattleState) -> BattleState:
        """`CombatEnv.adopt_state()`'s counterpart to `start_combat()` - establishes
        `battle_state` (already initialized elsewhere, typically `preflight_validate()`'s
        own `BattleEmulator.initialize()` call, done once per scenario to validate it
        before an episode may even start) as this session's live starting point.

        Restoring via `ResetFromScenario` always mints a fresh `combatSessionId`
        (Emulator commit `ce7ecc2`'s `BeginNewCombatSession()`, called from every
        `ResetFromScenario`) - `battle_state`'s OWN `decision_frame` therefore can never
        equal the post-restore live frame, so this always returns a freshly-fetched
        `BattleState` reflecting the actual restored session/step identity, never
        `battle_state` unchanged. Deliberately conservative: always restores
        unconditionally (does not attempt to detect "the live instance already happens
        to match, skip the restore" the way `step()`'s `_is_still_current()` does for
        the steady-state case) - a correct-but-cheaper optimization for this one
        episode-start call is a follow-up, not attempted in this pass, per this task's
        own note against adding cleverness to a correctness-critical path without
        separate verification."""
        self._game = self._mutating_game()
        scenario = build_scenario_from_state(battle_state.engine_state, battle_state.shuffle_rng_seed)
        try:
            self._game.ResetFromScenario(scenario)
        except _quiescent_exception_type() as exc:  # noqa: BLE001
            raise QuiescentBoundaryViolation(str(exc)) from exc
        legal_actions = legal_actions_to_list(self._game.GetLegalActions())
        obs = self._game.GetObservation()
        fresh_state = self._emulator._wrap(  # noqa: SLF001 - same white-box pattern used throughout this package
            obs,
            turn=battle_state.turn,
            enemy_max_hps=battle_state.enemy_max_hps,
            shuffle_rng_seed=battle_state.shuffle_rng_seed,
            legal_actions=legal_actions,
        )
        self._current_frame = fresh_state.decision_frame
        self._session_faulted = False  # only on this success path - see _ensure_session_not_faulted's docstring note
        return fresh_state

    def get_observation(self):
        """Phase 3A.3: explicit, fault-gated wrapper around `GameInstance.GetObservation()`
        - added alongside the Action fault contract so that "fault後のObservation取得を
        拒否" (this task's required test) has a concrete method to call. Returns the raw
        CLR `GameObservation` (same object `_is_still_current()` already reads) - callers
        needing plain Python data should use `to_plain(session.get_observation().State)`,
        matching the existing pattern elsewhere in this package."""
        if self._game is None:
            raise RuntimeError("start_combat()/resume_from() must be called before get_observation()")
        self._ensure_session_not_faulted()
        try:
            return self._game.GetObservation()
        except _faulted_combat_session_exception_type() as exc:  # noqa: BLE001 - defensive, see FaultedCombatSessionError docstring
            raise FaultedCombatSessionError(str(exc)) from exc

    def get_legal_actions(self) -> list[dict]:
        """Phase 3A.3: explicit, fault-gated wrapper around `GameInstance.GetLegalActions()`
        - see `get_observation()`'s docstring for why this exists as its own method now."""
        if self._game is None:
            raise RuntimeError("start_combat()/resume_from() must be called before get_legal_actions()")
        self._ensure_session_not_faulted()
        try:
            return legal_actions_to_list(self._game.GetLegalActions())
        except _faulted_combat_session_exception_type() as exc:  # noqa: BLE001
            raise FaultedCombatSessionError(str(exc)) from exc

    def capture_snapshot(self):
        """Canonical CombatStateSnapshot contract v0.3 Phase 2A: explicit, diagnostic-only
        capture of the full live combat state at the CURRENT Quiescent Decision Boundary,
        via `GameInstance.CaptureSnapshotJson()` (Emulator commit `5766528`). Returns a
        `combat_state_snapshot.CombatStateSnapshot` - a type family deliberately SEPARATE
        from `BattleState`/Observation (Phase 1), never fed into `BattleEmulator.
        apply_action()`/`HeuristicAgent`/Policy/Choice Policy.

        Never called automatically by `start_combat()`/`resume_from()`/`step()` - the
        normal decision loop does not capture a Snapshot on every decision (per this
        task's "通常decisionループでは自動Captureしない" instruction); only an explicit
        caller request (e.g. a diagnostic script, or a future Phase 2B/3 consumer)
        invokes this. Read-only with respect to live state - the Emulator's own Capture
        implementation is verified side-effect-free (no hook fires, no RNG advances, no
        change to what Observation/LegalActions would return - see the Emulator's Phase
        2A acceptance tests)."""
        if self._game is None:
            raise RuntimeError("start_combat()/resume_from() must be called before capture_snapshot()")
        self._ensure_session_not_faulted()
        try:
            json_text = self._game.CaptureSnapshotJson()
        except _quiescent_exception_type() as exc:  # noqa: BLE001
            raise QuiescentBoundaryViolation(str(exc)) from exc
        except _faulted_combat_session_exception_type() as exc:  # noqa: BLE001 - defensive, see FaultedCombatSessionError docstring
            raise FaultedCombatSessionError(str(exc)) from exc

        from combat_state_snapshot import CombatStateSnapshot

        return CombatStateSnapshot.from_json(json_text)

    def _is_still_current(self) -> bool:
        """Cheap, read-only check (`GameInstance.GetObservation()`, no restore)
        confirming nothing else touched the shared GameInstance since this session's
        last step - see module docstring."""
        obs = self._game.GetObservation()
        return (
            getattr(obs, "CombatSessionId", None) == self._current_frame.combat_session_id
            and int(getattr(obs, "StepIndex", -1)) == self._current_frame.step_index
        )

    def _resynchronize(self, battle_state: BattleState) -> None:
        """Re-establishes this session's own retained state on the shared GameInstance
        via exactly one ResetFromScenario call - reuses build_scenario_from_state(), the
        same mechanism BattleEmulator._restore() already uses, rather than duplicating
        it. See module docstring - a deliberate, counted fallback, not a silent one."""
        scenario = build_scenario_from_state(battle_state.engine_state, battle_state.shuffle_rng_seed)
        try:
            self._mutating_game().ResetFromScenario(scenario)
        except _quiescent_exception_type() as exc:  # noqa: BLE001
            raise QuiescentBoundaryViolation(str(exc)) from exc
        self.last_step_resynchronized = True
        self.resynchronize_count += 1
        self._session_faulted = False  # only on this success path - see _ensure_session_not_faulted's docstring note

    def _handle_action_fault(
        self, clr_exc, action: "dict | None", target_index: "int | None", target_enemy_index: "int | None"
    ) -> "None":
        """Phase 3A.3: translates a caught CLR `ActionFaultedException` into
        `ActionExecutionError`, marking this session faulted FIRST (before raising) so
        that even if a caller's exception handler itself calls back into this session,
        it is already correctly rejected. Never returns - always raises."""
        context = _build_fault_context(clr_exc, self._current_frame, action, target_index, target_enemy_index)
        self._session_faulted = True
        self.last_fault_context = context
        raise ActionExecutionError(context) from clr_exc

    def step(
        self,
        battle_state: BattleState,
        action: dict,
        target_index: "int | None" = None,
        target_enemy_index: "int | None" = None,
        continuation_resolver: "Callable | None" = None,
        continuation_deadline: "float | None" = None,
        stop_at_pending: bool = False,
    ) -> BattleState:
        """Commits `action` (chosen against `battle_state`, this session's own last
        return value) via a direct `Step()` call - no restore, unless
        `_is_still_current()` detects interference (see `_resynchronize()`).
        ActionContinuation is resolved via repeated `Step()` calls on the same live
        `game`, exactly as `BattleEmulator.apply_action()` already does - the only
        difference from that method is the absence of an unconditional restore at entry.

        `stop_at_pending`: when True, returns immediately with a genuinely
        multi-candidate ActionContinuation PendingChoice still open (boundary=pending)
        instead of auto-resolving it via `continuation_resolver` - callers that need to
        treat each Pending decision as its own branch point (Search's Branch Worker
        Pool, and Main replaying a Search-committed Plan Path that passed through one)
        set this. The engine's OWN transparent single-real-candidate auto-confirm
        (`CardSelectorPrefs.RequireManualConfirmation`, see
        `C:\\STS2_Emulator\\docs\\reports\\pending_choice_exposure_20260803.md`) never
        creates a PendingChoice at all, so it is unaffected either way - this flag only
        ever changes behavior when there is a genuine multi-candidate choice open.

        `battle_state.decision_frame` must equal this session's current frame - this is
        the DecisionFrame validation this task's contract calls for: an action chosen
        against a stale/earlier battle_state (e.g. a caller that cached a decision's
        legal_actions across a state change, against legal_action_schema.json's own
        rule) is rejected here rather than silently misexecuted.

        End Turn (`action_type == "system"`) is additionally rejected up front, before
        ever calling into the CLR, if a living enemy still has no current Move
        (`Intent.stateId == "UNSET_MOVE"`) - see `SnapshotRestoreMissingMoveError`.
        Without this guard, that same situation makes the underlying engine hang for
        ~15s and then raise an opaque wait-timeout instead of the real cause.

        Phase 3A.3 (Action fault contract): if the underlying C# action genuinely faults
        or is canceled, this method raises `ActionExecutionError` INSTEAD OF returning a
        `BattleState` - no StepResult/Observation/LegalActions from the faulted attempt
        is ever wrapped or returned, so a caller recording a trajectory (e.g.
        `CombatEnv.step()`, which only assigns `self._state = next_state` AFTER this call
        returns) can never observe or persist a faulted transition. The session is marked
        faulted before this raises; every subsequent call to `step()`/`get_observation()`/
        `get_legal_actions()`/`capture_snapshot()` on this same instance raises
        `FaultedCombatSessionError` immediately, without attempting the same action again
        and without silently calling `ResetFromScenario` to paper over it - only an
        explicit new `start_combat()`/`resume_from()` call clears the fault."""
        if self._game is None or self._current_frame is None:
            raise RuntimeError("start_combat()/resume_from() must be called before step()")
        self._ensure_session_not_faulted()
        if battle_state.decision_frame != self._current_frame:
            raise DecisionFrameMismatchError(
                f"action submitted against stale DecisionFrame {battle_state.decision_frame!r} - "
                f"session's current frame is {self._current_frame!r}"
            )
        if action.get("action_type") == "system":
            missing = _enemies_missing_current_move(battle_state.engine_state)
            if missing:
                raise SnapshotRestoreMissingMoveError(missing)

        self.last_step_resynchronized = False
        game = self._mutating_game()
        if not self._is_still_current():
            self._resynchronize(battle_state)

        try:
            next_state = self._emulator.step_live_action(
                game, battle_state, action, target_index=target_index, target_enemy_index=target_enemy_index
            )
        except _quiescent_exception_type() as exc:  # noqa: BLE001
            raise QuiescentBoundaryViolation(str(exc)) from exc
        except _action_faulted_exception_type() as exc:  # noqa: BLE001
            self._handle_action_fault(exc, action, target_index, target_enemy_index)
        except _faulted_combat_session_exception_type() as exc:  # noqa: BLE001 - defensive, see FaultedCombatSessionError docstring
            self._session_faulted = True
            raise FaultedCombatSessionError(str(exc)) from exc

        continuation_steps = 0
        resolver = continuation_resolver or self._emulator._default_choose_action_continuation_live  # noqa: SLF001
        while is_action_continuation_pending_choice(next_state.engine_state):
            if stop_at_pending:
                break
            continuation_steps += 1
            if continuation_steps > MAX_CONTINUATION_STEPS:
                raise RuntimeError("ActionContinuation did not resolve within 50 continuation steps.")
            legal = self._emulator.enumerate_legal_actions(next_state)
            continuation_action = resolver(game, next_state, legal, continuation_deadline)
            try:
                next_state = self._emulator.step_live_action(game, next_state, continuation_action)
            except _quiescent_exception_type() as exc:  # noqa: BLE001
                raise QuiescentBoundaryViolation(str(exc)) from exc
            except _action_faulted_exception_type() as exc:  # noqa: BLE001
                self._handle_action_fault(exc, continuation_action, None, None)
            except _faulted_combat_session_exception_type() as exc:  # noqa: BLE001
                self._session_faulted = True
                raise FaultedCombatSessionError(str(exc)) from exc

        self.step_count += 1
        self._current_frame = next_state.decision_frame
        return next_state
