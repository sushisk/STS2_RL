"""Phase 2A RL-side acceptance tests (this task's 5節/6節 + 受け入れ条件).

1. Cross-check: raw Emulator Observation (GetObservation(), bypassing RL wrappers) vs.
   Snapshot's public projection vs. RL's existing BattleState.engine_state, at the same
   instant, for HP/Block/Energy/piles/enemies/relics/powers/potions/orbs/turn/round/
   pendingChoice/terminal. Any mismatch is classified as either "Observation側欠落" (RL's
   own wrapper diverges from the raw Emulator Observation) or "Snapshot Capture不良"
   (the Snapshot diverges from the raw Emulator Observation that both other sources
   agree on).
2. Capture-is-side-effect-free: Observation/LegalActions unchanged immediately before vs.
   after a capture_snapshot() call.
3. Re-serialization equality: Python round-trip (dataclass -> dict -> JSON) of a captured
   Snapshot, normalized (drop SnapshotId/CapturedAtUtc, which are expected to differ
   between captures), matches a second back-to-back capture at the same boundary.
4. Scenario `6546-21` full replay with a capture_snapshot() call injected at every
   decision - confirms identical decision count/outcome to the Phase 1 baseline (49
   decisions, victory) despite the extra captures.
5. Phase 1 regression (test_scenario_v2.py + test_choice_semantics.py) - run separately,
   see this script's own printed reminder.

Read-only / diagnostic - does not modify LiveCombatSession/CombatEnv/BattleEmulator, and
does not feed any Snapshot into BattleEmulator.apply_action()/HeuristicAgent/Policy/
Choice Policy (Phase 2A's explicit prohibition).
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from combat_env import CombatEnv  # noqa: E402
from preflight_validate import preflight_validate  # noqa: E402
from legacy.policy_agent import build_policy_agent  # noqa: E402
from choice_semantics import ChoiceSemanticsTable  # noqa: E402
from legacy.choice_policy_agent import ChoicePolicyAgent, build_choice_decision, DEFAULT_CHOICE_POLICY_CHECKPOINT, make_ab_continuation_resolver  # noqa: E402
from emulator_bridge import to_plain  # noqa: E402


def load_manifest_row(trajectory_id: str, manifest_path: Path) -> dict:
    with manifest_path.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row["trajectory_id"] == trajectory_id:
                return row
    raise RuntimeError(f"{trajectory_id} not found in {manifest_path}")


def cross_check(env: CombatEnv) -> list[dict]:
    """Returns a list of mismatch records (empty if fully consistent)."""
    session = env._session  # noqa: SLF001
    raw_obs = session._game.GetObservation()  # noqa: SLF001 - deliberate bypass of RL wrappers for this cross-check
    raw_state = to_plain(raw_obs.State)
    rl_state = env.battle_state.engine_state
    snapshot = session.capture_snapshot()

    mismatches = []

    def check(field_name, raw_value, rl_value, snap_value):
        if raw_value == rl_value == snap_value:
            return
        source = "unknown"
        if raw_value != rl_value:
            source = "observation_side_gap (RL wrapper diverges from raw Emulator Observation)"
        elif raw_value != snap_value:
            source = "snapshot_capture_defect (Snapshot diverges from Emulator Observation both other sources agree on)"
        mismatches.append({"field": field_name, "raw": raw_value, "rl": rl_value, "snapshot": snap_value, "classification": source})

    check("hp", raw_state.get("hp"), rl_state.get("hp"), snapshot.Player.Hp)
    check("block", raw_state.get("block"), rl_state.get("block"), snapshot.Player.Block)
    check("energy", raw_state.get("energy"), rl_state.get("energy"), snapshot.Player.Energy)
    check("stars", raw_state.get("stars"), rl_state.get("stars"), snapshot.Player.Stars)
    check("gold", raw_state.get("gold"), rl_state.get("gold"), snapshot.Player.Gold)

    for pile_key, zone in (("hand", "hand"), ("drawPile", "draw_pile"), ("discardPile", "discard_pile"), ("exhaustPile", "exhaust_pile"), ("playPile", "play_pile")):
        raw_ids = [c.get("id") for c in (raw_state.get(pile_key) or [])]
        rl_ids = [c.get("id") for c in (rl_state.get(pile_key) or [])]
        snap_ids = [c.CardId for c in snapshot.Player.cards_in_zone(zone)]
        check(f"pile:{pile_key}", raw_ids, rl_ids, snap_ids)

    raw_relics = sorted(r.get("id") for r in (raw_state.get("relics") or []))
    rl_relics = sorted(r.get("id") for r in (rl_state.get("relics") or []))
    snap_relics = sorted(r.RelicId for r in snapshot.Player.Relics)
    check("relics", raw_relics, rl_relics, snap_relics)

    raw_powers = sorted((p.get("id"), p.get("amount")) for p in (raw_state.get("playerPowers") or []))
    rl_powers = sorted((p.get("id"), p.get("amount")) for p in (rl_state.get("playerPowers") or []))
    snap_powers = sorted((p.PowerId, p.Amount) for p in snapshot.Player.Powers)
    check("powers", raw_powers, rl_powers, snap_powers)

    raw_potions = [p.get("id") if p else None for p in (raw_state.get("potions") or [])]
    rl_potions = [p.get("id") if p else None for p in (rl_state.get("potions") or [])]
    snap_potions_by_slot = {p.Slot: p.PotionId for p in snapshot.Player.Potions if p is not None}
    snap_potions = [snap_potions_by_slot.get(i) for i in range(len(raw_potions))]
    check("potions", raw_potions, rl_potions, snap_potions)

    raw_orbs = [o.get("id") for o in (raw_state.get("orbs") or [])]
    rl_orbs = [o.get("id") for o in (rl_state.get("orbs") or [])]
    snap_orbs = [o.OrbId for o in sorted(snapshot.Player.Orbs, key=lambda o: o.Index)]
    check("orbs", raw_orbs, rl_orbs, snap_orbs)

    raw_enemies = [(e.get("hp"), e.get("isAlive")) for e in (raw_state.get("enemies") or [])]
    rl_enemies = [(e.get("hp"), e.get("isAlive")) for e in (rl_state.get("enemies") or [])]
    snap_enemies = [(e.Hp, e.IsAlive) for e in sorted(snapshot.Enemies, key=lambda e: e.Index)]
    check("enemies", raw_enemies, rl_enemies, snap_enemies)

    check("turnNumber", raw_state.get("turnNumber"), rl_state.get("turnNumber"), snapshot.TurnNumber)
    check("combatRoundNumber", raw_state.get("combatRoundNumber"), rl_state.get("combatRoundNumber"), snapshot.RoundNumber)

    # PendingChoice is a raw passthrough of the pre-existing BuildPendingChoiceDict()
    # shape (per CombatStateSnapshot.PendingChoice's own doc comment: "same shape
    # Observation.State.pendingChoice already exposes") - its OWN keys stay camelCase
    # even though every other Snapshot DTO property is PascalCase, since this one field
    # is a Dictionary<string, object?> passthrough, not a typed DTO with C# properties
    # of its own. Confirmed against a real capture's raw JSON before fixing this check
    # (originally miskeyed as "ChoiceType" here, a bug in this verification script, not
    # in the Emulator's Capture implementation).
    raw_pc = (raw_state.get("pendingChoice") or {}).get("choiceType")
    rl_pc = (rl_state.get("pendingChoice") or {}).get("choiceType")
    snap_pc = (snapshot.PendingChoice or {}).get("choiceType") if snapshot.PendingChoice else None
    check("pendingChoice.choiceType", raw_pc, rl_pc, snap_pc)

    raw_terminal = bool(raw_obs.IsTerminal)
    rl_terminal = env.battle_state.is_terminal
    snap_terminal = snapshot.IsTerminal
    if not (raw_terminal == rl_terminal == snap_terminal):
        mismatches.append({
            "field": "terminal", "raw": raw_terminal, "rl": rl_terminal, "snapshot": snap_terminal,
            "classification": "expected_divergence_source (RL's coerce_terminal_observation() intentionally "
                               "corrects known raw-Observation gaps - see battle_emulator.py; not a Snapshot defect "
                               "unless snapshot also disagrees with raw)"
                               if raw_terminal == snap_terminal else "snapshot_capture_defect",
        })

    return mismatches


def normalize_snapshot_dict(d: dict) -> dict:
    d = json.loads(json.dumps(d, default=str))
    d["Metadata"].pop("SnapshotId", None)
    d["Metadata"].pop("CapturedAtUtc", None)
    return d


def test_capture_side_effect_free(env: CombatEnv) -> None:
    session = env._session  # noqa: SLF001
    before_legal = list(env.get_legal_actions())
    before_state = json.loads(json.dumps(env.battle_state.engine_state, default=str))
    session.capture_snapshot()
    after_legal = list(env.get_legal_actions())
    after_state = json.loads(json.dumps(env.battle_state.engine_state, default=str))
    assert before_legal == after_legal, "FAIL: LegalActions changed across capture_snapshot()"
    assert before_state == after_state, "FAIL: engine_state changed across capture_snapshot()"
    print("PASS: capture_snapshot() is side-effect-free (LegalActions/engine_state unchanged)")


def test_reserialize_equality(env: CombatEnv) -> None:
    session = env._session  # noqa: SLF001
    snap1 = session.capture_snapshot()
    snap2 = session.capture_snapshot()
    d1 = normalize_snapshot_dict(asdict(snap1))
    d2 = normalize_snapshot_dict(asdict(snap2))
    assert d1 == d2, "FAIL: two back-to-back captures at the same boundary produced different normalized JSON"
    print("PASS: re-capture at the same boundary produces identical normalized JSON")


def test_scenario_6546_21_with_captures() -> None:
    row = load_manifest_row("6546-21", _HERE / "choice_policy_online_eval_manifest.jsonl")
    emulator, heuristic_agent, policy_agent = build_policy_agent()
    choice_decision = build_choice_decision(DEFAULT_CHOICE_POLICY_CHECKPOINT)
    choice_table = ChoiceSemanticsTable()
    assert choice_table.loaded_ok, choice_table.load_error
    cpa = ChoicePolicyAgent(policy_agent, choice_decision, choice_table)

    pre = preflight_validate(row["spec"], emulator)
    assert pre["status"] == "ok"
    env = CombatEnv(reward_fn=lambda *a: 0.0)
    env.adopt_state(emulator.clone_state(pre["battle_state"]), row["spec"])

    capture_count = 0
    decision_index = 0
    while not env.battle_state.is_terminal and decision_index < 60:
        legal = env.get_legal_actions()
        if not legal:
            raise AssertionError(f"FAIL: no_legal_actions_while_non_terminal reproduced at decision {decision_index}")
        # Inject a capture at every decision - diagnostic only, result discarded.
        snap = env._session.capture_snapshot()  # noqa: SLF001
        capture_count += 1
        assert snap.Metadata.Completeness in ("complete", "partial_known_gaps")

        record = cpa.decide(env.battle_state, legal, None)
        chosen_action = record.get("chosen_action")
        if chosen_action is None:
            break
        ctx = {"trajectory_id": "6546-21", "decision_index": decision_index, "_continuation_step_index": 0}
        resolver = make_ab_continuation_resolver(
            "choice_policy", choice_decision, choice_table, emulator._default_choose_action_continuation_live,  # noqa: SLF001
            None, [], ctx,
        )
        result = env.step(
            chosen_action, target_enemy_index=record.get("chosen_enemy_index"),
            target_index=record.get("chosen_target_index"), continuation_resolver=resolver,
        )
        decision_index += 1
        if result["done"]:
            break

    print(f"Scenario 6546-21 with per-decision capture: decisions={decision_index}, captures={capture_count}, "
          f"outcome={env.battle_state.outcome}, is_terminal={env.battle_state.is_terminal}")
    assert decision_index == 49, f"FAIL: expected 49 decisions (Phase 1 baseline), got {decision_index}"
    assert env.battle_state.outcome == "victory", f"FAIL: expected victory, got {env.battle_state.outcome}"
    print("PASS: Scenario 6546-21 behavior unchanged by per-decision Snapshot capture (49 decisions, victory)")


def main() -> None:
    row = load_manifest_row("302-13", _HERE / "choice_policy_online_eval_manifest.jsonl")
    emulator, heuristic_agent, policy_agent = build_policy_agent()
    pre = preflight_validate(row["spec"], emulator)
    assert pre["status"] == "ok"
    env = CombatEnv(reward_fn=lambda *a: 0.0)
    env.adopt_state(emulator.clone_state(pre["battle_state"]), row["spec"])

    print("=== 1. Cross-check (raw Observation / Snapshot / RL Observation) ===")
    mismatches = cross_check(env)
    if mismatches:
        print(f"MISMATCHES FOUND ({len(mismatches)}):")
        for m in mismatches:
            print(f"  {m}")
    else:
        print("PASS: raw Emulator Observation, Snapshot, and RL Observation agree on all checked fields")

    print("\n=== 2. Capture side-effect-free ===")
    test_capture_side_effect_free(env)

    print("\n=== 3. Re-serialization equality ===")
    test_reserialize_equality(env)

    print("\n=== 4. Scenario 6546-21 with per-decision capture ===")
    test_scenario_6546_21_with_captures()

    print("\n=== 5. Phase 1 regression ===")
    print("Run separately: python -m pytest Combat/tests/test_scenario_v2.py Combat/tests/test_choice_semantics.py -q")


if __name__ == "__main__":
    main()
