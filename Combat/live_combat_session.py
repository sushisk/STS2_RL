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

from battle_emulator import (
    BattleEmulator,
    BattleState,
    DecisionFrame,
    build_scenario_from_spec,
    build_scenario_from_state,
    is_action_continuation_pending_choice,
)
from emulator_bridge import ensure_loaded, legal_actions_to_list, shared_game_instance, to_plain

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


class LiveCombatSession:
    """One instance per episode. Holds the shared GameInstance's live progress across
    every real (committed) decision. Construct one per episode (mirrors CombatEnv's own
    per-episode lifetime), not one per process."""

    def __init__(self, repo_root: "Any | None" = None, use_legal_action_cache: bool = True) -> None:
        ensure_loaded(repo_root)
        self._repo_root = repo_root
        self._emulator = BattleEmulator(repo_root=repo_root, use_legal_action_cache=use_legal_action_cache)
        self._game = None
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

    def _ensure_session_not_faulted(self) -> None:
        if self._session_faulted:
            raise FaultedCombatSessionError(
                f"Rejected: this LiveCombatSession's combat session "
                f"(combat_session_id={self._current_frame.combat_session_id if self._current_frame else None}) "
                "faulted on a previous action and must not be read from or continued - its state may be partially "
                "updated. Call start_combat()/resume_from() to start a new combat.",
                combat_session_id=self._current_frame.combat_session_id if self._current_frame else None,
            )

    def start_combat(self, scenario_spec: dict) -> BattleState:
        """Calls `ResetFromScenario` exactly once - the contract's "StartCombat". Never
        call this again for the same episode; use `step()` for every subsequent
        decision."""
        scenario = build_scenario_from_spec(scenario_spec)
        self._game = shared_game_instance(self._repo_root)
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
        self._game = shared_game_instance(self._repo_root)
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
            self._game.ResetFromScenario(scenario)
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
    ) -> BattleState:
        """Commits `action` (chosen against `battle_state`, this session's own last
        return value) via a direct `Step()` call - no restore, unless
        `_is_still_current()` detects interference (see `_resynchronize()`).
        ActionContinuation is resolved via repeated `Step()` calls on the same live
        `game`, exactly as `BattleEmulator.apply_action()` already does - the only
        difference from that method is the absence of an unconditional restore at entry.

        `battle_state.decision_frame` must equal this session's current frame - this is
        the DecisionFrame validation this task's contract calls for: an action chosen
        against a stale/earlier battle_state (e.g. a caller that cached a decision's
        legal_actions across a state change, against legal_action_schema.json's own
        rule) is rejected here rather than silently misexecuted.

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

        self.last_step_resynchronized = False
        if not self._is_still_current():
            self._resynchronize(battle_state)

        try:
            next_state = self._emulator.step_live_action(
                self._game, battle_state, action, target_index=target_index, target_enemy_index=target_enemy_index
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
            continuation_steps += 1
            if continuation_steps > MAX_CONTINUATION_STEPS:
                raise RuntimeError("ActionContinuation did not resolve within 50 continuation steps.")
            legal = self._emulator.enumerate_legal_actions(next_state)
            continuation_action = resolver(self._game, next_state, legal, continuation_deadline)
            try:
                next_state = self._emulator.step_live_action(self._game, next_state, continuation_action)
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
