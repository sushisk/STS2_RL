"""Pure-Python Whole Run event RNG hypothesis derivation and memoization.

This module intentionally stays independent from CLR/emulator objects. It operates only
on the plain dict shape returned by `WholeRunSession.get_event_rng_state()`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

_STREAM_KEYS = (
    "event_rng",
    "player_rewards_rng",
    "player_shops_rng",
    "player_transformations_rng",
)

_COUNTER_BITS = 32
_COUNTER_SIGN_BIT = 1 << 31
_COUNTER_MOD = 1 << 32
_U64_MASK = (1 << 64) - 1


def _canonical_stream_seed(base_state: dict, stream_name: str, rng_id: int) -> bytes:
    stream = base_state[stream_name]
    parts = (
        str(base_state["event_id"]),
        stream_name,
        str(rng_id),
        str(int(stream["counter"])),
        str(int(stream["s0"])),
        str(int(stream["s1"])),
        str(int(stream["s2"])),
        str(int(stream["s3"])),
    )
    return "|".join(parts).encode("utf-8")


def _hash_word(seed: bytes, word_name: str) -> bytes:
    return hashlib.sha256(seed + b"|word=" + word_name.encode("utf-8")).digest()


def _counter_from_digest(digest: bytes) -> int:
    value = int.from_bytes(digest[:4], "big", signed=False)
    return value - _COUNTER_MOD if value >= _COUNTER_SIGN_BIT else value


def _u64_from_digest(digest: bytes) -> int:
    return int.from_bytes(digest[:8], "big", signed=False) & _U64_MASK


def derive_event_rng_hypothesis(base_state: dict, rng_id: int) -> dict:
    """Derive a new Whole Run event RNG hypothesis from a base event RNG snapshot."""
    if rng_id <= 0:
        raise ValueError("rng_id must be a positive int")

    derived: dict[str, Any] = {"event_id": base_state["event_id"]}
    for stream_name in _STREAM_KEYS:
        seed = _canonical_stream_seed(base_state, stream_name, rng_id)
        counter = _counter_from_digest(_hash_word(seed, "counter"))
        s0 = _u64_from_digest(_hash_word(seed, "s0"))
        s1 = _u64_from_digest(_hash_word(seed, "s1"))
        s2 = _u64_from_digest(_hash_word(seed, "s2"))
        s3 = _u64_from_digest(_hash_word(seed, "s3"))
        if s0 == s1 == s2 == s3 == 0:
            s0 = 1  # Avoid the all-zero xoshiro256** state, which is degenerate.
        derived[stream_name] = {
            "counter": counter,
            "s0": s0,
            "s1": s1,
            "s2": s2,
            "s3": s3,
        }
    return derived


@dataclass
class _HypothesisEntry:
    state: dict
    generation: int


@dataclass
class EventRngHypothesisRegistry:
    _entries: dict[tuple, _HypothesisEntry] = field(default_factory=dict)
    _branch_refs_by_key: dict[tuple, set[str]] = field(default_factory=dict)
    _generation_counts: dict[tuple, int] = field(default_factory=dict)

    def prepare_state(self, key: tuple, base_state: dict, rng_id: int) -> dict:
        """Return the state a Branch would use without mutating registry ownership."""
        entry = self._entries.get(key)
        if entry is not None:
            return entry.state
        return derive_event_rng_hypothesis(base_state, rng_id)

    def _ensure_entry(self, key: tuple, state: dict) -> _HypothesisEntry:
        entry = self._entries.get(key)
        if entry is not None:
            if entry.state != state:
                raise RuntimeError("prepared Event RNG state no longer matches live hypothesis")
            return entry
        generation = self._generation_counts.get(key, 0) + 1
        self._generation_counts[key] = generation
        entry = _HypothesisEntry(state=state, generation=generation)
        self._entries[key] = entry
        return entry

    def commit_branch(self, key: tuple, state: dict, branch_id: str) -> None:
        """Atomically publish a prepared hypothesis reference for one Branch."""
        self._ensure_entry(key, state)
        self.register_branch(key, branch_id)

    def get_or_create(self, key: tuple, base_state: dict, rng_id: int) -> dict:
        # Kept for existing callers/tests. New Branch preparation should prefer the
        # mutation-free prepare_state() + commit_branch() transaction.
        state = self.prepare_state(key, base_state, rng_id)
        return self._ensure_entry(key, state).state

    def register_branch(self, key: tuple, branch_id: str) -> None:
        refs = self._branch_refs_by_key.setdefault(key, set())
        refs.add(branch_id)

    def release_branch(self, key: tuple, branch_id: str) -> None:
        refs = self._branch_refs_by_key.get(key)
        if refs is not None:
            refs.discard(branch_id)
            if not refs:
                self._branch_refs_by_key.pop(key, None)
                self._entries.pop(key, None)
            return
        if key in self._entries and not self._branch_refs_by_key.get(key):
            self._entries.pop(key, None)

    def release_all_for_decision(self, parent_branch_id: str, decision_point_id: str) -> None:
        keys = [key for key in self._entries if key[0] == parent_branch_id and key[1] == decision_point_id]
        keys.extend(key for key in self._branch_refs_by_key if key[0] == parent_branch_id and key[1] == decision_point_id)
        for key in set(keys):
            self._entries.pop(key, None)
            self._branch_refs_by_key.pop(key, None)

    def release_all(self) -> None:
        self._entries.clear()
        self._branch_refs_by_key.clear()

    def is_live(self, key: tuple) -> bool:
        return key in self._entries

    def generation_of(self, key: tuple) -> int:
        return self._generation_counts.get(key, 0)
