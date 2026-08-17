"""Decision Context / Decision Signature primitives - Combat execution infrastructure
Phase 2 (docs/architecture/combat/mermaid_combat_snapshot_replay_detail.mermaid,
docs/architecture/combat/mermaid_combat_main_loop_detail.mermaid).

Scope (per this task's instruction): foundational data types only, working against the
EXISTING single-process bridge (`Combat/live_combat_session.py`,
`Combat/combat_state_snapshot.py`). No multi-process Branch Worker Pool (that is a later
phase - `replay_decision_context()` below is written so it can be reused unchanged by
that phase, but it only ever drives ONE `LiveCombatSession` here), no RNG Hypothesis ID
generation (`search_hypothesis_id` is reserved but never populated by this phase), no
Main Process state machine (only the small bookkeeping primitives that machine would
call are provided).

Decision Signature (DC_SIGNATURE, lightweight form fixed 2026-08-01) is explicitly NOT a
board-state signature: RL never predicts/recomputes HP/Block/Energy/Pile
composition/Power/Relic/enemy state (NOTE_NO_BOARD_PREDICTION in the snapshot-replay
diagram, NOTE_NO_BOARD_PREDICTION_MAIN in the main-loop diagram). `DecisionSignature`
below is therefore structurally incapable of carrying any such field - see its own
docstring and `Combat/tests/test_decision_context.py`'s structural test.
"""

from __future__ import annotations

import json
import dataclasses
from collections import Counter
from dataclasses import dataclass, field, fields
from typing import TYPE_CHECKING, Optional

from battle_emulator import BattleState, is_action_continuation_pending_choice, pending_choice_metadata
from search.replay_draw_restore import (
    VisibleDrawConstraint,
    VisibleDrawConstraints,
    VisibleDrawTransitionEvidence,
    visible_draw_constraints_from_committed_transition,
    visible_draw_transition_evidence_from_committed_transition,
)

if TYPE_CHECKING:
    from combat_state_snapshot import CombatStateSnapshot
    from live_combat_session import LiveCombatSession

# ---------------------------------------------------------------------------
# Boundary / choice-scope vocabulary - taken from what LiveCombatSession/BattleState
# actually expose (BattleState.is_terminal, engine_state["pendingChoice"],
# is_action_continuation_pending_choice()), NOT invented. "fault" is a legitimate value
# of the Boundary domain per DC_SIGNATURE ("到達したBoundary種別(Stable/Pending/
# Terminal/Fault)"), but LiveCombatSession never RETURNS a BattleState for a faulted
# Step - it raises ActionExecutionError/FaultedCombatSessionError instead (see that
# module's docstring). `boundary_of_battle_state()` below therefore never produces
# "fault" itself; that value exists so a caller building a DecisionSignature to
# describe/log an already-caught fault (never from `from_battle_state()`) has a place to
# put it.
# ---------------------------------------------------------------------------

BOUNDARY_STABLE = "stable"
BOUNDARY_PENDING = "pending"
BOUNDARY_TERMINAL = "terminal"
BOUNDARY_FAULT = "fault"
BOUNDARY_VALUES = frozenset({BOUNDARY_STABLE, BOUNDARY_PENDING, BOUNDARY_TERMINAL, BOUNDARY_FAULT})

CHOICE_SCOPE_TOP_LEVEL = "TopLevel"
CHOICE_SCOPE_ACTION_CONTINUATION = "ActionContinuation"


def boundary_of_battle_state(battle_state: "BattleState") -> str:
    """Derives the Boundary kind DC_SIGNATURE asks for from what `BattleState` already
    carries - no separate API call, per NOTE_NO_REREAD (candidate/boundary information
    always comes from the Decision Result already returned, never re-fetched)."""
    if battle_state.is_terminal:
        return BOUNDARY_TERMINAL
    if pending_choice_metadata(battle_state.engine_state):
        return BOUNDARY_PENDING
    return BOUNDARY_STABLE


class SemanticActionUnresolvedError(RuntimeError):
    """Raised by `SemanticAction.resolve()` when a recorded Semantic Action no longer
    matches anything in the current Decision Result's Choice Payload/legal actions - the
    SUB_REPLAY subgraph's REPLAY_RESOLVABLE=No / RESOLVABLE=No case ("Fault(再解決不能)
    として記録...種別: replay mismatch"). Caught by `replay_decision_context()` and
    turned into a `ReplayMismatch` outcome - deliberately NOT allowed to bubble out like
    a genuine `live_combat_session.py` Emulator exception, since a candidate becoming
    unresolvable is an expected, well-defined replay-mismatch case per the mermaid
    contract, not an Emulator-side fault."""


class PendingSnapshotRestoreViolationError(RuntimeError):
    """Raised when a Decision Context tries to Restore from a Pending/non-Stable root
    snapshot instead of restoring the prior Stable root and replaying to Pending."""


@dataclass(frozen=True)
class CombatStartReplayRoot:
    """The Combat Start Replay Root - `DecisionContext.root_snapshot`'s alternate "root"
    kind for Start-of-Combat Pending (TOOLBOX/CHOICES_PARADOX/GAMBLING_CHIP etc.), which
    fires immediately out of `ResetFromScenario`/`start_combat()`, before any Stable
    boundary has ever been reached - there is no prior Held Stable Snapshot to Restore.

    Holds exactly what is needed to start the SAME combat again from scratch (Scenario,
    RNG seed, Deck, Relic - everything `battle_emulator.build_scenario_from_spec()`
    consumes) - never a captured Pending Snapshot. A sibling Worker reproduces the
    Start-of-Combat Pending by calling `session.start_combat(scenario_spec)` again with
    this SAME spec (never `restore_snapshot()`), then replaying `replay_prefix` exactly
    like every other Bootstrap+Replay case. `scenario_spec` is a plain JSON-safe dict
    (the same shape `LiveCombatSession.start_combat()` already accepts), so it needs no
    special IPC handling - unlike a `CombatStateSnapshot`, it never wraps a live CLR
    object."""

    scenario_spec: dict


@dataclass(frozen=True)
class SemanticAction:
    """The 'Semantic Action' side of DC_SIGNATURE's Semantic Action <-> ActionId
    correspondence: (action_type, semantic_key). Deliberately NOT an action_id -
    action_id "is valid only within the DecisionFrame it was issued against"
    (combat_state_contract.v0.3.md, and `live_combat_session.py`'s own
    `DecisionFrameMismatchError` docstring) and is re-resolved fresh against whatever
    legal_actions the CURRENT Decision Result reports every time this is replayed. The
    semantic key is authored by the Emulator for exactly this re-resolution purpose; this
    package only echoes it."""

    action_type: str
    semantic_key: str

    def resolve(self, legal_actions: "list[dict]") -> dict:
        """Re-resolves this Semantic Action against `legal_actions` (always the CURRENT
        Decision Result's own Choice Payload - never a fresh GetLegalActions() re-read,
        per NOTE_NO_REREAD). Returns the matching legal-action dict (carries the
        `action_id` to actually Step with). Raises `SemanticActionUnresolvedError` - not
        silently returning None - if nothing matches, so callers can't accidentally step
        with the wrong candidate."""
        for action in legal_actions:
            if (
                action.get("action_type") == self.action_type
                and action.get("semantic_key", "") == self.semantic_key
            ):
                return action
        raise SemanticActionUnresolvedError(
            f"{self!r} did not resolve against any of {len(legal_actions)} legal action(s)"
        )


def _candidate_semantic_key(action: dict) -> tuple:
    return (action.get("action_type"), action.get("semantic_key", ""))


def candidate_multiset(legal_actions: "list[dict]") -> "tuple[tuple[tuple, int], ...]":
    """Builds the canonical multiset DC_SIGNATURE requires for Pending's candidate
    Semantic Key set: "canonical multiset - 重複を保持したまま比較する。setに潰して
    重複情報を失ってはならない". Two candidates sharing the exact same Semantic Key
    (e.g. two copies of the same card in hand, same legal target) collapse to ONE key
    with count=2 here - never silently deduplicated to count=1 by accidentally passing
    through a `set`. Represented as a sorted tuple of (key, count) pairs (not a bare
    `Counter`) so the result is hashable/immutable, matching this dataclass family's
    `frozen=True` fields; sorted by `repr()` since `card_id`/`target_type` may be `None`
    and plain tuple sorting cannot compare `None` against `str`."""
    counts = Counter(_candidate_semantic_key(a) for a in legal_actions)
    return tuple(sorted(counts.items(), key=lambda kv: repr(kv[0])))


@dataclass(frozen=True)
class DecisionSignature:
    """Decision Signature (DC_SIGNATURE) - "Action実行とDecision Boundary制御情報の
    確認専用。盤面Signatureではない". Confirms ONLY: the pre/post-step State Identity,
    the Semantic Action <-> resolved ActionId correspondence (one-time, not a persistent
    cross-state id), the semantic key/selection the EMULATOR ITSELF reports as resolved
    (echoed from the Decision Result/StepResult, never independently recomputed here),
    the Boundary kind reached, and (Pending only) choice_scope/choice_kind/the candidate
    Semantic Key multiset.

    Hard constraint (structural, not just convention): this dataclass has a FIXED field
    list and no `extra`/`metadata` catch-all, so it is structurally incapable of
    smuggling in a board-state field (HP/Block/Energy/Pile composition/Power/
    Relic/enemy state) - see `Combat/tests/test_decision_context.py`'s structural test,
    which asserts none of `dataclasses.fields(DecisionSignature)` match those names.

    Two comparison methods are provided, deliberately different, because
    `combat_session_id` and `resolved_action_id` are NOT stable across a Restore
    boundary:
      * `LiveCombatSession.restore_snapshot()`/`restore_snapshot_json()` always mint a
        brand new `combatSessionId` (confirmed empirically here and documented in that
        module's own docstrings/tests) - two independent restores of the very same
        Snapshot get two different session ids, even though `step_index` deterministically
        restarts from the same baseline both times.
      * `action_id` "is valid only within the DecisionFrame it was issued against"
        (combat_state_contract.v0.3.md §5) - DC_SIGNATURE's own text says as much
        ("action_id自体を状態を跨いで永続的な識別子として比較することはしない —
        従来通り").
    `__eq__`/`diff()` (dataclass default equality, all fields) are for direct,
    same-origin comparisons (e.g. unit tests constructing two signatures by hand).
    `matches_for_replay()`/`diff_for_replay()` are what `replay_decision_context()`
    actually uses for REPLAY_SIG_CHECK/CTX_SIG_CHECK - they compare everything BUT
    `combat_session_id`/`resolved_action_id`, plus a `choice_kind` mismatch is ignored
    specifically when at least one side is the Emulator's "Unsupported" catch-all (see
    `choice_kind`'s own field comment and `_CHOICE_KIND_UNCLASSIFIED`).
    """

    # --- State identity (Stable/Pending common) ---
    combat_session_id: "Optional[str]"
    step_index: int
    continuation_step_index: "Optional[int]"
    # --- Semantic Action <-> ActionId correspondence (Stable/Pending common) ---
    semantic_action: SemanticAction
    resolved_action_id: int
    # --- Emulator-reported resolved semantic key/selection (Stable/Pending common) ---
    resolved_semantic_key: str
    resolved_target_index: "Optional[int]"
    resolved_target_slot_index: "Optional[int]"
    # --- Boundary kind reached (Stable/Pending common) ---
    boundary: str
    # --- Pending-only (None/None/None whenever boundary != "pending") ---
    choice_scope: "Optional[str]" = None
    # Derived from the Emulator's own `GetPendingChoiceType()`, a closed enumeration of
    # a handful of named mechanics (Wish/GamblingChip/ChoicesParadox/Toolbox) that
    # exists primarily to support `CombatScenario.PendingChoice`'s scenario-authoring
    # reconstruction. Any Pending choice outside those falls into a single shared
    # "Unsupported" bucket, so two DIFFERENT unrecognized choices are indistinguishable
    # by this field alone whenever either side is that bucket - `diff_for_replay()`
    # accounts for this (see `_CHOICE_KIND_UNCLASSIFIED`) rather than trusting a raw
    # equality check in that case. When BOTH sides are actual recognized kinds, this
    # field IS a meaningful, enforced replay-safety signal.
    # `candidate_semantic_keys` is the structurally-grounded signal that actually
    # verifies "did we reach the same Pending choice".
    choice_kind: "Optional[str]" = None
    candidate_semantic_keys: "Optional[tuple[tuple[tuple, int], ...]]" = None

    def __post_init__(self) -> None:
        if self.boundary not in BOUNDARY_VALUES:
            raise ValueError(f"unknown Boundary {self.boundary!r} (known: {sorted(BOUNDARY_VALUES)})")
        pending_only_set = (
            self.choice_scope is not None or self.choice_kind is not None or self.candidate_semantic_keys is not None
        )
        if self.boundary != BOUNDARY_PENDING and pending_only_set:
            raise ValueError(
                "choice_scope/choice_kind/candidate_semantic_keys are Pending-only fields "
                f"(boundary={self.boundary!r})"
            )

    @classmethod
    def from_battle_state(
        cls,
        battle_state: "BattleState",
        *,
        semantic_action: SemanticAction,
        resolved_action: dict,
        target_index: "Optional[int]" = None,
        target_enemy_index: "Optional[int]" = None,
        legal_actions: "Optional[list[dict]]" = None,
    ) -> "DecisionSignature":
        """Builds a DecisionSignature from whatever `LiveCombatSession` actually returns
        after a Start/Step/Restore call - `battle_state` is that real return value
        (a `battle_emulator.BattleState`), `resolved_action` is the legal-action dict
        `SemanticAction.resolve()` matched (its `semantic_key` is the Emulator's OWN
        reported replay key, echoed verbatim - never recomputed). `legal_actions`
        defaults to `battle_state`'s own cached candidates
        (`_cached_legal_actions` - always populated for a BattleState LiveCombatSession
        itself produced), matching NOTE_NO_REREAD."""
        frame = battle_state.decision_frame
        boundary = boundary_of_battle_state(battle_state)
        choice_scope = None
        choice_kind = None
        candidate_keys = None
        if boundary == BOUNDARY_PENDING:
            pending = pending_choice_metadata(battle_state.engine_state) or {}
            choice_scope = (
                CHOICE_SCOPE_ACTION_CONTINUATION
                if is_action_continuation_pending_choice(battle_state.engine_state)
                else CHOICE_SCOPE_TOP_LEVEL
            )
            choice_kind = pending.get("choiceType")
            candidates = legal_actions if legal_actions is not None else (battle_state._cached_legal_actions or [])  # noqa: SLF001
            candidate_keys = candidate_multiset(candidates)

        return cls(
            combat_session_id=frame.combat_session_id if frame is not None else None,
            step_index=frame.step_index if frame is not None else -1,
            continuation_step_index=frame.continuation_step_index if frame is not None else None,
            semantic_action=semantic_action,
            resolved_action_id=int(resolved_action["action_id"]),
            resolved_semantic_key=resolved_action.get("semantic_key", ""),
            resolved_target_index=target_index,
            resolved_target_slot_index=target_enemy_index,
            boundary=boundary,
            choice_scope=choice_scope,
            choice_kind=choice_kind,
            candidate_semantic_keys=candidate_keys,
        )

    def diff(self, other: "DecisionSignature") -> "list[str]":
        """Field-by-field divergence report over ALL fields (including
        `combat_session_id`/`resolved_action_id`) - for direct, same-origin comparisons
        (e.g. unit tests). Use `diff_for_replay()` for SUB_REPLAY-style checks."""
        return [f.name for f in fields(self) if getattr(self, f.name) != getattr(other, f.name)]

    _REPLAY_INCOMPARABLE_FIELDS = frozenset({"combat_session_id", "resolved_action_id"})

    # The Emulator's own sentinel for "this Pending choice didn't match any of
    # GetPendingChoiceType()'s recognized mechanics" (see `choice_kind`'s field
    # comment). A `choice_kind` mismatch is only ignored for replay-safety purposes
    # when at least one side is this unreliable catch-all - two genuinely RECOGNIZED
    # kinds (e.g. "ToolboxChooseCard" vs "WishDrawToHand") still must match, since that
    # comparison IS meaningful when the Emulator actually classified both sides.
    _CHOICE_KIND_UNCLASSIFIED = "Unsupported"

    def diff_for_replay(self, other: "DecisionSignature") -> "list[str]":
        """Like `diff()` but excludes `combat_session_id`/`resolved_action_id` (never
        portable across a Restore boundary - see the class docstring), and ignores a
        `choice_kind` mismatch specifically when at least one side is the Emulator's
        "Unsupported" catch-all (see `_CHOICE_KIND_UNCLASSIFIED`) - `candidate_semantic_
        keys` is what actually verifies Pending-choice identity in that case."""
        diffs = [name for name in self.diff(other) if name not in self._REPLAY_INCOMPARABLE_FIELDS]
        if "choice_kind" in diffs and self._CHOICE_KIND_UNCLASSIFIED in (self.choice_kind, other.choice_kind):
            diffs = [name for name in diffs if name != "choice_kind"]
        return diffs

    def matches_for_replay(self, other: "DecisionSignature") -> bool:
        """REPLAY_SIG_CHECK/CTX_SIG_CHECK-style comparison: everything except
        `combat_session_id`/`resolved_action_id`. See class docstring."""
        return not self.diff_for_replay(other)


# ---------------------------------------------------------------------------
# Decision Context (DC_DEF)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplayPrefixEntry:
    """One committed Transition Record in the Replay Prefix / Plan Path.

    ``visible_draw_constraints`` contains only Gate-A+Gate-B-proven sequential draws,
    represented as ``(root-relative offset, observable_card_key)``.  No physical card
    identity is stored. ``visible_draw_tracking_blocked`` records an intervening
    DrawPile mutation that could not be explained safely; later transitions must not
    invent root-relative draw offsets past that point.
    """

    semantic_action: SemanticAction
    expected_signature: DecisionSignature
    target_index: "Optional[int]" = None
    target_enemy_index: "Optional[int]" = None
    visible_draw_constraints: VisibleDrawConstraints = ()
    visible_draw_tracking_blocked: bool = False


@dataclass
class DecisionContext:
    """Canonical Decision Context (DC_DEF) - the Search Coordinator's own record of
    where a search branch is, independent of any Worker. Fields, exactly DC_DEF's list:

    * `root_snapshot` - the Stable Root Snapshot. Carried as the `CombatStateSnapshot`
      dataclass `LiveCombatSession.capture_snapshot()` already returns, NOT its JSON text
      - `LiveCombatSession.restore_snapshot()` accepts this Python dataclass directly
      (converting it through the same canonical JSON serializer internally either way,
      per that method's own docstring), so holding the dataclass avoids a redundant
      serialize/parse round trip for the (expected) common case of "capture, then later
      restore the same object" without losing anything (the dataclass IS the cheapest
      form that is also directly usable). For a Start-of-Combat Pending Root Action
      Evaluation Segment (no Stable boundary has ever been reached yet), this field
      instead holds a `CombatStartReplayRoot` - see that type's own docstring for why a
      Combat Start Replay Root, not a captured Pending Snapshot, is used. Every consumer
      of this field must branch on `isinstance(root_snapshot, CombatStartReplayRoot)`.
    * `replay_prefix` - ordered `ReplayPrefixEntry` list from the Stable Root to here;
      resets to empty at every Stable capture (DC_STABLE/DC_PENDING; NOTE_REPLAY_VS_PLAN).
    * `plan_path` - ordered `ReplayPrefixEntry` list from the SEARCH START to here; never
      resets across an evaluation boundary (NOTE_PLAN_PATH) - semantically distinct from
      `replay_prefix` even though both share the `ReplayPrefixEntry` element type.
    * `current_decision_result` - the live `BattleState` LiveCombatSession most recently
      returned (Start/Step/Restore's own return value - never re-fetched via
      GetObservation/GetLegalActions, per NOTE_NO_REREAD).
    * `current_context_signature` - `DecisionSignature` for "have we actually arrived
      where we think we have" (CTX_SIG_CHECK).
    * `search_hypothesis_id` - reserved, NOT populated by this phase. Populated by a
      later phase per docs/architecture/combat/mermaid_combat_rng_hypothesis_detail.mermaid
      once candidate classification/pruning survives to the point an RNG Hypothesis is
      actually decided (TO_RNG in the snapshot-replay diagram) - always `None` here.
    """

    root_snapshot: "CombatStateSnapshot | CombatStartReplayRoot"
    replay_prefix: "list[ReplayPrefixEntry]"
    plan_path: "list[ReplayPrefixEntry]"
    current_decision_result: "BattleState"
    current_context_signature: DecisionSignature
    search_hypothesis_id: "Optional[str]" = None

    @classmethod
    def from_combat_start_pending(
        cls,
        combat_start_replay_root: "CombatStartReplayRoot",
        current_decision_result: "BattleState",
        current_context_signature: DecisionSignature,
    ) -> "DecisionContext":
        """Start-of-Combat Pending's own root-context constructor - structurally identical
        to `from_main_stable_capture()` (both Replay Prefix and Plan Path start empty),
        kept as a separate, honestly-named classmethod rather than reusing that one
        because the root here is never a "Stable Capture" - it is a `CombatStartReplayRoot`,
        and Main must never treat it as (or store it into) a Held Stable Snapshot."""
        return cls(
            root_snapshot=combat_start_replay_root,
            replay_prefix=[],
            plan_path=[],
            current_decision_result=current_decision_result,
            current_context_signature=current_context_signature,
            search_hypothesis_id=None,
        )

    @classmethod
    def from_main_stable_capture(
        cls,
        root_snapshot: "CombatStateSnapshot",
        current_decision_result: "BattleState",
        current_context_signature: DecisionSignature,
    ) -> "DecisionContext":
        """MAIN_CAP: "Main ProcessがStableで探索開始" - Main just captured a fresh
        Stable Root Snapshot via CaptureSnapshot to hand off to the Search Coordinator.
        Both Replay Prefix AND Plan Path start EMPTY ("Plan Path = 空(この探索呼び出し
        の起点)" - this Decision Context IS the start of the search invocation, so there
        is no parent Plan Path to inherit). `search_hypothesis_id=None` because this
        Root is Main's own real RNG, not a hypothesis."""
        return cls(
            root_snapshot=root_snapshot,
            replay_prefix=[],
            plan_path=[],
            current_decision_result=current_decision_result,
            current_context_signature=current_context_signature,
            search_hypothesis_id=None,
        )

    @classmethod
    def from_prev_child(
        cls,
        root_snapshot: "CombatStateSnapshot",
        current_decision_result: "BattleState",
        current_context_signature: DecisionSignature,
        parent_plan_path: "list[ReplayPrefixEntry]",
    ) -> "DecisionContext":
        """PREV_CHILD: continuing from a prior evaluation boundary's Child Snapshot
        (RNG-independent Beam Search continuation only - Hypothesis evaluation always
        completes within one Root Action Evaluation Segment and never reaches this
        path). `root_snapshot` here is necessarily Stable (mermaid_combat_commit_detail's
        EXPAND). Replay Prefix resets to empty exactly like MAIN_CAP (every Stable
        capture resets it) - but Plan Path is INHERITED from the parent Decision Context
        UNCHANGED ("Plan Path = 親Decision ContextのPlan Pathをそのまま継承") - this is
        the field whose behavior differs from MAIN_CAP, since PREV_CHILD is a
        continuation of an already-running search, not its start.
        `search_hypothesis_id=None` because this is the RNG-independent path."""
        return cls(
            root_snapshot=root_snapshot,
            replay_prefix=[],
            plan_path=list(parent_plan_path),
            current_decision_result=current_decision_result,
            current_context_signature=current_context_signature,
            search_hypothesis_id=None,
        )


# ---------------------------------------------------------------------------
# SUB_REPLAY: single-process Restore + verified Replay Prefix replay
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ReplaySuccess:
    """Full success: Replay Prefix replayed cleanly and CTX_SIG_CHECK passed.
    `final_state` is the live `BattleState` now sitting at `context`'s Decision Context
    (ready for `RESOLVE_CANDIDATE`/`STEP_CANDIDATE` - not performed by this function)."""

    final_state: "BattleState"


@dataclass(frozen=True)
class ReplayMismatch:
    """A well-defined, NON-exceptional replay-mismatch outcome (SUB_REPLAY's
    REPLAY_RESOLVABLE=No/REPLAY_SIG_CHECK=No/RESOLVABLE=No/CTX_SIG_CHECK=No nodes - all
    of which the mermaid source itself classifies as "Fault(...)として記録...種別:
    replay mismatch", i.e. a recorded, expected outcome of a divergent replay - NOT a
    live Emulator-level fault, which instead surfaces as a real exception, see
    `replay_decision_context()`'s own docstring).

    `step_index` is the 0-based index into `context.replay_prefix` where the mismatch
    was detected, or `None` for a CTX_SIG_CHECK (arrival) mismatch detected only after
    the whole prefix replayed successfully. `stage` distinguishes which SUB_REPLAY
    decision node produced this outcome."""

    step_index: "Optional[int]"
    stage: str  # "resolve" | "signature" | "context_signature"
    detail: str
    diverged_fields: "list[str]" = field(default_factory=list)


ReplayOutcome = "ReplaySuccess | ReplayMismatch"


def _root_snapshot_capture_boundary(root_snapshot) -> "Optional[str]":
    if isinstance(root_snapshot, str):
        payload = json.loads(root_snapshot)
        return payload.get("Metadata", {}).get("CaptureBoundary")
    if isinstance(root_snapshot, dict):
        return root_snapshot.get("Metadata", {}).get("CaptureBoundary")
    metadata = getattr(root_snapshot, "Metadata", None)
    if metadata is not None:
        boundary = getattr(metadata, "CaptureBoundary", None)
        return str(boundary) if boundary is not None else None
    return None


def _raise_if_root_snapshot_not_restore_eligible(root_snapshot) -> None:
    if isinstance(root_snapshot, CombatStartReplayRoot):
        # Nothing to Restore-eligibility-check: a Combat Start Replay Root is never
        # Captured/Restored at all - see that type's own docstring.
        return

    from combat_state_snapshot import capture_boundary_is_restore_eligible

    capture_boundary = _root_snapshot_capture_boundary(root_snapshot)
    if capture_boundary is not None and not capture_boundary_is_restore_eligible(capture_boundary):
        raise PendingSnapshotRestoreViolationError(
            "DecisionContext root_snapshot has "
            f"capture_boundary={capture_boundary!r}, which is not Restore-eligible. "
            "This is a design violation of NOTE_NO_REGEN: Pending must be reached by "
            "Restoring the prior Stable root snapshot and replaying the semantic action "
            "prefix, never by Restoring a Pending snapshot directly."
        )


def replay_decision_context(session: "LiveCombatSession", context: "DecisionContext") -> "ReplaySuccess | ReplayMismatch":
    """SUB_REPLAY: Restore `context.root_snapshot`, then replay `context.replay_prefix`
    one Transition Record at a time with REPLAY_SIG_CHECK after each Step, then
    CTX_SIG_CHECK against `context.current_context_signature` once the whole prefix has
    replayed. Single-process only in THIS phase - drives exactly one `LiveCombatSession`
    (no Branch Worker Pool yet) - but written so a later phase can reuse this function
    unchanged per-worker.

    Fault handling, precisely per the mermaid contract's own split:
      * A recorded candidate that no longer resolves, or an observed/arrival signature
        that diverges from what was recorded, is a well-defined REPLAY MISMATCH - this
        function RETURNS a `ReplayMismatch`, it does not raise for these (SUB_REPLAY
        itself routes both of these to "記録する" - i.e. a recorded outcome, not a raised
        exception on this side).
      * A genuine `live_combat_session.py` exception (`SnapshotRestoreRejectedError`,
        `SnapshotRestoreFailedError`, `QuiescentBoundaryViolation`, `ActionExecutionError`,
        `FaultedCombatSessionError`, `DecisionFrameMismatchError`) is NEVER caught here -
        it propagates to the caller completely unmodified. This function does not wrap
        these in a try/except at all, so there is nothing to accidentally swallow.
    """
    from live_combat_session import LiveCombatSession  # noqa: F401 - documents the expected type, avoids a hard import cycle

    _raise_if_root_snapshot_not_restore_eligible(context.root_snapshot)
    if isinstance(context.root_snapshot, CombatStartReplayRoot):
        # Start-of-Combat Pending's own Bootstrap: re-run the SAME combat start, never
        # Restore - there is no prior Stable boundary to Restore from at all.
        state = session.start_combat(context.root_snapshot.scenario_spec)
    else:
        state = session.restore_snapshot(context.root_snapshot)
    last_observed: "Optional[DecisionSignature]" = None

    for index, entry in enumerate(context.replay_prefix):
        legal_actions = state._cached_legal_actions or session.get_legal_actions()  # noqa: SLF001
        try:
            resolved_action = entry.semantic_action.resolve(legal_actions)
        except SemanticActionUnresolvedError as exc:
            return ReplayMismatch(step_index=index, stage="resolve", detail=str(exc))

        state = session.step(
            state,
            resolved_action,
            target_index=entry.target_index,
            target_enemy_index=entry.target_enemy_index,
            stop_at_pending=True,
        )

        observed = DecisionSignature.from_battle_state(
            state,
            semantic_action=entry.semantic_action,
            resolved_action=resolved_action,
            target_index=entry.target_index,
            target_enemy_index=entry.target_enemy_index,
        )
        if not observed.matches_for_replay(entry.expected_signature):
            return ReplayMismatch(
                step_index=index,
                stage="signature",
                detail=f"observed post-step signature diverged from replay prefix entry {index}",
                diverged_fields=observed.diff_for_replay(entry.expected_signature),
            )
        last_observed = observed

    # CTX_SIG_CHECK: confirm we actually arrived at `context`'s own Decision Context.
    # When the Replay Prefix is non-empty, the LAST step's own just-computed signature
    # IS the arrival signature - reuse it directly rather than reconstructing a second
    # one from `context.current_context_signature`'s own fields (which would be
    # tautological: it would just be comparing those fields against themselves). When
    # the Replay Prefix is EMPTY (a fresh Stable root, MAIN_CAP/PREV_CHILD with nothing
    # replayed yet), there is no "action taken to arrive here" to build a comparable
    # signature from at all - only the Boundary itself is meaningfully checkable at that
    # point (the restore either produced the boundary the caller recorded, or it did not).
    expected_ctx = context.current_context_signature
    if last_observed is not None:
        if not last_observed.matches_for_replay(expected_ctx):
            return ReplayMismatch(
                step_index=None,
                stage="context_signature",
                detail="arrival state's signature diverged from Current Context Signature",
                diverged_fields=last_observed.diff_for_replay(expected_ctx),
            )
    else:
        arrival_boundary = boundary_of_battle_state(state)
        if arrival_boundary != expected_ctx.boundary:
            return ReplayMismatch(
                step_index=None,
                stage="context_signature",
                detail=(
                    f"restored root's Boundary ({arrival_boundary!r}) diverged from "
                    f"Current Context Signature's recorded Boundary ({expected_ctx.boundary!r})"
                ),
                diverged_fields=["boundary"],
            )

    return ReplaySuccess(final_state=state)


# ---------------------------------------------------------------------------
# Main-loop-side bookkeeping building blocks (mermaid_combat_main_loop_detail.mermaid's
# STABLE_CAPTURE/STEP_STABLE_CAPTURE/PENDING_HOLD/APPEND_RECORD). Phase 2 does NOT
# implement the full Main Process state machine (a separate future phase) - these are
# only the small, pure primitives that bookkeeping needs, so a future integration can
# call them directly instead of re-deriving the reset/append semantics.
# ---------------------------------------------------------------------------


def start_new_replay_prefix_from_stable() -> "list[ReplayPrefixEntry]":
    """On every Stable boundary (STABLE_CAPTURE / STEP_STABLE_CAPTURE), the Held Stable
    Snapshot's Replay Prefix resets to empty - "次のStepを行う前に必ず再現元を確保する"."""
    return []


def _representative_signature_for_empty_prefix(current_result: "BattleState") -> DecisionSignature:
    """Builds a real (non-fabricated) `DecisionSignature` to serve as `current_context_
    signature` for a Stable root whose Replay Prefix is still empty (MAIN_DC - no
    Transition Record exists yet in this segment to reuse as the arrival signature).

    Judgment call: `replay_decision_context()` documents that when `context.
    replay_prefix` is empty, CTX_SIG_CHECK compares ONLY `.boundary` - the Semantic
    Action/resolved-action/candidate fields of `current_context_signature` are never
    actually read in that branch. Any legal candidate from the CURRENT Decision Result's
    own Choice Payload is therefore a safe, real representative pick for those otherwise-
    unused fields; the first candidate in the Emulator's own reported order is used,
    deterministically, rather than fabricating a placeholder action that was never
    actually offered."""
    legal_actions = current_result._cached_legal_actions or []  # noqa: SLF001 - NOTE_NO_REREAD, same pattern as replay_decision_context()
    if not legal_actions:
        if current_result.is_terminal:
            action = {"action_type": "system", "parameters": {}, "action_id": -1, "semantic_key": ""}
            semantic_action = SemanticAction(action_type="system", semantic_key="")
            return DecisionSignature.from_battle_state(
                current_result, semantic_action=semantic_action, resolved_action=action
            )
        raise RuntimeError(
            "cannot build a Decision Context signature: Current Decision Result reports no candidates"
        )
    action = legal_actions[0]
    semantic_action = SemanticAction(
        action_type=action.get("action_type"), semantic_key=action.get("semantic_key", "")
    )
    return DecisionSignature.from_battle_state(current_result, semantic_action=semantic_action, resolved_action=action)


def build_decision_context_from_held_stable(
    held_stable_snapshot: "CombatStateSnapshot",
    replay_prefix: "list[ReplayPrefixEntry]",
    current_result: "BattleState",
) -> DecisionContext:
    """Builds a Decision Context from a Held Stable Snapshot, Replay Prefix, and current
    Decision Result.

    When the Replay Prefix is non-empty, its last entry's observed post-step signature is
    the Current Context Signature. When it is empty, a representative signature is built
    from the first legal action in the Emulator's own reported order; see
    `_representative_signature_for_empty_prefix()` for the documented judgment call."""
    if replay_prefix:
        current_context_signature = replay_prefix[-1].expected_signature
    else:
        current_context_signature = _representative_signature_for_empty_prefix(current_result)

    context = DecisionContext.from_main_stable_capture(
        held_stable_snapshot, current_result, current_context_signature
    )
    if replay_prefix:
        context = dataclasses.replace(context, replay_prefix=list(replay_prefix))
    return context


def append_replay_prefix_entry(
    replay_prefix: "list[ReplayPrefixEntry]", entry: ReplayPrefixEntry
) -> "list[ReplayPrefixEntry]":
    """On a successful Step, its Transition Record is appended to the current Replay
    Prefix (APPEND_RECORD) - and, by the same pattern, to Plan Path too (call this same
    function on Plan Path as a separate call; the two lists are appended to
    independently even though the entry is identical, since they reset on different
    schedules - see `DecisionContext`'s own docstring). Returns a NEW list rather than
    mutating `replay_prefix` in place - functional, state-in/state-out style, consistent
    with the rest of this package (e.g. `BattleEmulator`'s own methods)."""
    return [*replay_prefix, entry]
