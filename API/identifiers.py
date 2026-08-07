"""Identifier lifecycle management for the RL-Training Communication contract v0.5.

Three independent per-instance ledgers, matching contract §3:

* `RequestLedger` - `request_id` dedup/conflict ("同一要求の再送で処理を二重実行しない" /
  "同一IDで内容が異なる場合はrejected").
* `BranchIdRegistry` - `branch_id` uniqueness for the lifetime of the instance, even
  across Cancel/Release ("Cancel／Release後も再利用禁止").
* `DecisionPointRegistry` - per-Branch `decision_point_id` issuance/staleness
  ("古いIDによる要求はrejected").

None of these know anything about Combat/Whole Run - they operate purely on opaque
strings, so both `instance_combat.py` and `instance_whole_run.py` share them unchanged.
"""

from __future__ import annotations

import hashlib
import itertools
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Optional

from API.dto import ROOT_BRANCH_ID
from API.validation import RequestRejected


def _payload_digest(payload: dict) -> str:
    # request_id itself is excluded - it's the ledger KEY, not part of the content being
    # compared for "same id, same content" vs "same id, different content".
    body = {k: v for k, v in payload.items() if k != "request_id"}
    text = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass
class RequestLedger:
    """Remember request digests and completed responses for safe replay.

    Per-instance ledgers default to an unbounded lifetime because request-id uniqueness is
    part of the instance contract. Server-wide replay caches may opt into
    ``max_completed_entries`` so old completed responses do not retain large DTOs for the
    lifetime of a long-running server. In-flight entries are never evicted.
    """

    max_completed_entries: int | None = None
    _entries: OrderedDict[str, tuple[str, Optional[dict]]] = field(
        default_factory=OrderedDict
    )

    def __post_init__(self) -> None:
        if self.max_completed_entries is not None and self.max_completed_entries <= 0:
            raise ValueError("max_completed_entries must be positive when provided")

    def begin(self, payload: dict) -> Optional[dict]:
        """Call before executing a request.

        Returns a cached RESPONSE to replay verbatim if ``request_id`` was already
        completed with identical content; returns ``None`` for a genuinely new request
        or a matching request still in flight. Raises ``RequestRejected`` when the same
        request id is reused with different content.
        """
        request_id = payload["request_id"]
        found, cached_response = self._lookup(payload)
        if found:
            return cached_response

        self._entries[request_id] = (_payload_digest(payload), None)
        return None

    def replay(self, payload: dict) -> Optional[dict]:
        """Return an existing cached response without inserting a new ledger entry."""
        found, cached_response = self._lookup(payload)
        return cached_response if found else None

    def complete(self, payload: dict, response: dict) -> None:
        request_id = payload["request_id"]
        digest = _payload_digest(payload)
        self._entries[request_id] = (digest, response)
        self._entries.move_to_end(request_id)
        self._evict_completed_entries()

    def __len__(self) -> int:
        return len(self._entries)

    def _lookup(self, payload: dict) -> tuple[bool, Optional[dict]]:
        request_id = payload["request_id"]
        digest = _payload_digest(payload)
        existing = self._entries.get(request_id)
        if existing is None:
            return False, None
        existing_digest, cached_response = existing
        if existing_digest != digest:
            raise RequestRejected(
                f"request_id {request_id!r} reused with different content"
            )
        if cached_response is not None:
            self._entries.move_to_end(request_id)
        return True, cached_response

    def _evict_completed_entries(self) -> None:
        limit = self.max_completed_entries
        if limit is None:
            return

        while len(self._entries) > limit:
            for request_id, (_, cached_response) in self._entries.items():
                if cached_response is not None:
                    del self._entries[request_id]
                    break
            else:
                # Every retained entry is still in flight. Do not make a duplicate
                # execution possible merely to satisfy the memory bound.
                break


@dataclass
class BranchIdRegistry:
    _ever_used: set = field(default_factory=lambda: {ROOT_BRANCH_ID})

    def register(self, branch_id: str) -> None:
        if branch_id in self._ever_used:
            raise RequestRejected(f"branch_id {branch_id!r} already used (branch IDs are never reusable)")
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
            raise RequestRejected(f"branch_id {branch_id!r} has no active decision_point_id")
        if current != given_decision_point_id:
            raise RequestRejected(
                f"stale decision_point_id for branch_id {branch_id!r}: given={given_decision_point_id!r} "
                f"current={current!r}"
            )

    def clear(self, branch_id: str) -> None:
        self._current.pop(branch_id, None)


@dataclass
class RngHypothesisTable:
    """Maps `(parent_branch_id, decision_point_id, rng_id)` -> a stable 0-based
    hypothesis index (contract §3 "Hypothesisの識別単位"). First-seen rng_id for a given
    (parent, decision_point) key gets index 0, second-seen gets index 1, etc. - a purely
    positional mapping, so "same rng_id => same Hypothesis" and "different rng_id =>
    different Hypothesis" both hold trivially without needing rng_id values themselves to
    be contiguous or bounded ahead of time.
    """

    _index_by_key: dict[tuple, int] = field(default_factory=dict)
    _next_index_by_parent_decision: dict[tuple, int] = field(default_factory=dict)

    def hypothesis_index_for(self, parent_branch_id: str, decision_point_id: str, rng_id: int) -> int:
        key = (parent_branch_id, decision_point_id, rng_id)
        if key in self._index_by_key:
            return self._index_by_key[key]
        parent_decision_key = (parent_branch_id, decision_point_id)
        next_index = self._next_index_by_parent_decision.get(parent_decision_key, 0)
        self._index_by_key[key] = next_index
        self._next_index_by_parent_decision[parent_decision_key] = next_index + 1
        return next_index
