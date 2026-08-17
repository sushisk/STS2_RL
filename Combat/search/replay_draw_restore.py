"""Infer already-observed draw order from public replay state."""
from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional


ObservableCardKey = tuple[
    str,              # CardId
    str,              # Type
    str,              # Rarity
    int,              # public/effective Cost
    str,              # TargetType
    bool,             # upgraded
    int,              # UpgradeLevel
    Optional[str],    # TinkerTimeType
    Optional[str],    # TinkerTimeRider
    Optional[tuple],  # enchantment signature
]
VisibleDrawConstraint = tuple[int, ObservableCardKey]
VisibleDrawConstraints = tuple[VisibleDrawConstraint, ...]

# P is the multiset sum of persistent cards from whichever ordinary card zones are
# actually public in both the immediate pre/post observations. Missing optional zones
# are ignored rather than treated as evidence or as a proof failure.
_PUBLIC_NON_DRAW_ZONES = ("hand", "discardPile", "exhaustPile", "playPile")


@dataclass(frozen=True)
class ObservableTransferEvidence:
    """Gate-A result before any draw-order interpretation."""

    removed_from_draw: Counter
    option_keys: tuple[ObservableCardKey, ...]


@dataclass(frozen=True)
class VisibleDrawTransitionEvidence:
    """One committed transition's replay-relevant draw evidence.

    ``blocks_later_pinning`` is set when the public DrawPile changed but the transition
    could not be proven as an ordered sequential draw. Later transitions must not
    invent root-relative draw offsets past such a mutation.
    """

    constraints: VisibleDrawConstraints = ()
    blocks_later_pinning: bool = False
    tracking_error: Optional[str] = None


def _enchantment_key(value: object) -> Optional[tuple]:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    return (value.get("id"), value.get("amount"), value.get("status"))


def observable_card_key_from_public(card: object) -> Optional[ObservableCardKey]:
    """Return behavior-relevant state available on public card/choice DTOs.

    optionId, list position, zone and any physical-copy identity are intentionally
    excluded. Missing/invalid required gameplay fields fail closed instead of silently
    collapsing distinct cards.
    """
    if not isinstance(card, dict):
        return None
    card_id = card.get("id")
    card_type = card.get("type")
    rarity = card.get("rarity")
    cost = card.get("cost")
    target_type = card.get("targetType")
    upgrade_level = card.get("upgradeLevel", 0)
    if not isinstance(card_id, str) or not card_id:
        return None
    if not isinstance(card_type, str) or not isinstance(rarity, str) or not isinstance(target_type, str):
        return None
    if isinstance(cost, bool) or not isinstance(cost, int):
        return None
    if isinstance(upgrade_level, bool) or not isinstance(upgrade_level, int):
        return None
    return (
        card_id,
        card_type,
        rarity,
        cost,
        target_type,
        bool(card.get("upgraded", False)),
        upgrade_level,
        card.get("tinkerTimeType"),
        card.get("tinkerTimeRider"),
        _enchantment_key(card.get("enchantment")),
    )


def observable_card_key_from_snapshot(card: object) -> ObservableCardKey:
    """Project a Snapshot card into the public replay-equivalence domain.

    Current ``CardInstanceSnapshot`` does not carry enchantment. Its projection is
    therefore ``None`` for enchantment; an observed enchanted key will not match and
    materialization will fail closed until the Snapshot contract carries equivalent
    gameplay state.
    """
    return (
        str(getattr(card, "CardId", "")),
        str(getattr(card, "Type", "")),
        str(getattr(card, "Rarity", "")),
        int(getattr(card, "Cost", 0)),
        str(getattr(card, "TargetType", "")),
        bool(getattr(card, "IsUpgraded", False)),
        int(getattr(card, "UpgradeLevel", 0)),
        getattr(card, "TinkerTimeType", None),
        getattr(card, "TinkerTimeRider", None),
        None,
    )


def snapshot_card_replay_internal_key(card: object) -> tuple:
    """Detect hidden behavior differences among cards with one observable key.

    Physical identity and zone are excluded. Local cost modifiers and temporary star
    costs remain included via ``dataclasses.asdict`` so two publicly-indistinguishable
    cards that can replay differently cause a fail-closed materialization.
    """
    payload = dataclasses.asdict(card)
    payload.pop("InstanceId", None)
    payload.pop("Zone", None)

    def freeze(value: Any) -> Any:
        if isinstance(value, dict):
            return tuple(sorted((str(k), freeze(v)) for k, v in value.items()))
        if isinstance(value, list):
            return tuple(freeze(v) for v in value)
        return value

    return freeze(payload)


def card_id_from_observable_key(key: ObservableCardKey) -> str:
    return str(key[0])


def _keys_from_public_cards(cards: object) -> Optional[tuple[ObservableCardKey, ...]]:
    if not isinstance(cards, list):
        return None
    keys = tuple(observable_card_key_from_public(card) for card in cards)
    if any(key is None for key in keys):
        return None
    return keys  # type: ignore[return-value]


def _counter_subset(left: Counter, right: Counter) -> bool:
    return all(count <= right.get(key, 0) for key, count in left.items())


def _draw_multiset_changed(pre_state: object, post_state: object) -> bool:
    pre_engine = getattr(pre_state, "engine_state", None)
    post_engine = getattr(post_state, "engine_state", None)
    if not isinstance(pre_engine, dict) or not isinstance(post_engine, dict):
        return True
    pre_keys = _keys_from_public_cards(pre_engine.get("drawPile"))
    post_keys = _keys_from_public_cards(post_engine.get("drawPile"))
    if pre_keys is None or post_keys is None:
        return True
    return Counter(pre_keys) != Counter(post_keys)


def observable_transfer_evidence(
    pre_state: object,
    post_state: object,
) -> Optional[ObservableTransferEvidence]:
    """Gate A: prove ``O = R + E, E subset-of P`` using unordered public state.

    ``R`` is exactly the observable multiset removed from DrawPile. ``P`` is the
    multiset sum of cards that persist across each eligible non-DrawPile public zone.
    This intentionally does not infer source card, choice operation, origin metadata,
    or draw order.
    """
    pre_engine = getattr(pre_state, "engine_state", None)
    post_engine = getattr(post_state, "engine_state", None)
    if not isinstance(pre_engine, dict) or not isinstance(post_engine, dict):
        return None
    pending = post_engine.get("pendingChoice")
    if not isinstance(pending, dict):
        return None

    pre_draw = _keys_from_public_cards(pre_engine.get("drawPile"))
    post_draw = _keys_from_public_cards(post_engine.get("drawPile"))
    options = _keys_from_public_cards(pending.get("options"))
    if pre_draw is None or post_draw is None or options is None or not options:
        return None

    d0 = Counter(pre_draw)
    d1 = Counter(post_draw)
    if not _counter_subset(d1, d0):
        return None
    removed = d0 - d1
    if not removed:
        return None

    option_counts = Counter(options)
    if not _counter_subset(removed, option_counts):
        return None

    persistent = Counter()
    for zone in _PUBLIC_NON_DRAW_ZONES:
        if zone not in pre_engine or zone not in post_engine:
            continue
        pre_zone = _keys_from_public_cards(pre_engine.get(zone))
        post_zone = _keys_from_public_cards(post_engine.get(zone))
        if pre_zone is None or post_zone is None:
            return None
        persistent += Counter(pre_zone) & Counter(post_zone)

    explained_non_draw = option_counts - removed
    if not _counter_subset(explained_non_draw, persistent):
        return None

    return ObservableTransferEvidence(removed_from_draw=removed, option_keys=options)


def _distinct_draw_sequences_from_options(
    transfer: ObservableTransferEvidence,
    *,
    limit: int = 2,
) -> tuple[tuple[ObservableCardKey, ...], ...]:
    """Return at most ``limit`` distinct R-subsequences of PendingChoice order.

    Gate A proved ``O = R + E``. Remove exactly ``E`` occurrences while preserving
    option order. Different occurrence assignments are acceptable only when they
    collapse to the same observable-key sequence; otherwise Gate B is ambiguous and
    fails closed.
    """
    if limit <= 0:
        return ()
    options = transfer.option_keys
    target_keys = tuple(sorted(transfer.removed_from_draw.keys(), key=repr))
    slots = {key: index for index, key in enumerate(target_keys)}
    initial = tuple(int(transfer.removed_from_draw[key]) for key in target_keys)

    @lru_cache(maxsize=None)
    def visit(
        index: int,
        remaining: tuple[int, ...],
    ) -> tuple[tuple[ObservableCardKey, ...], ...]:
        if not any(remaining):
            return ((),)
        if index >= len(options):
            return ()

        results: list[tuple[ObservableCardKey, ...]] = []

        def add(sequence: tuple[ObservableCardKey, ...]) -> None:
            if sequence not in results and len(results) < limit:
                results.append(sequence)

        for tail in visit(index + 1, remaining):
            add(tail)
            if len(results) >= limit:
                return tuple(results)

        key = options[index]
        slot = slots.get(key)
        if slot is not None and remaining[slot] > 0:
            next_remaining = list(remaining)
            next_remaining[slot] -= 1
            for tail in visit(index + 1, tuple(next_remaining)):
                add((key, *tail))
                if len(results) >= limit:
                    break
        return tuple(results)

    return visit(0, initial)


def visible_draw_transition_evidence_from_committed_transition(
    post_state: object,
    root_snapshot: object,
    replay_prefix: list[object],
    *,
    pre_battle_state: object,
) -> VisibleDrawTransitionEvidence:
    """Return Gate-A+B-proven constraints using public state only.

    ``root_snapshot`` is retained temporarily for call-site compatibility but is
    deliberately ignored. Gate B treats the unique draw-origin subsequence of ordered
    ``pendingChoice.options`` as sequential draw order under the Emulator publication
    contract. Snapshot DrawPile order, RNG state, raw draw history, and physical card
    identity are not consulted as proof or validation inputs.
    """
    del root_snapshot

    if any(
        bool(getattr(entry, "visible_draw_tracking_blocked", False))
        for entry in replay_prefix
    ):
        return VisibleDrawTransitionEvidence(
            blocks_later_pinning=True,
            tracking_error="draw tracking was already blocked by an earlier Replay Prefix transition",
        )

    transfer = observable_transfer_evidence(pre_battle_state, post_state)
    if transfer is None:
        changed = _draw_multiset_changed(pre_battle_state, post_state)
        return VisibleDrawTransitionEvidence(
            blocks_later_pinning=changed,
            tracking_error=(
                "Gate A could not explain an observed DrawPile multiset mutation"
                if changed
                else None
            ),
        )

    sequences = _distinct_draw_sequences_from_options(transfer)
    if len(sequences) != 1:
        return VisibleDrawTransitionEvidence(
            blocks_later_pinning=True,
            tracking_error=(
                "Gate B could not uniquely separate draw-origin option occurrences "
                "from E; draw order is ambiguous under observable card equality"
            ),
        )
    sequence = sequences[0]

    cursor = sum(
        len(getattr(entry, "visible_draw_constraints", ()) or ())
        for entry in replay_prefix
    )
    return VisibleDrawTransitionEvidence(
        constraints=tuple(
            (cursor + offset, key)
            for offset, key in enumerate(sequence)
        )
    )
