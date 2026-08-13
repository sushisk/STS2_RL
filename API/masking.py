"""`masked_emulator_dto` builder for the RL-Training Communication contract v0.5.

Implements the rules from `docs/contracts/rl_training_dto_documentation_v0_5.md` §3
"masked_emulator_dto", built on the classification already established by the Part A
audit (`Outputs/reports/rl_dto_exposure_audit_20260803.md`).

Design: a single recursive, NAME-DRIVEN scrub (`_scrub`) walks the entire dict/list tree
and, at every level, removes any key whose name matches a forbidden pattern, converts the
three Pile keys to Multiset counts, deletes `playPile` outright, and applies an allowlist
to `Metrics`/`Extras`/`Info`-named keys wherever they occur - rather than an
enumerate-every-safe-field allowlist for the whole tree. This is deliberately
conservative (over-strip, never under-strip) and, critically, makes it possible to write
one recursive "no forbidden key anywhere" audit (see `API/tests/test_mask_audit.py`)
that exercises the exact same name list this builder uses, so builder and auditor can
never silently drift apart.

`pendingChoice` is the exception to the broad name-driven rule: choice-card semantic and
identity fields are security/behavior-sensitive, so they are normalized through an
explicit public allowlist before the recursive scrub continues.

Applies identically to Combat and Whole Run state dicts (contract: "CombatとWhole Runで
同じ公開規則を使用してください") - callers never need a domain-specific mask function.
"""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from API.choice_card_semantics import normalize_pending_choice
from API.dto import DTO_VERSION, MASK_VERSION

_FORBIDDEN_KEY_SUBSTRINGS: tuple[str, ...] = (
    "snapshot", "savestate", "save_state", "replay", "lease", "worker", "pid",
    "generation", "combatsessionid", "combat_session_id", "sessionid", "session_id",
    "context_id", "contextid", "rngstate", "rng_state", "runrng", "run_rng", "seed",
    "hypothesis", "shuffle", "cursor", "eventqueue", "event_queue", "encounterqueue",
    "encounter_queue", "futureevents", "future_events", "upcomingencounters",
    "upcoming_encounters", "bossqueue", "boss_queue", "ancientqueue", "ancient_queue",
    "unknown_fields",
)

_MULTISET_PILE_KEYS = frozenset({"drawPile", "discardPile", "exhaustPile"})
_DELETE_KEYS = frozenset({"playPile"})
_DELETE_KEYS |= {"reward"}
_ALLOWLISTED_METRICS_EXTRAS_INFO_KEYS: frozenset[str] = frozenset()
_METRICS_EXTRAS_INFO_KEY_NAMES = frozenset({"metrics", "extras", "info"})


def _card_identity_key(entry: dict) -> tuple:
    """All public fields emitted for one card, excluding only the multiset count itself.

    Pile masking is allowed to remove order, not observable card state. Including every
    emitted public field here prevents a representative record from silently swallowing
    differences in live/derived fields such as ``cost``.
    """
    enchantment = entry.get("enchantment") or {}
    return (
        entry.get("id"),
        entry.get("type"),
        entry.get("rarity"),
        entry.get("cost"),
        entry.get("targetType"),
        bool(entry.get("upgraded", False)),
        int(entry.get("upgradeLevel", 0) or 0),
        entry.get("tinkerTimeType"),
        entry.get("tinkerTimeRider"),
        enchantment.get("id"),
        enchantment.get("amount"),
        enchantment.get("status"),
    )


def _sort_key(identity_key: tuple) -> tuple:
    """Stable heterogeneous sort that never compares unlike Python scalar types."""
    return tuple(f"{type(part).__name__}:{part!r}" for part in identity_key)


def _public_enchantment(enchantment: Any) -> dict | None:
    """Return only the public enchantment fields allowed by the masked DTO contract."""
    if not isinstance(enchantment, dict):
        return None
    return {
        "id": enchantment.get("id"),
        "amount": enchantment.get("amount"),
        "status": enchantment.get("status"),
    }


def _multiset_of(pile: Any) -> list:
    """Reduce a pile to per-distinct-public-card-state counts, discarding order only."""
    if not isinstance(pile, list):
        return []
    counts: Counter = Counter()
    representatives: dict[tuple, dict] = {}
    for entry in pile:
        if not isinstance(entry, dict) or entry.get("id") is None:
            continue
        key = _card_identity_key(entry)
        counts[key] += 1
        representatives.setdefault(key, entry)
    result = []
    for key in sorted(counts, key=_sort_key):
        entry = representatives[key]
        result.append(
            {
                "id": entry.get("id"),
                "type": entry.get("type"),
                "rarity": entry.get("rarity"),
                "cost": entry.get("cost"),
                "targetType": entry.get("targetType"),
                "upgraded": bool(entry.get("upgraded", False)),
                "upgradeLevel": int(entry.get("upgradeLevel", 0) or 0),
                "tinkerTimeType": entry.get("tinkerTimeType"),
                "tinkerTimeRider": entry.get("tinkerTimeRider"),
                "enchantment": _public_enchantment(entry.get("enchantment")),
                "count": counts[key],
            }
        )
    return result


def _is_forbidden_key(key: str) -> bool:
    lowered = key.lower()
    return any(pattern in lowered for pattern in _FORBIDDEN_KEY_SUBSTRINGS)


def _scrub(node: Any) -> Any:
    if isinstance(node, dict):
        scrubbed: dict = {}
        for key, value in node.items():
            if not isinstance(key, str):
                continue
            if _is_forbidden_key(key):
                continue
            if key in _DELETE_KEYS:
                continue
            if key in _MULTISET_PILE_KEYS:
                scrubbed[key] = _multiset_of(value)
                continue
            if key == "pendingChoice":
                scrubbed[key] = _scrub(normalize_pending_choice(value))
                continue
            if key.lower() in _METRICS_EXTRAS_INFO_KEY_NAMES and isinstance(value, dict):
                scrubbed[key] = {k: _scrub(v) for k, v in value.items() if k in _ALLOWLISTED_METRICS_EXTRAS_INFO_KEYS}
                continue
            scrubbed[key] = _scrub(value)
        return scrubbed
    if isinstance(node, list):
        return [_scrub(item) for item in node]
    return node


def mask_public_fragment(value: Any) -> Any:
    """Return a masked deep copy of a public DTO fragment."""
    return _scrub(copy.deepcopy(value))


def build_masked_emulator_dto(raw_state: dict, *, extra: "dict | None" = None) -> dict:
    """Return a masked copy of raw Emulator state plus DTO/mask version stamps."""
    masked = mask_public_fragment(raw_state)
    if extra:
        masked.update(mask_public_fragment(extra))
    combat_terminal = masked.get("terminal") is True
    run_terminal = masked.get("run_terminal") is True
    if combat_terminal and run_terminal:
        raise RuntimeError("masked_emulator_dto cannot be both terminal and run_terminal")
    if combat_terminal:
        masked["legal_actions"] = []
    elif run_terminal and "legal_actions" in masked:
        masked["legal_actions"] = []
    masked["dto_version"] = DTO_VERSION
    masked["mask_version"] = MASK_VERSION
    return masked


def mask_legal_actions(legal_actions: list[dict]) -> list[dict]:
    """Publish legal actions after the same recursive safety scrub."""
    result = []
    for action in legal_actions:
        scrubbed = mask_public_fragment(action)
        if "action_id" in scrubbed:
            scrubbed["action_id"] = str(scrubbed["action_id"])
        result.append(scrubbed)
    return result
