# Fixed50 failure classification report (2026-07-21)

## Summary

- total_scenarios: 50
- ok: 42
- quarantined: 8
- illegal_action_count: 0
- heuristic_exception_count: 0
- emulator_step_exception_count: 0
- timeout_count: 0
- determinism_rate_pct: 100.0
- truncated_count: 9
- cycle_detected_count: 0
- no_progress_detected_count: 4
- data_usage_counts: `{'usable_complete': 29, 'usable_partial': 13, 'exclude_state_mismatch': 8}`
- truncation_classification_counts: `{'A_normal_long_combat': 5, 'B_heuristic_stagnation': 4}`

## Former heuristic exceptions: STUNNED

- fixed50:2446-14: character=DEFECT encounter=LAGAVULIN_MATRIARCH_BOSS decision_index=14 classification=non_learning_transition
  - forced_moves=[{'enemy_index': 0, 'enemy_id': 'LAGAVULIN_MATRIARCH', 'move_id': 'STUNNED', 'state_log': ['SLEEP_MOVE', 'SLEEP_MOVE', 'SLEEP_MOVE', 'SLASH_MOVE']}]
  - previous_action=DUALCAST previous_decision=13
  - observation_enemy_intents=[('LAGAVULIN_MATRIARCH', 'STUNNED', ['SLEEP_MOVE', 'SLEEP_MOVE', 'SLEEP_MOVE', 'SLASH_MOVE'])]
  - exception_type_before_fix=RuntimeError wrapping candidate ArgumentException
  - stack_trace_source=GameInstance.ApplyScenarioMoveHistory -> ResolveMoveState -> ArgumentException: Unknown move id: STUNNED
  - reproducibility=reproduced by fixed50 rerun; now detected before Heuristic evaluation
- fixed50:5658-15: character=NECROBINDER encounter=CEREMONIAL_BEAST_BOSS decision_index=12 classification=non_learning_transition
  - forced_moves=[{'enemy_index': 0, 'enemy_id': 'CEREMONIAL_BEAST', 'move_id': 'STUNNED', 'state_log': ['STAMP_MOVE', 'PLOW_MOVE', 'PLOW_MOVE']}]
  - previous_action=ULTIMATE_STRIKE previous_decision=11
  - observation_enemy_intents=[('CEREMONIAL_BEAST', 'STUNNED', ['STAMP_MOVE', 'PLOW_MOVE', 'PLOW_MOVE'])]
  - exception_type_before_fix=RuntimeError wrapping candidate ArgumentException
  - stack_trace_source=GameInstance.ApplyScenarioMoveHistory -> ResolveMoveState -> ArgumentException: Unknown move id: STUNNED
  - reproducibility=reproduced by fixed50 rerun; now detected before Heuristic evaluation
- fixed50:7522-23: character=REGENT encounter=BOWLBUGS_NORMAL decision_index=6 classification=non_learning_transition
  - forced_moves=[{'enemy_index': 2, 'enemy_id': 'BOWLBUG_ROCK', 'move_id': 'STUNNED', 'state_log': ['HEADBUTT_MOVE']}]
  - previous_action=End Turn previous_decision=5
  - observation_enemy_intents=[('BOWLBUG_EGG', 'BITE_MOVE', ['BITE_MOVE', 'BITE_MOVE']), ('BOWLBUG_NECTAR', 'BUFF_MOVE', ['THRASH_MOVE', 'BUFF_MOVE']), ('BOWLBUG_ROCK', 'STUNNED', ['HEADBUTT_MOVE']), ('BOWLBUG_SILK', 'THRASH_MOVE', ['TOXIC_SPIT_MOVE', 'THRASH_MOVE'])]
  - exception_type_before_fix=RuntimeError wrapping candidate ArgumentException
  - stack_trace_source=GameInstance.ApplyScenarioMoveHistory -> ResolveMoveState -> ArgumentException: Unknown move id: STUNNED
  - reproducibility=reproduced by fixed50 rerun; now detected before Heuristic evaluation
- fixed50:6314-24: character=NECROBINDER encounter=BOWLBUGS_NORMAL decision_index=6 classification=non_learning_transition
  - forced_moves=[{'enemy_index': 2, 'enemy_id': 'BOWLBUG_ROCK', 'move_id': 'STUNNED', 'state_log': ['HEADBUTT_MOVE']}]
  - previous_action=End Turn previous_decision=5
  - observation_enemy_intents=[('BOWLBUG_EGG', 'BITE_MOVE', ['BITE_MOVE', 'BITE_MOVE']), ('BOWLBUG_NECTAR', 'BUFF_MOVE', ['THRASH_MOVE', 'BUFF_MOVE']), ('BOWLBUG_ROCK', 'STUNNED', ['HEADBUTT_MOVE']), ('BOWLBUG_SILK', 'THRASH_MOVE', ['TOXIC_SPIT_MOVE', 'THRASH_MOVE'])]
  - exception_type_before_fix=RuntimeError wrapping candidate ArgumentException
  - stack_trace_source=GameInstance.ApplyScenarioMoveHistory -> ResolveMoveState -> ArgumentException: Unknown move id: STUNNED
  - reproducibility=reproduced by fixed50 rerun; now detected before Heuristic evaluation

## Quarantine classification

- classification_counts: `{'relic_after_obtained_side_effect': 5, 'unsupported_state': 3}`
- fixed50:3315-9: class=relic_after_obtained_side_effect reasons=['relic_mismatch'] character=REGENT encounter=FLYCONID_NORMAL recommended=exclude_from_teacher_data; audit relic AfterObtained or replacement behavior
- fixed50:2744-10: class=unsupported_state reasons=['no_legal_actions'] character=REGENT encounter=PHROG_PARASITE_ELITE recommended=exclude_from_teacher_data unless the unsupported state is made restorable
- fixed50:2428-11: class=unsupported_state reasons=['no_legal_actions'] character=NECROBINDER encounter=PHROG_PARASITE_ELITE recommended=exclude_from_teacher_data unless the unsupported state is made restorable
- fixed50:6420-19: class=relic_after_obtained_side_effect reasons=['relic_mismatch'] character=SILENT encounter=TUNNELER_WEAK recommended=exclude_from_teacher_data; audit relic AfterObtained or replacement behavior
- fixed50:5067-20: class=unsupported_state reasons=['no_legal_actions'] character=DEFECT encounter=EXOSKELETONS_WEAK recommended=exclude_from_teacher_data unless the unsupported state is made restorable
- fixed50:4228-34: class=relic_after_obtained_side_effect reasons=['relic_mismatch'] character=IRONCLAD encounter=DEVOTED_SCULPTOR_WEAK recommended=exclude_from_teacher_data; audit relic AfterObtained or replacement behavior
- fixed50:5483-41: class=relic_after_obtained_side_effect reasons=['relic_mismatch'] character=DEFECT encounter=MECHA_KNIGHT_ELITE recommended=exclude_from_teacher_data; audit relic AfterObtained or replacement behavior
- fixed50:4650-48: class=relic_after_obtained_side_effect reasons=['relic_mismatch'] character=REGENT encounter=AEONGLASS_BOSS recommended=exclude_from_teacher_data; audit relic AfterObtained or replacement behavior

## Relic side-effect aggregation

- DIVINE_DESTINY: scenarios=1 known_after_obtained_candidate=False ids=['fixed50:4650-48']
  - added={'CIRCLET': 1}
  - removed={'DIVINE_DESTINY': 1}
  - impact=preflight state mismatch; excluded before Heuristic, no teacher labels emitted
  - avoidable_by_exclusion=yes
  - emulator_fix_needed=needs owner decision; RL should not rewrite expected state
- LARGE_CAPSULE: scenarios=2 known_after_obtained_candidate=True ids=['fixed50:3315-9', 'fixed50:4228-34']
  - added={'WHITE_STAR': 1, 'ORANGE_DOUGH': 1, 'LIZARD_TAIL': 1, 'BOOK_OF_FIVE_RINGS': 1}
  - removed={}
  - impact=preflight state mismatch; excluded before Heuristic, no teacher labels emitted
  - avoidable_by_exclusion=yes
  - emulator_fix_needed=needs owner decision; RL should not rewrite expected state
- NEOWS_TORMENT: scenarios=1 known_after_obtained_candidate=True ids=['fixed50:5483-41']
  - added={'CIRCLET': 1}
  - removed={'INFUSED_CORE': 1}
  - impact=preflight state mismatch; excluded before Heuristic, no teacher labels emitted
  - avoidable_by_exclusion=yes
  - emulator_fix_needed=needs owner decision; RL should not rewrite expected state
- RING_OF_THE_DRAKE: scenarios=1 known_after_obtained_candidate=False ids=['fixed50:6420-19']
  - added={'CIRCLET': 1}
  - removed={'RING_OF_THE_DRAKE': 1}
  - impact=preflight state mismatch; excluded before Heuristic, no teacher labels emitted
  - avoidable_by_exclusion=yes
  - emulator_fix_needed=needs owner decision; RL should not rewrite expected state

## Truncated classification

- fixed50:3486-13: character=DEFECT encounter=VANTOM_BOSS classification=A_normal_long_combat data_usage=usable_partial
  - decision_count=50 unique_state_count=50 repeated_state_count=0
  - enemy_hp_progression=[183, 183, 182, 182, 181, 180, 179, 179, 178, 177, 176, 175, 174, 173, 172, 171, 170, 169, 168, 167, 166, 165, 164, 163, 162, 161, 160, 159, 158, 157, 156, 155, 154, 153, 152, 151, 150, 149, 148, 147, 146, 145, 144, 143, 142, 141, 140, 139, 138, 137, 136]
  - player_hp_progression=[64, 65, 66, 67, 68, 69, 70, 71, 72, 73, 74, 75, 76, 77, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78, 78]
  - last_10_actions=['CLAW', 'BEAM_CELL', 'CLAW', 'BEAM_CELL', 'CLAW', 'BEAM_CELL', 'CLAW', 'BEAM_CELL', 'CLAW', 'BEAM_CELL']
  - termination_reason=truncated_at_max_decisions:50
- fixed50:6017-25: character=DEFECT encounter=HUNTER_KILLER_NORMAL classification=A_normal_long_combat data_usage=usable_partial
  - decision_count=50 unique_state_count=50 repeated_state_count=0
  - enemy_hp_progression=[126, 126, 126, 126, 126, 126, 120, 120, 117, 117, 101, 92, 92, 92, 89, 79, 79, 79, 74, 74, 71, 71, 71, 71, 68, 68, 68, 68, 68, 65, 55, 55, 55, 52, 52, 52, 52, 52, 52, 52, 49, 39, 39, 23, 18, 18, 18, 18, 15, 15, 15]
  - player_hp_progression=[63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 63, 46, 46, 46, 46, 46, 46, 46, 46, 46, 46, 46, 35, 35, 35, 35, 35, 35, 35, 35, 35, 35, 35]
  - last_10_actions=['FIGHT_THROUGH', 'LEAP', 'DUALCAST', 'TURBO', 'DEFEND_DEFECT', 'ITERATION', 'GO_FOR_THE_EYES', 'End Turn', 'DEFEND_DEFECT', 'ITERATION']
  - termination_reason=truncated_at_max_decisions:50
- fixed50:3342-27: character=DEFECT encounter=ENTOMANCER_ELITE classification=B_heuristic_stagnation data_usage=usable_partial
  - decision_count=50 unique_state_count=50 repeated_state_count=0
  - enemy_hp_progression=[155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155, 155]
  - player_hp_progression=[82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82]
  - last_10_actions=['STRIKE_DEFECT', 'STRIKE_DEFECT', 'STRIKE_DEFECT', 'STRIKE_DEFECT', 'STRIKE_DEFECT', 'STRIKE_DEFECT', 'STRIKE_DEFECT', 'STRIKE_DEFECT', 'STRIKE_DEFECT', 'STRIKE_DEFECT']
  - termination_reason=truncated_at_max_decisions:50
- fixed50:2488-29: character=NECROBINDER encounter=KNOWLEDGE_DEMON_BOSS classification=A_normal_long_combat data_usage=usable_partial
  - decision_count=50 unique_state_count=50 repeated_state_count=0
  - enemy_hp_progression=[399, 392, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388, 388]
  - player_hp_progression=[61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61]
  - last_10_actions=['DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION']
  - termination_reason=truncated_at_max_decisions:50
- fixed50:6773-30: character=REGENT encounter=KNOWLEDGE_DEMON_BOSS classification=B_heuristic_stagnation data_usage=usable_partial
  - decision_count=50 unique_state_count=50 repeated_state_count=0
  - enemy_hp_progression=[399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399]
  - player_hp_progression=[75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75, 75]
  - last_10_actions=['DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION', 'DISINTEGRATION']
  - termination_reason=truncated_at_max_decisions:50
- fixed50:1642-31: character=NECROBINDER encounter=KNOWLEDGE_DEMON_BOSS classification=B_heuristic_stagnation data_usage=usable_partial
  - decision_count=50 unique_state_count=50 repeated_state_count=0
  - enemy_hp_progression=[399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399, 399]
  - player_hp_progression=[66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66, 66]
  - last_10_actions=['End Turn', 'End Turn', 'End Turn', 'End Turn', 'End Turn', 'End Turn', 'End Turn', 'End Turn', 'End Turn', 'End Turn']
  - termination_reason=truncated_at_max_decisions:50
- fixed50:741-40: character=SILENT encounter=FROG_KNIGHT_NORMAL classification=B_heuristic_stagnation data_usage=usable_partial
  - decision_count=50 unique_state_count=50 repeated_state_count=0
  - enemy_hp_progression=[199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199, 199]
  - player_hp_progression=[70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70, 70]
  - last_10_actions=['End Turn', 'UNTOUCHABLE', 'DEFEND_SILENT', 'End Turn', 'BACKFLIP', 'DEFEND_SILENT', 'DEFEND_SILENT', 'MIND_BLAST', 'End Turn', 'ANTICIPATE']
  - termination_reason=truncated_at_max_decisions:50
- fixed50:851-44: character=NECROBINDER encounter=SOUL_NEXUS_ELITE classification=A_normal_long_combat data_usage=usable_partial
  - decision_count=50 unique_state_count=50 repeated_state_count=0
  - enemy_hp_progression=[254, 254, 247, 241, 241, 241, 241, 241, 241, 241, 241, 228, 228, 228, 228, 228, 228, 228, 228, 228, 228, 219, 219, 219, 212, 206, 206, 206, 206, 206, 206, 206, 199, 199, 199, 192, 186, 186, 186, 186, 177, 177, 177, 170, 161, 161, 161, 161, 152, 152, 152]
  - player_hp_progression=[54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54, 54]
  - last_10_actions=['End Turn', 'DEFEND_NECROBINDER', 'UNLEASH', 'STRIKE_NECROBINDER', 'End Turn', 'DEFY', 'DEFEND_NECROBINDER', 'DEATH_MARCH', 'End Turn', 'DEFEND_NECROBINDER']
  - termination_reason=truncated_at_max_decisions:50
- fixed50:5074-46: character=DEFECT encounter=QUEEN_BOSS classification=A_normal_long_combat data_usage=usable_partial
  - decision_count=50 unique_state_count=50 repeated_state_count=0
  - enemy_hp_progression=[630, 630, 630, 624, 621, 621, 611, 611, 611, 611, 611, 608, 608, 608, 608, 605, 605, 595, 595, 585, 581, 578, 578, 578, 573, 570, 567, 567, 567, 563, 563, 563, 560, 553, 538, 513, 509, 506, 503, 503, 496, 493, 478, 478, 474, 471, 468, 468, 468, 453, 449]
  - player_hp_progression=[82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82, 82]
  - last_10_actions=['End Turn', 'BOOST_AWAY', 'DEFEND_DEFECT', 'STRIKE_DEFECT', 'End Turn', 'End Turn', 'CHARGE_BATTERY', 'COMPACT', 'TURBO', 'STRIKE_DEFECT']
  - termination_reason=truncated_at_max_decisions:50

## Targeted max100 recheck for A long combats

- output_dir: `Combat/data/trajectories_fixed50_A_long_max100/`
- scenarios: 5 (`fixed50:3486-13`, `fixed50:6017-25`, `fixed50:2488-29`, `fixed50:851-44`, `fixed50:5074-46`)
- result: 3 complete victories, 1 still truncated at 100, 1 new isolated Heuristic/Emulator restore issue
- completed at max100:
  - `fixed50:3486-13`: 51 decisions, victory
  - `fixed50:6017-25`: 60 decisions, victory
  - `fixed50:851-44`: 77 decisions, victory
- still long:
  - `fixed50:2488-29`: 100 decisions, still in_progress, no repeated state, no no-progress flag. Enemy HP drops from 399 to 388 early and then remains at 388 while repeated `DISINTEGRATION` actions continue. Keep as `usable_partial`; do not use terminal outcome.
- newly exposed restore issue:
  - `fixed50:5074-46`: max50 was A-long; at decision 66 Heuristic candidate scoring failed because every candidate restored into an invalid QUEEN_BOSS state and surfaced `Illegal action: 0`.
  - Reproduction state after decision 65 has only `QUEEN` alive with intent `ENRAGE_MOVE`; `TORCH_HEAD_AMALGAM` has died.
  - Fresh restore/log shows Emulator-side `System.InvalidOperationException: Sequence contains no matching element` at `Queen.AfterAddedToRoom()` line 128, where Queen expects `CombatState.Enemies.First(c => c.Monster is TorchHeadAmalgam)`.
  - Classification: `exclude_heuristic_exception` for max100 output; underlying owner candidate is Emulator restore support for Queen after the Amalgam is gone. RL should not rewrite the state to re-add/replace enemies.

## Judgment

- 100-combat progression: not recommended yet until relic side-effect policy, Queen restore policy, and long-combat partial-label policy are accepted.
- Main imitation data: use `usable_complete` only for now (29/50).
- `usable_partial` exists (13/50) but terminal outcome must not be used as a win/loss label.
