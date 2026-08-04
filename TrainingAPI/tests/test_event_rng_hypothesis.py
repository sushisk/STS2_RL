"""Pure-Python coverage for Whole Run event RNG hypothesis derivation.

No CLR/emulator access is used here. The tests operate only on dicts and the registry
class in `TrainingAPI.whole_run_event_rng`.
"""

from __future__ import annotations

import copy
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from TrainingAPI import whole_run_event_rng as wh  # noqa: E402
from TrainingAPI.whole_run_event_rng import EventRngHypothesisRegistry, derive_event_rng_hypothesis  # noqa: E402


def _base_state():
    return {
        "event_id": "NEOW",
        "event_rng": {"counter": 3, "s0": 111111, "s1": 222222, "s2": 333333, "s3": 444444},
        "player_rewards_rng": {"counter": 7, "s0": 555555, "s1": 666666, "s2": 777777, "s3": 888888},
        "player_shops_rng": {"counter": 1, "s0": 999999, "s1": 101010, "s2": 202020, "s3": 303030},
        "player_transformations_rng": {"counter": 2, "s0": 404040, "s1": 505050, "s2": 606060, "s3": 707070},
    }


def _signature(state: dict):
    return (
        state["event_id"],
        state["event_rng"]["counter"],
        state["event_rng"]["s0"],
        state["event_rng"]["s1"],
        state["event_rng"]["s2"],
        state["event_rng"]["s3"],
        state["player_rewards_rng"]["counter"],
        state["player_rewards_rng"]["s0"],
        state["player_rewards_rng"]["s1"],
        state["player_rewards_rng"]["s2"],
        state["player_rewards_rng"]["s3"],
        state["player_shops_rng"]["counter"],
        state["player_shops_rng"]["s0"],
        state["player_shops_rng"]["s1"],
        state["player_shops_rng"]["s2"],
        state["player_shops_rng"]["s3"],
        state["player_transformations_rng"]["counter"],
        state["player_transformations_rng"]["s0"],
        state["player_transformations_rng"]["s1"],
        state["player_transformations_rng"]["s2"],
        state["player_transformations_rng"]["s3"],
    )


def test_derive_event_rng_hypothesis_is_deterministic_and_pure():
    base = _base_state()
    first = derive_event_rng_hypothesis(base, 5)
    second = derive_event_rng_hypothesis(base, 5)
    assert first == second
    assert first is not second
    assert base == _base_state()


def test_derive_event_rng_hypothesis_rejects_non_positive_rng_id():
    base = _base_state()
    for invalid in (0, -1, -99):
        raised = False
        try:
            derive_event_rng_hypothesis(base, invalid)
        except ValueError:
            raised = True
        assert raised, f"rng_id={invalid} must raise ValueError"


def test_derive_event_rng_hypothesis_produces_valid_field_ranges():
    derived = derive_event_rng_hypothesis(_base_state(), 17)
    assert derived["event_id"] == "NEOW"
    for stream_name in ("event_rng", "player_rewards_rng", "player_shops_rng", "player_transformations_rng"):
        stream = derived[stream_name]
        assert isinstance(stream["counter"], int)
        assert -(2**31) <= stream["counter"] <= 2**31 - 1
        for word in ("s0", "s1", "s2", "s3"):
            assert isinstance(stream[word], int)
            assert 0 <= stream[word] <= 2**64 - 1


def test_derive_event_rng_hypothesis_rng_ids_1_to_20_are_distinct():
    base = _base_state()
    signatures = [(_signature(derive_event_rng_hypothesis(base, rng_id)), rng_id) for rng_id in range(1, 21)]
    seen = {}
    for signature, rng_id in signatures:
        assert signature not in seen, f"collision between rng_id={seen[signature]} and rng_id={rng_id}"
        seen[signature] = rng_id


def test_derive_event_rng_hypothesis_streams_are_independent():
    base = _base_state()
    modified = copy.deepcopy(base)
    modified["event_rng"]["counter"] = 999
    modified["event_rng"]["s0"] = 888
    modified["event_rng"]["s1"] = 777
    modified["event_rng"]["s2"] = 666
    modified["event_rng"]["s3"] = 555

    original = derive_event_rng_hypothesis(base, 9)
    changed = derive_event_rng_hypothesis(modified, 9)

    assert original["event_id"] == changed["event_id"] == "NEOW"
    assert original["event_rng"] != changed["event_rng"]
    assert original["player_rewards_rng"] == changed["player_rewards_rng"]
    assert original["player_shops_rng"] == changed["player_shops_rng"]
    assert original["player_transformations_rng"] == changed["player_transformations_rng"]


def test_derive_event_rng_hypothesis_zero_state_guard():
    class _ZeroDigest:
        def digest(self):
            return b"\x00" * 32

    def _fake_sha256(_data=b""):
        return _ZeroDigest()

    original = wh.hashlib.sha256
    wh.hashlib.sha256 = _fake_sha256
    try:
        derived = derive_event_rng_hypothesis(_base_state(), 3)
    finally:
        wh.hashlib.sha256 = original

    for stream_name in ("event_rng", "player_rewards_rng", "player_shops_rng", "player_transformations_rng"):
        stream = derived[stream_name]
        assert stream["counter"] == 0
        assert stream["s0"] == 1
        assert stream["s1"] == 0
        assert stream["s2"] == 0
        assert stream["s3"] == 0


def test_registry_get_or_create_is_idempotent_and_memoized_by_key():
    reg = EventRngHypothesisRegistry()
    key = ("root", "dp-1", 7)
    first_base = _base_state()
    second_base = copy.deepcopy(first_base)
    second_base["event_rng"]["counter"] = 12345

    first = reg.get_or_create(key, first_base, 7)
    second = reg.get_or_create(key, second_base, 7)
    assert first is second
    assert first == second
    assert reg.is_live(key)
    assert reg.generation_of(key) == 1


def test_registry_register_and_release_branch_reference_counting():
    reg = EventRngHypothesisRegistry()
    key = ("root", "dp-2", 11)
    reg.get_or_create(key, _base_state(), 11)
    reg.register_branch(key, "b1")
    reg.register_branch(key, "b2")
    assert reg.is_live(key)

    reg.release_branch(key, "b1")
    assert reg.is_live(key)
    assert reg.generation_of(key) == 1

    reg.release_branch(key, "b2")
    assert not reg.is_live(key)


def test_registry_release_branch_then_recreate_starts_new_generation():
    reg = EventRngHypothesisRegistry()
    key = ("root", "dp-3", 13)
    first = reg.get_or_create(key, _base_state(), 13)
    reg.register_branch(key, "b1")
    reg.release_branch(key, "b1")
    assert not reg.is_live(key)

    second = reg.get_or_create(key, _base_state(), 13)
    assert reg.is_live(key)
    assert reg.generation_of(key) == 2
    assert first == second
    assert first is not second


def test_registry_release_all_for_decision_scopes_to_exact_pair():
    reg = EventRngHypothesisRegistry()
    key_a = ("root", "dp-4", 1)
    key_b = ("root", "dp-4", 2)
    key_c = ("root", "dp-5", 3)
    reg.get_or_create(key_a, _base_state(), 1)
    reg.get_or_create(key_b, _base_state(), 2)
    reg.get_or_create(key_c, _base_state(), 3)
    reg.register_branch(key_a, "ba")
    reg.register_branch(key_b, "bb")
    reg.register_branch(key_c, "bc")

    reg.release_all_for_decision("root", "dp-4")
    assert not reg.is_live(key_a)
    assert not reg.is_live(key_b)
    assert reg.is_live(key_c)


def test_registry_release_all_clears_everything():
    reg = EventRngHypothesisRegistry()
    key_a = ("root", "dp-6", 1)
    key_b = ("branch", "dp-7", 2)
    reg.get_or_create(key_a, _base_state(), 1)
    reg.get_or_create(key_b, _base_state(), 2)
    reg.register_branch(key_a, "ba")
    reg.register_branch(key_b, "bb")

    reg.release_all()
    assert not reg.is_live(key_a)
    assert not reg.is_live(key_b)


def test_registry_generation_increments_after_full_release_cycle():
    reg = EventRngHypothesisRegistry()
    key = ("root", "dp-8", 4)
    reg.get_or_create(key, _base_state(), 4)
    reg.register_branch(key, "b1")
    reg.release_all()
    assert not reg.is_live(key)

    reg.get_or_create(key, _base_state(), 4)
    assert reg.generation_of(key) == 2
    assert reg.is_live(key)


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
