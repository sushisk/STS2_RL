"""Paired-Emulator regression for privacy-safe exact-instance Replay Prefix pinning.

Requires an STS2_Emulator build containing PR #25's visible ``cardInstanceId`` contract.
The pure hosted tests cover the RL transformation without CLR; this test covers the real
production path end-to-end through ``CombatInstance.commit_action()`` and
``emulate_action()``.
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance  # noqa: E402


def _config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "SILENT",
        "player_hp": 70,
        "player_max_hp": 70,
        # Keep one pre-existing card after Acrobatics is played. The discard PendingChoice
        # contains this card plus the three newly drawn cards; replay pinning must verify
        # the existing hand card without mistaking it for a draw constraint.
        "hand_cards": [
            {"card_id": "ACROBATICS", "is_upgraded": False},
            {"card_id": "NEUTRALIZE", "is_upgraded": False},
        ],
        "draw_pile_cards": [
            {"card_id": "DEFEND_SILENT", "is_upgraded": True},
            {"card_id": "DEFEND_SILENT", "is_upgraded": False},
            {"card_id": "STRIKE_SILENT", "is_upgraded": False},
            {"card_id": "SURVIVOR", "is_upgraded": False},
        ],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 999, "max_hp": 999}],
    }


def _legal_actions(response: dict) -> list[dict]:
    return response["masked_emulator_dto"]["legal_actions"]


def _find_card(response: dict, action_type: str, card_id: str) -> dict:
    for action in _legal_actions(response):
        if action["action_type"] != action_type:
            continue
        if (action.get("parameters") or {}).get("cardId") == card_id:
            return action
    raise AssertionError(f"missing {action_type} {card_id}: {_legal_actions(response)!r}")


def test_acrobatics_replay_prefix_uses_visible_exact_instances_under_every_hypothesis() -> None:
    inst = CombatInstance("acrobatics-exact-instance-replay", _config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        acrobatics = _find_card(start, "card", "ACROBATICS")
        pending = inst.commit_action(start["decision_point_id"], acrobatics["action_id"])
        assert pending["status"] == "completed", pending

        candidates = [a for a in _legal_actions(pending) if a["action_type"] == "choice_card"]
        assert len(candidates) == 4, candidates
        identities = [
            (
                action["parameters"]["cardId"],
                action["parameters"].get("cardInstanceId"),
            )
            for action in candidates
        ]
        assert all(instance_id for _card_id, instance_id in identities), identities
        assert len({instance_id for _card_id, instance_id in identities}) == 4, identities
        assert any(card_id == "NEUTRALIZE" for card_id, _instance_id in identities), identities
        defend_instances = [
            instance_id for card_id, instance_id in identities if card_id == "DEFEND_SILENT"
        ]
        assert len(defend_instances) == 2 and defend_instances[0] != defend_instances[1], identities

        # Each rng_id forces a different CardId-level hypothesis index. Reconstructing the
        # real Acrobatics prefix must nevertheless reproduce the already-visible concrete
        # choice set, including the pre-existing hand card, so resolving any true
        # candidate must not fault with replay_mismatch.
        for rng_id in range(1, 9):
            candidate = candidates[(rng_id - 1) % len(candidates)]
            result = inst.emulate_action(
                parent_branch_id="root",
                branch_id=f"acrobatics-hyp-{rng_id}",
                rng_id=rng_id,
                decision_point_id=pending["decision_point_id"],
                action_id=candidate["action_id"],
                simulation_options=None,
            )
            assert result["status"] == "completed", (rng_id, candidate, result)
            assert result.get("fault_kind") != "replay_mismatch", (rng_id, result)
    finally:
        inst.close()


def _run_all() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = [], []
    for test in tests:
        try:
            test()
            passed.append(test.__name__)
            print(f"PASS {test.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
