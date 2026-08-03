"""Fault/Lifecycle coverage (RL担当指示：RL–Training API実装 §8 "Fault／Lifecycle").

Combat's own Worker timeout/kill/Respawn/Cancel primitives are already exhaustively
tested at the `BranchWorkerPool`/`BranchManager` level (Phase N:
`Combat/tests/test_worker_respawn.py`, `Combat/tests/test_branch_manager.py`) - this
file proves the TrainingAPI WIRE LAYER correctly surfaces those same events (a hung
Branch Worker becomes a `faulted` Response, not a hang/crash; a released Branch is
rejected on reuse; `close_instance` is safe to call twice; a Training-side disconnect -
modeled here as tearing down the OS process without an explicit `close_instance` first -
still cleans up every resource).

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from TrainingAPI.api_runtime import RLApiServerProcess  # noqa: E402
from TrainingAPI.dto import SCHEMA_VERSION  # noqa: E402
from TrainingAPI.instance_combat import CombatInstance  # noqa: E402
from TrainingAPI.server import RLApiServer  # noqa: E402
from TrainingAPI.validation import RequestRejected  # noqa: E402


def _combat_config():
    return {
        "instance_type": "combat", "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1, "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def test_worker_timeout_surfaces_as_faulted_and_pool_recovers():
    inst = CombatInstance("f1", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        legal = start["masked_emulator_dto"]["legal_actions"]
        defend_id = next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD")

        # An unrealistically short max_time_ms guarantees the real Bootstrap Step cannot
        # finish in time, forcing the same queue.Empty/respawn path Phase N's
        # branch_worker_pool tests exercise directly - here observed through the wire
        # contract's own `emulate_action` response instead.
        timed_out = inst.emulate_action(
            parent_branch_id="root", branch_id="b-timeout", rng_id=1, decision_point_id=dp0, action_id=defend_id,
            simulation_options={"max_time_ms": 1},
        )
        assert timed_out["status"] == "faulted", timed_out
        assert timed_out.get("fault_kind") is not None
        assert isinstance(timed_out.get("error"), str) and timed_out["error"]

        # The Pool must still be usable afterward - a hung Worker never takes the whole
        # Pool down (Part B guarantee), and root itself must be completely unaffected.
        assert inst.get_decision("root")["decision_point_id"] == dp0
        recovered = inst.emulate_action(
            parent_branch_id="root", branch_id="b-after", rng_id=1, decision_point_id=dp0, action_id=defend_id, simulation_options=None
        )
        assert recovered["status"] == "completed", recovered
    finally:
        inst.close()


def test_release_then_get_decision_rejects_reuse():
    inst = CombatInstance("f2", _combat_config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        legal = start["masked_emulator_dto"]["legal_actions"]
        defend_id = next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD")
        em = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=defend_id, simulation_options=None)
        assert em["status"] == "completed"
        inst.release_branches(["b1"])

        released = inst.get_decision("b1")
        assert released["status"] == "released"
        assert "masked_emulator_dto" not in released, "a released Branch's Decision content must not be returned"

        # branch_id must never be reusable, even after Release.
        raised = False
        try:
            inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=defend_id, simulation_options=None)
        except RequestRejected:
            raised = True
        assert raised, "a Released branch_id must never be reusable"
    finally:
        inst.close()


def test_holder_lease_invalidated_on_cancel():
    """A Branch that reached a Pending boundary (established a real Lease) must have
    that Lease invalidated when Cancelled - verified via the Combat BranchManager's own
    LeaseRegistry, which the Instance owns internally."""
    config = {
        "instance_type": "combat", "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": ["DEFEND_IRONCLAD", "STRIKE_IRONCLAD"],
        "exhaust_pile": [], "player_powers": [], "relics": [], "potions": [{"slot": 0, "potion_id": "LIQUID_MEMORIES"}],
        "seed": 1, "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    inst = CombatInstance("f3", config, worker_count=2)
    try:
        start = inst.start_instance_response()
        dp0 = start["decision_point_id"]
        legal = start["masked_emulator_dto"]["legal_actions"]
        potion_id = next(a["action_id"] for a in legal if a.get("action_type") == "potion")
        em = inst.emulate_action(parent_branch_id="root", branch_id="b1", rng_id=1, decision_point_id=dp0, action_id=potion_id, simulation_options=None)
        assert em["status"] == "completed", em

        registry_size_before = len(inst._lease_registry._leases)  # noqa: SLF001
        assert registry_size_before >= 1, "a Pending boundary must have established a real Lease"

        inst.cancel_branches(["b1"])
        registry_size_after = len(inst._lease_registry._leases)  # noqa: SLF001
        assert registry_size_after == 0, "Cancelling the Lease-holding Branch must invalidate its Lease"
    finally:
        inst.close()


def test_close_instance_is_idempotent_via_server():
    server = RLApiServer()
    try:
        start_resp = server.handle_request(
            {"schema_version": SCHEMA_VERSION, "request_id": "r1", "operation": "start_instance", "instance_config": _combat_config()}
        )
        assert start_resp["status"] == "completed"
        instance_id = start_resp["instance_id"]

        close1 = server.handle_request({"schema_version": SCHEMA_VERSION, "request_id": "r2", "operation": "close_instance", "instance_id": instance_id})
        assert close1["status"] == "completed"

        # Second close of the same (now-gone) instance_id must be a clean rejection, not
        # a crash or hang - the server itself must remain fully usable afterward.
        close2 = server.handle_request({"schema_version": SCHEMA_VERSION, "request_id": "r3", "operation": "close_instance", "instance_id": instance_id})
        assert close2["status"] == "rejected", close2

        start_resp2 = server.handle_request(
            {"schema_version": SCHEMA_VERSION, "request_id": "r4", "operation": "start_instance", "instance_config": _combat_config()}
        )
        assert start_resp2["status"] == "completed", "the server must remain usable for other instances after a double-close"
    finally:
        server.close_all()


def test_close_instance_releases_all_worker_processes():
    inst = CombatInstance("f4", _combat_config(), worker_count=2)
    worker_ids = list(inst._pool.worker_ids)  # noqa: SLF001
    inst.close()
    for worker_id in worker_ids:
        assert not inst._pool.is_worker_alive(worker_id), f"worker {worker_id} must be terminated after close()"  # noqa: SLF001


def test_training_disconnect_without_explicit_close_instance_still_cleans_up():
    """Models a Training-side disconnect: the OS process is torn down directly, without
    ever sending `close_instance` first. `_rl_runtime_process_main`'s `finally:
    server.close_all()` must still run and release every resource - proc.close() itself
    must complete promptly (no hang) even though an instance was left open."""
    proc = RLApiServerProcess(request_timeout_s=60.0)
    start_resp = proc.call(
        {"schema_version": SCHEMA_VERSION, "request_id": "r1", "operation": "start_instance", "instance_config": _combat_config()}
    )
    assert start_resp["status"] == "completed", start_resp
    # No close_instance call here - simulate an abrupt Training-side disconnect.
    proc.close()
    assert not proc.is_alive(), "the RL Runtime process must terminate cleanly even after an abrupt disconnect"


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
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(_run_all())
