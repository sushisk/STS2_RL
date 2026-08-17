"""Infer already-observed draw order from public replay state."""
from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import dataclass
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


@dataclass(frozen=True)
class VisibleDrawTransitionEvidence:
    """One committed transition's replay-relevant public draw evidence.

    ``blocks_later_pinning`` is set when DrawPile changed but the immediate public
    pre/post state cannot explain that change as the supported Hand-transfer shape.
    Later transitions must not invent root-relative draw offsets past such a mutation.
    """

    constraints: VisibleDrawConstraints = ()
    blocks_later_pinning: bool = False
    tracking_error: Optional[str] = None


def _enchantment_key(value: object) -> tuple[bool, Optional[tuple]]:
    if value is None:
        return True, None
    if not isinstance(value, dict):
        return False, None
    if set(value) != {"id", "amount", "status"}:
        return False, None

    enchantment_id = value["id"]
    amount = value["amount"]
    status = value["status"]
    if not isinstance(enchantment_id, str) or not enchantment_id:
        return False, None
    if isinstance(amount, bool) or not isinstance(amount, int):
        return False, None
    if not isinstance(status, str) or not status:
        return False, None
    return True, (enchantment_id, amount, status)


def observable_card_key_from_public(card: object) -> Optional[ObservableCardKey]:
    """Return behavior-relevant state available on public card DTOs.

    Choice-local ids, list position, zone and physical-copy identity are intentionally
    excluded. Missing/invalid required gameplay fields fail closed.
    """
    if not isinstance(card, dict):
        return None
    card_id = card.get("id")
    card_type = card.get("type")
    rarity = card.get("rarity")
    cost = card.get("cost")
    target_type = card.get("targetType")
    if "upgraded" not in card or "upgradeLevel" not in card:
        return None
    upgraded = card["upgraded"]
    upgrade_level = card["upgradeLevel"]
    tinker_type = card.get("tinkerTimeType")
    tinker_rider = card.get("tinkerTimeRider")
    enchantment_ok, enchantment = _enchantment_key(card.get("enchantment"))
    if not isinstance(card_id, str) or not card_id:
        return None
    if not isinstance(card_type, str) or not isinstance(rarity, str) or not isinstance(target_type, str):
        return None
    if isinstance(cost, bool) or not isinstance(cost, int):
        return None
    if not isinstance(upgraded, bool):
        return None
    if isinstance(upgrade_level, bool) or not isinstance(upgrade_level, int):
        return None
    if tinker_type is not None and not isinstance(tinker_type, str):
        return None
    if tinker_rider is not None and not isinstance(tinker_rider, str):
        return None
    if not enchantment_ok:
        return None
    return (
        card_id,
        card_type,
        rarity,
        cost,
        target_type,
        upgraded,
        upgrade_level,
        tinker_type,
        tinker_rider,
        enchantment,
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
    """Detect hidden behavior differences among cards with one observable key."""
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


def _matches_one_card_consumed(
    pre_hand: tuple[ObservableCardKey, ...],
    surviving_prefix: tuple[ObservableCardKey, ...],
) -> bool:
    """Whether ``surviving_prefix`` is ``pre_hand`` with exactly one occurrence removed."""
    if len(pre_hand) != len(surviving_prefix) + 1:
        return False
    return any(
        pre_hand[:index] + pre_hand[index + 1 :] == surviving_prefix
        for index in range(len(pre_hand))
    )


def _visible_hand_draw_sequence(
    pre_state: object,
    post_state: object,
) -> Optional[tuple[ObservableCardKey, ...]]:
    """Recognize one-card consumption followed by DrawPile cards appended to Hand.

    DrawPile is compared only as an unordered multiset. The sequence comes from the
    newly appended Hand suffix. This deliberately does not inspect PendingChoice or any
    producer/provenance metadata.
    """
    pre_engine = getattr(pre_state, "engine_state", None)
    post_engine = getattr(post_state, "engine_state", None)
    if not isinstance(pre_engine, dict) or not isinstance(post_engine, dict):
        return None

    pre_draw = _keys_from_public_cards(pre_engine.get("drawPile"))
    post_draw = _keys_from_public_cards(post_engine.get("drawPile"))
    pre_hand = _keys_from_public_cards(pre_engine.get("hand"))
    post_hand = _keys_from_public_cards(post_engine.get("hand"))
    if pre_draw is None or post_draw is None or pre_hand is None or post_hand is None:
        return None

    d0 = Counter(pre_draw)
    d1 = Counter(post_draw)
    if not _counter_subset(d1, d0):
        return None

    removed = d0 - d1
    if not removed:
        return ()

    draw_count = sum(removed.values())
    if len(post_hand) < draw_count:
        return None

    surviving_prefix = post_hand[:-draw_count]
    drawn_suffix = post_hand[-draw_count:]
    if Counter(drawn_suffix) != removed:
        return None
    if not _matches_one_card_consumed(pre_hand, surviving_prefix):
        return None

    return drawn_suffix


def visible_draw_transition_evidence_from_committed_transition(
    post_state: object,
    replay_prefix: list[object],
    *,
    pre_battle_state: object,
) -> VisibleDrawTransitionEvidence:
    """Return constraints for the supported public DrawPile -> Hand transition shape.

    Snapshot/public DrawPile list order, PendingChoice ordering, RNG state, raw draw
    history, and physical card identity are not proof inputs.
    """
    if any(
        bool(getattr(entry, "visible_draw_tracking_blocked", False))
        for entry in replay_prefix
    ):
        return VisibleDrawTransitionEvidence(
            blocks_later_pinning=True,
            tracking_error="draw tracking was already blocked by an earlier Replay Prefix transition",
        )

    sequence = _visible_hand_draw_sequence(pre_battle_state, post_state)
    if sequence is None:
        changed = _draw_multiset_changed(pre_battle_state, post_state)
        return VisibleDrawTransitionEvidence(
            blocks_later_pinning=changed,
            tracking_error=(
                "observed DrawPile mutation was not explained by one-card Hand consumption "
                "followed by appending exactly the removed observable cards"
                if changed
                else None
            ),
        )
    if not sequence:
        return VisibleDrawTransitionEvidence()

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
