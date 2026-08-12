"""Coverage for `WholeRunInstance`'s explicit `god_mode` instance_config opt-in.

See Outputs/reports/god_mode_data_collection_proposal_20260812.md and
Run/tests/test_god_mode_scope.py (the AST guard that allowlists exactly this one call
site). GodModeEnabled is verified via the internal RunSnapshot (save_state()) rather than
`playerPowers` in the observation, since the god-mode powers are only (re-)applied at
each combat's own CombatSetUp - they are not present while navigating the map/events, but
GodModeEnabled itself is boundary-independent and round-trips through every snapshot.
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_whole_run import WholeRunInstance  # noqa: E402
from API.validation import RequestRejected  # noqa: E402


def _config(*, god_mode: bool | None = None) -> dict:
    config = {"instance_type": "whole_run", "seed": 1, "character_id": "IRONCLAD", "ascension": 0}
    if god_mode is not None:
        config["god_mode"] = god_mode
    return config


def _advance_to_map_select(inst: WholeRunInstance) -> None:
    # SaveState() cannot run mid-event (see GameInstance.SaveState's doc comment) - every
    # Whole Run's first decision is the pre-Map NEOW-style event, so the snapshot check
    # below must advance past it first.
    decision = inst.start_instance_response()
    for _ in range(50):
        if decision["masked_emulator_dto"].get("boundary") == "map_select":
            return
        legal = decision["masked_emulator_dto"]["legal_actions"]
        decision = inst.commit_action(
            decision_point_id=decision["decision_point_id"], action_id=legal[0]["action_id"]
        )
    raise AssertionError("never reached map_select")


def _god_mode_enabled(inst: WholeRunInstance) -> bool:
    # Emulator's StateEnvelope/RunSnapshot use no camelCase naming policy - the JSON
    # keys are the C# PascalCase property names as-is (Kind/Run/GodModeEnabled).
    _advance_to_map_select(inst)
    snapshot = json.loads(inst._session.save_state())  # noqa: SLF001
    return bool(snapshot["Run"]["GodModeEnabled"])


class GodModeOptInTest(unittest.TestCase):
    def test_god_mode_true_enables_invincibility_for_the_instance(self) -> None:
        inst = WholeRunInstance("wr-god-mode-on", _config(god_mode=True), branch_worker_count=2)
        try:
            self.assertTrue(_god_mode_enabled(inst))
        finally:
            inst.close()

    def test_god_mode_omitted_defaults_to_disabled(self) -> None:
        inst = WholeRunInstance("wr-god-mode-default", _config(), branch_worker_count=2)
        try:
            self.assertFalse(_god_mode_enabled(inst))
        finally:
            inst.close()

    def test_god_mode_false_is_explicitly_disabled(self) -> None:
        inst = WholeRunInstance("wr-god-mode-off", _config(god_mode=False), branch_worker_count=2)
        try:
            self.assertFalse(_god_mode_enabled(inst))
        finally:
            inst.close()

    def test_non_bool_god_mode_is_rejected_without_truthy_coercion(self) -> None:
        config = _config()
        config["god_mode"] = "false"

        with self.assertRaises(RequestRejected):
            WholeRunInstance("wr-god-mode-invalid", config, branch_worker_count=2)


if __name__ == "__main__":
    unittest.main()
