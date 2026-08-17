from pathlib import Path
import re


def one(text, old, new, label):
    if old not in text:
        raise SystemExit(f"missing anchor: {label}")
    return text.replace(old, new, 1)


# replay_draw_restore.py
p = Path("Combat/search/replay_draw_restore.py")
s = p.read_text()
s = one(
    s,
    "from dataclasses import dataclass\nfrom typing import Any, Optional\n",
    "from dataclasses import dataclass\nfrom functools import lru_cache\nfrom typing import Any, Optional\n",
    "lru_cache import",
)
s = one(
    s,
    "    constraints: VisibleDrawConstraints = ()\n    blocks_later_pinning: bool = False\n",
    "    constraints: VisibleDrawConstraints = ()\n    blocks_later_pinning: bool = False\n    tracking_error: Optional[str] = None\n",
    "tracking error",
)

start = s.index("def _is_subsequence(")
end = s.index("def visible_draw_transition_evidence_from_committed_transition(")
gate_b = '''def _distinct_draw_sequences_from_options(
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

        # This option occurrence belongs to E.
        for tail in visit(index + 1, remaining):
            add(tail)
            if len(results) >= limit:
                return tuple(results)

        # This option occurrence belongs to R.
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


def ordered_draw_sequence(
    pre_state: object,
    post_state: object,
    transfer: ObservableTransferEvidence,
) -> Optional[tuple[ObservableCardKey, ...]]:
    """Generic Gate B: the unique option-order subsequence whose multiset is ``R``."""
    del pre_state, post_state
    sequences = _distinct_draw_sequences_from_options(transfer)
    return sequences[0] if len(sequences) == 1 else None


def _stable_root_draw_slice(
    root_snapshot: object,
    cursor: int,
    count: int,
) -> Optional[tuple[ObservableCardKey, ...]]:
    """Independent order sentinel from Held Stable Snapshot, never a fallback source."""
    player = getattr(root_snapshot, "Player", None)
    draw_pile = getattr(player, "DrawPile", None)
    if draw_pile is None:
        return None
    cards = list(draw_pile or ())
    if cursor < 0 or count <= 0 or cursor + count > len(cards):
        return None
    return tuple(
        observable_card_key_from_snapshot(card)
        for card in cards[cursor : cursor + count]
    )


def _order_validation_projection(key: ObservableCardKey) -> tuple:
    # Snapshot currently lacks enchantment. Compare every mutually-representable field;
    # full materialization still rejects an enchantment representation mismatch.
    return key[:-1]


def _card_ids(sequence: tuple[ObservableCardKey, ...]) -> list[str]:
    return [card_id_from_observable_key(key) for key in sequence]


'''
s = s[:start] + gate_b + s[end:]

start = s.index("def visible_draw_transition_evidence_from_committed_transition(")
end = s.index("def visible_draw_constraints_from_committed_transition(")
evidence = '''def visible_draw_transition_evidence_from_committed_transition(
    post_state: object,
    root_snapshot: object,
    replay_prefix: list[object],
    *,
    triggering_action: object,
    pre_battle_state: object,
) -> VisibleDrawTransitionEvidence:
    """Return generic Gate-A+B-proven root-relative draw constraints.

    Gate B derives order from PendingChoice options after removing ``E``. The inferred
    order is independently checked against Held Stable Snapshot at the current root-
    relative cursor. Snapshot order is never used as a fallback derivation when the two
    disagree.
    """
    del triggering_action

    if any(
        bool(getattr(entry, "visible_draw_tracking_blocked", False))
        for entry in replay_prefix
    ):
        return VisibleDrawTransitionEvidence(
            blocks_later_pinning=True,
            tracking_error=(
                "draw tracking was already blocked by an earlier Replay Prefix transition"
            ),
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
    expected = _stable_root_draw_slice(root_snapshot, cursor, len(sequence))
    if expected is None:
        return VisibleDrawTransitionEvidence(
            blocks_later_pinning=True,
            tracking_error=(
                "Gate B could not validate option-derived draw order against Held "
                f"Stable Snapshot offsets {cursor}..{cursor + len(sequence) - 1}"
            ),
        )

    inferred_projection = tuple(
        _order_validation_projection(key) for key in sequence
    )
    stable_projection = tuple(
        _order_validation_projection(key) for key in expected
    )
    if inferred_projection != stable_projection:
        return VisibleDrawTransitionEvidence(
            blocks_later_pinning=True,
            tracking_error=(
                "Gate B option-order contract rejected: option-derived draw order "
                f"{_card_ids(sequence)!r} disagrees with Held Stable Snapshot order "
                f"{_card_ids(expected)!r} at root offsets "
                f"{cursor}..{cursor + len(sequence) - 1}"
            ),
        )

    return VisibleDrawTransitionEvidence(
        constraints=tuple(
            (cursor + offset, key)
            for offset, key in enumerate(sequence)
        )
    )


'''
s = s[:start] + evidence + s[end:]
p.write_text(s)


# decision_context.py
p = Path("Combat/search/decision_context.py")
s = p.read_text()
s = one(
    s,
    '    visible_draw_constraints: VisibleDrawConstraints = ()\n'
    '    visible_draw_tracking_blocked: bool = False\n',
    '    visible_draw_constraints: VisibleDrawConstraints = ()\n'
    '    visible_draw_tracking_blocked: bool = False\n'
    '    visible_draw_tracking_error: "Optional[str]" = None\n',
    "ReplayPrefixEntry error field",
)
s = one(
    s,
    "    identity is stored. ``visible_draw_tracking_blocked`` records an intervening\n"
    "    DrawPile mutation that could not be explained safely; later transitions must not\n"
    "    invent root-relative draw offsets past that point.\n",
    "    identity is stored. ``visible_draw_tracking_blocked`` records an intervening\n"
    "    DrawPile mutation that could not be explained safely; later transitions must not\n"
    "    invent root-relative draw offsets past that point. ``visible_draw_tracking_error``\n"
    "    retains the fail-closed Gate-A/Gate-B diagnostic so an ordering-contract regression\n"
    "    remains observable.\n",
    "ReplayPrefixEntry docstring",
)
p.write_text(s)


# main_loop.py
p = Path("Combat/search/main_loop.py")
s = p.read_text()
anchor = (
    "            visible_draw_tracking_blocked=(\n"
    "                draw_evidence.blocks_later_pinning if draw_evidence is not None else False\n"
    "            ),\n"
)
s = one(
    s,
    anchor,
    anchor
    + "            visible_draw_tracking_error=(\n"
      "                draw_evidence.tracking_error if draw_evidence is not None else None\n"
      "            ),\n",
    "main loop tracking error",
)
p.write_text(s)


# instance_combat.py
p = Path("API/instance_combat.py")
s = p.read_text()
anchor = "                    visible_draw_tracking_blocked=draw_evidence.blocks_later_pinning,\n"
s = one(
    s,
    anchor,
    anchor + "                    visible_draw_tracking_error=draw_evidence.tracking_error,\n",
    "API tracking error",
)
p.write_text(s)


# tests
p = Path("Combat/tests/test_replay_prefix_visible_draw_pinning.py")
s = p.read_text()
s = one(
    s,
    "def _entry(*constraints, blocked: bool = False):\n"
    "    return SimpleNamespace(\n"
    "        visible_draw_constraints=tuple(constraints),\n"
    "        visible_draw_tracking_blocked=blocked,\n"
    "    )\n",
    "def _entry(*constraints, blocked: bool = False, error: str | None = None):\n"
    "    return SimpleNamespace(\n"
    "        visible_draw_constraints=tuple(constraints),\n"
    "        visible_draw_tracking_blocked=blocked,\n"
    "        visible_draw_tracking_error=error,\n"
    "    )\n",
    "test entry helper",
)
s = one(
    s,
    "def _evidence(pre, post, prefix=()):\n"
    "    return visible_draw_transition_evidence_from_committed_transition(\n"
    "        post,\n"
    "        _root([]),\n"
    "        list(prefix),\n"
    '        triggering_action=SemanticAction("card", "0:ANY_CARD"),\n'
    "        pre_battle_state=pre,\n"
    "    )\n",
    '''def _root_from_public(cards):
    return _root([
        _card(
            f"root-{index}",
            card["id"],
            upgraded=bool(card.get("upgraded", False)),
            cost=int(card.get("cost", 0)),
        )
        for index, card in enumerate(cards)
    ])


def _evidence(pre, post, prefix=(), *, root_draw=None):
    if root_draw is None:
        root_draw = pre.engine_state["drawPile"]
    return visible_draw_transition_evidence_from_committed_transition(
        post,
        _root_from_public(root_draw),
        list(prefix),
        triggering_action=SemanticAction("card", "0:ANY_CARD"),
        pre_battle_state=pre,
    )
''',
    "evidence helper",
)

pattern = re.compile(
    r'(def test_card_allowlist_and_choice_semantics_are_not_safety_gates\(\) -> None:.*?'
    r'result = visible_draw_transition_evidence_from_committed_transition\(\n'
    r'\s*post,\n)\s*_root\(\[\]\),',
    re.S,
)
s, count = pattern.subn(r"\1        _root_from_public([a]),", s, count=1)
if count != 1:
    raise SystemExit("failed to patch direct dispatcher test root")

pattern = re.compile(
    r'def test_drawn_only_choice_passes_gate_a_and_reuses_same_order_prover_extension_point\(\) -> None:'
    r'.*?\n\n\ndef test_drawpile_publication_order_is_not_gate_a_evidence',
    re.S,
)
replacement = '''def test_drawn_only_choice_uses_generic_gate_b_without_mechanic_specific_prover() -> None:
    a, b, c, d = (_public(name) for name in ("A", "B", "C", "D"))
    h = _public("H")
    pre = _state(hand=[h], draw=[a, b, c, d])
    post = _state(hand=[h], draw=[d], options=[a, b, c])

    transfer = observable_transfer_evidence(pre, post)
    assert transfer is not None
    sequence = replay_draw_restore.ordered_draw_sequence(pre, post, transfer)
    assert sequence is not None
    assert [card_id_from_observable_key(key) for key in sequence] == ["A", "B", "C"]

    result = _evidence(pre, post)
    assert result.blocks_later_pinning is False
    assert result.tracking_error is None
    assert [card_id_from_observable_key(key) for _offset, key in result.constraints] == ["A", "B", "C"]


def test_drawpile_publication_order_is_not_gate_a_evidence'''
s, count = pattern.subn(replacement, s, count=1)
if count != 1:
    raise SystemExit("failed to replace drawn-only test")

s = one(
    s,
    "    first = _evidence(_state(hand=[h], draw=[a, b, c]), post)\n"
    "    second = _evidence(_state(hand=[h], draw=[b, a, c]), post)\n",
    "    root_draw = [a, b, c]\n"
    "    first = _evidence(_state(hand=[h], draw=[a, b, c]), post, root_draw=root_draw)\n"
    "    second = _evidence(_state(hand=[h], draw=[b, a, c]), post, root_draw=root_draw)\n",
    "public order test",
)

anchor = "def test_unaccounted_drawpile_mutation_blocks_later_root_relative_pinning() -> None:\n"
extra = '''def test_gate_b_rejects_wrong_option_order_against_stable_root_and_records_error() -> None:
    a, b, c, d = (_public(name) for name in ("A", "B", "C", "D"))
    pre = _state(hand=[], draw=[a, b, c, d])
    post = _state(hand=[], draw=[d], options=[c, b, a])

    result = _evidence(pre, post, root_draw=[a, b, c, d])
    assert result.constraints == ()
    assert result.blocks_later_pinning is True
    assert result.tracking_error is not None
    assert "option-order contract rejected" in result.tracking_error
    assert "['C', 'B', 'A']" in result.tracking_error
    assert "['A', 'B', 'C']" in result.tracking_error


def test_gate_b_fails_closed_when_r_and_e_occurrences_make_order_ambiguous() -> None:
    a, b, c = (_public(name) for name in ("A", "B", "C"))
    pre = _state(hand=[a], draw=[a, b, c])
    post = _state(hand=[a], draw=[c], options=[a, b, a])

    result = _evidence(pre, post, root_draw=[a, b, c])
    assert result.constraints == ()
    assert result.blocks_later_pinning is True
    assert result.tracking_error is not None
    assert "ambiguous under observable card equality" in result.tracking_error


'''
s = one(s, anchor, extra + anchor, "new Gate B tests")
p.write_text(s)
