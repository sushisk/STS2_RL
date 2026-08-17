from pathlib import Path
import re


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        raise SystemExit(f"missing anchor: {label}")
    return text.replace(old, new, 1)


# ---------------------------------------------------------------------------
# Combat/search/replay_draw_restore.py
# ---------------------------------------------------------------------------
path = Path("Combat/search/replay_draw_restore.py")
text = path.read_text()
text = replace_once(
    text,
    '"""Infer already-observed draw order for replay-prefix reconstruction."""',
    '"""Infer already-observed draw order from public replay state."""',
    "module docstring",
)

# Remove the Held-Stable-Snapshot answer-key helpers entirely.
text, count = re.subn(
    r'\n\ndef _stable_root_draw_slice\(.*?\n\ndef visible_draw_transition_evidence_from_committed_transition\(',
    '\n\ndef visible_draw_transition_evidence_from_committed_transition(',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit("failed to remove Stable-root order sentinel helpers")

old_sig = '''def visible_draw_transition_evidence_from_committed_transition(\n    post_state: object,\n    root_snapshot: object,\n    replay_prefix: list[object],\n    *,\n    pre_battle_state: object,\n) -> VisibleDrawTransitionEvidence:\n    \"\"\"Return generic Gate-A+B-proven root-relative draw constraints.\n\n    Gate B derives order from PendingChoice options after removing ``E``. The inferred\n    order is independently checked against Held Stable Snapshot at the current root-\n    relative cursor. Snapshot order is never used as a fallback derivation when the two\n    disagree.\n    \"\"\"\n'''
new_sig = '''def visible_draw_transition_evidence_from_committed_transition(\n    post_state: object,\n    replay_prefix: list[object],\n    *,\n    pre_battle_state: object,\n) -> VisibleDrawTransitionEvidence:\n    \"\"\"Return Gate-A+B-proven root-relative draw constraints from public state only.\n\n    Gate B interprets the unique ``R`` subsequence of ordered PendingChoice options as\n    sequential draw order. That ordering is an Emulator publication contract; this\n    function deliberately does not consult Snapshot DrawPile order, RNG state, raw draw\n    history, or physical card identity as an answer key.\n    \"\"\"\n'''
text = replace_once(text, old_sig, new_sig, "evidence signature/docstring")

# Remove the Stable-root validation block; keep the public option-derived sequence.
start = text.index('    cursor = sum(\n', text.index('def visible_draw_transition_evidence_from_committed_transition('))
marker = '    return VisibleDrawTransitionEvidence(\n        constraints=tuple(\n            (cursor + offset, key)\n            for offset, key in enumerate(sequence)\n        )\n    )\n'
end = text.index(marker, start) + len(marker)
replacement = '''    cursor = sum(\n        len(getattr(entry, \"visible_draw_constraints\", ()) or ())\n        for entry in replay_prefix\n    )\n    return VisibleDrawTransitionEvidence(\n        constraints=tuple(\n            (cursor + offset, key)\n            for offset, key in enumerate(sequence)\n        )\n    )\n'''
text = text[:start] + replacement + text[end:]
path.write_text(text)


# ---------------------------------------------------------------------------
# Combat/search/decision_context.py
# ---------------------------------------------------------------------------
path = Path("Combat/search/decision_context.py")
text = path.read_text()
text = replace_once(
    text,
    'from search.replay_draw_restore import VisibleDrawConstraints\n',
    'from search.replay_draw_restore import (\n    VisibleDrawConstraints,\n    visible_draw_transition_evidence_from_committed_transition,\n)\n',
    "decision_context replay import",
)
text = replace_once(
    text,
    '    `step_index` is the 0-based index into `context.replay_prefix` where the mismatch\n    was detected, or `None` for a CTX_SIG_CHECK (arrival) mismatch detected only after\n    the whole prefix replayed successfully. `stage` distinguishes which SUB_REPLAY\n    decision node produced this outcome.\"\"\"\n',
    '    `step_index` is the 0-based index into `context.replay_prefix` where the mismatch\n    was detected, or `None` for a CTX_SIG_CHECK (arrival) mismatch detected only after\n    the whole prefix replayed successfully. `stage` distinguishes resolution, ordinary\n    signature, public draw-evidence, and final-context mismatches.\"\"\"\n',
    "ReplayMismatch docstring",
)
text = replace_once(
    text,
    '    stage: str  # "resolve" | "signature" | "context_signature"\n',
    '    stage: str  # "resolve" | "signature" | "visible_draw_evidence" | "context_signature"\n',
    "ReplayMismatch stage comment",
)

old_step = '''        state = session.step(\n            state,\n            resolved_action,\n            target_index=entry.target_index,\n            target_enemy_index=entry.target_enemy_index,\n            stop_at_pending=True,\n        )\n\n        observed = DecisionSignature.from_battle_state(\n'''
new_step = '''        pre_step_state = state\n        state = session.step(\n            state,\n            resolved_action,\n            target_index=entry.target_index,\n            target_enemy_index=entry.target_enemy_index,\n            stop_at_pending=True,\n        )\n\n        observed = DecisionSignature.from_battle_state(\n'''
text = replace_once(text, old_step, new_step, "capture replay pre-step public state")

anchor = '''        if not observed.matches_for_replay(entry.expected_signature):\n            return ReplayMismatch(\n                step_index=index,\n                stage=\"signature\",\n                detail=f\"observed post-step signature diverged from replay prefix entry {index}\",\n                diverged_fields=observed.diff_for_replay(entry.expected_signature),\n            )\n        last_observed = observed\n'''
replacement = '''        if not observed.matches_for_replay(entry.expected_signature):\n            return ReplayMismatch(\n                step_index=index,\n                stage=\"signature\",\n                detail=f\"observed post-step signature diverged from replay prefix entry {index}\",\n                diverged_fields=observed.diff_for_replay(entry.expected_signature),\n            )\n\n        # Re-derive Gate A/B from the replayed public pre/post states. This is a stronger\n        # check than candidate semantic keys alone because observable card state (upgrade,\n        # cost, tinker/enchantment, etc.) participates in the evidence. It deliberately\n        # does not inspect hidden Snapshot DrawPile order.\n        replayed_draw_evidence = visible_draw_transition_evidence_from_committed_transition(\n            state,\n            context.replay_prefix[:index],\n            pre_battle_state=pre_step_state,\n        )\n        draw_evidence_diffs: list[str] = []\n        if replayed_draw_evidence.constraints != entry.visible_draw_constraints:\n            draw_evidence_diffs.append(\"visible_draw_constraints\")\n        if replayed_draw_evidence.blocks_later_pinning != entry.visible_draw_tracking_blocked:\n            draw_evidence_diffs.append(\"visible_draw_tracking_blocked\")\n        if draw_evidence_diffs:\n            return ReplayMismatch(\n                step_index=index,\n                stage=\"visible_draw_evidence\",\n                detail=(\n                    f\"replayed public Gate A/B evidence diverged from Replay Prefix entry {index}\"\n                ),\n                diverged_fields=draw_evidence_diffs,\n            )\n        last_observed = observed\n'''
text = replace_once(text, anchor, replacement, "public replay evidence validation")
path.write_text(text)


# ---------------------------------------------------------------------------
# Combat/search/main_loop.py
# ---------------------------------------------------------------------------
path = Path("Combat/search/main_loop.py")
text = path.read_text()
text = replace_once(
    text,
    '''            visible_draw_transition_evidence_from_committed_transition(\n                next_result,\n                loop_state.held_stable_snapshot,\n                loop_state.replay_prefix,\n                pre_battle_state=current_result,\n            )\n''',
    '''            visible_draw_transition_evidence_from_committed_transition(\n                next_result,\n                loop_state.replay_prefix,\n                pre_battle_state=current_result,\n            )\n''',
    "main_loop evidence call",
)
path.write_text(text)


# ---------------------------------------------------------------------------
# API/instance_combat.py
# ---------------------------------------------------------------------------
path = Path("API/instance_combat.py")
text = path.read_text()
text = replace_once(
    text,
    '''                draw_evidence = visible_draw_transition_evidence_from_committed_transition(\n                    next_state,\n                    self._held_stable_snapshot,\n                    self._replay_prefix,\n                    pre_battle_state=pre_state,\n                )\n''',
    '''                draw_evidence = visible_draw_transition_evidence_from_committed_transition(\n                    next_state,\n                    self._replay_prefix,\n                    pre_battle_state=pre_state,\n                )\n''',
    "instance_combat evidence call",
)
path.write_text(text)


# ---------------------------------------------------------------------------
# Combat/tests/test_replay_prefix_visible_draw_pinning.py
# ---------------------------------------------------------------------------
path = Path("Combat/tests/test_replay_prefix_visible_draw_pinning.py")
text = path.read_text()
text, count = re.subn(
    r'\n\ndef _root_from_public\(cards\):.*?\n\ndef _evidence\(pre, post, prefix=\(\), \*, root_draw=None\):.*?\n\n\ndef test_acrobatics_shape_',
    '''\n\ndef _evidence(pre, post, prefix=()):\n    return visible_draw_transition_evidence_from_committed_transition(\n        post,\n        list(prefix),\n        pre_battle_state=pre,\n    )\n\n\ndef test_acrobatics_shape_''',
    text,
    count=1,
    flags=re.S,
)
if count != 1:
    raise SystemExit("failed to simplify test evidence helper")

text = replace_once(
    text,
    '''    result = visible_draw_transition_evidence_from_committed_transition(\n        post,\n        _root_from_public([a]),\n        [],\n        pre_battle_state=pre,\n    )\n''',
    '''    result = visible_draw_transition_evidence_from_committed_transition(\n        post,\n        [],\n        pre_battle_state=pre,\n    )\n''',
    "direct evidence test call",
)
text = text.replace(', root_draw=root_draw)', ')')
text = text.replace(', root_draw=[a, b, c, d])', ')')
text = text.replace(', root_draw=[a, b, c])', ')')

old_test = '''def test_gate_b_rejects_wrong_option_order_against_stable_root_and_records_error() -> None:\n    a, b, c, d = (_public(name) for name in (\"A\", \"B\", \"C\", \"D\"))\n    pre = _state(hand=[], draw=[a, b, c, d])\n    post = _state(hand=[], draw=[d], options=[c, b, a])\n\n    result = _evidence(pre, post)\n    assert result.constraints == ()\n    assert result.blocks_later_pinning is True\n    assert result.tracking_error is not None\n    assert \"option-order contract rejected\" in result.tracking_error\n    assert \"['C', 'B', 'A']\" in result.tracking_error\n    assert \"['A', 'B', 'C']\" in result.tracking_error\n'''
new_test = '''def test_gate_b_uses_public_option_order_without_hidden_snapshot_answer_key() -> None:\n    a, b, c, d = (_public(name) for name in (\"A\", \"B\", \"C\", \"D\"))\n    pre = _state(hand=[], draw=[a, b, c, d])\n    post = _state(hand=[], draw=[d], options=[c, b, a])\n\n    result = _evidence(pre, post)\n    assert result.blocks_later_pinning is False\n    assert result.tracking_error is None\n    assert [card_id_from_observable_key(key) for _offset, key in result.constraints] == [\"C\", \"B\", \"A\"]\n'''
text = replace_once(text, old_test, new_test, "replace hidden sentinel regression")

# The root_draw local is no longer used in the publication-order test.
text = text.replace('    root_draw = [a, b, c]\n', '')
path.write_text(text)


# ---------------------------------------------------------------------------
# Outputs/reports/replay_prefix_draw_pinning_design_20260816.md
# Replace the concise report wholesale so stale sentinel language cannot survive.
# ---------------------------------------------------------------------------
path = Path("Outputs/reports/replay_prefix_draw_pinning_design_20260816.md")
path.write_text('''# Replay-prefix DrawPile restoration — current design\n\nStatus: implemented in STS2_RL PR #64. Replay safety is derived from public observation state; hidden Stable Snapshot DrawPile order is not a proof or validation input.\n\n## Invariants\n\n1. **Gate A — unordered transfer**\n   - `R = pre.drawPile - post.drawPile` using multisets of `observable_card_key`.\n   - `R` must be non-empty and represented in `pendingChoice.options`.\n   - Remaining option occurrences `E` must be explainable by cards that persist in published non-DrawPile zones.\n2. **Gate B — public option order**\n   - remove exactly `E` from ordered `pendingChoice.options`;\n   - accept only when all valid occurrence assignments collapse to one observable-key sequence;\n   - that sequence is the already-observed draw order.\n3. **Root-relative prefix**\n   - accepted draws are recorded as `(offset, observable_card_key)`;\n   - unexplained DrawPile mutation or ambiguous Gate B blocks later pinning;\n   - constraints must remain one generated contiguous prefix `0..N-1`.\n4. **Materialization**\n   - only the proven observed prefix is pinned;\n   - unobserved hypothesis CardIds retain their relative candidate order;\n   - physical `InstanceId` is never a proof input;\n   - observable-equal Snapshot copies with different hidden gameplay state fail closed.\n\n## Gate B ordering contract\n\nGate B relies on a public Emulator contract: when freshly removed DrawPile cards are exposed in `pendingChoice.options`, those draw-origin occurrences preserve sequential draw order. RL does **not** verify that premise by reading `StableRoot.Player.DrawPile`, raw draw history, RNG state, or instance identity. Doing so would turn hidden state into an answer key and violate the public-proof boundary.\n\nA producer that consistently publishes the wrong option order cannot be distinguished from a correct producer using public state alone. Therefore that premise belongs to Emulator contract/regression testing, not to RL runtime provenance logic.\n\n## Runtime replay consistency check\n\nRL still detects observable regressions during Replay Prefix execution. After each replayed step it recomputes Gate A/B from the replayed public pre/post states and compares the result with the recorded entry:\n\n```text\nreplayed.constraints == recorded.visible_draw_constraints\nreplayed.blocked     == recorded.visible_draw_tracking_blocked\n```\n\nThis is stronger than candidate semantic keys alone because `observable_card_key` includes public gameplay-relevant state such as upgrade/cost/tinker/enchantment. A mismatch is returned as `ReplayMismatch(stage="visible_draw_evidence")`.\n\nThis check cannot prove an unobservable hidden draw order; it only verifies that replay remains consistent with the same public evidence.\n\n## Deliberately excluded from the safety decision\n\n- CardId/mechanic allowlists;\n- choice operation/source/origin labels;\n- `triggering_action`;\n- `rng_id`;\n- public or Snapshot physical-card identity;\n- raw draw history;\n- public Observation DrawPile order;\n- **Stable Snapshot DrawPile order**.\n\n## Cross-repo validation\n\nSTS2_Emulator should regression-test the PendingChoice ordering publication contract against real engine behavior. Such tests may use engine-internal knowledge as a test oracle, but that information must not become an RL runtime input or a public replay-provenance field.\n''')

print("patched public-only Gate B design")
