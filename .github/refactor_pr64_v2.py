from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing anchor: {label}")
    return text.replace(old, new, 1)


path = Path("Combat/search/rng_hypothesis.py")
text = path.read_text()
text = replace_once(
    text,
    '''    ordered = tuple(sorted(constraints, key=lambda item: item[0]))\n    if [offset for offset, _key in ordered] != list(range(len(ordered))):\n        raise ValueError(\n            "visible draw constraints must form one contiguous prefix from Stable-root offset 0"\n        )\n    return ordered\n''',
    '''    constraints_tuple = tuple(constraints)\n    if [offset for offset, _key in constraints_tuple] != list(range(len(constraints_tuple))):\n        raise ValueError(\n            "visible draw constraints must form one ordered contiguous prefix from Stable-root offset 0"\n        )\n    return constraints_tuple\n''',
    "preserve constraint order",
)

start = text.index("def _draw_pile_instances_for_hypothesis(")
end = text.index("def derive_substituted_snapshot(")
replacement = '''def _draw_pile_instances_for_hypothesis(\n    root_snapshot: CombatStateSnapshot,\n    ordered_card_ids: tuple[str, ...],\n    pinned_observable_keys: tuple[tuple[int, ObservableCardKey], ...] = (),\n) -> list[dict]:\n    root_cards = list(root_snapshot.Player.DrawPile)\n    requested = Counter(ordered_card_ids)\n    available = Counter(str(card.CardId) for card in root_cards)\n    if requested != available:\n        raise ValueError(\n            "hypothesis DrawPile multiset does not match root snapshot Player.DrawPile multiset: "\n            f"requested={dict(sorted(requested.items()))}, available={dict(sorted(available.items()))}"\n        )\n\n    offsets = [offset for offset, _key in pinned_observable_keys]\n    if offsets != list(range(len(offsets))):\n        raise ValueError("pinned draw constraints must be an ordered contiguous prefix")\n\n    pinned_keys = tuple(key for _offset, key in pinned_observable_keys)\n    for key, count in Counter(pinned_keys).items():\n        matches = [card for card in root_cards if observable_card_key_from_snapshot(card) == key]\n        if len(matches) < count:\n            raise ValueError(\n                "pinned observable card state is absent from the root DrawPile in the required count"\n            )\n        if len({snapshot_card_replay_internal_key(card) for card in matches}) != 1:\n            raise ValueError(\n                "pinned observable card state maps to multiple hidden gameplay states; "\n                "public evidence is insufficient for safe replay materialization"\n            )\n\n    used_instance_ids: set[str] = set()\n    pinned_cards: list[CardInstanceSnapshot] = []\n    for offset, key in pinned_observable_keys:\n        expected_card_id = ordered_card_ids[offset]\n        if card_id_from_observable_key(key) != expected_card_id:\n            raise ValueError(\n                f"pinned observable card has CardId={card_id_from_observable_key(key)!r}, "\n                f"but hypothesis position {offset} requires {expected_card_id!r}"\n            )\n        matches = sorted(\n            (\n                card\n                for card in root_cards\n                if str(card.InstanceId) not in used_instance_ids\n                and observable_card_key_from_snapshot(card) == key\n            ),\n            key=_card_identity_key,\n        )\n        if not matches:\n            raise ValueError("pinned observable card state was exhausted during concrete allocation")\n        chosen = matches[0]\n        pinned_cards.append(chosen)\n        used_instance_ids.add(str(chosen.InstanceId))\n\n    by_card_id: dict[str, deque[CardInstanceSnapshot]] = {}\n    for card in sorted(\n        (card for card in root_cards if str(card.InstanceId) not in used_instance_ids),\n        key=_card_identity_key,\n    ):\n        by_card_id.setdefault(str(card.CardId), deque()).append(card)\n\n    tail_cards: list[CardInstanceSnapshot] = []\n    for card_id in ordered_card_ids[len(pinned_cards) :]:\n        cards = by_card_id.get(card_id)\n        if not cards:\n            raise ValueError(\n                f"hypothesis concrete-card allocation exhausted CardId {card_id!r} after observable-state pinning"\n            )\n        tail_cards.append(cards.popleft())\n\n    return [dataclasses.asdict(card) for card in pinned_cards + tail_cards]\n\n\n'''
text = text[:start] + replacement + text[end:]
path.write_text(text)


path = Path("Combat/tests/test_replay_prefix_visible_draw_pinning.py")
text = path.read_text()
anchor = '''    invalid = SimpleNamespace(root_snapshot=root, replay_prefix=[_entry((1, b_key))])\n    with pytest.raises(ValueError, match="contiguous prefix"):\n        _pinned_prefix_visible_draw_constraints(invalid)\n'''
replacement = '''    invalid = SimpleNamespace(root_snapshot=root, replay_prefix=[_entry((1, b_key))])\n    with pytest.raises(ValueError, match="contiguous prefix"):\n        _pinned_prefix_visible_draw_constraints(invalid)\n\n    out_of_order = SimpleNamespace(\n        root_snapshot=root,\n        replay_prefix=[_entry((1, b_key), (0, a_key))],\n    )\n    with pytest.raises(ValueError, match="ordered contiguous prefix"):\n        _pinned_prefix_visible_draw_constraints(out_of_order)\n'''
text = replace_once(text, anchor, replacement, "out-of-order constraint regression")
path.write_text(text)
