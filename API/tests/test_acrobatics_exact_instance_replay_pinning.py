"""Paired-Emulator regression for privacy-safe exact-instance Replay Prefix pinning.

Requires an STS2_Emulator build containing PR #25's visible ``cardInstanceId`` contract.
The pure hosted tests cover the RL transformation without CLR; this test covers the real
production path end-to-end through ``CombatInstance.commit_action()`` and
``emulate_action()``, including the review-critical Restore -> Capture namespace rollover.
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
from search.decision_context import start_new_replay_prefix_from_stable  # noqa: E402


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


def _reroot_through_restore_then_capture(inst: CombatInstance):
    """Exercise Emulator #25's fresh-session public-card namespace after Restore.

    The original Stable snapshot S0 belongs to session A. Restoring it creates session B;
    capturing immediately afterward must produce S1 whose Metadata.CombatSessionId is B,
    and every later visible cardv-* token in that live session must be re-derivable from
    S1. CombatInstance normally reaches this shape through resumed/restored execution;
    this paired test establishes it directly before exposing the first public decision.
    """
    s0 = inst._held_stable_snapshot  # noqa: SLF001 - intentional paired white-box gate
    assert s0 is not None
    restored = inst._session.restore_snapshot(s0)  # noqa: SLF001
    s1 = inst._session.capture_snapshot()  # noqa: SLF001
    assert s0.Metadata.CombatSessionId
    assert s1.Metadata.CombatSessionId
    assert s1.Metadata.CombatSessionId != s0.Metadata.CombatSessionId

    inst._root_state = restored  # noqa: SLF001
    inst._held_stable_snapshot = s1  # noqa: SLF001
    inst._replay_prefix = start_new_replay_prefix_from_stable()  # noqa: SLF001
    return s1


def test_acrobatics_replay_prefix_uses_visible_exact_instances_under_every_hypothesis() -> None:
    inst = CombatInstance("acrobatics-exact-instance-replay", _config(), worker_count=2)
    try:
        rerooted_snapshot = _reroot_through_restore_then_capture(inst)
        start = inst.start_instance_response()
        acrobatics = _find_card(start, "card", "ACROBATICS")
        pending = inst.commit_action(start["decision_point_id"], acrobatics["action_id"])
        assert pending["status"] == "completed", pending

        candidates = [a for a in _legal_actions(pending) if a["action_type"] == "choice_card"]
        assert len(candidates) == 4, candidates
        card_ids = [(action.get("parameters") or {}).get("cardId") for action in candidates]
        assert card_ids.count("NEUTRALIZE") == 1, card_ids
        assert card_ids.count("DEFEND_SILENT") == 2, card_ids
        assert card_ids.count("STRIKE_SILENT") == 1, card_ids

        # cardInstanceId is an Emulator -> RL reconstruction signal. RL must consume it
        # before building the Training-facing DTO, then redact it from both the PendingChoice
        # and matching choice_card actions to avoid persistent concrete-copy linkability.
        assert all(
            "cardInstanceId" not in (action.get("parameters") or {}) for action in candidates
        ), candidates
        pending_options = pending["masked_emulator_dto"]["pendingChoice"]["options"]
        assert len(pending_options) == 4, pending_options
        assert all("cardInstanceId" not in option for option in pending_options), pending_options

        # The raw visible ids have already been translated internally before masking. Verify
        # that only the three newly drawn Acrobatics cards became exact Snapshot constraints,
        # and that the two same-CardId DEFEND copies remain distinct concrete instances with
        # their upgraded/unupgraded state preserved.
        assert len(inst._replay_prefix) == 1  # noqa: SLF001
        constraints = inst._replay_prefix[0].visible_draw_constraints  # noqa: SLF001
        assert len(constraints) == 3, constraints
        assert [card_id for card_id, _instance_id in constraints].count("DEFEND_SILENT") == 2
        assert all(not instance_id.startswith("cardv-") for _card_id, instance_id in constraints)

        root_draw_by_instance = {
            str(card.InstanceId): card for card in rerooted_snapshot.Player.DrawPile
        }
        assert all(instance_id in root_draw_by_instance for _card_id, instance_id in constraints)
        defend_instance_ids = [
            instance_id
            for card_id, instance_id in constraints
            if card_id == "DEFEND_SILENT"
        ]
        assert len(defend_instance_ids) == 2 and defend_instance_ids[0] != defend_instance_ids[1]
        assert {
            bool(root_draw_by_instance[instance_id].IsUpgraded)
            for instance_id in defend_instance_ids
        } == {False, True}

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
