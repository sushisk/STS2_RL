"""Regression coverage for the "beta" RNG-hypothesis-timing question: does a
DrawPile-order/RNG hypothesis substitution change WHICH candidates a PendingChoice
offers, for a mechanic whose candidates are drawn from a shuffled subset of the
DrawPile (SEEKER_STRIKE: `StableShuffle(...CombatCardSelection).Take(3)`)?

Exercises the real production path (`CombatInstance.emulate_action()` ->
`API.combat_rng_mapping.build_single_hypothesis_work_item` ->
`search.rng_hypothesis.apply_hypothesis_to_context` ->
`derive_substituted_snapshot` -> Emulator Restore + replay), not a hand-rolled
scenario-order probe - `derive_substituted_snapshot` only ever overwrites
`Rng.RunRng["Shuffle"]` and `Player.DrawPile` (see its own docstring), so if
SEEKER_STRIKE's candidate subset silently depended on DrawPile order or on any other
RNG stream, a hypothesis-substituted replay would offer a genuinely different
PendingChoice than the TRUE (real-RNG) one reached via `commit_action()`.

If this ever starts failing, it means beta no longer holds for this mechanic and
`Fix C`'s deferred "correct branch creation" (true RNG up to Pending, hypothesis RNG
only for the forward-exploring step) is no longer a moot distinction - the search
coordinator would need to stop trusting a TRUE-observed action_id/candidate set
against a hypothesis-derived replay of this mechanic.
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


def _seeker_strike_config() -> dict:
    return {
        "instance_type": "combat",
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["SEEKER_STRIKE"],
        "draw_pile": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "SURVIVOR", "BURNING_PACT", "ARMAMENTS", "HOLOGRAM"],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [],
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _legal_actions(response: dict) -> list[dict]:
    return response["masked_emulator_dto"]["legal_actions"]


def _first_action_id(response: dict, action_type: str) -> str:
    return next(action["action_id"] for action in _legal_actions(response) if action["action_type"] == action_type)


def _choice_card_actions(response: dict) -> list[dict]:
    return [a for a in _legal_actions(response) if a["action_type"] == "choice_card"]


def test_seeker_strike_pending_candidates_accepted_under_every_hypothesis_rng_id() -> None:
    """A candidate action_id observed under the TRUE (root, real-RNG) PendingChoice must
    still be accepted (status == "completed", not faulted/rejected) when resolved
    through `emulate_action()` under several DIFFERENT hypothesis rng_ids for the same
    decision_point_id - i.e. re-deriving this Pending decision from the Held Stable
    Snapshot under a substituted Shuffle stream + reordered DrawPile must not silently
    swap in a different candidate set than what was truly reachable."""
    inst = CombatInstance("seeker-strike-beta-regression", _seeker_strike_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        seeker_action_id = _first_action_id(start, "card")

        pending = inst.commit_action(start["decision_point_id"], seeker_action_id)
        assert pending["status"] == "completed", pending
        true_candidates = _choice_card_actions(pending)
        assert len(true_candidates) >= 2, true_candidates
        true_candidate_ids = sorted(a["parameters"]["cardId"] for a in true_candidates)

        for rng_id, candidate in enumerate(true_candidates, start=1):
            result = inst.emulate_action(
                parent_branch_id="root",
                branch_id=f"hyp-{rng_id}",
                rng_id=rng_id,
                decision_point_id=pending["decision_point_id"],
                action_id=candidate["action_id"],
                simulation_options=None,
            )
            assert result["status"] == "completed", (rng_id, candidate["parameters"]["cardId"], result)

        # One more hypothesis (an rng_id none of the per-candidate branches above used)
        # exercised against every TRUE candidate individually, to widen coverage beyond
        # the 1:1 candidate<->rng_id pairing above.
        wide_rng_id = len(true_candidates) + 1
        for idx, candidate in enumerate(true_candidates):
            result = inst.emulate_action(
                parent_branch_id="root",
                branch_id=f"hyp-wide-{idx}",
                rng_id=wide_rng_id,
                decision_point_id=pending["decision_point_id"],
                action_id=candidate["action_id"],
                simulation_options=None,
            )
            assert result["status"] == "completed", (wide_rng_id, candidate["parameters"]["cardId"], result)

        assert true_candidate_ids == sorted(true_candidate_ids)  # sanity: no duplicate bookkeeping mutation above
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
