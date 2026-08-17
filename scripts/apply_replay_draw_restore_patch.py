from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def write(path: str, text: str) -> None:
    (ROOT / path).write_text(text, encoding="utf-8")


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one literal match, found {count}")
    return text.replace(old, new, 1)


def regex_once(text: str, pattern: str, replacement: str, label: str) -> str:
    result, count = re.subn(pattern, replacement, text, count=1, flags=re.S)
    if count != 1:
        raise RuntimeError(f"{label}: expected one regex match, found {count}")
    return result


# ---------------------------------------------------------------------------
# decision_context.py: keep ReplayPrefixEntry, remove card-specific proof implementation.
# ---------------------------------------------------------------------------
path = "Combat/search/decision_context.py"
text = read(path)
text = replace_once(
    text,
    "from battle_emulator import BattleState, is_action_continuation_pending_choice, pending_choice_metadata\n",
    "from battle_emulator import BattleState, is_action_continuation_pending_choice, pending_choice_metadata\n"
    "from search.replay_draw_restore import (\n"
    "    VisibleDrawConstraint,\n"
    "    VisibleDrawConstraints,\n"
    "    VisibleDrawTransitionEvidence,\n"
    "    visible_draw_constraints_from_committed_transition,\n"
    "    visible_draw_transition_evidence_from_committed_transition,\n"
    ")\n",
    "decision_context import",
)
replacement = '''@dataclass(frozen=True)
class ReplayPrefixEntry:
    """One committed Transition Record in the Replay Prefix / Plan Path.

    ``visible_draw_constraints`` contains only Gate-A+Gate-B-proven sequential draws,
    represented as ``(root-relative offset, observable_card_key)``.  No physical card
    identity is stored. ``visible_draw_tracking_blocked`` records an intervening
    DrawPile mutation that could not be explained safely; later transitions must not
    invent root-relative draw offsets past that point.
    """

    semantic_action: SemanticAction
    expected_signature: DecisionSignature
    target_index: "Optional[int]" = None
    target_enemy_index: "Optional[int]" = None
    visible_draw_constraints: VisibleDrawConstraints = ()
    visible_draw_tracking_blocked: bool = False


@dataclass
class DecisionContext:'''
text = regex_once(
    text,
    r"VisibleDrawConstraint = tuple\[int, str, str\].*?@dataclass\nclass DecisionContext:",
    replacement,
    "decision_context replay block",
)
write(path, text)


# ---------------------------------------------------------------------------
# main_loop.py: provide the immediate pre/post public states to Gate A/B.
# ---------------------------------------------------------------------------
path = "Combat/search/main_loop.py"
text = read(path)
text = replace_once(
    text,
    "    start_new_replay_prefix_from_stable,\n    visible_draw_constraints_from_committed_transition,\n)\n",
    "    start_new_replay_prefix_from_stable,\n)\n"
    "from search.replay_draw_restore import visible_draw_transition_evidence_from_committed_transition\n",
    "main_loop import",
)
old = '''        entry = ReplayPrefixEntry(
            semantic_action=planned_step.semantic_action,
            expected_signature=observed_signature,
            target_index=planned_step.target_index,
            target_enemy_index=planned_step.target_enemy_index,
            visible_draw_constraints=(
                visible_draw_constraints_from_committed_transition(
                    next_result,
                    loop_state.held_stable_snapshot,
                    loop_state.replay_prefix,
                    triggering_action=planned_step.semantic_action,
                )
                if loop_state.held_stable_snapshot is not None
                else ()
            ),
        )
'''
new = '''        draw_evidence = (
            visible_draw_transition_evidence_from_committed_transition(
                next_result,
                loop_state.held_stable_snapshot,
                loop_state.replay_prefix,
                triggering_action=planned_step.semantic_action,
                pre_battle_state=current_result,
            )
            if loop_state.held_stable_snapshot is not None
            else None
        )
        entry = ReplayPrefixEntry(
            semantic_action=planned_step.semantic_action,
            expected_signature=observed_signature,
            target_index=planned_step.target_index,
            target_enemy_index=planned_step.target_enemy_index,
            visible_draw_constraints=draw_evidence.constraints if draw_evidence is not None else (),
            visible_draw_tracking_blocked=(
                draw_evidence.blocks_later_pinning if draw_evidence is not None else False
            ),
        )
'''
text = replace_once(text, old, new, "main_loop evidence")
write(path, text)


# ---------------------------------------------------------------------------
# API/instance_combat.py: same immediate pre/post evidence on committed real actions.
# ---------------------------------------------------------------------------
path = "API/instance_combat.py"
text = read(path)
text = replace_once(
    text,
    "    start_new_replay_prefix_from_stable,\n    visible_draw_constraints_from_committed_transition,\n)\n",
    "    start_new_replay_prefix_from_stable,\n)\n"
    "from search.replay_draw_restore import visible_draw_transition_evidence_from_committed_transition\n",
    "instance_combat import",
)
text = replace_once(
    text,
    "        try:\n            next_state = self._session.step(self._root_state, chosen, target_index=target_index, target_enemy_index=target_enemy_index, stop_at_pending=True)\n",
    "        pre_state = self._root_state\n"
    "        try:\n"
    "            next_state = self._session.step(pre_state, chosen, target_index=target_index, target_enemy_index=target_enemy_index, stop_at_pending=True)\n",
    "instance_combat pre state",
)
old = '''                entry = ReplayPrefixEntry(
                    semantic_action=_semantic_action_for(chosen),
                    expected_signature=observed_signature,
                    target_index=target_index,
                    target_enemy_index=target_enemy_index,
                    visible_draw_constraints=visible_draw_constraints_from_committed_transition(
                        next_state,
                        self._held_stable_snapshot,
                        self._replay_prefix,
                        triggering_action=_semantic_action_for(chosen),
                    ),
                )
'''
new = '''                draw_evidence = visible_draw_transition_evidence_from_committed_transition(
                    next_state,
                    self._held_stable_snapshot,
                    self._replay_prefix,
                    triggering_action=_semantic_action_for(chosen),
                    pre_battle_state=pre_state,
                )
                entry = ReplayPrefixEntry(
                    semantic_action=_semantic_action_for(chosen),
                    expected_signature=observed_signature,
                    target_index=target_index,
                    target_enemy_index=target_enemy_index,
                    visible_draw_constraints=draw_evidence.constraints,
                    visible_draw_tracking_blocked=draw_evidence.blocks_later_pinning,
                )
'''
text = replace_once(text, old, new, "instance_combat evidence")
write(path, text)


# ---------------------------------------------------------------------------
# rng_hypothesis.py: materialize observable replay-equivalence keys, not InstanceId.
# ---------------------------------------------------------------------------
path = "Combat/search/rng_hypothesis.py"
text = read(path)
text = replace_once(
    text,
    "from search.decision_context import CombatStartReplayRoot, DecisionContext\n",
    "from search.decision_context import CombatStartReplayRoot, DecisionContext\n"
    "from search.replay_draw_restore import (\n"
    "    ObservableCardKey,\n"
    "    card_id_from_observable_key,\n"
    "    observable_card_key_from_snapshot,\n"
    "    snapshot_card_replay_internal_key,\n"
    ")\n",
    "rng_hypothesis import",
)
new_block = r'''def _pinned_prefix_visible_draw_constraints(
    decision_context: DecisionContext,
) -> tuple[tuple[int, ObservableCardKey], ...]:
    """Return the proven sequential-draw prefix before the first blocked mutation."""
    constraints: list[tuple[int, ObservableCardKey]] = []
    for entry in decision_context.replay_prefix:
        constraints.extend(entry.visible_draw_constraints)
        if getattr(entry, "visible_draw_tracking_blocked", False):
            break
    if not constraints:
        return ()
    if isinstance(decision_context.root_snapshot, CombatStartReplayRoot):
        raise ValueError("visible draw constraints require a Stable root snapshot")

    ordered = tuple(sorted(constraints, key=lambda item: item[0]))
    offsets = [offset for offset, _key in ordered]
    if len(set(offsets)) != len(offsets):
        raise ValueError("visible draw constraints contain duplicate root-relative offsets")
    if offsets != list(range(len(offsets))):
        raise ValueError(
            "visible draw constraints must form one contiguous prefix from Stable-root offset 0"
        )

    root_cards = list(decision_context.root_snapshot.Player.DrawPile)
    if len(ordered) > len(root_cards):
        raise ValueError("visible draw constraints exceed the Stable root DrawPile length")
    requested = Counter(key for _offset, key in ordered)
    available = Counter(observable_card_key_from_snapshot(card) for card in root_cards)
    missing = {key: count - available.get(key, 0) for key, count in requested.items() if count > available.get(key, 0)}
    if missing:
        raise ValueError(
            "visible draw constraints contain observable card state absent from the Stable root DrawPile: "
            f"{missing!r}"
        )
    return ordered


def _reorder_hypothesis_for_visible_draw_constraints(
    hypothesis: SearchHypothesisId,
    constraints: tuple[tuple[int, ObservableCardKey], ...],
) -> SearchHypothesisId:
    """Pin observed CardIds at proven offsets while preserving the candidate remainder."""
    remaining = list(hypothesis.ordered_draw_pile_card_ids)
    ordered: list[Optional[str]] = [None] * len(remaining)
    for offset, key in constraints:
        card_id = card_id_from_observable_key(key)
        if offset < 0 or offset >= len(ordered) or ordered[offset] is not None:
            raise ValueError("invalid or duplicate visible draw offset")
        try:
            remaining.remove(card_id)
        except ValueError as exc:
            raise ValueError(
                f"visible draw constraint requires CardId {card_id!r} that is absent "
                "from this hypothesis multiset"
            ) from exc
        ordered[offset] = card_id
    tail = iter(remaining)
    resolved = tuple(next(tail) if card_id is None else card_id for card_id in ordered)
    return dataclasses.replace(hypothesis, ordered_draw_pile_card_ids=resolved)


def _draw_pile_instances_for_hypothesis(
    root_snapshot: CombatStateSnapshot,
    ordered_card_ids: tuple[str, ...],
    pinned_observable_keys: tuple[tuple[int, ObservableCardKey], ...] = (),
) -> list[dict]:
    """Allocate concrete Snapshot cards, failing closed on hidden replay ambiguity."""
    root_cards = list(root_snapshot.Player.DrawPile)
    requested = Counter(ordered_card_ids)
    available = Counter(str(card.CardId) for card in root_cards)
    if requested != available:
        raise ValueError(
            "hypothesis DrawPile multiset does not match root snapshot Player.DrawPile multiset: "
            f"requested={dict(sorted(requested.items()))}, available={dict(sorted(available.items()))}"
        )

    offsets = [offset for offset, _key in pinned_observable_keys]
    if len(set(offsets)) != len(offsets):
        raise ValueError("pinned draw constraints contain duplicate offsets")

    pinned_counts = Counter(key for _offset, key in pinned_observable_keys)
    for key, count in pinned_counts.items():
        matches = [card for card in root_cards if observable_card_key_from_snapshot(card) == key]
        if len(matches) < count:
            raise ValueError(
                "pinned observable card state is absent from the root DrawPile in the required count"
            )
        internal_keys = {snapshot_card_replay_internal_key(card) for card in matches}
        if len(internal_keys) != 1:
            raise ValueError(
                "pinned observable card state maps to multiple hidden gameplay states; "
                "public evidence is insufficient for safe replay materialization"
            )

    allocated: list[Optional[CardInstanceSnapshot]] = [None] * len(ordered_card_ids)
    used_instance_ids: set[str] = set()
    for offset, key in sorted(pinned_observable_keys, key=lambda item: item[0]):
        if offset < 0 or offset >= len(allocated):
            raise ValueError(f"pinned draw offset {offset} is outside the root DrawPile")
        expected_card_id = ordered_card_ids[offset]
        if card_id_from_observable_key(key) != expected_card_id:
            raise ValueError(
                f"pinned observable card has CardId={card_id_from_observable_key(key)!r}, "
                f"but hypothesis position {offset} requires {expected_card_id!r}"
            )
        matches = sorted(
            (
                card
                for card in root_cards
                if str(card.InstanceId) not in used_instance_ids
                and observable_card_key_from_snapshot(card) == key
            ),
            key=_card_identity_key,
        )
        if not matches:
            raise ValueError("pinned observable card state was exhausted during concrete allocation")
        chosen = matches[0]
        allocated[offset] = chosen
        used_instance_ids.add(str(chosen.InstanceId))

    by_card_id: dict[str, deque[CardInstanceSnapshot]] = {}
    for card in sorted(
        (card for card in root_cards if str(card.InstanceId) not in used_instance_ids),
        key=_card_identity_key,
    ):
        by_card_id.setdefault(str(card.CardId), deque()).append(card)

    for offset, card_id in enumerate(ordered_card_ids):
        if allocated[offset] is not None:
            continue
        cards = by_card_id.get(card_id)
        if not cards:
            raise ValueError(
                f"hypothesis concrete-card allocation exhausted CardId {card_id!r} after observable-state pinning"
            )
        allocated[offset] = cards.popleft()
    if any(card is None for card in allocated):
        raise AssertionError("concrete DrawPile allocation left an unfilled position")
    return [dataclasses.asdict(card) for card in allocated if card is not None]


def derive_substituted_snapshot(
    root_snapshot: CombatStateSnapshot,
    hypothesis: SearchHypothesisId,
    *,
    pinned_draw_card_keys: tuple[tuple[int, ObservableCardKey], ...] = (),
) -> CombatStateSnapshot:
    """Return a Method-B derived snapshot with optional observed-draw state pins."""
    payload = _snapshot_plain_dict(root_snapshot)
    run_rng = payload["Rng"]["RunRng"]
    if "Shuffle" not in run_rng:
        raise ValueError('root_snapshot.Rng.RunRng is missing required "Shuffle" stream')
    run_rng["Shuffle"] = _rng_to_dict(hypothesis.rng)
    payload["Player"]["DrawPile"] = _draw_pile_instances_for_hypothesis(
        root_snapshot,
        hypothesis.ordered_draw_pile_card_ids,
        pinned_observable_keys=pinned_draw_card_keys,
    )
    return CombatStateSnapshot.from_dict(payload)


def build_grid('''
text = regex_once(
    text,
    r"def _pinned_prefix_visible_draw_constraints\(.*?\n\ndef build_grid\(",
    new_block,
    "rng_hypothesis pinning block",
)
old_apply = r'''def apply_hypothesis_to_context(
    decision_context: DecisionContext,
    hypothesis: SearchHypothesisId,
) -> DecisionContext:
    """Apply one CardId-level belief plus proven root-relative replay constraints.

    The hypothesis itself deliberately remains CardId-level. Exact internal Snapshot
    instance pins come only from mechanically-audited visible Replay Prefix transitions
    whose positions are proven relative to the Stable root. They constrain concrete
    allocation in the derived snapshot but are never added to ``SearchHypothesisId`` or
    exposed as hidden future order. Present-but-invalid constraints reject the hypothesis
    rather than silently restoring the old unpinned behavior.
    """
    if isinstance(decision_context.root_snapshot, CombatStartReplayRoot):
        raise TypeError("Genesis hypotheses must use derive_combat_start_replay_roots()")

    constraints = _pinned_prefix_visible_draw_constraints(decision_context)
    effective_hypothesis = hypothesis
    if constraints:
        effective_hypothesis = _reorder_hypothesis_for_visible_draw_constraints(
            hypothesis, constraints
        )

    context = dataclasses.replace(
        decision_context,
        root_snapshot=derive_substituted_snapshot(
            decision_context.root_snapshot,
            effective_hypothesis,
            pinned_draw_card_instances=tuple(
                (offset, instance_id) for offset, _card_id, instance_id in constraints
            ),
        ),
    )
    return with_search_hypothesis(context, effective_hypothesis)'''
new_apply = r'''def apply_hypothesis_to_context(
    decision_context: DecisionContext,
    hypothesis: SearchHypothesisId,
) -> DecisionContext:
    """Apply a CardId-level belief plus only the already-observed draw prefix.

    The hypothesis remains CardId-level. Replay constraints pin observable
    replay-equivalent card state at proven root-relative offsets; physical InstanceId is
    never part of the proof. Hidden gameplay-state ambiguity rejects materialization
    rather than guessing a concrete copy.
    """
    if isinstance(decision_context.root_snapshot, CombatStartReplayRoot):
        raise TypeError("Genesis hypotheses must use derive_combat_start_replay_roots()")

    constraints = _pinned_prefix_visible_draw_constraints(decision_context)
    effective_hypothesis = hypothesis
    if constraints:
        effective_hypothesis = _reorder_hypothesis_for_visible_draw_constraints(
            hypothesis, constraints
        )

    context = dataclasses.replace(
        decision_context,
        root_snapshot=derive_substituted_snapshot(
            decision_context.root_snapshot,
            effective_hypothesis,
            pinned_draw_card_keys=constraints,
        ),
    )
    return with_search_hypothesis(context, effective_hypothesis)'''
text = replace_once(text, old_apply, new_apply, "rng_hypothesis apply")
write(path, text)


# ---------------------------------------------------------------------------
# Pure unit regressions for Gate A/B and observable-state materialization.
# ---------------------------------------------------------------------------
unit_test = r'''"""Regressions for observable transfer proof and replay draw materialization."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

import pytest

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from battle_emulator import BattleState
from combat_state_snapshot import CardInstanceSnapshot, LocalCostModifierSnapshot, SerializableRngSnapshot
from search.decision_context import SemanticAction
import search.replay_draw_restore as replay_draw_restore
from search.replay_draw_restore import (
    card_id_from_observable_key,
    observable_card_key_from_public,
    observable_transfer_evidence,
    visible_draw_transition_evidence_from_committed_transition,
)
from search.rng_hypothesis import (
    SearchHypothesisId,
    _draw_pile_instances_for_hypothesis,
    _pinned_prefix_visible_draw_constraints,
    _reorder_hypothesis_for_visible_draw_constraints,
)


def _card(instance_id: str, card_id: str, *, upgraded: bool = False, cost: int = 1, modifiers=()) -> CardInstanceSnapshot:
    return CardInstanceSnapshot(
        InstanceId=instance_id,
        CardId=card_id,
        Type="Skill",
        Rarity="Common",
        Cost=cost,
        TargetType="None",
        IsUpgraded=upgraded,
        UpgradeLevel=1 if upgraded else 0,
        LocalCostModifiers=list(modifiers),
    )


def _public(card_id: str, *, upgraded: bool = False, cost: int = 1, option_id: str | None = None) -> dict:
    card = {
        "id": card_id,
        "type": "Skill",
        "rarity": "Common",
        "cost": cost,
        "targetType": "None",
        "upgraded": upgraded,
        "upgradeLevel": 1 if upgraded else 0,
        "tinkerTimeType": None,
        "tinkerTimeRider": None,
        "enchantment": None,
    }
    if option_id is not None:
        card["optionId"] = option_id
    return card


def _state(*, hand, draw, options=None, **pending_overrides) -> BattleState:
    engine = {
        "hand": list(hand),
        "drawPile": list(draw),
        "discardPile": [],
        "exhaustPile": [],
        "playPile": [],
    }
    if options is not None:
        pending = {
            "scope": "ActionContinuation",
            "choiceOperation": "discard",
            "sourceZone": "hand",
            "originEntityType": "card",
            "originEntityId": "ACROBATICS",
            "options": list(options),
        }
        pending.update(pending_overrides)
        engine["pendingChoice"] = pending
    return BattleState(engine_state=engine, is_terminal=False, outcome="in_progress", turn=1)


def _root(draw_cards):
    return SimpleNamespace(Player=SimpleNamespace(DrawPile=list(draw_cards)))


def _entry(*constraints, blocked: bool = False):
    return SimpleNamespace(
        visible_draw_constraints=tuple(constraints),
        visible_draw_tracking_blocked=blocked,
    )


def _rng() -> SerializableRngSnapshot:
    return SerializableRngSnapshot(Counter=1, State0=2, State1=3, State2=4, State3=5)


def _evidence(pre, post, prefix=()):
    return visible_draw_transition_evidence_from_committed_transition(
        post,
        _root([]),
        list(prefix),
        triggering_action=SemanticAction("card", "0:ANY_CARD"),
        pre_battle_state=pre,
    )


def test_acrobatics_shape_uses_generic_transfer_and_hand_append_order() -> None:
    h = _public("NEUTRALIZE", option_id="h")
    a_up = _public("A", upgraded=True, option_id="a-up")
    a = _public("A", option_id="a")
    b = _public("B", option_id="b")
    c = _public("C")
    pre = _state(hand=[_public("ACROBATICS"), h], draw=[a_up, a, b, c])
    post = _state(hand=[h, a_up, a, b], draw=[c], options=[h, a_up, a, b])

    result = _evidence(pre, post)
    assert result.blocks_later_pinning is False
    assert [offset for offset, _key in result.constraints] == [0, 1, 2]
    assert [card_id_from_observable_key(key) for _offset, key in result.constraints] == ["A", "A", "B"]
    assert [key[5] for _offset, key in result.constraints[:2]] == [True, False]


def test_card_allowlist_and_choice_semantics_are_not_safety_gates() -> None:
    h = _public("H")
    a = _public("A")
    pre = _state(hand=[_public("PREPARED"), h], draw=[a])
    post = _state(
        hand=[h, a],
        draw=[],
        options=[h, a],
        scope="SomethingElse",
        choiceOperation="select",
        sourceZone="mystery",
        originEntityId="NOT_PREPARED",
    )
    result = visible_draw_transition_evidence_from_committed_transition(
        post,
        _root([]),
        [],
        triggering_action=SemanticAction("potion", "unrelated"),
        pre_battle_state=pre,
    )
    assert len(result.constraints) == 1
    assert card_id_from_observable_key(result.constraints[0][1]) == "A"


def test_drawn_only_choice_passes_gate_a_and_reuses_same_order_prover_extension_point() -> None:
    a, b, c, d = (_public(name) for name in ("A", "B", "C", "D"))
    h = _public("H")
    pre = _state(hand=[h], draw=[a, b, c, d])
    post = _state(hand=[h], draw=[d], options=[a, b, c])

    transfer = observable_transfer_evidence(pre, post)
    assert transfer is not None
    assert transfer.removed_from_draw == Counter(
        observable_card_key_from_public(card) for card in (a, b, c)
    )
    assert transfer.explained_non_draw == Counter()
    assert replay_draw_restore.ordered_draw_sequence(pre, post, transfer) is None

    original = replay_draw_restore._ORDERED_DRAW_PROVERS
    try:
        def drawn_only_order(_pre, _post, evidence):
            return evidence.option_keys if not evidence.explained_non_draw else None

        replay_draw_restore._ORDERED_DRAW_PROVERS = (*original, drawn_only_order)
        result = _evidence(pre, post)
        assert [card_id_from_observable_key(key) for _offset, key in result.constraints] == ["A", "B", "C"]
    finally:
        replay_draw_restore._ORDERED_DRAW_PROVERS = original


def test_drawpile_publication_order_is_not_gate_a_evidence() -> None:
    h = _public("H")
    a, b, c = (_public(name) for name in ("A", "B", "C"))
    post = _state(hand=[h, a, b], draw=[c], options=[h, a, b])
    first = _evidence(_state(hand=[h], draw=[a, b, c]), post)
    second = _evidence(_state(hand=[h], draw=[b, a, c]), post)
    assert first.constraints == second.constraints


def test_unaccounted_drawpile_mutation_blocks_later_root_relative_pinning() -> None:
    a, b = _public("A"), _public("B")
    pre = _state(hand=[], draw=[a, b])
    post = _state(hand=[], draw=[b], options=[_public("X")])
    blocked = _evidence(pre, post)
    assert blocked.constraints == ()
    assert blocked.blocks_later_pinning is True

    later_pre = _state(hand=[], draw=[b])
    later_post = _state(hand=[b], draw=[], options=[b])
    later = _evidence(later_pre, later_post, [_entry(blocked=True)])
    assert later.constraints == ()
    assert later.blocks_later_pinning is True


def test_zero_draw_transition_does_not_block_cursor() -> None:
    a = _public("A")
    pre = _state(hand=[], draw=[a])
    post = _state(hand=[], draw=[a], options=[_public("X")])
    result = _evidence(pre, post)
    assert result.constraints == ()
    assert result.blocks_later_pinning is False


def test_consumer_stops_at_block_and_requires_contiguous_prefix() -> None:
    a_key = observable_card_key_from_public(_public("A"))
    b_key = observable_card_key_from_public(_public("B"))
    assert a_key is not None and b_key is not None
    root = _root([_card("i-a", "A"), _card("i-b", "B")])
    context = SimpleNamespace(
        root_snapshot=root,
        replay_prefix=[
            _entry((0, a_key)),
            _entry(blocked=True),
            _entry((1, b_key)),
        ],
    )
    assert _pinned_prefix_visible_draw_constraints(context) == ((0, a_key),)

    invalid = SimpleNamespace(root_snapshot=root, replay_prefix=[_entry((1, b_key))])
    with pytest.raises(ValueError, match="contiguous prefix"):
        _pinned_prefix_visible_draw_constraints(invalid)


def test_observable_state_pin_distinguishes_upgraded_copy_without_instance_contract() -> None:
    root = _root([
        _card("i-a", "A"),
        _card("i-a-up", "A", upgraded=True),
        _card("i-b", "B"),
    ])
    upgraded_key = observable_card_key_from_public(_public("A", upgraded=True))
    b_key = observable_card_key_from_public(_public("B"))
    assert upgraded_key is not None and b_key is not None
    constraints = ((0, upgraded_key), (2, b_key))
    raw = SearchHypothesisId(
        rng=_rng(), ordered_draw_pile_card_ids=("B", "A", "A"), hypothesis_index=7
    )
    pinned = _reorder_hypothesis_for_visible_draw_constraints(raw, constraints)
    assert pinned.ordered_draw_pile_card_ids == ("A", "A", "B")
    allocated = _draw_pile_instances_for_hypothesis(
        root,
        pinned.ordered_draw_pile_card_ids,
        pinned_observable_keys=constraints,
    )
    assert allocated[0]["CardId"] == "A"
    assert allocated[0]["IsUpgraded"] is True
    assert allocated[2]["CardId"] == "B"


def test_hidden_gameplay_state_ambiguity_fails_closed() -> None:
    mod_a = LocalCostModifierSnapshot(Amount=-1, Type="A", Expiration=1, IsReduceOnly=False)
    mod_b = LocalCostModifierSnapshot(Amount=-1, Type="B", Expiration=2, IsReduceOnly=False)
    root = _root([
        _card("i-a1", "A", cost=0, modifiers=[mod_a]),
        _card("i-a2", "A", cost=0, modifiers=[mod_b]),
    ])
    key = observable_card_key_from_public(_public("A", cost=0))
    assert key is not None
    with pytest.raises(ValueError, match="hidden gameplay states"):
        _draw_pile_instances_for_hypothesis(
            root,
            ("A", "A"),
            pinned_observable_keys=((0, key),),
        )
'''
write("Combat/tests/test_replay_prefix_visible_draw_pinning.py", unit_test)


api_test = r'''"""Paired-Emulator regression for observable-state replay draw pinning."""
from __future__ import annotations
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "Combat", _ROOT / "Run", _ROOT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from API.instance_combat import CombatInstance


def _config() -> dict:
    return {
        "instance_type": "combat", "character_id": "SILENT", "player_hp": 70, "player_max_hp": 70,
        "hand_cards": [{"card_id": "ACROBATICS", "is_upgraded": False}, {"card_id": "NEUTRALIZE", "is_upgraded": False}],
        "draw_pile_cards": [
            {"card_id": "DEFEND_SILENT", "is_upgraded": True}, {"card_id": "DEFEND_SILENT", "is_upgraded": False},
            {"card_id": "STRIKE_SILENT", "is_upgraded": False}, {"card_id": "SURVIVOR", "is_upgraded": False},
        ],
        "discard_pile": [], "exhaust_pile": [], "player_powers": [], "relics": [], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 999, "max_hp": 999}],
    }


def _legal_actions(response: dict) -> list[dict]:
    return response["masked_emulator_dto"]["legal_actions"]


def _find_card(response: dict, action_type: str, card_id: str) -> dict:
    return next(a for a in _legal_actions(response) if a["action_type"] == action_type and (a.get("parameters") or {}).get("cardId") == card_id)


def test_acrobatics_replay_prefix_pins_observable_state_without_instance_identity() -> None:
    inst = CombatInstance("acrobatics-observable-replay", _config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        acrobatics = _find_card(start, "card", "ACROBATICS")
        pending = inst.commit_action(start["decision_point_id"], acrobatics["action_id"])
        assert pending["status"] == "completed", pending
        candidates = [a for a in _legal_actions(pending) if a["action_type"] == "choice_card"]
        assert len(candidates) == 4, candidates

        constraints = inst._replay_prefix[0].visible_draw_constraints
        assert [offset for offset, _key in constraints] == [0, 1, 2]
        defend_keys = [key for _offset, key in constraints if key[0] == "DEFEND_SILENT"]
        assert len(defend_keys) == 2
        assert {bool(key[5]) for key in defend_keys} == {False, True}
        assert inst._replay_prefix[0].visible_draw_tracking_blocked is False

        for rng_id in range(1, 9):
            candidate = candidates[(rng_id - 1) % len(candidates)]
            result = inst.emulate_action(
                parent_branch_id="root", branch_id=f"acrobatics-hyp-{rng_id}", rng_id=rng_id,
                decision_point_id=pending["decision_point_id"], action_id=candidate["action_id"], simulation_options=None,
            )
            assert result["status"] == "completed", (rng_id, candidate, result)
            assert result.get("fault_kind") != "replay_mismatch", (rng_id, result)
    finally:
        inst.close()


def _run_all() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    failed = []
    for test in tests:
        try:
            test(); print(f"PASS {test.__name__}")
        except Exception:
            failed.append(test.__name__); print(f"FAIL {test.__name__}"); traceback.print_exc()
    print(f"\n{len(tests) - len(failed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
'''
write("API/tests/test_acrobatics_exact_instance_replay_pinning.py", api_test)

print("replay draw restore patch applied")
