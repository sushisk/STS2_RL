"""Identifier lifecycle management for RL/Training DTO v0.6.

``SessionLedger`` owns transport request sequencing. Branch, decision-point, and RNG
hypothesis registries remain per-instance gameplay identifiers and are independent of the
transport redesign.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import dataclass, field
from typing import Optional

from API.dto import (
    FAULT_SESSION_SEQUENCE_CONFLICT,
    FAULT_SESSION_SEQUENCE_GAP,
    ROOT_BRANCH_ID,
)
from API.validation import RequestRejected


def _payload_digest(payload: dict) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass
class SessionLedger:
    """Retain only the latest executable request for one logical client session."""

    last_seq: int = 0
    last_digest: str | None = None
    last_response: Optional[dict] = None
    active_instance_id: str | None = None

    def begin(self, payload: dict) -> Optional[dict]:
        seq = payload["request_seq"]
        digest = _payload_digest(payload)

        if seq == self.last_seq:
            if digest != self.last_digest:
                raise RequestRejected(
                    f"request_seq {seq} reused with different content",
                    fault_kind=FAULT_SESSION_SEQUENCE_CONFLICT,
                )
            if self.last_response is None:
                raise RequestRejected(
                    f"request_seq {seq} is already in flight",
                    fault_kind=FAULT_SESSION_SEQUENCE_CONFLICT,
                )
            return self.last_response

        expected = self.last_seq + 1
        if seq != expected:
            raise RequestRejected(
                f"request_seq must be {expected}, got {seq}",
                fault_kind=FAULT_SESSION_SEQUENCE_GAP,
            )

        self.last_seq = seq
        self.last_digest = digest
        self.last_response = None
        return None

    def complete(self, response: dict) -> None:
        self.last_response = response


@dataclass
class BranchIdRegistry:
    _ever_used: set = field(default_factory=lambda: {ROOT_BRANCH_ID})

    def register(self, branch_id: str) -> None:
        if branch_id in self._ever_used:
            raise RequestRejected(
                f"branch_id {branch_id!r} already used (branch IDs are never reusable)"
            )
        self._ever_used.add(branch_id)

    def is_known(self, branch_id: str) -> bool:
        return branch_id in self._ever_used


@dataclass
class DecisionPointRegistry:
    _counter: "itertools.count" = field(default_factory=lambda: itertools.count(1))
    _current: dict[str, str] = field(default_factory=dict)

    def issue(self, branch_id: str) -> str:
        decision_point_id = f"d-{branch_id}-{next(self._counter):06d}"
        self._current[branch_id] = decision_point_id
        return decision_point_id

    def current(self, branch_id: str) -> Optional[str]:
        return self._current.get(branch_id)

    def validate(self, branch_id: str, given_decision_point_id: str) -> None:
        current = self._current.get(branch_id)
        if current is None:
            raise RequestRejected(
                f"branch_id {branch_id!r} has no active decision_point_id"
            )
        if current != given_decision_point_id:
            raise RequestRejected(
                f"stale decision_point_id for branch_id {branch_id!r}: "
                f"given={given_decision_point_id!r} current={current!r}"
            )

    def clear(self, branch_id: str) -> None:
        self._current.pop(branch_id, None)


@dataclass
class RngHypothesisTable:
    """Map `(parent_branch_id, decision_point_id, rng_id)` to a stable index."""

    _index_by_key: dict[tuple, int] = field(default_factory=dict)
    _next_index_by_parent_decision: dict[tuple, int] = field(default_factory=dict)

    def hypothesis_index_for(
        self,
        parent_branch_id: str,
        decision_point_id: str,
        rng_id: int,
    ) -> int:
        key = (parent_branch_id, decision_point_id, rng_id)
        if key in self._index_by_key:
            return self._index_by_key[key]
        parent_decision_key = (parent_branch_id, decision_point_id)
        next_index = self._next_index_by_parent_decision.get(parent_decision_key, 0)
        self._index_by_key[key] = next_index
        self._next_index_by_parent_decision[parent_decision_key] = next_index + 1
        return next_index

    def snapshot(self) -> int:
        """Return an O(1) rollback marker for allocations made after this point.

        ``dict`` preserves insertion order, and ``popitem()`` removes the newest entry.
        That lets failure rollback remove only keys allocated by the current batch instead
        of copying the complete hypothesis table on every successful batch.
        """
        return len(self._index_by_key)

    def restore(self, snapshot: int) -> None:
        """Rollback allocations made after ``snapshot`` in O(delta) time.

        Successful batches pay no copy/commit cost. On failure, only keys added since the
        marker are removed, and each affected parent/decision counter is rewound to the
        index of the removed allocation. For a valid marker this path is deliberately
        failure-free so rollback cannot create a second coordinator cleanup failure.
        """
        if not isinstance(snapshot, int) or isinstance(snapshot, bool):
            raise TypeError("RNG rollback snapshot must be an integer marker")
        if snapshot < 0 or snapshot > len(self._index_by_key):
            raise ValueError("RNG rollback snapshot is outside the current allocation history")

        while len(self._index_by_key) > snapshot:
            key, index = self._index_by_key.popitem()
            parent_decision_key = (key[0], key[1])
            if index == 0:
                self._next_index_by_parent_decision.pop(parent_decision_key, None)
            else:
                self._next_index_by_parent_decision[parent_decision_key] = index
