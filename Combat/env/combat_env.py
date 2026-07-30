"""CombatEnv: the mid-term plan's Phase 1 1-action RL API
(`reset(scenario) -> observation`, `get_legal_actions() -> legal_actions`,
`step(action) -> observation, reward_info, done, metadata`), as a thin, STATEFUL adapter.

Canonical CombatStateSnapshot contract v0.3 Phase 1 (2026-07-26): this class now
delegates its own committed-decision path to `Combat/live_combat_session.py::
LiveCombatSession` instead of restoring via `BattleEmulator.enumerate_legal_actions()`/
`apply_action()` on every decision. `ResetFromScenario` is now called at most once per
episode (`reset()`/`adopt_state()`), never again for `get_legal_actions()`/`step()` -
those trust the live GameInstance's own `Step()` result directly, backed by the
Emulator's new Quiescent Decision Boundary enforcement (see `live_combat_session.py`'s
module docstring and `C:\\STS2_Emulator\\docs\\reports\\combat_state_contract_phase1_
emulator_report_20260726.md`).

Unchanged by this: the `battle_state` property (still the escape hatch
HeuristicAgent/beam_search.py/lookahead.py use for their own restore-based hypothetical
branching, entirely untouched - Phase 1's contract explicitly keeps that path
"legacy_approximate_restore", see this class's own docstring below).

Target addressing: prefer `target_enemy_index` (the STABLE `state["enemies"][i]["index"]`
value - see Common/schemas/combat_state_schema.json) over the positional `target_index`
`BattleEmulator.apply_action()` also still accepts - `target_enemy_index` is correct
regardless of enemy deaths or duplicate monster types, matching Common/schemas/
legal_action_schema.json's `choice_target` action `Parameters.enemyIndex`.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from battle_emulator import BattleState  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402

RewardFn = Callable[["BattleState | None", dict, BattleState, bool], float]


def default_reward_fn(
    prev_state: "BattleState | None", action: dict, next_state: BattleState, done: bool
) -> float:
    """Illustrative placeholder only - NOT the final RL reward design (that is plan
    Phase 5's job, section 7.4: "初期報酬"/"補助報酬"). Implements just that section's
    stated priority order at the coarsest possible level (win >> avoid death > avoid HP
    loss) so CombatEnv is usable end-to-end before Phase 5 exists. Replace via the
    `reward_fn` constructor argument - do not tune constants here to "improve" behavior
    without also updating plan Phase 5's design doc, since this is meant to stay a
    minimal placeholder, not accrete real reward-shaping logic.
    """
    reward = 0.0
    if prev_state is not None:
        hp_before = prev_state.engine_state.get("hp", 0) or 0
        hp_after = next_state.engine_state.get("hp", 0) or 0
        reward += -1.0 * max(0, hp_before - hp_after)
    if done:
        outcome = (next_state.outcome or "").lower()
        if outcome == "victory":
            reward += 100.0
        elif outcome == "defeat":
            reward += -100.0
    return reward


class CombatEnv:
    """One live combat episode. Not thread-safe and not re-entrant across multiple
    CombatEnv instances used concurrently in one OS process - inherits BattleEmulator's
    "one live GameInstance per OS process" constraint (see battle_emulator.py's module
    docstring); a multi-worker rollout pool needs one CombatEnv per worker PROCESS, not
    per thread (see Combat/evaluation/reports/emulator_hang/worker_timeout_policy.md for
    the related one-worker-per-process contract around ScenarioInitializationTimeoutException).
    """

    def __init__(
        self,
        repo_root: "Path | None" = None,
        reward_fn: RewardFn = default_reward_fn,
        use_legal_action_cache: bool = True,
    ):
        self._session = LiveCombatSession(repo_root=repo_root, use_legal_action_cache=use_legal_action_cache)
        self._reward_fn = reward_fn
        self._state: "BattleState | None" = None
        self._scenario_spec: "dict | None" = None
        self._step_index = 0

    def reset(self, scenario_spec: dict) -> dict:
        """Starts a new episode from `scenario_spec` (see
        Common/schemas/combat_scenario_input_schema.json) and returns the initial
        observation (see Common/schemas/transition_schema.json's game_observation
        definition). Any `ScenarioInitializationTimeoutException` from the underlying
        emulator propagates uncaught - per worker_timeout_policy.md, the caller (not
        this class) owns persisting the failing scenario and discarding this whole
        worker process; this class does not catch/retry/paper over that exception.

        Calls `ResetFromScenario` exactly once (via `LiveCombatSession.start_combat()`) -
        this is the episode's ONLY restore; every subsequent `step()` stays on the same
        live GameInstance (Phase 1 contract)."""
        self._scenario_spec = scenario_spec
        self._state = self._session.start_combat(scenario_spec)
        self._step_index = 0
        return self._observation()

    def adopt_state(self, battle_state: BattleState, scenario_spec: dict) -> dict:
        """Starts a new episode from an ALREADY-INITIALIZED BattleState (e.g.
        Combat/data/preflight_validate.py's pre-flight checks, which need the resulting
        state to compare against the spec before deciding whether this episode may even
        start). `scenario_spec` is stored only for bookkeeping (observation provenance).

        Unlike the pre-Phase-1 version of this method, `battle_state` IS re-applied here
        (via `LiveCombatSession.resume_from()`, exactly one `ResetFromScenario` call) -
        it can no longer simply be trusted as already-live, since something else may
        have touched the shared GameInstance since `battle_state` was captured (most
        commonly: another episode's own `LiveCombatSession`/HeuristicAgent search
        already advanced/restored the shared instance past that point). The returned
        observation reflects the freshly-established live state (same engine_state
        content, fresh `decision_frame` - see `resume_from()`'s own docstring for why a
        stale `decision_frame` can never be reused as-is)."""
        self._scenario_spec = scenario_spec
        self._state = self._session.resume_from(battle_state)
        self._step_index = 0
        return self._observation()

    @property
    def battle_state(self) -> BattleState:
        """The current episode's raw BattleState - an escape hatch for Heuristic/search
        code (HeuristicAgent, TurnBeamSearcher, LookaheadSearcher - see Combat/heuristic_agent.py
        etc.) that needs BattleEmulator's stateless branching API (apply_action() on a
        hypothetical, uncommitted state) to explore candidate futures before deciding
        what to actually commit via this class's own step(). CombatEnv does not - and is
        not meant to - reimplement that branching/search capability itself; it owns only
        the real, committed trajectory. See Outputs/reports/heuristic_code_audit.md for
        why search internals still use BattleEmulator directly while the OUTER real-decision
        loop (what becomes a teacher-data trajectory) goes through reset()/step() here.

        Phase 1 note: this search path still restores the shared GameInstance on every
        candidate it evaluates (unchanged, "legacy_approximate_restore" per the Phase 1
        contract) - `LiveCombatSession.step()` detects and recovers from this
        automatically (see its module docstring's "_is_still_current()" note), so
        callers do not need to do anything differently here."""
        self._require_reset()
        return self._state

    def get_legal_actions(self) -> list[dict]:
        """Legal actions for the CURRENT state (see Common/schemas/legal_action_schema.json).
        Re-fetch after every step() - action_id is not stable across states.

        Phase 1: no restore/re-fetch happens here - this is exactly the LegalActions
        list `LiveCombatSession` already cached on `self._state` from the last
        start_combat()/resume_from()/step() call's own StepResult/ResetResult (the
        contract's "StepResultのObservation／LegalActionsを継続利用する")."""
        self._require_reset()
        if self._state.is_terminal:
            return []
        return self._state._cached_legal_actions or []  # noqa: SLF001 - same white-box pattern used throughout this package

    def step(
        self,
        action: dict,
        target_enemy_index: "int | None" = None,
        target_index: "int | None" = None,
        continuation_resolver=None,
        continuation_deadline: "float | None" = None,
    ) -> dict:
        """Applies `action` (one entry from get_legal_actions()), resolving an AnyEnemy
        target choice via `target_enemy_index` (preferred - stable, see class docstring)
        or `target_index` (positional fallback) if the action needs one. Returns a
        Common/schemas/transition_schema.json-shaped dict: {action_id, reward, done,
        observation, legal_actions, info}.

        Phase 1: committed via `LiveCombatSession.step()` - a direct `Step()` call on
        the same live GameInstance this episode has held since `reset()`/`adopt_state()`,
        not a fresh restore (see that method's own docstring for the one exception:
        automatic resynchronization if something else touched the shared instance)."""
        self._require_reset()
        prev_state = self._state
        next_state = self._session.step(
            prev_state,
            action,
            target_index=target_index,
            target_enemy_index=target_enemy_index,
            continuation_resolver=continuation_resolver,
            continuation_deadline=continuation_deadline,
        )
        self._state = next_state
        self._step_index += 1

        reward = self._reward_fn(prev_state, action, next_state, next_state.is_terminal)
        return {
            "action_id": action["action_id"],
            "reward": reward,
            "done": next_state.is_terminal,
            "observation": self._observation(),
            "legal_actions": [] if next_state.is_terminal else (next_state._cached_legal_actions or []),  # noqa: SLF001
            "info": {"step_index": self._step_index, "outcome": next_state.outcome},
        }

    def _observation(self) -> dict:
        state = self._state.engine_state
        return {
            "seed": state.get("seed"),
            "seedText": state.get("seedText"),
            "characterId": state.get("characterId"),
            "ascension": state.get("ascension"),
            "stepIndex": self._step_index,
            "turn": self._state.turn,
            "isTerminal": self._state.is_terminal,
            "outcome": self._state.outcome,
            "state": state,
        }

    def _require_reset(self) -> None:
        if self._state is None:
            raise RuntimeError("CombatEnv.reset(scenario_spec) must be called before get_legal_actions()/step()")
