from __future__ import annotations

import sys
import unittest
from pathlib import Path

RUN_DIR = Path(__file__).resolve().parents[1]
if str(RUN_DIR) not in sys.path:
    sys.path.insert(0, str(RUN_DIR))

from legal_action_identity import legal_action_semantic_key
from reward_auto_progress import drain_trivial_reward_frontier
from worker_pool import ChoiceWorkItem, _bootstrap_reach


class FakeSession:
    def __init__(self, frames):
        self.frames = list(frames)
        self.index = 0
        self.stepped = []
        self.loaded = []
        self.entered_rooms = []

    def load_state(self, snapshot):
        self.loaded.append(snapshot)

    def choose_room(self, room_id):
        self.entered_rooms.append(room_id)
        return {"room_id": room_id}

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

    def test_potion_replacement_slots_have_distinct_semantic_identity(self):
        common = {
            "action_type": "choice_reward_potion_replace",
            "label": "FAIRY_POTION:replace",
        }
        slot0 = {
            **common,
            "action_id": 11,
            "parameters": {
                "potionId": "FAIRY_POTION",
                "potionSlot": 0,
                "replacedPotionId": "FIRE_POTION",
            },
        }
        slot1 = {
            **common,
            "action_id": 12,
            "parameters": {
                "potionId": "FAIRY_POTION",
                "potionSlot": 1,
                "replacedPotionId": "BLOCK_POTION",
            },
        }
        self.assertNotEqual(legal_action_semantic_key(slot0), legal_action_semantic_key(slot1))

    def test_discover_prefix_records_hidden_take_after_visible_action(self):
        session = FakeSession(
            [
                {
                    "boundary": "event_choice",
                    "legal_actions": [
                        {"action_id": 5, "action_type": "choice_event_option", "label": "continue", "is_available": True}
                    ],
                },
                {
                    "boundary": "reward_select",
                    # Match the real Emulator shape for an empty-slot PotionReward:
                    # TAKE is transport, while SKIP remains a visible policy choice.
                    "legal_actions": [
                        {"action_id": 7, "action_type": "choice_reward_potion_take", "is_available": True},
                        {"action_id": 8, "action_type": "choice_reward_skip", "is_available": True},
                    ],
                },
                {"boundary": "rest_choice", "legal_actions": []},
            ]
        )
        work = ChoiceWorkItem(
            work_id="discover",
            context_id="ctx",
            choice_type="rest",
            map_snapshot="snapshot",
            room_id=9,
            action_prefix=[],
            relic_injection=None,
            target_boundary="rest_choice",
            work_kind="sub_branch",
            discover_prefix=True,
        )

        discovered = _bootstrap_reach(session, work)

        self.assertEqual([5, 7], discovered)
        self.assertEqual([5, 7], session.stepped)
        self.assertEqual("rest_choice", session.get_observation()["boundary"])

    def test_literal_replay_consumes_saved_hidden_take_once_without_auto_drain(self):
        session = FakeSession(
            [
                {
                    "boundary": "event_choice",
                    "legal_actions": [
                        {"action_id": 5, "action_type": "choice_event_option", "label": "continue", "is_available": True}
                    ],
                },
                {
                    "boundary": "reward_select",
                    "legal_actions": [
                        {"action_id": 7, "action_type": "choice_reward_potion_take", "is_available": True},
                        {"action_id": 8, "action_type": "choice_reward_skip", "is_available": True},
                    ],
                },
                {"boundary": "rest_choice", "legal_actions": []},
            ]
        )
        work = ChoiceWorkItem(
            work_id="replay",
            context_id="ctx",
            choice_type="rest",
            map_snapshot="snapshot",
            room_id=9,
            action_prefix=[5, 7],
            relic_injection=None,
            target_boundary="rest_choice",
            work_kind="sub_branch",
            discover_prefix=False,
        )

        discovered = _bootstrap_reach(session, work)

        self.assertIsNone(discovered)
        self.assertEqual([5, 7], session.stepped)
        self.assertEqual("rest_choice", session.get_observation()["boundary"])


if __name__ == "__main__":
    unittest.main()
