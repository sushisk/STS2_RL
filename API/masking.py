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

Applies identically to Combat and Whole Run state dicts (contract: "CombatとWhole Runで
同じ公開規則を使用してください") - callers never need a domain-specific mask function.
"""

from __future__ import annotations

import copy
from collections import Counter
from typing import Any

from API.dto import DTO_VERSION, MASK_VERSION

# Any dict key whose lowercased name CONTAINS one of these substrings is removed
# entirely, anywhere in the tree. Deliberately broad/conservative (Part A: "不必要な
# RL独自DTOへのコピーは避けるが、Hidden Information/RL内部情報は必ず除去する").
_FORBIDDEN_KEY_SUBSTRINGS: tuple[str, ...] = (
    "snapshot",
    "savestate",
    "save_state",
    "replay",
    "lease",
    "worker",
    "pid",
    "generation",
    "combatsessionid",
    "combat_session_id",
    "sessionid",
    "session_id",
    "context_id",
    "contextid",
    "rngstate",
    "rng_state",
    "runrng",
    "run_rng",
    "seed",
    "hypothesis",
    "shuffle",
    "cursor",
    "eventqueue",
    "event_queue",
    "encounterqueue",
    "encounter_queue",
    "futureevents",
    "future_events",
    "upcomingencounters",
    "upcoming_encounters",
    "bossqueue",
    "boss_queue",
    "ancientqueue",
    "ancient_queue",
    "unknown_fields",
)

# Piles that must be reduced from an ordered list-of-cards to a card_id -> count
# Multiset. Matched by exact key name (case-sensitive - these come from
# `emulator_bridge.py`/`run_emulator_bridge.py`'s established camelCase dict shape).
_MULTISET_PILE_KEYS = frozenset({"drawPile", "discardPile", "exhaustPile"})

# Deleted outright (mask version 1.0 rule), not reduced to a Multiset.
_DELETE_KEYS = frozenset({"playPile"})

# Emulator-specific Combat Reward (contract: "Emulator固有Combat Rewardを除去") - the
# Combat-only 1.0/-1.0 terminal reward signal flagged as a design question in the Part A
# audit §4; excluded from the public DTO until Training explicitly asks for its own
# reward shaping to live in this field.
_DELETE_KEYS |= {"reward"}

# Metrics/Extras/Info are allowlist-controlled (contract: "allowlist外のMetrics/Extras/
# Infoを除去"). No keys are approved yet (Part A §4: unverified free-form content) - so
# today's allowlist is empty, meaning these three fields are stripped to `{}` wherever
# they appear. Extend this set only once Training/Emulator confirm specific sub-keys are
# safe.
_ALLOWLISTED_METRICS_EXTRAS_INFO_KEYS: frozenset[str] = frozenset()
_METRICS_EXTRAS_INFO_KEY_NAMES = frozenset({"metrics", "extras", "info"})


def _card_id_of(card_entry: Any) -> "str | None":
    if isinstance(card_entry, dict):
        return card_entry.get("id") or card_entry.get("cardId") or card_entry.get("card_id")
    return None


def _multiset_of(pile: Any) -> dict:
    if not isinstance(pile, list):
        return {}
    counts: Counter = Counter()
    for entry in pile:
        card_id = _card_id_of(entry)
        if card_id is not None:
            counts[card_id] += 1
    return dict(sorted(counts.items()))


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
            if key.lower() in _METRICS_EXTRAS_INFO_KEY_NAMES and isinstance(value, dict):
                scrubbed[key] = {k: _scrub(v) for k, v in value.items() if k in _ALLOWLISTED_METRICS_EXTRAS_INFO_KEYS}
                continue
            scrubbed[key] = _scrub(value)
        # Recursively re-apply the same rule to Transition.FinalObservation
        # (contract: "Transition.FinalObservationにも再帰的に同じマスクを適用") - by the
        # time we get here `transition` has already been scrubbed structurally above;
        # this just ensures `final_observation`'s own nested content went through
        # exactly the same rules as a top-level Observation would (it already did, since
        # `_scrub` is already fully recursive - this branch exists only as a documented,
        # explicitly-tested guarantee, not extra logic).
        return scrubbed
    if isinstance(node, list):
        return [_scrub(item) for item in node]
    return node


def mask_public_fragment(value: Any) -> Any:
    """Return a masked deep copy of a public DTO fragment.

    Use this for response metadata such as ``transition`` that is attached outside the
    primary Emulator state passed to ``build_masked_emulator_dto``. Keeping the copy and
    recursive scrub in one helper prevents callers from accidentally appending an
    unmasked nested fragment after the main DTO has already been built.
    """
    return _scrub(copy.deepcopy(value))


def build_masked_emulator_dto(raw_state: dict, *, extra: "dict | None" = None) -> dict:
    """Return a masked copy of `raw_state` (a Combat `engine_state`/Whole Run
    `Observation.State`-shaped dict, or a full StepResult-shaped dict containing one)
    plus `dto_version`/`mask_version` stamps. `raw_state` is never mutated.

    Terminal observations have no legal continuation. Combat terminal payloads always
    publish an explicit empty ``legal_actions`` list. Whole Run terminal Branch shortcuts
    may omit the field, but whenever ``legal_actions`` is present at ``run_terminal`` it
    is normalized to an empty list so stale cached actions can never cross the wire.
    Combat ``terminal`` and Whole Run ``run_terminal`` are mutually exclusive markers.
    """
    masked = mask_public_fragment(raw_state)
    if extra:
        masked.update(mask_public_fragment(extra))
    combat_terminal = masked.get("terminal") is True
    run_terminal = masked.get("run_terminal") is True
    if combat_terminal and run_terminal:
        raise RuntimeError(
            "masked_emulator_dto cannot be both terminal and run_terminal"
        )
    if combat_terminal:
        masked["legal_actions"] = []
    elif run_terminal and "legal_actions" in masked:
        masked["legal_actions"] = []
    masked["dto_version"] = DTO_VERSION
    masked["mask_version"] = MASK_VERSION
    return masked


def mask_legal_actions(legal_actions: list[dict]) -> list[dict]:
    """Legal Actions are published as-is (contract §3: "そのまま公開する情報" includes
    Legal Actions) except that `action_id` is re-stamped as an opaque string (contract
    §3 `action_id`: "TrainingはAction内容を再構築せず、IDをそのまま返す" - a string is a
    safer opaque-token representation than a bare int that invites arithmetic/ordering
    assumptions).
    """
    result = []
    for action in legal_actions:
        scrubbed = mask_public_fragment(action)
        if "action_id" in scrubbed:
            scrubbed["action_id"] = str(scrubbed["action_id"])
        result.append(scrubbed)
    return result
