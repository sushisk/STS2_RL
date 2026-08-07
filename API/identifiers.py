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
import threading
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
class _RequestEntry:
    digest: str
    response: Optional[dict] = None
    done: threading.Event = field(default_factory=threading.Event)


@dataclass
class RequestLedger:
    _entries: dict[str, _RequestEntry] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def begin(self, payload: dict) -> Optional[dict]:
        """Claim a request or replay an identical completed request.

        When an identical request is already in flight, callers wait for that execution
        to complete and then receive the cached response. This makes a retry arriving on
        another TCP connection safe: the retry never executes the operation a second time.
        If the owning execution aborts, one waiter claims a fresh execution and the others
        continue waiting on that new owner.
        """
        request_id = payload["request_id"]
        digest = _payload_digest(payload)

        while True:
            with self._lock:
                existing = self._entries.get(request_id)
                if existing is None:
                    self._entries[request_id] = _RequestEntry(digest=digest)
                    return None
                if existing.digest != digest:
                    raise RequestRejected(
                        f"request_id {request_id!r} reused with different content"
                    )
                if existing.done.is_set():
                    return existing.response
                event = existing.done

            event.wait()

    def lookup_completed(self, payload: dict) -> Optional[dict]:
        """Return an identical completed response without claiming a new request."""
        request_id = payload["request_id"]
        digest = _payload_digest(payload)

        while True:
            with self._lock:
                existing = self._entries.get(request_id)
                if existing is None:
                    return None
                if existing.digest != digest:
                    raise RequestRejected(
                        f"request_id {request_id!r} reused with different content"
                    )
                if existing.done.is_set():
                    return existing.response
                event = existing.done

            event.wait()

    def complete(self, payload: dict, response: dict) -> None:
        request_id = payload["request_id"]
        digest = _payload_digest(payload)
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None:
                entry = _RequestEntry(digest=digest)
                self._entries[request_id] = entry
            elif entry.digest != digest:
                raise RequestRejected(
                    f"request_id {request_id!r} reused with different content"
                )
            entry.response = response
            entry.done.set()

    def abort(self, payload: dict) -> None:
        """Release an in-flight claim when execution failed before a response existed."""
        request_id = payload["request_id"]
        digest = _payload_digest(payload)
        with self._lock:
            entry = self._entries.get(request_id)
            if entry is None or entry.digest != digest or entry.done.is_set():
                return
            del self._entries[request_id]
            entry.done.set()


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
