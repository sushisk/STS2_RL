"""Observable-state proof for replay-prefix DrawPile reconstruction.

The core rule is deliberately split in two:

* Gate A proves an unordered DrawPile -> PendingChoice transfer from public card state.
* Gate B proves that the transferred cards have an ordered representation that is safe
  to interpret as sequential draw order.

Neither gate uses card-name allowlists, rng_id, public instance identity, or Snapshot
InstanceId.  The output is an RL-internal sequence of root-relative offsets paired with
observable replay-equivalence keys.  Concrete Snapshot allocation is handled separately
by ``search.rng_hypothesis``.
"""
from __future__ import annotations

import dataclasses
from collections import Counter
from dataclasses import dataclass
from typing import Any, Optional


ObservableCardKey = tuple[
    str,              # CardId
    str,              # Type
    str,              # Rarity
    int,              # effective Cost
    str,              # TargetType
    bool,             # upgraded
    int,              # UpgradeLevel
    Optional[str],    # TinkerTimeType
    Optional[str],    # TinkerTimeRider
    Optional[tuple],  # enchantment signature
]
VisibleDrawConstraint = tuple[int, ObservableCardKey]
VisibleDrawConstraints = tuple[VisibleDrawConstraint, ...]

_PUBLIC_NON_DRAW_ZONES = ("hand", "discardPile", "exhaustPile", "playPile")
_SNAPSHOT_NON_DRAW_ZONES = {
    "hand": "Hand",
    "discardPile": "DiscardPile",
    "exhaustPile": "ExhaustPile",
    "playPile": "PlayPile",
}


@dataclass(frozen=True)
class ObservableTransferEvidence:
    """Gate-A result before any draw-order interpretation."""

    removed_from_draw: Counter
    explained_non_draw: Counter
    option_keys: tuple[ObservableCardKey, ...]


@dataclass(frozen=True)
class VisibleDrawTransitionEvidence:
    """One committed transition's replay-relevant draw evidence.

    ``blocks_later_pinning`` is set when the public DrawPile changed but the transition
    could not be proven as an ordered sequential draw.  Later transitions must not
    invent root-relative offsets past such a mutation.
    """

    constraints: VisibleDrawConstraints = ()
    blocks_later_pinning: bool = False


def _enchantment_key(value: object) -> Optional[tuple]:
    if value is None:
        return None
    if not isinstance(value, dict):
        return None
    return (value.get("id"), value.get("amount"), value.get("status"))


def observable_card_key_from_public(card: object) -> Optional[ObservableCardKey]:
    """Return behavior-relevant state available on public card/choice DTOs.

    optionId, list position, zone and any physical-copy identity are intentionally
    excluded.  Missing/invalid required gameplay fields fail closed instead of silently
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

    Current ``CardInstanceSnapshot`` does not carry enchantment.  Its projection is
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

    Physical identity and zone are excluded.  Local cost modifiers and temporary star
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


def _keys_from_snapshot_cards(cards: object) -> tuple[ObservableCardKey, ...]:
    return tuple(observable_card_key_from_snapshot(card) for card in (cards or ()))


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
        pre_zone = _keys_from_public_cards(pre_engine.get(zone))
        post_zone = _keys_from_public_cards(post_engine.get(zone))
        if pre_zone is None or post_zone is None:
            return None
        persistent += Counter(pre_zone) & Counter(post_zone)

    explained_non_draw = option_counts - removed
    if not _counter_subset(explained_non_draw, persistent):
        return None

    return ObservableTransferEvidence(
        removed_from_draw=removed,
        explained_non_draw=explained_non_draw,
        option_keys=options,
    )


def _is_subsequence(needle: tuple[ObservableCardKey, ...], haystack: tuple[ObservableCardKey, ...]) -> bool:
    if not needle:
        return True
    index = 0
    for value in haystack:
        if value == needle[index]:
            index += 1
            if index == len(needle):
                return True
    return False


def _hand_append_draw_order(
    pre_state: object,
    post_state: object,
    transfer: ObservableTransferEvidence,
) -> Optional[tuple[ObservableCardKey, ...]]:
    """Gate-B prover for the common draw -> append-to-Hand -> choose-from-Hand path.

    This is structural, not card-specific.  It requires the published options to be the
    post-transition Hand sequence, the non-draw options to form a surviving pre-Hand
    prefix/subsequence, and the DrawPile-removed cards to be one contiguous suffix.
    The real-game Acrobatics path satisfies exactly this engine-level ordering contract.
    """
    pre_engine = getattr(pre_state, "engine_state", None)
    post_engine = getattr(post_state, "engine_state", None)
    if not isinstance(pre_engine, dict) or not isinstance(post_engine, dict):
        return None
    pre_hand = _keys_from_public_cards(pre_engine.get("hand"))
    post_hand = _keys_from_public_cards(post_engine.get("hand"))
    if pre_hand is None or post_hand is None:
        return None
    options = transfer.option_keys
    if options != post_hand:
        return None

    draw_count = sum(transfer.removed_from_draw.values())
    if draw_count <= 0 or draw_count > len(options):
        return None
    prefix = options[:-draw_count]
    suffix = options[-draw_count:]
    if Counter(prefix) != transfer.explained_non_draw:
        return None
    if Counter(suffix) != transfer.removed_from_draw:
        return None
    if not _is_subsequence(prefix, pre_hand):
        return None
    return suffix


_ORDERED_DRAW_PROVERS = (
    _hand_append_draw_order,
)


def ordered_draw_sequence(
    pre_state: object,
    post_state: object,
    transfer: ObservableTransferEvidence,
) -> Optional[tuple[ObservableCardKey, ...]]:
    """Gate B: return one unambiguous engine-proven sequential draw order.

    New mechanics extend ``_ORDERED_DRAW_PROVERS`` at the engine-primitive/structural
    level.  In particular, a future draw-N-choose-M path whose options contain only the
    drawn cards can reuse Gate A immediately, but should add an order prover only after
    the Emulator/game contract confirms that those option positions preserve draw order.
    """
    matches = []
    for prover in _ORDERED_DRAW_PROVERS:
        sequence = prover(pre_state, post_state, transfer)
        if sequence is not None:
            matches.append(sequence)
    if not matches:
        return None
    first = matches[0]
    if any(sequence != first for sequence in matches[1:]):
        return None
    return first


def visible_draw_transition_evidence_from_committed_transition(
    post_state: object,
    root_snapshot: object,
    replay_prefix: list[object],
    *,
    triggering_action: object,
    pre_battle_state: object,
) -> VisibleDrawTransitionEvidence:
    """Return Gate-A+B-proven root-relative draw constraints for one real transition."""
    del root_snapshot, triggering_action  # intentionally not proof inputs

    if any(bool(getattr(entry, "visible_draw_tracking_blocked", False)) for entry in replay_prefix):
        return VisibleDrawTransitionEvidence(blocks_later_pinning=True)

    transfer = observable_transfer_evidence(pre_battle_state, post_state)
    if transfer is None:
        return VisibleDrawTransitionEvidence(
            blocks_later_pinning=_draw_multiset_changed(pre_battle_state, post_state)
        )

    sequence = ordered_draw_sequence(pre_battle_state, post_state, transfer)
    if sequence is None:
        return VisibleDrawTransitionEvidence(blocks_later_pinning=True)

    cursor = sum(len(getattr(entry, "visible_draw_constraints", ()) or ()) for entry in replay_prefix)
    constraints = tuple((cursor + offset, key) for offset, key in enumerate(sequence))
    return VisibleDrawTransitionEvidence(constraints=constraints)


def visible_draw_constraints_from_committed_transition(
    post_state: object,
    root_snapshot: object,
    replay_prefix: list[object],
    *,
    triggering_action: object,
    pre_battle_state: object,
) -> VisibleDrawConstraints:
    """Compatibility wrapper for callers that only need the constraint tuple."""
    return visible_draw_transition_evidence_from_committed_transition(
        post_state,
        root_snapshot,
        replay_prefix,
        triggering_action=triggering_action,
        pre_battle_state=pre_battle_state,
    ).constraints
