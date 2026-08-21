"""Real-Emulator coverage for WholeRunSession combat snapshot round-trips."""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RUN_DIR = _ROOT / "Run"
for _path in (_ROOT, _RUN_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from whole_run_session import MAP_SELECT, RUN_TERMINAL, WholeRunSession, pick_default_action  # noqa: E402


def _advance_to_combat(session: WholeRunSession) -> dict:
    session.start_run(1, "Ironclad", 0)
    observation = session.get_observation()
    for _ in range(80):
        if observation["boundary"] == RUN_TERMINAL:
            raise AssertionError("run reached terminal before a Combat decision")
        if observation["boundary"] == MAP_SELECT:
            rooms = session.get_map_rooms()
            entered = session.choose_room(rooms[0]["room_id"])
            observation = session.get_observation()
            if entered["is_combat"]:
                return observation
            continue
        actions = session.get_legal_actions()
        assert actions, f"no legal actions before Combat: {observation}"
        observation = session.step(pick_default_action(actions)["action_id"])["observation"]
    raise AssertionError("expected to reach a Combat decision within 80 steps")


def test_whole_run_combat_snapshot_round_trip_restores_combat_only_state() -> None:
    session = WholeRunSession()
    observation = _advance_to_combat(session)
    before = {
        "hp": observation["state"]["hp"],
        "energy": observation["state"]["energy"],
        "block": observation["state"]["block"],
        "enemies": observation["state"]["enemies"],
        "hand": observation["state"]["hand"],
        "legal_actions": session.get_legal_actions(),
    }
    snapshot = session.capture_combat_snapshot()

    action = next(action for action in before["legal_actions"] if action["is_available"])
    session.step(action["action_id"])
    session.restore_combat_snapshot(snapshot)

    restored_observation = session.get_observation()
    restored_state = restored_observation["state"]
    assert {key: restored_state[key] for key in ("hp", "energy", "block", "enemies", "hand")} == {
        key: before[key] for key in ("hp", "energy", "block", "enemies", "hand")
    }
    assert session.get_legal_actions() == before["legal_actions"]
    assert restored_state.get("totalFloor") is None
