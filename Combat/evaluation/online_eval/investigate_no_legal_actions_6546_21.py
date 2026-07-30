"""Minimal reproduction + root-cause classification for this task's "no_legal_actions調査"
- scenario 6546-21, which stopped at decision_index=13 with termination_reason=
"no_legal_actions_while_non_terminal" during Choice Policy Stage C (Combat/evaluation/
reports/choice_policy_online_eval_stage_c/combats.jsonl).

Replays the identical scenario/arm (choice_policy) deterministically via the SAME
ChoicePolicyAgent used in Stage C (no code change), then at the exact point
env.get_legal_actions() returns empty while battle_state.is_terminal is False, dumps every
diagnostic field relevant to the classification list this task specifies:
    normal_terminal_detection_issue / scenario_restore_gap / emulator_legal_action_bug /
    combat_adapter_progress_bug / data_issue / unknown

Also independently re-drives the SAME point with (a) the plain Heuristic decider instead
of Choice Policy, and (b) a raw BattleEmulator.enumerate_legal_actions() call bypassing
CombatEnv entirely, to test "Policy／Choice Policyに依存せず再現するか".

Run: python investigate_no_legal_actions_6546_21.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from combat_env import CombatEnv  # noqa: E402
from preflight_validate import preflight_validate  # noqa: E402
from battle_emulator import is_action_continuation_pending_choice  # noqa: E402
from choice_semantics import ChoiceSemanticsTable  # noqa: E402
from choice_policy_agent import ChoicePolicyAgent, build_choice_decision, DEFAULT_CHOICE_POLICY_CHECKPOINT  # noqa: E402
from policy_agent import build_policy_agent  # noqa: E402


def load_spec() -> dict:
    with (_HERE / "choice_policy_online_eval_manifest.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row["trajectory_id"] == "6546-21":
                return row["spec"]
    raise RuntimeError("6546-21 not found in manifest")


def summarize_state(engine_state: dict) -> dict:
    return {
        "hp": engine_state.get("hp"), "maxHp": engine_state.get("maxHp"), "block": engine_state.get("block"),
        "energy": engine_state.get("energy"), "turnNumber": engine_state.get("turnNumber"),
        "combatRoundNumber": engine_state.get("combatRoundNumber"), "stepIndex": engine_state.get("stepIndex"),
        "pendingChoice": engine_state.get("pendingChoice"),
        "hand_count": len(engine_state.get("hand") or []),
        "drawPile_count": len(engine_state.get("drawPile") or []),
        "discardPile_count": len(engine_state.get("discardPile") or []),
        "exhaustPile_count": len(engine_state.get("exhaustPile") or []),
        "playPile_count": len(engine_state.get("playPile") or []),
        "enemies": [
            {"id": e.get("id"), "index": e.get("index"), "hp": e.get("hp"), "isAlive": e.get("isAlive"), "intent": (e.get("intent") or {}).get("stateId")}
            for e in (engine_state.get("enemies") or [])
        ],
    }


def main() -> None:
    spec = load_spec()
    emulator, heuristic_agent, policy_agent = build_policy_agent()
    choice_decision = build_choice_decision(DEFAULT_CHOICE_POLICY_CHECKPOINT)
    choice_table = ChoiceSemanticsTable()
    assert choice_table.loaded_ok, choice_table.load_error
    choice_policy_agent = ChoicePolicyAgent(policy_agent, choice_decision, choice_table)

    pre = preflight_validate(spec, emulator)
    print(f"preflight status: {pre['status']}, reasons: {pre.get('reasons')}")
    assert pre["status"] == "ok"

    env = CombatEnv(reward_fn=lambda *a: 0.0)
    env.adopt_state(emulator.clone_state(pre["battle_state"]), spec)

    decision_index = 0
    max_decisions = 60
    from choice_policy_agent import make_ab_continuation_resolver
    fallback_resolver = emulator._default_choose_action_continuation_live  # noqa: SLF001

    while not env.battle_state.is_terminal and decision_index < max_decisions:
        legal = env.get_legal_actions()
        if not legal:
            print(f"\n=== FAILURE at decision_index={decision_index}: get_legal_actions() returned EMPTY, is_terminal={env.battle_state.is_terminal} ===")
            print("engine_state summary:", json.dumps(summarize_state(env.battle_state.engine_state), indent=2, ensure_ascii=False, default=str))
            print("battle_state.outcome:", env.battle_state.outcome)
            print("is_action_continuation_pending_choice:", is_action_continuation_pending_choice(env.battle_state.engine_state))

            # (b) raw BattleEmulator.enumerate_legal_actions bypass of CombatEnv's own cache/logic
            raw_legal = emulator.enumerate_legal_actions(env.battle_state)
            print(f"raw emulator.enumerate_legal_actions() also empty: {len(raw_legal) == 0} (count={len(raw_legal)})")

            # Fresh restore + a brand-new GetLegalActions() call, bypassing ALL caching
            game = emulator._restore(env.battle_state)  # noqa: SLF001
            from battle_emulator import legal_actions_to_list
            fresh_legal = legal_actions_to_list(game.GetLegalActions())
            print(f"fresh restore + GetLegalActions() (no cache at all): count={len(fresh_legal)}")
            if fresh_legal:
                print("  -> fresh call DOES find legal actions the cached/env path missed:", [a.get("label") for a in fresh_legal[:10]])

            break

        record = choice_policy_agent.decide(env.battle_state, legal, None)
        chosen_action = record.get("chosen_action")
        if chosen_action is None:
            print(f"decider returned no action at decision_index={decision_index}")
            break
        step_kwargs = {
            "continuation_resolver": make_ab_continuation_resolver(
                "choice_policy", choice_decision, choice_table, fallback_resolver, None, [], {"trajectory_id": "6546-21", "decision_index": decision_index, "_continuation_step_index": 0},
            )
        }
        result = env.step(chosen_action, target_enemy_index=record.get("chosen_enemy_index"), target_index=record.get("chosen_target_index"), **step_kwargs)
        print(f"[{decision_index}] chose {chosen_action.get('label')} ({chosen_action.get('action_type')}) -> done={result['done']} outcome={result['info']['outcome']}")
        decision_index += 1
        if result["done"]:
            print("Combat ended normally (done=True) - no repro of the failure at this decision_index.")
            break
    else:
        print("Loop exited without hitting the failure (is_terminal or max_decisions) - could not reproduce.")


if __name__ == "__main__":
    main()
