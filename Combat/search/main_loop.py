"""Main Process decision-loop state machine - Combat execution infrastructure Phase 3
(docs/architecture/combat/mermaid_combat_main_loop_detail.mermaid).

Builds directly on Phase 2 (`Combat/search/decision_context.py`, already merged) -
`DecisionSignature`, `DecisionContext`, `SemanticAction`, `ReplayPrefixEntry`,
`boundary_of_battle_state()`, `start_new_replay_prefix_from_stable()`,
`append_replay_prefix_entry()` are reused unchanged, not reimplemented. This module adds
the orchestration layer the mermaid source calls "Main Process": a loop that drives ONE
`LiveCombatSession` (`Combat/live_combat_session.py` - the real Start/Step/Capture/Restore
bridge), always working from the CURRENT Decision Result (StartCombat/Step/Restore's own
return value) and never re-reading via GetObservation/GetLegalActions (NOTE_NO_REREAD).

Scope (per this phase's task instruction - deliberately NOT the full mermaid contract):
  * Held Stable Snapshot / Replay Prefix bookkeeping exactly per STABLE_CAPTURE/
    STEP_STABLE_CAPTURE/PENDING_HOLD/STEP_PENDING_HOLD.
  * `PENDING_STATIC`: a self-contained, Restore/Step/Worker-free static evaluator over the
    CURRENT Decision Result's own Choice Payload - the diagram's own documented interim
    safety net (NOTE_PENDING_FUTURE below), not a search or scoring pipeline. It exists
    because "no mechanism yet exists to prove candidate execution won't consume future
    non-public RNG" (NOTE_PENDING_FUTURE's own wording) - a later phase may relax this
    once such a mechanism exists; this module does not attempt to build one.
  * `EXEC_LOOP`: re-resolves each Planned Sequence entry against the CURRENT Choice
    Payload immediately before each Step (never a stale cached list), Steps exactly once,
    and checks Boundary/Fault after EVERY step (never deferred to the end of the
    sequence).
  * `VERIFY_TRANSITION`: DC_SIGNATURE-lightweight comparison (`DecisionSignature.
    matches_for_replay()`) against a recorded Expected Post-Step Signature when one
    exists; unconditionally treated as matching when none exists (Direct/PENDING_STATIC
    steps never carry one - "不一致による誤ったDISCARDを起こさない").
  * A Direct-vs-Search routing skeleton with an INJECTABLE Search strategy callback. This
    module does NOT implement Search Coordinator/Candidate Pipeline/Branch Worker Pool/
    RNG Hypothesis (all later phases, per mermaid_rough_combat.mermaid's MAIN_PROCESS vs.
    SEARCH_COORDINATOR subgraph boundary) - `search_strategy` is a stub injection point
    only. A Pending boundary structurally CANNOT route into Search
    (`PendingSearchNotAllowedError`) UNLESS it is a genuine Start-of-Combat Pending with a
    Combat Start Replay Root available (`held_stable_snapshot is None` and
    `combat_start_replay_root is not None` - see `build_combat_start_decision_context()`).
    NOTE_PENDING_FUTURE's original restriction applied to Main-observed Pending
    generally because no mechanism existed to Restore/reproduce ANY Pending root for
    sibling Bootstrap - Start-of-Combat Pending now has one (the Combat Start Replay
    Root: re-run `start_combat()` with the SAME Scenario/RNG/Deck/Relic, never Restore a
    captured Pending Snapshot), so it is promoted out of PENDING_STATIC-only per this
    task's own instruction. Every OTHER Main-observed Pending (mid-combat, a Held Stable
    Snapshot already exists but nothing analogous exists for Restoring a mid-combat
    Pending capture) remains PENDING_STATIC-only, unchanged.

Fault handling: `LiveCombatSession.step()` never returns a Boundary=Fault `BattleState` -
it RAISES (`ActionExecutionError`/`FaultedCombatSessionError`/`QuiescentBoundaryViolation`)
per that module's own docstring and Common/contracts/emulator_dto_contract_rl_required.v1.md
S7/S8. STEP_FAULT_CHECK/STEP_FAULT_JUMP are therefore modeled here as an exception-catching
branch structurally separate from the ordinary DISCARD/RESYNC (plan-mismatch) path - a
caught fault clears the remaining Planned Sequence and returns a `MainCombatFaultOutcome`
immediately, WITHOUT appending a Transition Record for the failed step (NOTE_FAULT_FIRST).

Native `test_*()` + `_run_all()` test style, no mocks, real `LiveCombatSession` - see
`Combat/tests/test_main_loop.py`.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional, Union

from battle_emulator import BattleState

from live_combat_session import (
    ActionExecutionError,
    FaultedCombatSessionError,
    LiveCombatSession,
    QuiescentBoundaryViolation,
)
from search.decision_context import (
    BOUNDARY_PENDING,
    BOUNDARY_STABLE,
    BOUNDARY_TERMINAL,
    CombatStartReplayRoot,
    DecisionContext,
    DecisionSignature,
    ReplayPrefixEntry,
    SemanticAction,
    SemanticActionUnresolvedError,
    _representative_signature_for_empty_prefix,
    append_replay_prefix_entry,
    build_decision_context_from_held_stable,
    boundary_of_battle_state,
    start_new_replay_prefix_from_stable,
)
from search.replay_draw_restore import visible_draw_transition_evidence_from_committed_transition

if TYPE_CHECKING:
    from combat_state_snapshot import CombatStateSnapshot

# ---------------------------------------------------------------------------
# Routing vocabulary (NEW_DECISION / NEW_DECISION_PENDING)
# ---------------------------------------------------------------------------

ROUTE_DIRECT = "direct"
ROUTE_SEARCH = "search"
ROUTE_PENDING_STATIC = "pending_static"
ROUTE_VALUES = frozenset({ROUTE_DIRECT, ROUTE_SEARCH, ROUTE_PENDING_STATIC})


class PendingSearchNotAllowedError(RuntimeError):
    """Structural guard for NOTE_PENDING_FUTURE: a Pending boundary (Main-observed, real
    RNG directly beneath it) must never route into the Restore/Step/Worker fan-out Search
    path - "この経路のEvaluatorにも...実RNGのまま分岐探索してEvaluatorへ結果が見える事態を
    避ける" - UNLESS it is a genuine Start-of-Combat Pending with a Combat Start Replay
    Root available (`held_stable_snapshot is None` and `combat_start_replay_root is not
    None`), which reproduces via re-running `start_combat()` from the same Scenario/RNG/
    Deck/Relic rather than any Restore at all. Raised, never silently downgraded, for
    every other case of a `routing_policy` callback returning `ROUTE_SEARCH` while the
    current boundary is Pending."""


def default_routing_policy(boundary: str) -> str:
    """The conservative default: always Direct, regardless of boundary. A caller wanting
    Search invoked at Stable boundaries must inject its own `routing_policy` (and a
    `search_strategy`) - this module never invokes Search on its own initiative."""
    return ROUTE_DIRECT


# ---------------------------------------------------------------------------
# Planned Sequence (SET_SEQ) / Transition Record building blocks
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedStep:
    """One entry of a Planned Sequence (採用中のPlanned Sequence) - a Semantic Action to
    re-resolve against the CURRENT Choice Payload and Step, plus the target parameters
    `LiveCombatSession.step()` needs alongside it (mirrors `ReplayPrefixEntry`'s own
    shape, since a committed Planned Sequence step becomes exactly one
    `ReplayPrefixEntry` once it succeeds). `expected_signature` is `None` for
    Direct/PENDING_STATIC steps ("Expected Post-Step Signatureは付与しない = 自己決定で
    あり事前検証対象ではない") and set only for a Search-sourced step that carries one
    (a later-phase concern this module only threads through)."""

    semantic_action: SemanticAction
    target_index: "Optional[int]" = None
    target_enemy_index: "Optional[int]" = None
    expected_signature: "Optional[DecisionSignature]" = None


PlannedSequence = "list[PlannedStep]"


@dataclass(frozen=True)
class SearchSuccess:
    """SEARCH_RESULT's `SearchSuccess(PlannedSequence)` branch - what an injected
    `search_strategy` callback returns on success."""

    planned_sequence: "list[PlannedStep]"


@dataclass(frozen=True)
class SearchEvaluationFailure:
    """SEARCH_RESULT's `SearchEvaluationFailure` branch ("全候補がFault等で評価不能") -
    what an injected `search_strategy` callback returns when it could not produce any
    adoptable candidate. Main itself stays healthy (`CombatAbortedByDecisionFailure`, not
    `MainCombatFaultOutcome` - "Main自身は正常であり、Main Combat Faultへは変換しない")."""

    detail: str


SearchStrategy = Callable[["DecisionContext"], "Union[SearchSuccess, SearchEvaluationFailure]"]


# ---------------------------------------------------------------------------
# Outer-loop outcomes (RETURN)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CombatTerminalOutcome:
    """BOUNDARY=Terminal -> END -> CombatResultを確定."""

    final_state: BattleState


@dataclass(frozen=True)
class MainCombatFaultOutcome:
    """STEP_FAULT_JUMP -> FAULT_OUT. `error` is the real exception `LiveCombatSession.
    step()` raised (`ActionExecutionError`/`FaultedCombatSessionError`/
    `QuiescentBoundaryViolation`) - preserved, not summarized, so a caller can still
    inspect e.g. `ActionExecutionError.context`."""

    error: BaseException


@dataclass(frozen=True)
class CombatAbortedByDecisionFailureOutcome:
    """SEARCH_FAIL_HANDLE -> ABORT_POLICY -> COMBAT_ABORTED. A third, distinct
    termination kind - neither a Main Combat Fault nor a planned Terminal. Per the
    diagram's own note, this is the ONLY currently-defined ABORT_POLICY response
    ("この探索呼び出しを打ち切る") - there is deliberately no implicit fallback to
    Direct/PENDING_STATIC here."""

    detail: str
    search_failure: SearchEvaluationFailure


MainLoopOutcome = Union[CombatTerminalOutcome, MainCombatFaultOutcome, CombatAbortedByDecisionFailureOutcome]


# ---------------------------------------------------------------------------
# MainLoopState: the Main Process's own retained bookkeeping
# ---------------------------------------------------------------------------


@dataclass
class MainLoopState:
    """Everything mermaid_combat_main_loop_detail.mermaid's MAIN subgraph keeps between
    decisions. Mutable (unlike Phase 2's frozen dataclasses) - this IS the running state
    machine's own state, mutated in place by `run_until_terminal_or_fault()`/
    `_run_exec_loop()`, matching this repo's other stateful session wrappers
    (`LiveCombatSession` itself is a plain mutable class, not a dataclass)."""

    session: LiveCombatSession
    current_result: BattleState
    held_stable_snapshot: "Optional[CombatStateSnapshot]" = None
    replay_prefix: "list[ReplayPrefixEntry]" = field(default_factory=list)
    planned_sequence: "list[PlannedStep]" = field(default_factory=list)
    # Set once, at genesis, from whatever `scenario_spec` this episode's `start_combat()`
    # call used. Never cleared (harmless to retain after the real first Stable boundary,
    # since every routing decision below only ever consults it while
    # `held_stable_snapshot is None`) - the sole purpose is letting a Start-of-Combat
    # Pending (TOOLBOX/CHOICES_PARADOX/GAMBLING_CHIP etc.) build a Combat Start Replay
    # Root DecisionContext for Search, per NOTE_PENDING_FUTURE's relaxation below.
    combat_start_replay_root: "Optional[CombatStartReplayRoot]" = None


def initialize_main_loop_state(
    session: LiveCombatSession,
    initial_result: BattleState,
    *,
    combat_start_replay_root: "Optional[CombatStartReplayRoot]" = None,
) -> MainLoopState:
    """Wraps a fresh `start_combat()`/`resume_from()` return value into a `MainLoopState`.
    Does NOT itself perform the genesis Held Stable Snapshot capture - that happens on
    `run_until_terminal_or_fault()`'s first iteration exactly like every subsequent
    Stable visit to BOUNDARY (STABLE_CAPTURE).

    `combat_start_replay_root` is optional and backward-compatible: omit it (as every
    caller that only cares about the Direct/PENDING_STATIC path for Start-of-Combat
    Pending already does) and Search simply stays unavailable for a genesis Pending,
    exactly as before this Combat Start Replay Root support was added. Pass it - built
    from the SAME `scenario_spec` the caller's own `session.start_combat(scenario_spec)`
    call just used - to make Search available for it."""
    return MainLoopState(
        session=session, current_result=initial_result, combat_start_replay_root=combat_start_replay_root
    )


def _capture_stable(loop_state: MainLoopState) -> None:
    """STABLE_CAPTURE / STEP_STABLE_CAPTURE: CaptureSnapshot, hold it as the Held Stable
    Snapshot, reset the Replay Prefix to empty (Phase 2's own
    `start_new_replay_prefix_from_stable()` primitive - "次のStepを行う前に必ず再現元を
    確保する")."""
    loop_state.held_stable_snapshot = loop_state.session.capture_snapshot()
    loop_state.replay_prefix = start_new_replay_prefix_from_stable()


# ---------------------------------------------------------------------------
# Main Decision Context (MAIN_DC / MAIN_DC2)
# ---------------------------------------------------------------------------


def build_main_decision_context(loop_state: MainLoopState) -> DecisionContext:
    """MAIN_DC ("Held Stable Snapshot＋Replay Prefix(空)＋Current Result") when the
    Replay Prefix is currently empty, or MAIN_DC2 ("Held Stable Snapshot(直前分)＋Replay
    Prefix(Stableから現在まで)＋Current Result(直前StepのPending Choice Payload)") when
    it is not - both built via Phase 2's `DecisionContext.from_main_stable_capture()` per
    this task's instruction, with the Replay Prefix overridden afterward for the MAIN_DC2
    case (that classmethod always starts both lists empty, since it is also used - by a
    later phase - for the genuinely-fresh MAIN_CAP case; overriding here is cheaper and
    no less correct than adding a second, main-loop-only constructor to Phase 2's file,
    which this task's constraints forbid touching).

    `current_context_signature`: when the Replay Prefix is non-empty, its LAST entry's
    own `expected_signature` field already holds the real, just-observed post-step
    signature for arriving here (Main's own `APPEND_RECORD` stores the OBSERVED signature
    into that field - the same field doubles as "what a later replay should expect", per
    `ReplayPrefixEntry`'s own docstring) - reused directly, not rebuilt. When empty, see
    `_representative_signature_for_empty_prefix()`."""
    if loop_state.held_stable_snapshot is None:
        raise RuntimeError("build_main_decision_context() called before any Held Stable Snapshot was captured")
    return build_decision_context_from_held_stable(
        loop_state.held_stable_snapshot, loop_state.replay_prefix, loop_state.current_result
    )


def build_combat_start_decision_context(loop_state: MainLoopState) -> DecisionContext:
    """Start-of-Combat Pending's own counterpart to `build_main_decision_context()`.

    Only ever called while `loop_state.held_stable_snapshot is None` (no real Stable
    boundary has been reached yet - per NOTE_NO_HELD_SNAPSHOT_AT_GENESIS, a
    Start-of-Combat Pending must never be treated as or stored into a Held Stable
    Snapshot). Uses `loop_state.combat_start_replay_root` as the root instead - see
    `DecisionContext.from_combat_start_pending()`/`CombatStartReplayRoot`'s own
    docstrings. Structurally mirrors `build_main_decision_context()` otherwise (same
    empty-vs-non-empty Replay Prefix handling), since Main's own bookkeeping
    (`loop_state.replay_prefix`/`APPEND_RECORD`) does not distinguish the two cases."""
    if loop_state.held_stable_snapshot is not None:
        raise RuntimeError(
            "build_combat_start_decision_context() called after a real Held Stable Snapshot "
            "already exists - use build_main_decision_context() instead"
        )
    if loop_state.combat_start_replay_root is None:
        raise RuntimeError("build_combat_start_decision_context() called without a Combat Start Replay Root")

    if loop_state.replay_prefix:
        current_context_signature = loop_state.replay_prefix[-1].expected_signature
    else:
        current_context_signature = _representative_signature_for_empty_prefix(loop_state.current_result)

    context = DecisionContext.from_combat_start_pending(
        loop_state.combat_start_replay_root, loop_state.current_result, current_context_signature
    )
    if loop_state.replay_prefix:
        context = dataclasses.replace(context, replay_prefix=list(loop_state.replay_prefix))
    return context


# ---------------------------------------------------------------------------
# PENDING_STATIC: lightweight, Restore/Step/Worker-free per-choice_kind evaluator
# ---------------------------------------------------------------------------

# Deliberately NOT reusing choice_semantics.py's ChoiceSemanticsTable directly: that
# module is explicitly a "LOGGING/ANALYSIS LAYER ONLY" whose own docstring states
# "nothing in this file may ever change which action gets chosen" - wiring its output
# into an actual selection would violate that module's own non-negotiable invariant.
# PENDING_STATIC instead uses the one piece of shared vocabulary that IS meant to
# influence behavior at this layer: LegalActions' own `action_type` (choice_card/
# choice_confirm/choice_skip), already present on every candidate without any lookup.
_PENDING_STATIC_ACTION_TYPE_RANK: "dict[str, int]" = {
    "choice_card": 0,
    "choice_confirm": 0,
    "choice_skip": 1,
}
_PENDING_STATIC_DEFAULT_RANK = 0  # an unrecognized action_type ranks alongside choice_card/choice_confirm, ahead of skip


def pending_static_select(current_result: BattleState) -> PlannedStep:
    """PENDING_STATIC: "Current Decision Resultの候補へ直接適用し、最上位1件を選択
    (Restore／Step／Worker分岐は一切行わない)". Genuinely lightweight per this task's
    instruction: a simple, deterministic priority rule over the CURRENT Decision Result's
    own `legal_actions` (never re-fetched, never Restored/Stepped against) - prefer an
    action that actually resolves the choice (`choice_card`/`choice_confirm`) over
    skipping it (`choice_skip`), and within a tier keep the Emulator's own reported
    candidate order (first-listed wins) rather than imposing any further ranking.

    Interim safety net only (NOTE_PENDING_FUTURE, mermaid_combat_main_loop_detail.
    mermaid): "候補実行が未来の非公開RNGを消費しないことを...保証できるようになった場合は
    ...Worker展開を許可する余地を残す。現段階ではその判定機構自体が存在しないため...全て
    静的評価とする". This function is exactly that static evaluation, nothing more -
    no scoring pipeline, no Order-Masked Observation feature extraction (that belongs to
    a later phase's real Candidate Pipeline, which this rule is explicitly NOT).

    Always returns a Planned Sequence of length 1 with no Expected Post-Step Signature
    (self-decided, not a prior search's prediction to verify against - per SET_SEQ's own
    note)."""
    legal_actions = current_result._cached_legal_actions or []  # noqa: SLF001 - NOTE_NO_REREAD
    if not legal_actions:
        raise RuntimeError("PENDING_STATIC: Current Decision Result reports no candidates to choose from")

    def _rank(indexed: "tuple[int, dict]") -> "tuple[int, int]":
        index, action = indexed
        return (_PENDING_STATIC_ACTION_TYPE_RANK.get(action.get("action_type"), _PENDING_STATIC_DEFAULT_RANK), index)

    _, chosen = min(enumerate(legal_actions), key=_rank)
    semantic_action = SemanticAction(
        action_type=chosen.get("action_type"), semantic_key=chosen.get("semantic_key", "")
    )
    return PlannedStep(semantic_action=semantic_action, target_index=None, target_enemy_index=None, expected_signature=None)


def first_candidate_direct_selector(current_result: BattleState) -> PlannedStep:
    """A minimal, reusable Direct (DIRECT node) selector: "Current Decision Resultの候補
    からSemantic Actionを直接1つ選択" - here, simply the first candidate in the
    Emulator's own reported order, with no target resolved (target_index/
    target_enemy_index stay `None`). Suitable for tests/demos and any candidate that does
    not require a target; a caller whose Direct decisions need real targeting (e.g. an
    attack card that must name an enemy) should inject its own `direct_selector`
    callback into `run_until_terminal_or_fault()` instead of this one."""
    legal_actions = current_result._cached_legal_actions or []  # noqa: SLF001 - NOTE_NO_REREAD
    if not legal_actions:
        raise RuntimeError("DIRECT: Current Decision Result reports no candidates to choose from")
    action = legal_actions[0]
    semantic_action = SemanticAction(
        action_type=action.get("action_type"), semantic_key=action.get("semantic_key", "")
    )
    return PlannedStep(semantic_action=semantic_action, target_index=None, target_enemy_index=None, expected_signature=None)


DirectSelector = Callable[[BattleState], PlannedStep]


# ---------------------------------------------------------------------------
# EXEC_LOOP: RESOLVE -> STEP -> STEP_FAULT_CHECK -> APPEND_RECORD -> VERIFY_TRANSITION
# ---------------------------------------------------------------------------

# _run_exec_loop()'s two non-fault exit signals - see run_until_terminal_or_fault()'s own
# docstring for why these are handled slightly differently by the caller (a normal
# SEQ_REMAIN=No sequence exhaustion goes straight to MAIN_DC/MAIN_DC2 per the diagram,
# skipping a redundant top-of-BOUNDARY recapture; DISCARD/RESYNC and an in-sequence
# Terminal both route back through the full top-of-loop BOUNDARY handling).
_EXEC_SEQUENCE_EXHAUSTED = "sequence_exhausted"
_EXEC_GO_TO_BOUNDARY = "go_to_boundary"


def _run_exec_loop(loop_state: MainLoopState) -> "Union[str, MainCombatFaultOutcome]":
    """EXEC_LOOP subgraph: consumes `loop_state.planned_sequence` one Semantic Action at
    a time, re-resolving each against the CURRENT Choice Payload immediately before
    Stepping (never a stale cached list - RESOLVE reads `loop_state.current_result`'s own
    `_cached_legal_actions` fresh on every iteration), Stepping exactly once per
    iteration, and checking Boundary/Fault after EACH step (STEP_FAULT_CHECK - never
    deferred to the end of the sequence)."""
    while loop_state.planned_sequence:
        planned_step = loop_state.planned_sequence[0]
        current_result = loop_state.current_result
        legal_actions = current_result._cached_legal_actions or []  # noqa: SLF001 - NOTE_NO_REREAD

        # RESOLVE -> RESOLVE_OK
        try:
            resolved_action = planned_step.semantic_action.resolve(legal_actions)
        except SemanticActionUnresolvedError:
            loop_state.planned_sequence = []  # DISCARD
            return _EXEC_GO_TO_BOUNDARY  # RESYNC -> BOUNDARY

        # STEP (Emulator API boundary, exactly one Step)
        try:
            next_result = loop_state.session.step(
                current_result,
                resolved_action,
                target_index=planned_step.target_index,
                target_enemy_index=planned_step.target_enemy_index,
                stop_at_pending=True,
            )
        except (ActionExecutionError, FaultedCombatSessionError, QuiescentBoundaryViolation) as exc:
            # STEP_FAULT_CHECK=Yes -> STEP_FAULT_JUMP: NOTE_FAULT_FIRST - no Transition
            # Record append, no DISCARD/RESYNC (structurally separate path), straight to
            # Main Combat Fault. Remaining Planned Sequence is abandoned (it can never be
            # executed against a faulted session anyway).
            loop_state.planned_sequence = []
            return MainCombatFaultOutcome(error=exc)

        # STEP_RESULT: Current Decision Resultを差し替える (no GetObservation re-read)
        loop_state.current_result = next_result

        # APPEND_RECORD (happens before VERIFY_TRANSITION per the diagram's own node
        # order - the Step itself already genuinely committed on the live session
        # regardless of what VERIFY_TRANSITION concludes next; only DISCARD's remaining-
        # sequence abandonment is conditioned on that check, never this record).
        observed_signature = DecisionSignature.from_battle_state(
            next_result,
            semantic_action=planned_step.semantic_action,
            resolved_action=resolved_action,
            target_index=planned_step.target_index,
            target_enemy_index=planned_step.target_enemy_index,
        )
        draw_evidence = (
            visible_draw_transition_evidence_from_committed_transition(
                next_result,
                loop_state.replay_prefix,
                pre_battle_state=current_result,
            )
            if loop_state.held_stable_snapshot is not None
            else None
        )
        entry = ReplayPrefixEntry(
            semantic_action=planned_step.semantic_action,
            expected_signature=observed_signature,
            target_index=planned_step.target_index,
            target_enemy_index=planned_step.target_enemy_index,
            visible_draw_constraints=draw_evidence.constraints if draw_evidence is not None else (),
            visible_draw_tracking_blocked=(
                draw_evidence.blocks_later_pinning if draw_evidence is not None else False
            ),
            visible_draw_tracking_error=(
                draw_evidence.tracking_error if draw_evidence is not None else None
            ),
        )
        loop_state.replay_prefix = append_replay_prefix_entry(loop_state.replay_prefix, entry)
        loop_state.planned_sequence = loop_state.planned_sequence[1:]

        # VERIFY_TRANSITION: unconditional match when no Expected Post-Step Signature was
        # recorded (Direct/PENDING_STATIC steps never carry one) - "不一致による誤った
        # DISCARDを起こさない".
        if planned_step.expected_signature is not None:
            matches = observed_signature.matches_for_replay(planned_step.expected_signature)
        else:
            matches = True
        if not matches:
            loop_state.planned_sequence = []  # DISCARD remaining Planned Sequence only
            return _EXEC_GO_TO_BOUNDARY  # RESYNC -> BOUNDARY

        # TERMINAL_CHECK
        boundary = boundary_of_battle_state(next_result)
        if boundary == BOUNDARY_TERMINAL:
            return _EXEC_GO_TO_BOUNDARY  # CONSUME_END -> BOUNDARY -> END

        # STEP_BOUNDARY: performed every Step, never deferred to sequence completion.
        if boundary == BOUNDARY_STABLE:
            _capture_stable(loop_state)
        # Pending: STEP_PENDING_HOLD - Held Stable Snapshot untouched, Replay Prefix
        # already carries this step's Transition Record via APPEND_RECORD above; nothing
        # further to do here.

        # SEQ_REMAIN_S / SEQ_REMAIN_P is exactly "is the while loop's own condition still
        # true" - looping back re-enters at RESOLVE for the next planned step.

    return _EXEC_SEQUENCE_EXHAUSTED


# ---------------------------------------------------------------------------
# Outer loop: BOUNDARY -> (Terminal/Fault/Stable/Pending) -> NEW_DECISION[_PENDING] -> EXEC_LOOP
# ---------------------------------------------------------------------------


def run_until_terminal_or_fault(
    loop_state: MainLoopState,
    *,
    direct_selector: DirectSelector,
    search_strategy: "Optional[SearchStrategy]" = None,
    routing_policy: Callable[[str], str] = default_routing_policy,
    pending_selector: DirectSelector = pending_static_select,
    max_iterations: int = 10000,
) -> MainLoopOutcome:
    """The Main Process outer decision loop (everything in the mermaid source's MAIN
    subgraph outside EXEC_LOOP itself). Runs until Terminal, a Main Combat Fault, or a
    `CombatAbortedByDecisionFailure` - whichever comes first.

    `direct_selector`: required. Called whenever `routing_policy` picks `ROUTE_DIRECT`
    (Stable or Pending) - "Current Decision Resultの候補からSemantic Actionを直接1つ選択".
    `search_strategy`: optional; only ever invoked when `routing_policy` picks
    `ROUTE_SEARCH` at a STABLE boundary (never Pending - see `PendingSearchNotAllowedError`).
    `routing_policy`: decides Direct vs. Search vs. PENDING_STATIC per boundary; defaults
    to always-Direct (`default_routing_policy`) so a caller who never wants Search/
    PENDING_STATIC invoked doesn't have to supply one.
    `pending_selector`: called whenever `routing_policy` picks `ROUTE_PENDING_STATIC` -
    defaults to `pending_static_select` (unchanged backward-compatible behavior: prefer
    resolving over skipping, otherwise keep Emulator order). Injectable so a caller can
    substitute `Combat/execution_mode.py`'s `zero_index_pending_selector` (no tier, index
    0 only) or an `external_control` selector that resolves an externally-specified
    action - see that module's own docstring for the RL/Training division of
    responsibility this parameter exists to support.

    One deliberate simplification vs. strict diagram fidelity, documented here rather
    than hidden: when a Planned Sequence's last step happens to land on a Stable boundary
    AND the sequence is now fully exhausted (SEQ_REMAIN_S=No), the diagram flows directly
    STEP_STABLE_CAPTURE -> MAIN_DC without another top-of-BOUNDARY visit; this
    implementation still re-derives `boundary_of_battle_state()` and (if Stable) calls
    `_capture_stable()` again at the top of the next iteration regardless of how the
    previous iteration ended. `CaptureSnapshot()` is documented read-only/side-effect-free
    (`LiveCombatSession.capture_snapshot()`'s own docstring), so the extra call costs one
    redundant Emulator round trip in that specific case and changes no observable
    behavior - traded for a single unified control-flow path instead of duplicating the
    routing block for the sake of skipping it.
    """
    for _ in range(max_iterations):
        boundary = boundary_of_battle_state(loop_state.current_result)

        if boundary == BOUNDARY_TERMINAL:
            return CombatTerminalOutcome(final_state=loop_state.current_result)

        if boundary == BOUNDARY_STABLE:
            _capture_stable(loop_state)
        elif boundary == BOUNDARY_PENDING:
            # PENDING_HOLD - Held Stable Snapshot/Replay Prefix stay exactly as of the
            # last Stable capture, untouched. If this is a genesis Pending, no Stable
            # root exists yet and Search is structurally disallowed below.
            pass
        else:  # pragma: no cover - boundary_of_battle_state() never returns Fault (see decision_context.py)
            raise RuntimeError(f"unexpected boundary {boundary!r} outside EXEC_LOOP")

        if not loop_state.planned_sequence:
            route = routing_policy(boundary)
            if route not in ROUTE_VALUES:
                raise ValueError(f"routing_policy returned unknown route {route!r} (known: {sorted(ROUTE_VALUES)})")
            is_combat_start_pending_search = (
                boundary == BOUNDARY_PENDING
                and route == ROUTE_SEARCH
                and loop_state.held_stable_snapshot is None
                and loop_state.combat_start_replay_root is not None
            )
            if boundary == BOUNDARY_PENDING and route == ROUTE_SEARCH and not is_combat_start_pending_search:
                raise PendingSearchNotAllowedError(
                    f"routing_policy returned {ROUTE_SEARCH!r} for a Pending boundary - "
                    "Main-observed Pending must never route into Search/Worker fan-out "
                    "(NOTE_PENDING_FUTURE, mermaid_combat_main_loop_detail.mermaid), "
                    "EXCEPT a genuine Start-of-Combat Pending with a Combat Start Replay "
                    "Root available - that requires held_stable_snapshot is None and "
                    "combat_start_replay_root is not None, neither of which held here"
                )

            if route == ROUTE_DIRECT:
                loop_state.planned_sequence = [direct_selector(loop_state.current_result)]
            elif route == ROUTE_PENDING_STATIC:
                if boundary != BOUNDARY_PENDING:
                    raise RuntimeError("ROUTE_PENDING_STATIC is only a valid routing_policy answer at a Pending boundary")
                loop_state.planned_sequence = [pending_selector(loop_state.current_result)]
            else:  # ROUTE_SEARCH, boundary is Stable or a genuine Start-of-Combat Pending
                if search_strategy is None:
                    raise RuntimeError("routing_policy selected ROUTE_SEARCH but no search_strategy callable was provided")
                context = (
                    build_combat_start_decision_context(loop_state)
                    if is_combat_start_pending_search
                    else build_main_decision_context(loop_state)
                )
                search_result = search_strategy(context)
                if isinstance(search_result, SearchEvaluationFailure):
                    # SEARCH_FAIL_HANDLE -> ABORT_POLICY -> COMBAT_ABORTED. No implicit
                    # Direct fallback - "暗黙のDirect fallbackは行わない".
                    return CombatAbortedByDecisionFailureOutcome(
                        detail=search_result.detail, search_failure=search_result
                    )
                loop_state.planned_sequence = list(search_result.planned_sequence)

        exec_outcome = _run_exec_loop(loop_state)
        if isinstance(exec_outcome, MainCombatFaultOutcome):
            return exec_outcome
        # else _EXEC_SEQUENCE_EXHAUSTED or _EXEC_GO_TO_BOUNDARY - either way, loop back to
        # re-derive boundary_of_battle_state() from the (possibly updated) current_result.

    raise RuntimeError(f"run_until_terminal_or_fault exceeded max_iterations={max_iterations} without terminating")