"""Phase 1 acceptance test (統合順序4): Scenario 6546-21 via the NEW LiveCombatSession-
backed CombatEnv + full Choice Policy adapter stack (unchanged from Stage C except
combat_env.py's internal implementation). Confirms:
    - 0 QuiescentBoundaryViolation
    - 0 no_legal_actions_while_non_terminal
    - the scenario completes (reaches terminal or a documented truncation, NOT the old
      13-decision failure)
    - reports LiveCombatSession.resynchronize_count (expected > 0 whenever Choice
      fallback/shadow evaluation touches the shared GameInstance - see live_combat_
      session.py's module docstring; this is NOT a failure, it's the designed recovery
      path for the "legacy_approximate_restore" search side)

Run: python verify_live_combat_session_6546_21.py
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
from choice_semantics import ChoiceSemanticsTable  # noqa: E402
from choice_policy_agent import ChoicePolicyAgent, build_choice_decision, DEFAULT_CHOICE_POLICY_CHECKPOINT, make_ab_continuation_resolver  # noqa: E402
from policy_agent import build_policy_agent  # noqa: E402
from live_combat_session import QuiescentBoundaryViolation, DecisionFrameMismatchError  # noqa: E402


def load_spec() -> dict:
    with (_HERE / "choice_policy_online_eval_manifest.jsonl").open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            if row["trajectory_id"] == "6546-21":
                return row["spec"]
    raise RuntimeError("6546-21 not found in manifest")


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
    print(f"resynchronize_count after adopt_state: {env._session.resynchronize_count}")  # noqa: SLF001

    decision_index = 0
    max_decisions = 60
    quiescent_violations = 0
    no_legal_actions_hit = False
    resync_at_decisions: list[int] = []

    while not env.battle_state.is_terminal and decision_index < max_decisions:
        try:
            legal = env.get_legal_actions()
        except QuiescentBoundaryViolation as exc:
            quiescent_violations += 1
            print(f"[{decision_index}] QuiescentBoundaryViolation on get_legal_actions(): {exc}")
            break

        if not legal:
            no_legal_actions_hit = True
            print(f"[{decision_index}] no_legal_actions_while_non_terminal (FAILURE - old bug reproduced)")
            break

        try:
            record = choice_policy_agent.decide(env.battle_state, legal, None)
        except QuiescentBoundaryViolation as exc:
            quiescent_violations += 1
            print(f"[{decision_index}] QuiescentBoundaryViolation during decide(): {exc}")
            break

        chosen_action = record.get("chosen_action")
        if chosen_action is None:
            print(f"[{decision_index}] decider returned no action")
            break

        pre_resync_count = env._session.resynchronize_count  # noqa: SLF001
        try:
            result = env.step(
                chosen_action,
                target_enemy_index=record.get("chosen_enemy_index"),
                target_index=record.get("chosen_target_index"),
                continuation_resolver=make_ab_continuation_resolver(
                    "choice_policy", choice_decision, choice_table,
                    emulator._default_choose_action_continuation_live,  # noqa: SLF001
                    None, [], {"trajectory_id": "6546-21", "decision_index": decision_index, "_continuation_step_index": 0},
                ),
            )
        except QuiescentBoundaryViolation as exc:
            quiescent_violations += 1
            print(f"[{decision_index}] QuiescentBoundaryViolation during step(): {exc}")
            break
        except DecisionFrameMismatchError as exc:
            print(f"[{decision_index}] DecisionFrameMismatchError (should not happen with this harness): {exc}")
            raise

        if env._session.resynchronize_count > pre_resync_count:  # noqa: SLF001
            resync_at_decisions.append(decision_index)

        print(
            f"[{decision_index}] chose {chosen_action.get('label')} ({chosen_action.get('action_type')}, "
            f"source={record.get('decision_source')}) -> done={result['done']} outcome={result['info']['outcome']}"
        )
        decision_index += 1
        if result["done"]:
            break

    print("\n=== RESULT ===")
    print(f"decisions completed: {decision_index}")
    print(f"final is_terminal: {env.battle_state.is_terminal}, outcome: {env.battle_state.outcome}")
    print(f"QuiescentBoundaryViolation count: {quiescent_violations}")
    print(f"no_legal_actions_while_non_terminal hit: {no_legal_actions_hit}")
    print(f"total resynchronize_count: {env._session.resynchronize_count}")  # noqa: SLF001
    print(f"decisions that required resynchronize: {resync_at_decisions}")
    print(f"total live step_count (Step() calls committed, excludes continuation micro-steps): {env._session.step_count}")  # noqa: SLF001

    assert quiescent_violations == 0, "FAIL: QuiescentBoundaryViolation occurred"
    assert not no_legal_actions_hit, "FAIL: no_legal_actions_while_non_terminal reproduced"
    assert env.battle_state.is_terminal, "FAIL: scenario did not reach terminal within max_decisions"
    print("\nPASS: Scenario 6546-21 completed normally via LiveCombatSession, 0 boundary violations, 0 no_legal_actions.")


if __name__ == "__main__":
    main()
