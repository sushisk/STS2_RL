"""One live whole run through real combats, with no fakes anywhere.

The rest of the combat-phase tests drive enter/leave with a stub session whose
``step`` never reaches ``_mutating_game``.  That made them blind to the defect this
file exists for: ``leave_combat_phase`` drained the reward frontier while the access
state was still TRANSFERRING, and draining steps the board.  Nothing may hold a
mutating capability mid-transfer - that exclusion is what the transfer is for - so
every such leave failed with ``Game access is TRANSFERRING``.

It did not fail on every combat.  ``drain_trivial_reward_frontier`` only steps when
the frontier has a single legal action, so a reward screen offering a choice drained
nothing, mutated nothing, and passed.  Deterministic, but only on combats that end
into a trivial frontier - which is why a suite of stubs stayed green while the real
wiring could not complete a run.

The failure also did not stay local: the faulting path poisons the process-wide
GameAccess, so one real defect took down 194 tests that had nothing to do with it.
"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_whole_run_beam import WholeRunInstance  # noqa: E402


def _pick(legal: list) -> str:
    for action in legal:
        if action.get("action_type") == "card":
            return action["action_id"]
    return legal[0]["action_id"]


def test_a_live_run_plays_through_whole_combats_without_faulting():
    instance = WholeRunInstance(
        "live-combat-phase",
        {"instance_type": "whole_run", "seed": 1, "character_id": "IRONCLAD", "ascension": 0},
        branch_worker_count=2,
    )
    try:
        decision = instance.start_instance_response()
        combats_completed = 0
        was_in_combat = False
        combat_branch_checked = False

        for step in range(120):
            dto = decision.get("masked_emulator_dto")
            assert dto is not None, (
                f"root commit faulted at step {step}: "
                f"{ {k: v for k, v in decision.items() if k != 'masked_emulator_dto'} }"
            )
            in_combat = instance._combat_phase is not None  # noqa: SLF001
            if was_in_combat and not in_combat:
                combats_completed += 1
            was_in_combat = in_combat

            # This is intentionally a real adopted CombatPhase with real workers, not
            # the transaction fakes used by the unit tests.  A successful response must
            # be usable through the ordinary public branch endpoints.
            if in_combat and not combat_branch_checked:
                legal = dto.get("legal_actions") or []
                if legal:
                    branch_id = "live-combat-branch"
                    emulated = instance.emulate_actions(
                        items=[
                            {
                                "parent_branch_id": "root",
                                "branch_id": branch_id,
                                "rng_id": 0,
                                "decision_point_id": decision["decision_point_id"],
                                "action_id": _pick(legal),
                            }
                        ],
                        simulation_options={"stop_condition": "next_decision"},
                    )
                    branch = emulated["branch_results"][branch_id]
                    assert branch["status"] == "completed"
                    usable = instance.get_decision(branch_id)
                    assert usable["status"] == "completed"
                    assert usable["branch_id"] == branch_id
                    instance.release_branches([branch_id])
                    assert instance.get_branch_status([branch_id])["branch_statuses"][branch_id] == "released"
                    combat_branch_checked = True

            legal = dto.get("legal_actions") or []
            if not legal:
                break
            decision = instance.commit_action(decision["decision_point_id"], _pick(legal))

        assert combats_completed >= 2, (
            "expected the run to enter and leave at least two combats; "
            f"only {combats_completed} completed"
        )
        assert combat_branch_checked, "expected at least one real CombatPhase branch"
    finally:
        instance.close()
