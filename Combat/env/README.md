# Combat/env

`combat_env.py` - `CombatEnv`, the stateful, single-episode wrapper over
`Combat/battle_emulator.py`'s stateless `BattleEmulator`. This is the intended entry
point for any code that runs a REAL (to-be-committed) combat trajectory:

```text
reset(scenario_spec) / adopt_state(battle_state, scenario_spec)
    -> get_legal_actions()
    -> step(action, target_enemy_index=..., target_index=...)
    -> repeat
```

`adopt_state()` exists so a caller that already had to call
`BattleEmulator.initialize()` for its own reason (e.g. `Combat/data/preflight_validate.py`,
which needs the resulting state to validate against the scenario spec before deciding
whether the episode may even start) doesn't have to initialize twice.

`CombatEnv.battle_state` is a deliberate escape hatch: Heuristic search code
(`Combat/heuristic_agent.py`, `beam_search.py`, `lookahead.py`) needs
`BattleEmulator`'s stateless "apply this action to a hypothetical, uncommitted state"
branching to explore candidates - `CombatEnv` intentionally does not reimplement that;
it owns only the real, committed trajectory. See `STS2_RL/docs/RL_HANDOFF.md` section 7
for the full rationale and current migration status.
