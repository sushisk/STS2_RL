from pathlib import Path
import re


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing anchor: {label}")
    return text.replace(old, new, 1)


# Combat/search/replay_draw_restore.py
path = Path("Combat/search/replay_draw_restore.py")
text = path.read_text()
text, count = re.subn(
    r'^"""Observable-state proof for replay-prefix DrawPile reconstruction\..*?"""\n',
    '"""Infer already-observed draw order for replay-prefix reconstruction."""\n',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit("failed to shorten replay_draw_restore module docstring")
text = replace_once(
    text,
    '    removed_from_draw: Counter\n    explained_non_draw: Counter\n    option_keys: tuple[ObservableCardKey, ...]\n',
    '    removed_from_draw: Counter\n    option_keys: tuple[ObservableCardKey, ...]\n',
    "ObservableTransferEvidence fields",
)
text = replace_once(
    text,
    '    return ObservableTransferEvidence(\n        removed_from_draw=removed,\n        explained_non_draw=explained_non_draw,\n        option_keys=options,\n    )\n',
    '    return ObservableTransferEvidence(removed_from_draw=removed, option_keys=options)\n',
    "ObservableTransferEvidence constructor",
)
text, count = re.subn(
    r'\n\ndef ordered_draw_sequence\(.*?\n\ndef _stable_root_draw_slice\(',
    '\n\ndef _stable_root_draw_slice(',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit("failed to remove redundant ordered_draw_sequence wrapper")
text = replace_once(
    text,
    '    *,\n    triggering_action: object,\n    pre_battle_state: object,\n',
    '    *,\n    pre_battle_state: object,\n',
    "visible draw evidence signature",
)
text = replace_once(text, '    del triggering_action\n\n', '', "unused triggering_action")
text, count = re.subn(
    r'\n\ndef visible_draw_constraints_from_committed_transition\(.*\Z',
    '\n',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit("failed to remove compatibility wrapper")
path.write_text(text)


# Combat/search/decision_context.py
path = Path("Combat/search/decision_context.py")
text = path.read_text()
text = replace_once(
    text,
    'from search.replay_draw_restore import (\n    VisibleDrawConstraint,\n    VisibleDrawConstraints,\n    VisibleDrawTransitionEvidence,\n    visible_draw_constraints_from_committed_transition,\n    visible_draw_transition_evidence_from_committed_transition,\n)\n',
    'from search.replay_draw_restore import VisibleDrawConstraints\n',
    "decision_context replay imports",
)
text, count = re.subn(
    r'class ReplayPrefixEntry:\n    """One committed Transition Record in the Replay Prefix / Plan Path\..*?    """\n',
    'class ReplayPrefixEntry:\n    """One committed replay step plus any proven root-relative draw prefix."""\n',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit("failed to shorten ReplayPrefixEntry docstring")
path.write_text(text)


# Combat/search/main_loop.py
path = Path("Combat/search/main_loop.py")
text = path.read_text()
text = replace_once(
    text,
    '                triggering_action=planned_step.semantic_action,\n                pre_battle_state=current_result,\n',
    '                pre_battle_state=current_result,\n',
    "main_loop obsolete triggering_action",
)
path.write_text(text)


# API/instance_combat.py
path = Path("API/instance_combat.py")
text = path.read_text()
old = '''                draw_evidence = visible_draw_transition_evidence_from_committed_transition(\n                    next_state,\n                    self._held_stable_snapshot,\n                    self._replay_prefix,\n                    triggering_action=_semantic_action_for(chosen),\n                    pre_battle_state=pre_state,\n                )\n                entry = ReplayPrefixEntry(\n                    semantic_action=_semantic_action_for(chosen),\n'''
new = '''                semantic_action = _semantic_action_for(chosen)\n                draw_evidence = visible_draw_transition_evidence_from_committed_transition(\n                    next_state,\n                    self._held_stable_snapshot,\n                    self._replay_prefix,\n                    pre_battle_state=pre_state,\n                )\n                entry = ReplayPrefixEntry(\n                    semantic_action=semantic_action,\n'''
text = replace_once(text, old, new, "instance_combat duplicate semantic action")
path.write_text(text)


# Combat/search/rng_hypothesis.py
path = Path("Combat/search/rng_hypothesis.py")
text = path.read_text()
start = text.index("def _pinned_prefix_visible_draw_constraints(")
end = text.index("def _draw_pile_instances_for_hypothesis(")
replacement = '''def _pinned_prefix_visible_draw_constraints(\n    decision_context: DecisionContext,\n) -> tuple[tuple[int, ObservableCardKey], ...]:\n    constraints: list[tuple[int, ObservableCardKey]] = []\n    for entry in decision_context.replay_prefix:\n        constraints.extend(entry.visible_draw_constraints)\n        if entry.visible_draw_tracking_blocked:\n            break\n\n    ordered = tuple(sorted(constraints, key=lambda item: item[0]))\n    if [offset for offset, _key in ordered] != list(range(len(ordered))):\n        raise ValueError(\n            "visible draw constraints must form one contiguous prefix from Stable-root offset 0"\n        )\n    return ordered\n\n\ndef _reorder_hypothesis_for_visible_draw_constraints(\n    hypothesis: SearchHypothesisId,\n    constraints: tuple[tuple[int, ObservableCardKey], ...],\n) -> SearchHypothesisId:\n    remaining = list(hypothesis.ordered_draw_pile_card_ids)\n    pinned: list[str] = []\n    for offset, key in constraints:\n        if offset != len(pinned):\n            raise ValueError("visible draw constraints must be a contiguous prefix")\n        card_id = card_id_from_observable_key(key)\n        try:\n            remaining.remove(card_id)\n        except ValueError as exc:\n            raise ValueError(\n                f"visible draw constraint requires CardId {card_id!r} absent from the hypothesis"\n            ) from exc\n        pinned.append(card_id)\n    return dataclasses.replace(\n        hypothesis,\n        ordered_draw_pile_card_ids=tuple(pinned + remaining),\n    )\n\n\n'''
text = text[:start] + replacement + text[end:]
path.write_text(text)


# Combat/tests/test_replay_prefix_visible_draw_pinning.py
path = Path("Combat/tests/test_replay_prefix_visible_draw_pinning.py")
text = path.read_text()
text = replace_once(
    text,
    '''    transfer = observable_transfer_evidence(pre, post)\n    assert transfer is not None\n    sequence = replay_draw_restore.ordered_draw_sequence(pre, post, transfer)\n    assert sequence is not None\n    assert [card_id_from_observable_key(key) for key in sequence] == ["A", "B", "C"]\n\n    result = _evidence(pre, post)\n''',
    '''    assert observable_transfer_evidence(pre, post) is not None\n\n    result = _evidence(pre, post)\n''',
    "drawn-only redundant direct Gate B assertion",
)
text = text.replace('        triggering_action=SemanticAction("card", "0:ANY_CARD"),\n', '')
text = text.replace('        triggering_action=SemanticAction("potion", "unrelated"),\n', '')
text = text.replace('from search.decision_context import SemanticAction\n', '')
text = replace_once(
    text,
    '    constraints = ((0, upgraded_key), (2, b_key))\n',
    '    constraints = ((0, upgraded_key), (1, b_key))\n',
    "contiguous pinning test constraints",
)
text = replace_once(
    text,
    '    assert pinned.ordered_draw_pile_card_ids == ("A", "A", "B")\n',
    '    assert pinned.ordered_draw_pile_card_ids == ("A", "B", "A")\n',
    "contiguous reordered hypothesis",
)
text = replace_once(
    text,
    '    assert allocated[2]["CardId"] == "B"\n',
    '    assert allocated[1]["CardId"] == "B"\n',
    "contiguous allocated position",
)
text = text.replace("import search.replay_draw_restore as replay_draw_restore\n", "")
path.write_text(text)
