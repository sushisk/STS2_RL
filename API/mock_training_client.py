"""Mock Training Client (RL担当指示：RL–Training API実装 §7).

No model inference - every Action choice here is either explicitly given by the caller
or resolved by a plain Legal Action index, exactly per the instruction ("Action選択は
テスト用に明示指定またはLegal Action index指定で構いません"). This is a thin
request-builder + `RLApiServerProcess.call()` wrapper: it never talks to the RL Runtime
except through the wire contract (plain dicts), matching how a real Training process
would - and, like any real Training process, it never imports pythonnet/CLR (nothing in
this module does).
"""

from __future__ import annotations

import itertools
import uuid
from typing import Any, Optional

from API.api_runtime import RLApiServerProcess
from API.dto import ROOT_BRANCH_ID, ROOT_RNG_ID, SCHEMA_VERSION


class MockTrainingClient:
    def __init__(self, server_process: Optional[RLApiServerProcess] = None, *, request_timeout_s: float = 60.0) -> None:
        self._owns_process = server_process is None
        self.process = server_process or RLApiServerProcess(request_timeout_s=request_timeout_s)
        self._client_session_id = str(uuid.uuid4())
        self._request_serial = itertools.count(1)
        self.instance_id: Optional[str] = None
        self._branch_serial = itertools.count(1)

    def close(self) -> None:
        if self._owns_process:
            self.process.close()

    def __enter__(self) -> "MockTrainingClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def next_branch_id(self) -> str:
        return f"branch-{next(self._branch_serial):06d}"

    def _call(self, operation: str, *, request_id: Optional[str] = None, **fields: Any) -> dict:
        # request_id must be exactly f"{client_session_id}:{request_seq}" for this
        # session/sequence (ApiContract._new_request's own convention). A deliberately
        # malformed override is rejected before SessionLedger, so it must not consume the
        # client's sequence number; the next normal request must reuse that request_seq.
        request_seq = next(self._request_serial)
        expected_request_id = f"{self._client_session_id}:{request_seq}"
        if request_id and request_id != expected_request_id:
            self._request_serial = itertools.count(request_seq)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "client_session_id": self._client_session_id,
            "request_seq": request_seq,
            "request_id": request_id or expected_request_id,
            "operation": operation,
        }
        if self.instance_id is not None and "instance_id" not in fields:
            payload["instance_id"] = self.instance_id
        payload.update(fields)
        return self.process.call(payload)

    # -- operations --------------------------------------------------------------------

    def start_instance(self, instance_config: dict, *, request_id: Optional[str] = None) -> dict:
        response = self._call("start_instance", request_id=request_id, instance_config=instance_config)
        if response.get("status") == "completed":
            self.instance_id = response["instance_id"]
        return response

    def get_decision(self, branch_id: str = ROOT_BRANCH_ID, *, request_id: Optional[str] = None) -> dict:
        return self._call("get_decision", request_id=request_id, branch_id=branch_id)

    def commit_action(self, decision_point_id: str, action_id: str, *, request_id: Optional[str] = None) -> dict:
        return self._call(
            "commit_action",
            request_id=request_id,
            branch_id=ROOT_BRANCH_ID,
            rng_id=ROOT_RNG_ID,
            decision_point_id=decision_point_id,
            action_id=action_id,
        )

    def emulate_action(
        self,
        *,
        parent_branch_id: str,
        rng_id: int,
        decision_point_id: str,
        action_id: str,
        branch_id: Optional[str] = None,
        simulation_options: Optional[dict] = None,
        request_id: Optional[str] = None,
    ) -> dict:
        branch_id = branch_id or self.next_branch_id()
        return self._call(
            "emulate_action",
            request_id=request_id,
            parent_branch_id=parent_branch_id,
            branch_id=branch_id,
            rng_id=rng_id,
            decision_point_id=decision_point_id,
            action_id=action_id,
            simulation_options=simulation_options,
        )

    def cancel_branches(self, branch_ids: list, *, request_id: Optional[str] = None) -> dict:
        return self._call("cancel_branches", request_id=request_id, branch_ids=branch_ids)

    def release_branches(self, branch_ids: list, *, request_id: Optional[str] = None) -> dict:
        return self._call("release_branches", request_id=request_id, branch_ids=branch_ids)

    def get_branch_status(self, branch_ids: list, *, request_id: Optional[str] = None) -> dict:
        return self._call("get_branch_status", request_id=request_id, branch_ids=branch_ids)

    def close_instance(self, *, request_id: Optional[str] = None) -> dict:
        response = self._call("close_instance", request_id=request_id)
        self.instance_id = None
        return response

    # -- convenience: legal-action-index resolution ------------------------------------

    @staticmethod
    def legal_action_id(decision_response: dict, index: int = 0) -> str:
        return decision_response["masked_emulator_dto"]["legal_actions"][index]["action_id"]


def _demo_combat() -> None:
    """Exercises every §7 scenario against a real Combat instance."""
    config = {
        "instance_type": "combat", "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD", "BASH"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1, "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    with MockTrainingClient() as client:
        start = client.start_instance(config)
        print("start_instance:", start["status"])

        dp = start["decision_point_id"]
        defend_id = client.legal_action_id(start, next(i for i, a in enumerate(start["masked_emulator_dto"]["legal_actions"]) if a.get("parameters", {}).get("cardId") == "DEFEND_IRONCLAD"))
        bash_id = client.legal_action_id(start, next(i for i, a in enumerate(start["masked_emulator_dto"]["legal_actions"]) if a.get("parameters", {}).get("cardId") == "BASH"))

        # root -> multiple Branches (same action, different rng_id; different actions, same rng_id)
        b1 = client.emulate_action(parent_branch_id="root", rng_id=1, decision_point_id=dp, action_id=defend_id)
        b2 = client.emulate_action(parent_branch_id="root", rng_id=2, decision_point_id=dp, action_id=defend_id)
        b3 = client.emulate_action(parent_branch_id="root", rng_id=1, decision_point_id=dp, action_id=bash_id)
        print("root branches:", b1["status"], b2["status"], b3["status"])

        # deep branch: branch off b1's own resulting decision
        deep = client.emulate_action(parent_branch_id=b1["branch_id"], rng_id=1, decision_point_id=b1["decision_point_id"], action_id=client.legal_action_id(b1))
        print("deep branch:", deep["status"])

        ids = [b1["branch_id"], b2["branch_id"], b3["branch_id"], deep["branch_id"]]
        print("branch status:", client.get_branch_status(ids)["branch_statuses"])
        client.cancel_branches([b3["branch_id"]])
        client.release_branches(ids)

        commit = client.commit_action(dp, defend_id)
        print("commit_action:", commit["status"])
        print("get_decision(root):", client.get_decision("root")["status"])
        print("close_instance:", client.close_instance()["status"])


if __name__ == "__main__":
    import multiprocessing

    multiprocessing.freeze_support()
    _demo_combat()
