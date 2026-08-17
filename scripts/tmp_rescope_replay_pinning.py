from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing anchor: {label}")
    return text.replace(old, new, 1)


# decision_context.py: remove the Emulator-side identity/provenance namespace and keep
# only a narrow, mechanically audited structural proof against the Held Stable root.
p = ROOT / "Combat/search/decision_context.py"
s = p.read_text()
s = s.replace("import hashlib\nimport hmac\n", "")
s = s.replace(
    '''    scenario_spec: dict\n    # Start-of-Combat has no Stable Snapshot/portable Snapshot InstanceId. Keep only\n    # visibility-safe (combat-global draw ordinal, visible option fingerprint) facts;\n    # each restarted session verifies them against its newly-issued exact token.\n    visible_draw_constraints: "tuple[tuple[int, str], ...]" = ()\n''',
    '''    scenario_spec: dict\n''',
)
start = s.index("def _public_card_instance_id(")
end = s.index("\n\n@dataclass\nclass DecisionContext:", start)
new_section = r'''def _visible_card_state_key_from_snapshot(card: object) -> tuple:
    """Gameplay-relevant card state that is already present in public card DTOs.

    Snapshot ``InstanceId`` and hidden pile position are deliberately excluded. If two
    root cards have the same key they are behaviorally indistinguishable for this replay
    pinning purpose; no client-visible physical-copy identity is needed.
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
    )


def _visible_card_state_key_from_option(option: object) -> "Optional[tuple]":
    if not isinstance(option, dict):
        return None
    card_id = option.get("id")
    card_type = option.get("type")
    rarity = option.get("rarity")
    cost = option.get("cost")
    target_type = option.get("targetType")
    upgrade_level = option.get("upgradeLevel", 0)
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
        bool(option.get("upgraded", False)),
        upgrade_level,
        option.get("tinkerTimeType"),
        option.get("tinkerTimeRider"),
    )


_AUDITED_DRAW_THEN_DISCARD_CARDS = frozenset({"ACROBATICS", "DAGGER_THROW", "PHOTON_CUT"})


def _semantic_card_id(action: SemanticAction) -> "Optional[str]":
    if action.action_type != "card":
        return None
    _slot, separator, card_id = action.semantic_key.partition(":")
    return card_id if separator and card_id else None


def _pending_source_card_id(pending: dict, triggering_action: SemanticAction) -> "Optional[str]":
    origin_type = pending.get("originEntityType")
    origin_id = pending.get("originEntityId")
    if isinstance(origin_id, str) and origin_id and (origin_type is None or str(origin_type).lower() == "card"):
        return origin_id.upper()
    source_effect = pending.get("sourceEffectId")
    if isinstance(source_effect, str) and source_effect.lower().startswith("card:"):
        return source_effect.split(":", 1)[1].upper()
    card_id = _semantic_card_id(triggering_action)
    return card_id.upper() if card_id else None


def _is_audited_zero_draw_target_prefix(
    replay_prefix: "list[ReplayPrefixEntry]", source_card_id: str
) -> bool:
    """Allow the mechanically-audited targeted-card hop and nothing broader.

    For a multi-enemy AnyEnemy card, Emulator ``BeginPlayCard`` publishes
    ``choice_target`` without enqueueing ``PlayCardAction``. Therefore that first
    transition consumes no draw RNG. The following ``choice_target`` step performs the
    real card resolution. This is the DaggerThrow/PhotonCut two-hop shape called out in
    the review. Any other non-empty prefix remains fail-closed.
    """
    if not replay_prefix:
        return True
    if len(replay_prefix) != 1:
        return False
    entry = replay_prefix[0]
    if entry.visible_draw_constraints:
        return False
    if (_semantic_card_id(entry.semantic_action) or "").upper() != source_card_id:
        return False
    candidate_multiset_value = entry.expected_signature.candidate_semantic_keys
    if not candidate_multiset_value:
        return False
    return all(
        isinstance(key, tuple) and len(key) >= 1 and key[0] == "choice_target"
        for key, _count in candidate_multiset_value
    )


def visible_draw_constraints_from_pending_choice(
    battle_state: "BattleState",
    root_snapshot: "CombatStateSnapshot",
    replay_prefix: "list[ReplayPrefixEntry]",
    *,
    triggering_action: SemanticAction,
) -> "tuple[tuple[int, str, str], ...]":
    """Return exact root-relative pins for the small mechanically-audited draw shape.

    This intentionally uses no Emulator-only ``cardInstanceId`` or HMAC namespace. The
    visible PendingChoice already contains the card's gameplay state, while the Held
    Stable root already contains the exact ordered ``CardInstanceSnapshot`` objects.
    Once the audited transition proves that the appended visible tail is the sequential
    draw prefix, each tail position maps directly to the root DrawPile at the same offset;
    the internal Snapshot ``InstanceId`` can then be retained only inside ReplayPrefix.

    Scope is deliberately narrow per review feedback: Acrobatics plus the structurally
    confirmed DaggerThrow/PhotonCut target-selection two-hop. Other cards, relic-origin
    choices, draw-pile reveal/tutor mechanics, later unknown-RNG prefix hops, and reshuffle
    cases fail closed. The consumer remains offset-aware/multi-entry so a future audited
    producer can add constraints without changing hypothesis materialization.
    """
    pending = battle_state.engine_state.get("pendingChoice")
    if not isinstance(pending, dict):
        return ()
    source_card_id = _pending_source_card_id(pending, triggering_action)
    if source_card_id not in _AUDITED_DRAW_THEN_DISCARD_CARDS:
        return ()
    if not _is_audited_zero_draw_target_prefix(replay_prefix, source_card_id):
        return ()
    if pending.get("scope") != "ActionContinuation":
        return ()
    if pending.get("choiceOperation") != "discard":
        return ()
    if pending.get("sourceZone") != "hand":
        return ()
    semantics = pending.get("choiceSemantics")
    if not isinstance(semantics, dict) or semantics.get("sourceZone") != "hand":
        return ()

    raw_options = pending.get("options")
    if not isinstance(raw_options, list) or not raw_options:
        return ()
    option_keys = [_visible_card_state_key_from_option(option) for option in raw_options]
    if any(key is None for key in option_keys):
        return ()

    player = getattr(root_snapshot, "Player", None)
    root_hand = list(getattr(player, "Hand", ()) or ())
    root_draw = list(getattr(player, "DrawPile", ()) or ())
    if not root_draw:
        return ()

    root_hand_keys = [_visible_card_state_key_from_snapshot(card) for card in root_hand]
    matching_missing_indices = []
    for missing_index, card in enumerate(root_hand):
        if str(getattr(card, "CardId", "")).upper() != source_card_id:
            continue
        expected_prefix = root_hand_keys[:missing_index] + root_hand_keys[missing_index + 1 :]
        if option_keys[: len(expected_prefix)] == expected_prefix:
            matching_missing_indices.append(missing_index)
    if len(matching_missing_indices) != 1:
        return ()

    existing_count = len(root_hand) - 1
    drawn_keys = option_keys[existing_count:]
    if not drawn_keys or len(drawn_keys) > len(root_draw):
        return ()

    constraints: list[tuple[int, str, str]] = []
    for offset, visible_key in enumerate(drawn_keys):
        root_card = root_draw[offset]
        if visible_key != _visible_card_state_key_from_snapshot(root_card):
            return ()
        constraints.append((offset, str(root_card.CardId), str(root_card.InstanceId)))
    return tuple(constraints)
'''
s = s[:start] + new_section + s[end:]
s = s.replace(
    '''        visible_constraints = visible_combat_start_draw_constraints(current_decision_result)\n        if visible_constraints:\n            combat_start_replay_root = dataclasses.replace(\n                combat_start_replay_root, visible_draw_constraints=visible_constraints\n            )\n''',
    '',
)
s = s.replace(
    '''        expected_draws = context.root_snapshot.visible_draw_constraints\n        if expected_draws and not _combat_start_draw_constraints_match(state, expected_draws):\n            return ReplayMismatch(\n                step_index=None,\n                stage="root_provenance",\n                detail="restarted Start-of-Combat visible draw provenance diverged from the recorded root",\n                diverged_fields=["visible_draw_constraints"],\n            )\n''',
    '',
)
s = s.replace(
    '''    stepping from Emulator-authored visibility-scoped draw provenance. Each value is a\n    ``(root-relative draw offset, CardId, CardInstanceSnapshot.InstanceId)`` tuple. The\n    producer/action type is deliberately absent: cards and relics share the same exact-\n    instance consumer. Search-internal/hypothesis steps leave it empty.\n''',
    '''    stepping, but only for a mechanically-audited visible draw-then-discard transition.\n    Each value is a ``(root-relative draw offset, CardId, internal Snapshot InstanceId)``\n    tuple. No new Emulator-side identity namespace is required; search-internal/\n    hypothesis steps leave it empty.\n''',
)
p.write_text(s)

# rng_hypothesis.py: retain the useful offset-aware consumer, remove Genesis propagation
# and public-token terminology.
p = ROOT / "Combat/search/rng_hypothesis.py"
s = p.read_text()
s = s.replace(
    '''                derived_replay_root=CombatStartReplayRoot(\n                    scenario_spec=spec,\n                    visible_draw_constraints=combat_start_replay_root.visible_draw_constraints,\n                ),\n''',
    '''                derived_replay_root=CombatStartReplayRoot(scenario_spec=spec),\n''',
)
s = s.replace(
    '''    The hypothesis itself deliberately remains CardId-level. Exact ``cardInstanceId``\n    values come only from a real, already-visible first Pending transition in the Replay\n    Prefix whose position was proven relative to the Stable root. They constrain concrete\n    allocation in the derived snapshot but are never added to ``SearchHypothesisId`` or\n    exposed as hidden future order.\n''',
    '''    The hypothesis itself deliberately remains CardId-level. Exact internal Snapshot\n    instance pins come only from mechanically-audited visible Replay Prefix transitions\n    whose positions are proven relative to the Stable root. They constrain concrete\n    allocation in the derived snapshot but are never added to ``SearchHypothesisId`` or\n    exposed as hidden future order.\n''',
)
s = s.replace("pinned draw constraints contain duplicate offsets or cardInstanceId values", "pinned draw constraints contain duplicate offsets or Snapshot instance ids")
s = s.replace("pinned cardInstanceId {instance_id!r} is absent from root DrawPile", "pinned Snapshot instance {instance_id!r} is absent from root DrawPile")
s = s.replace("pinned cardInstanceId {instance_id!r} has CardId={card.CardId!r}, ", "pinned Snapshot instance {instance_id!r} has CardId={card.CardId!r}, ")
p.write_text(s)

# API masking no longer needs a special concrete-card token rule.
p = ROOT / "API/masking.py"
s = p.read_text()
s = s.replace(
    '''explicit public allowlist before the recursive scrub continues. Emulator-only\n`cardInstanceId` is consumed by RL before masking for replay reconstruction and is then\nredacted here; Training never needs persistent concrete-card identity.\n''',
    '''explicit public allowlist before the recursive scrub continues.\n''',
)
s = s.replace('    "cardinstanceid", "card_instance_id", "unknown_fields",\n', '    "unknown_fields",\n')
p.write_text(s)

# Hosted gate: remove tests that existed only for the discarded public-token contract.
p = ROOT / ".github/workflows/paired-v07-counterpart-gate.yml"
s = p.read_text()
s = s.replace('            API/tests/test_visible_card_instance_masking.py \\\n', '')
s = s.replace('            Combat/tests/test_replay_prefix_draw_then_choose_generalization.py \\\n', '')
p.write_text(s)

# Narrow repo-local unit regression.
(ROOT / "Combat/tests/test_replay_prefix_visible_draw_pinning.py").write_text(r'''"""Repo-local regressions for the narrow replay-prefix draw pinning contract."""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

_COMBAT_DIR = Path(__file__).resolve().parents[1]
if str(_COMBAT_DIR) not in sys.path:
    sys.path.insert(0, str(_COMBAT_DIR))

from battle_emulator import BattleState
from combat_state_snapshot import CardInstanceSnapshot, SerializableRngSnapshot
from search.decision_context import SemanticAction, visible_draw_constraints_from_pending_choice
from search.rng_hypothesis import (
    SearchHypothesisId,
    _draw_pile_instances_for_hypothesis,
    _pinned_prefix_visible_draw_constraints,
    _reorder_hypothesis_for_visible_draw_constraints,
)


def _card(instance_id: str, card_id: str, *, upgraded: bool = False, cost: int = 1) -> CardInstanceSnapshot:
    return CardInstanceSnapshot(
        InstanceId=instance_id, CardId=card_id, Type="Skill", Rarity="Common", Cost=cost,
        TargetType="None", IsUpgraded=upgraded, UpgradeLevel=1 if upgraded else 0,
    )


def _option(card_id: str, *, upgraded: bool = False, cost: int = 1, option_id: str = "o") -> dict:
    return {
        "id": card_id, "type": "Skill", "rarity": "Common", "cost": cost,
        "targetType": "None", "upgraded": upgraded,
        "upgradeLevel": 1 if upgraded else 0, "tinkerTimeType": None,
        "tinkerTimeRider": None, "optionId": option_id,
    }


def _root(draw_cards, *, hand_cards=()):
    return SimpleNamespace(Player=SimpleNamespace(Hand=list(hand_cards), DrawPile=list(draw_cards)))


def _state(options, *, source_card: str = "ACROBATICS") -> BattleState:
    return BattleState(
        engine_state={"pendingChoice": {
            "scope": "ActionContinuation", "choiceOperation": "discard", "sourceZone": "hand",
            "originEntityType": "card", "originEntityId": source_card,
            "sourceEffectId": f"card:{source_card}", "choiceSemantics": {"sourceZone": "hand"},
            "options": list(options),
        }},
        is_terminal=False, outcome="in_progress", turn=1,
    )


def _target_prefix(card_id: str):
    return SimpleNamespace(
        semantic_action=SemanticAction("card", f"0:{card_id}"), visible_draw_constraints=(),
        expected_signature=SimpleNamespace(
            candidate_semantic_keys=((('choice_target', '0'), 1), (('choice_target', '1'), 1)),
        ),
    )


def _entry(*constraints):
    return SimpleNamespace(visible_draw_constraints=tuple(constraints))


def _rng() -> SerializableRngSnapshot:
    return SerializableRngSnapshot(Counter=1, State0=2, State1=3, State2=4, State3=5)


def test_acrobatics_uses_visible_state_and_root_position_without_public_instance_token() -> None:
    root = _root(
        [_card("i-a-up", "A", upgraded=True), _card("i-a", "A"), _card("i-b", "B"), _card("i-c", "C")],
        hand_cards=[_card("i-acro", "ACROBATICS"), _card("i-neutralize", "NEUTRALIZE")],
    )
    state = _state([
        _option("NEUTRALIZE", option_id="hand"), _option("A", upgraded=True, option_id="a-up"),
        _option("A", option_id="a"), _option("B", option_id="b"),
    ])
    assert all("cardInstanceId" not in option for option in state.engine_state["pendingChoice"]["options"])
    assert visible_draw_constraints_from_pending_choice(
        state, root, [], triggering_action=SemanticAction("card", "0:ACROBATICS")
    ) == ((0, "A", "i-a-up"), (1, "A", "i-a"), (2, "B", "i-b"))


def test_same_cardid_different_state_is_disambiguated_by_visible_card_state() -> None:
    root = _root(
        [_card("i-a-up", "A", upgraded=True), _card("i-a", "A")],
        hand_cards=[_card("i-acro", "ACROBATICS")],
    )
    assert visible_draw_constraints_from_pending_choice(
        _state([_option("A", upgraded=True)]), root, [],
        triggering_action=SemanticAction("card", "0:ACROBATICS"),
    ) == ((0, "A", "i-a-up"),)
    assert visible_draw_constraints_from_pending_choice(
        _state([_option("A", upgraded=False)]), root, [],
        triggering_action=SemanticAction("card", "0:ACROBATICS"),
    ) == ()


def test_non_audited_similar_shape_fails_closed() -> None:
    root = _root([_card("i-a", "A")], hand_cards=[_card("i-prepared", "PREPARED")])
    assert visible_draw_constraints_from_pending_choice(
        _state([_option("A")], source_card="PREPARED"), root, [],
        triggering_action=SemanticAction("card", "0:PREPARED"),
    ) == ()


def test_targeted_dagger_throw_two_hop_is_the_supported_nonempty_prefix_shape() -> None:
    root = _root(
        [_card("i-a", "A")],
        hand_cards=[_card("i-dagger", "DAGGER_THROW"), _card("i-neutralize", "NEUTRALIZE")],
    )
    state = _state([_option("NEUTRALIZE", option_id="hand"), _option("A", option_id="draw")], source_card="DAGGER_THROW")
    prefix = [_target_prefix("DAGGER_THROW")]
    assert visible_draw_constraints_from_pending_choice(
        state, root, prefix, triggering_action=SemanticAction("choice_target", "0")
    ) == ((0, "A", "i-a"),)

    unsafe = [SimpleNamespace(
        semantic_action=SemanticAction("card", "0:STRIKE_SILENT"), visible_draw_constraints=(),
        expected_signature=prefix[0].expected_signature,
    )]
    assert visible_draw_constraints_from_pending_choice(
        state, root, unsafe, triggering_action=SemanticAction("choice_target", "0")
    ) == ()


def test_offset_aware_consumer_retains_multi_entry_forward_compatibility() -> None:
    root = _root([_card("i-a", "A"), _card("i-b", "B"), _card("i-c", "C")])
    context = SimpleNamespace(
        root_snapshot=root,
        replay_prefix=[_entry((2, "C", "i-c")), _entry(), _entry((0, "A", "i-a"))],
    )
    assert _pinned_prefix_visible_draw_constraints(context) == ((0, "A", "i-a"), (2, "C", "i-c"))


def test_hypothesis_reorder_and_allocator_pin_internal_snapshot_instances_at_offsets() -> None:
    root = _root([_card("i-a", "A"), _card("i-a-up", "A", upgraded=True), _card("i-b", "B")])
    constraints = ((0, "A", "i-a-up"), (2, "B", "i-b"))
    raw = SearchHypothesisId(rng=_rng(), ordered_draw_pile_card_ids=("B", "A", "A"), hypothesis_index=7)
    pinned = _reorder_hypothesis_for_visible_draw_constraints(raw, constraints)
    assert pinned.ordered_draw_pile_card_ids == ("A", "A", "B")
    allocated = _draw_pile_instances_for_hypothesis(
        root, pinned.ordered_draw_pile_card_ids, pinned_instances=((0, "i-a-up"), (2, "i-b"))
    )
    assert [card["InstanceId"] for card in allocated] == ["i-a-up", "i-a", "i-b"]
    assert allocated[0]["IsUpgraded"] is True


def test_consumer_rejects_duplicate_offsets_or_mismatched_snapshot_instance() -> None:
    root = _root([_card("i-a", "A"), _card("i-b", "B")])
    duplicate = SimpleNamespace(root_snapshot=root, replay_prefix=[_entry((0, "A", "i-a"), (0, "B", "i-b"))])
    assert _pinned_prefix_visible_draw_constraints(duplicate) == ()
    mismatch = SimpleNamespace(root_snapshot=root, replay_prefix=[_entry((0, "A", "i-b"))])
    assert _pinned_prefix_visible_draw_constraints(mismatch) == ()
''')

# Paired real-Emulator regression, with no HMAC/session-namespace assumptions.
(ROOT / "API/tests/test_acrobatics_exact_instance_replay_pinning.py").write_text(r'''"""Paired-Emulator regression for local structural exact-instance replay pinning."""
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


def test_acrobatics_replay_prefix_pins_structural_state_without_emulator_identity_extension() -> None:
    inst = CombatInstance("acrobatics-structural-replay", _config(), worker_count=2)
    try:
        start = inst.start_instance_response()
        root_snapshot = inst._held_stable_snapshot
        assert root_snapshot is not None
        acrobatics = _find_card(start, "card", "ACROBATICS")
        pending = inst.commit_action(start["decision_point_id"], acrobatics["action_id"])
        assert pending["status"] == "completed", pending
        candidates = [a for a in _legal_actions(pending) if a["action_type"] == "choice_card"]
        assert len(candidates) == 4, candidates

        constraints = inst._replay_prefix[0].visible_draw_constraints
        assert [offset for offset, _card_id, _instance_id in constraints] == [0, 1, 2]
        root_by_instance = {str(card.InstanceId): card for card in root_snapshot.Player.DrawPile}
        defend_ids = [iid for _offset, card_id, iid in constraints if card_id == "DEFEND_SILENT"]
        assert len(defend_ids) == 2 and defend_ids[0] != defend_ids[1]
        assert {bool(root_by_instance[iid].IsUpgraded) for iid in defend_ids} == {False, True}

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
''')

(ROOT / "Outputs/reports/replay_prefix_draw_pinning_design_20260816.md").write_text(r'''# Replay-prefix draw pinning — scoped design after review

Status: implemented in STS2_RL PR #64. No new STS2_Emulator runtime identity contract is required.

## Problem

A real committed prefix can draw cards and stop at a discard PendingChoice. Replaying the same prefix under a different CardId-level RNG hypothesis can otherwise reach a different candidate set and fail `candidate_semantic_keys` verification. The harvested reproduction is Silent / `ACROBATICS` / seed 101. `DAGGER_THROW` and `PHOTON_CUT` have the same relevant shape after their multi-enemy target-selection hop.

The earlier redesign added a public `cardInstanceId` HMAC namespace plus draw-ordinal suffixes. Review found that correct but unnecessarily broad. The existing Stable root and visible card DTO already contain enough information for the confirmed bug.

## Final contract

`ReplayPrefixEntry.visible_draw_constraints` is RL-internal only:

```text
(root-relative draw offset, CardId, CardInstanceSnapshot.InstanceId)
```

No concrete identity is added to Emulator DTOs or Training DTOs.

For the mechanically audited draw-then-discard shape, RL proves all of the following:

1. source card is one of the audited effects (`ACROBATICS`, `DAGGER_THROW`, `PHOTON_CUT`);
2. the result is an ActionContinuation discard choice from Hand;
3. the played source card is the one root-Hand card that disappeared;
4. every other pre-existing Hand card remains the visible option prefix with the same public gameplay state;
5. the appended visible tail has the same gameplay state as the exact ordered cards at root DrawPile offsets `0..N-1`.

Once item 5 is proven, the root Snapshot itself identifies the exact internal card at each offset. No HMAC/public physical-copy token is needed. Upgrade level, cost, type, target, rarity and Tinker state are compared; therefore two copies sharing a CardId but differing in gameplay state cannot be silently swapped. Copies that are structurally identical are behaviorally interchangeable for this purpose.

For the multi-enemy targeted cards, Emulator `BeginPlayCard` publishes `choice_target` before enqueueing `PlayCardAction`. #64 accepts exactly that one audited zero-draw prefix hop, then applies the same root-offset proof when the card resolution reaches discard. Other non-empty unknown-RNG prefixes fail closed.

## Hypothesis materialization

`SearchHypothesisId` stays CardId-level. The consumer remains general enough to collect verified constraints from arbitrary ReplayPrefix entries and pin them by root-relative offset. This forward-compatible offset handling is intentionally retained even though the currently confirmed cases have at most one draw-constrained transition per Replay Prefix window.

Constrained CardIds and internal Snapshot instances are fixed at proven offsets; all remaining concrete cards are allocated by the existing `_card_identity_key` canonical ordering.

## Deliberate scope boundary

This fix does **not** infer draw provenance for arbitrary similar-looking card or relic choices. In particular, Start-of-Combat relic choices and direct draw-pile reveal/tutor mechanics need a separate mechanically audited contract if they are later shown to cause the same replay fault. They are not widened into this bug fix merely for symmetry.

That scope reduction is intentional: it addresses the reproduced fault and confirmed 2-hop variant while avoiding a new Emulator-visible identity/session/Restore namespace.

## Regression coverage

- `Combat/tests/test_replay_prefix_visible_draw_pinning.py`: mixed-hand Acrobatics, duplicate CardId state, fail-closed unaudited shapes, DaggerThrow target hop, offset-aware consumer, exact internal allocation.
- `API/tests/test_acrobatics_exact_instance_replay_pinning.py`: paired real-Emulator path and multiple `rng_id` hypotheses without replay mismatch.

STS2_Emulator PR #25 is therefore re-scoped to documentation/coordination only; its HMAC `cardInstanceId` implementation is not part of the final contract.
''')

# Delete over-broad contract-only tests and all temporary export machinery.
for rel in [
    "API/tests/test_visible_card_instance_masking.py",
    "Combat/tests/test_replay_prefix_draw_then_choose_generalization.py",
    ".github/workflows/tmp-export-relic-pinning-sources.yml",
    ".github/workflows/tmp-export-overspec-rescope.yml",
]:
    q = ROOT / rel
    if q.exists():
        q.unlink()

print("rescope patch applied")
