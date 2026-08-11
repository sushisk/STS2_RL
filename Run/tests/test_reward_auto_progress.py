from __future__ import annotations

import sys
import unittest
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parents[1]
if str(RUN_DIR) not in sys.path:
    sys.path.insert(0, str(RUN_DIR))

from reward_auto_progress import drain_trivial_reward_frontier


class FakeSession:
    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0
        self.stepped = []

    def get_observation(self):
        return {"boundary": self.frames[self.index]["boundary"], "state": self.frames[self.index].get("state", {})}

    def get_legal_actions(self):
        return list(self.frames[self.index].get("legal_actions", []))

    def step(self, action_id):
        self.stepped.append(action_id)
        self.index += 1
        frame = self.frames[self.index]
        return {"action_id": action_id, "observation": {"boundary": frame["boundary"]}}


class RewardAutoProgressTests(unittest.TestCase):
    def test_exact_take_action_is_auto_committed_without_state_inference(self):
        session = FakeSession(
            [
                {
                    "boundary": "reward_select",
                    # Deliberately no potion inventory in state: legality is Emulator-owned.
                    "state": {},
                    "legal_actions": [
                        {"action_id": 7, "action_type": "choice_reward_potion_take", "is_available": True}
                    ],
                },
                {"boundary": "event_choice", "legal_actions": []},
            ]
        )
        result = drain_trivial_reward_frontier(session)
        self.assertEqual((7,), result.auto_action_ids)
        self.assertEqual([7], session.stepped)
        self.assertEqual("event_choice", session.get_observation()["boundary"])

    def test_full_belt_replace_skip_is_not_auto_committed(self):
        session = FakeSession(
            [
                {
                    "boundary": "reward_select",
                    "legal_actions": [
                        {"action_id": 1, "action_type": "choice_reward_potion_replace", "is_available": True},
                        {"action_id": 2, "action_type": "choice_reward_skip", "is_available": True},
                    ],
                }
            ]
        )
        result = drain_trivial_reward_frontier(session)
        self.assertEqual((), result.auto_action_ids)
        self.assertEqual([], session.stepped)

    def test_consecutive_trivial_takes_are_recorded_in_order(self):
        session = FakeSession(
            [
                {"boundary": "reward_select", "legal_actions": [{"action_id": 3, "action_type": "choice_reward_potion_take", "is_available": True}]},
                {"boundary": "reward_select", "legal_actions": [{"action_id": 4, "action_type": "choice_reward_potion_take", "is_available": True}]},
                {"boundary": "map_select", "legal_actions": []},
            ]
        )
        result = drain_trivial_reward_frontier(session)
        self.assertEqual((3, 4), result.auto_action_ids)
        self.assertEqual([3, 4], session.stepped)

    def test_multiple_take_actions_fault_instead_of_guessing(self):
        session = FakeSession(
            [
                {
                    "boundary": "reward_select",
                    "legal_actions": [
                        {"action_id": 3, "action_type": "choice_reward_potion_take", "is_available": True},
                        {"action_id": 4, "action_type": "choice_reward_potion_take", "is_available": True},
                    ],
                }
            ]
        )
        with self.assertRaises(RuntimeError):
            drain_trivial_reward_frontier(session)


if __name__ == "__main__":
    unittest.main()
