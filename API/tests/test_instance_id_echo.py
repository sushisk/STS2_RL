"""Every Response for an Instance対象Request must echo the same `instance_id` as its
Request (contract §2.3 "Instance対象RequestのResponseは、同じinstance_idを必須とする").

This applies uniformly to `completed`/`partial` Responses for every Operation, AND to
`rejected`, `faulted`, and wire-level timeout Responses - Training must be able to
verify Request/Response Instance affinity for every outcome, not just the happy path.

Covers the two places this is actually assembled:
- `API/server.py` `_wrap()` (normal Responses) / `_rejected()` (rejected Responses).
- `API/api_runtime.py` `_rl_runtime_process_main`'s exception handler and
  `RLApiServerProcess.call()`'s `queue.Empty` (timeout) handler.

Native assertion runner, no pytest dependency.
"""

from __future__ import annotations

import itertools
import queue
import sys
import traceback
import uuid
from pathlib import Path
from unittest import mock

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.api_runtime import RLApiServerProcess  # noqa: E402
from API.dto import SCHEMA_VERSION  # noqa: E402
from API.server import RLApiServer  # noqa: E402


def _combat_config():
    return {
        "instance_type": "combat", "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1, "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _make_req():
    """Returns a `req(label, operation, instance_id=None, **fields)` builder bound to one
    fresh `client_session_id` and its own monotonically increasing `request_seq` - both
    required fields the v0.7 session model validates on every Request, which the
    original `_req()` never set. `label` is kept only for call-site readability; the wire
    `request_id` must be exactly `f"{client_session_id}:{request_seq}"` for this session/
    sequence (`ApiContract._new_request`'s own convention), not an arbitrary string. One
    call per test (a fresh session id keeps tests from ever colliding on sequence state,
    even though each test's own `RLApiServer()` is already independent)."""
    client_session_id = str(uuid.uuid4())
    seq = itertools.count(1)

    def req(label: str, operation: str, instance_id: "str | None" = None, **fields) -> dict:
        request_seq = next(seq)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "client_session_id": client_session_id,
            "request_seq": request_seq,
            "request_id": f"{client_session_id}:{request_seq}",
            "operation": operation,
        }
        del label  # readability-only at call sites, not part of the wire payload
        if instance_id is not None:
            payload["instance_id"] = instance_id
        payload.update(fields)
        return payload

    return req


def test_start_instance_response_carries_a_new_instance_id():
    req = _make_req()
    server = RLApiServer()
    try:
        resp = server.handle_request(req("r1", "start_instance", instance_config=_combat_config()))
        assert resp["status"] == "completed", resp
        assert isinstance(resp.get("instance_id"), str) and resp["instance_id"], resp
    finally:
        server.close_all()


def test_every_operation_response_instance_id_matches_request():
    """Drives the full 8-step sequence through `RLApiServer.handle_request` directly
    (bypassing the OS-process transport, matching how `test_fault_lifecycle.py`'s
    `test_close_instance_is_idempotent_via_server` exercises the server) and asserts,
    for EVERY Operation, that `response["instance_id"] == request["instance_id"]`."""
    req = _make_req()
    server = RLApiServer()
    try:
        start = server.handle_request(req("r-start", "start_instance", instance_config=_combat_config()))
        assert start["status"] == "completed", start
        instance_id = start["instance_id"]

        def _assert_echo(request: dict, response: dict) -> None:
            assert response.get("instance_id") == request["instance_id"], (request["operation"], response)

        gd = server.handle_request(req("r-gd", "get_decision", instance_id, branch_id="root"))
        _assert_echo({"operation": "get_decision", "instance_id": instance_id}, gd)
        assert gd["status"] == "completed", gd

        legal = start["masked_emulator_dto"]["legal_actions"]
        defend_id = next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD")
        bash_id = next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "BASH")

        em = server.handle_request(
            req(
                "r-em", "emulate_action", instance_id,
                parent_branch_id="root", branch_id="b1", rng_id=1,
                decision_point_id=start["decision_point_id"], action_id=defend_id,
            )
        )
        assert em["status"] == "completed", em
        _assert_echo({"operation": "emulate_action", "instance_id": instance_id}, em)

        status = server.handle_request(req("r-status", "get_branch_status", instance_id, branch_ids=["b1"]))
        assert status["status"] == "completed", status
        _assert_echo({"operation": "get_branch_status", "instance_id": instance_id}, status)

        cancel = server.handle_request(req("r-cancel", "cancel_branches", instance_id, branch_ids=["b1"]))
        assert cancel["status"] == "completed", cancel
        _assert_echo({"operation": "cancel_branches", "instance_id": instance_id}, cancel)

        release = server.handle_request(req("r-release", "release_branches", instance_id, branch_ids=["b1"]))
        assert release["status"] == "completed", release
        _assert_echo({"operation": "release_branches", "instance_id": instance_id}, release)

        commit = server.handle_request(
            req("r-commit", "commit_action", instance_id, branch_id="root", rng_id=0, decision_point_id=start["decision_point_id"], action_id=bash_id)
        )
        assert commit["status"] == "completed", commit
        _assert_echo({"operation": "commit_action", "instance_id": instance_id}, commit)

        close = server.handle_request(req("r-close", "close_instance", instance_id))
        assert close["status"] == "completed", close
        _assert_echo({"operation": "close_instance", "instance_id": instance_id}, close)
    finally:
        server.close_all()


def test_rejected_response_includes_request_instance_id():
    req = _make_req()
    server = RLApiServer()
    try:
        start = server.handle_request(req("r-start", "start_instance", instance_config=_combat_config()))
        instance_id = start["instance_id"]

        # A stale/unknown branch_id -> RequestRejected inside the Instance -> `rejected`.
        rejected = server.handle_request(req("r-bad-branch", "get_decision", instance_id, branch_id="no-such-branch"))
        assert rejected["status"] == "rejected", rejected
        assert rejected["instance_id"] == instance_id, rejected
        assert rejected.get("error")
    finally:
        server.close_all()


def test_rejected_response_for_unknown_instance_id_still_echoes_it():
    """Even when `instance_id` refers to no live Instance at all (server.py's own
    `unknown instance_id` rejection, before ever reaching an Instance), the Response
    must still echo the (unknown) `instance_id` the Request specified."""
    req = _make_req()
    server = RLApiServer()
    try:
        rejected = server.handle_request(req("r1", "get_decision", "inst-does-not-exist", branch_id="root"))
        assert rejected["status"] == "rejected", rejected
        assert rejected["instance_id"] == "inst-does-not-exist", rejected
    finally:
        server.close_all()


def test_faulted_response_includes_request_instance_id():
    """A Branch Worker timeout (real `BranchWorkerPool`/`BranchManager` timeout, same
    trigger as `test_fault_lifecycle.py::test_worker_timeout_surfaces_as_faulted_and_pool_recovers`)
    surfaces as a `faulted` Response through the full `RLApiServer.handle_request` wire
    layer, and must carry the same `instance_id` as the Request."""
    req = _make_req()
    server = RLApiServer()
    try:
        start = server.handle_request(req("r-start", "start_instance", instance_config=_combat_config()))
        instance_id = start["instance_id"]
        legal = start["masked_emulator_dto"]["legal_actions"]
        defend_id = next(a["action_id"] for a in legal if a.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD")

        timed_out = server.handle_request(
            req(
                "r-timeout", "emulate_action", instance_id,
                parent_branch_id="root", branch_id="b-timeout", rng_id=1,
                decision_point_id=start["decision_point_id"], action_id=defend_id,
                simulation_options={"max_time_ms": 1},
            )
        )
        assert timed_out["status"] == "faulted", timed_out
        assert timed_out["instance_id"] == instance_id, timed_out
    finally:
        server.close_all()


def test_wire_level_timeout_response_includes_request_instance_id():
    """`RLApiServerProcess.call()`'s own `queue.Empty` (RL Runtime process did not
    respond in time) handler in `API/api_runtime.py` - distinct from the Instance-level
    Branch Worker timeout above - must also echo `instance_id`. Deterministic: mocks
    `_out_queue.get` to raise `queue.Empty` immediately rather than relying on a real
    wall-clock timeout."""
    req = _make_req()
    proc = RLApiServerProcess(request_timeout_s=60.0)
    try:
        with mock.patch.object(proc._out_queue, "get", side_effect=queue.Empty):  # noqa: SLF001
            response = proc.call(req("r1", "get_decision", "inst-123", branch_id="root"))
        assert response["status"] == "faulted", response
        assert response["fault_kind"] == "task_timeout", response
        assert response["instance_id"] == "inst-123", response
    finally:
        proc.close()


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
