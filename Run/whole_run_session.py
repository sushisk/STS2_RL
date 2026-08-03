"""Thin, plain-data wrapper around a single `GameInstance` for Whole Run connectivity.

One `WholeRunSession` owns exactly one CLR `GameInstance` (see
`run_emulator_bridge.new_game_instance` for the one-instance-per-process
constraint). Every method here returns/accepts plain Python dict/list/str/int data,
never a live CLR object - this is what makes a `WholeRunSession` safe to use as the
per-worker-process handle in `choice_branch_runner.py`'s multiprocessing pool.

Boundary values and the Action DTO per boundary follow
`C:\\STS2_Emulator\\docs\\api\\whole_run_api_reference_20260803.md` exactly (updated by
Emulator commit `dd8c800`, "Separate combat completion result from current Boundary in
StepResult"): `run_terminal` (renamed from the old `terminal`; means the RUN itself is
over - real defeat, or any concluded combat in legacy no-map mode, never combat's own
end by itself), `pending_choice`, `event_choice`, `rest_choice`, `shop_choice`,
`reward_select` (new - a pending card reward), `map_select` (via
`get_map_rooms`/`choose_room`, not `step`), `stable`.

A combat concluding is reported via `StepResult.Transition` (see
`run_emulator_bridge.transition_outcome_to_dict`), never via `Boundary`/`Done` -
`step()`'s returned dict's `"transition"` key is non-None exactly on the Step that
concluded a combat, with `transition["kind"] == "combat_completed"`.
`step()`'s `"done"` key now means the run is over (`observation.boundary ==
"run_terminal"`), not "a combat just ended" - do not use it as a combat-end signal.

Step() (Emulator commit `dd8c800`) now runs all post-combat/room-exit processing before
building Observation/LegalActions/RoomContext exactly once, so a StepResult's own
`observation`/`legal_actions`/`room_context` always agree with an immediate follow-up
`get_observation()`/`get_legal_actions()`/`get_room_context()` call - this module does
NOT re-fetch after `step()` to work around staleness (that was only ever needed against
the pre-`dd8c800` baseline); callers that want to double-check this invariant should do
so as an explicit debug assertion, not a routine re-fetch.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import run_emulator_bridge as bridge

RUN_TERMINAL = "run_terminal"
PENDING_CHOICE = "pending_choice"
EVENT_CHOICE = "event_choice"
REST_CHOICE = "rest_choice"
SHOP_CHOICE = "shop_choice"
REWARD_SELECT = "reward_select"
MAP_SELECT = "map_select"
STABLE = "stable"
FAULT = "fault"  # GetRoomContext()-only; never appears on GameObservation.Boundary.

BOUNDARY_VALUES = frozenset(
    {RUN_TERMINAL, PENDING_CHOICE, EVENT_CHOICE, REST_CHOICE, SHOP_CHOICE, REWARD_SELECT, MAP_SELECT, STABLE}
)

TRANSITION_KIND_COMBAT_COMPLETED = "combat_completed"


class WholeRunSession:
    def __init__(self, repo_root: Path | None = None):
        self._game = bridge.new_game_instance(repo_root)

    @property
    def raw_game_instance(self):
        """Escape hatch for callers that need direct CLR access (e.g. EnableGodModeForTesting)."""
        return self._game

    def enable_god_mode_for_testing(self) -> None:
        self._game.EnableGodModeForTesting()

    def start_run(self, seed: int | str, character_id: str, ascension: int) -> dict:
        result = self._game.StartRun(seed, character_id, ascension)
        return bridge.run_reset_result_to_dict(result)

    def get_observation(self) -> dict:
        return bridge.observation_to_dict(self._game.GetObservation())

    def get_legal_actions(self) -> list[dict]:
        return bridge.legal_actions_to_list(self._game.GetLegalActions())

    def get_room_context(self) -> dict:
        return bridge.room_context_to_dict(self._game.GetRoomContext())

    def get_map_rooms(self) -> list[dict]:
        return bridge.map_room_options_to_list(self._game.GetMapRooms())

    def choose_room(self, room_id: int) -> dict:
        return bridge.room_enter_result_to_dict(self._game.ChooseRoom(room_id))

    def step(self, action_id: int) -> dict:
        return bridge.step_result_to_dict(self._game.Step(action_id))

    def get_run_state(self) -> dict:
        return bridge.run_state_summary_to_dict(self._game.GetRunState())

    def get_run_summary(self, won: bool) -> dict:
        return bridge.run_summary_to_dict(self._game.GetRunSummary(won))

    def save_state(self) -> str:
        return str(self._game.SaveState())

    def load_state(self, snapshot_json: str) -> None:
        self._game.LoadState(snapshot_json)

    def get_event_rng_state(self) -> dict:
        return bridge.event_rng_snapshot_to_dict(self._game.GetEventRngState())

    def set_event_rng_state(self, overrides: dict[str, dict[str, int]]) -> None:
        """Applies partial overrides (by stream name) on top of the current live EventRngSnapshot.

        `overrides` maps stream key -> {"counter"/"s0"/"s1"/"s2"/"s3": value}, using
        the same stream keys as `event_rng_snapshot_to_dict`'s output
        (`event_rng`/`player_rewards_rng`/`player_shops_rng`/`player_transformations_rng`).
        """
        snapshot = self._game.GetEventRngState()
        stream_by_key = {
            "event_rng": snapshot.EventRng,
            "player_rewards_rng": snapshot.PlayerRewardsRng,
            "player_shops_rng": snapshot.PlayerShopsRng,
            "player_transformations_rng": snapshot.PlayerTransformationsRng,
        }
        for key, rng_overrides in overrides.items():
            bridge.apply_rng_overrides(stream_by_key[key], rng_overrides)
        self._game.SetEventRngState(snapshot)


def combat_just_concluded(step_result: dict) -> bool:
    """True exactly when `step_result` (from `WholeRunSession.step`) is the Step that
    watched a combat go from in-progress to concluded - the authoritative Combat-end
    signal per Emulator commit `dd8c800` (`StepResult.Transition.Kind ==
    "combat_completed"`), replacing the old `Boundary == "terminal"` / `Done` checks.
    """
    transition = step_result.get("transition")
    return transition is not None and transition["kind"] == TRANSITION_KIND_COMBAT_COMPLETED


def resolve_boundary(session: WholeRunSession) -> str:
    """`GetObservation().Boundary`, unless a `RoomContext` fault should be surfaced first.

    `GameObservation.Boundary` never contains `"fault"` - `GetObservation()`/`Step()`
    raise `FaultedCombatSessionException` in that case instead. Callers that must
    distinguish "the session already faulted" from "raise on next call" without
    triggering the exception should call `session.get_room_context()["boundary"]`
    directly instead of this helper.
    """
    return session.get_observation()["boundary"]


ACTION_TYPE_PREFERENCE_ORDER: list[str] = [
    "choice_target",
    "card",
    "potion",
    "system",
    "choice_shop_leave",
    "choice_confirm",
    "choice_skip",
    "choice_reward_skip",
    "choice_event_option",
    "choice_rest_option",
    "choice_card",
    "choice_reward_card",
    "choice_shop_buy_relic",
    "choice_event_throw_potion",
]


def pick_default_action(legal_actions: list[dict]) -> dict:
    """A deterministic, policy-free action picker used by the room progression driver.

    Not a gameplay policy: exists only so the connectivity test can drive a run to
    completion without a trained agent. Prefers finishing a still-open combat/target
    decision (`choice_target`, `card`, `potion`) over ending the turn (`system`) so
    combats actually conclude instead of stalling on repeated do-nothing end-turns;
    outside combat, prefers ending choices quickly (`choice_shop_leave`,
    `choice_confirm`, `choice_skip`) so runs progress instead of stalling on
    open-ended shopping decisions.
    """
    available = [a for a in legal_actions if a["is_available"]]
    pool = available or legal_actions
    if not pool:
        raise ValueError("No legal actions to pick from.")
    for action_type in ACTION_TYPE_PREFERENCE_ORDER:
        for action in pool:
            if action["action_type"] == action_type:
                return action
    return pool[0]
