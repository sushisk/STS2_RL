# fixed50 remaining 4 detailed report

Source:
- output dir: `C:\STS2_RL\Combat\data\trajectories_fixed50_20260721_rerun_actioncontinuation_api`

## fixed50:2428-11

- data_usage: `exclude_emulator_issue`
- termination_reason: `step_exception:TimeoutException`
- warnings: `step_exception:TimeoutException:candidate_evaluation`
- decision_count: 18
- cycle_detected: False
- no_progress_detected: False
- final_outcome: `in_progress`

### Relics
BOUND_PHYLACTERY, SCROLL_BOXES, SWORD_OF_STONE, REPTILE_TRINKET, BAG_OF_PREPARATION

### Exception summary
- classification: `emulator_step_timeout`
- failing candidate: action_id=0 label=End Turn enemy_index=None
- exception_type: `TimeoutException`
```text
Timed out waiting for the next decision point or settlement.
   at Sts2Emulator.Api.GameInstance.WaitUntilChoiceOrSettled() in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 2468
   at Sts2Emulator.Api.GameInstance.Step(Int32 actionId) in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 1895
   at InvokeStub_GameInstance.Step(Object, Span`1)
   at System.Reflection.MethodBaseInvoker.InvokeWithOneArg(Object obj, BindingFlags invokeAttr, Binder binder, Object[] parameters, Cultur
```
```text
Traceback (most recent call last):
  File "C:\STS2_RL\Combat\heuristic_agent.py", line 126, in choose_action_with_detail
    resulting = self.emulator.apply_action(
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\STS2_RL\Combat\battle_emulator.py", line 721, in apply_action
    next_state = self.step_live_action(
                 ^^^^^^^^^^^^^^^^^^^^^^
  File "C:\STS2_RL\Combat\battle_emulator.py", line 650, in step_live_action
    result = game.Step(action["action_id"])
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
System.TimeoutException: Timed out waiting for the next decision point or settlement.
   at Sts2Emulator.Api.GameInstance.WaitUntilChoiceOrSettled() in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 2468
   at Sts2Emulator.Api.GameInstance.Step(Int32 actionId) in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 1895
   at InvokeStub_GameInstance.Step(Object, Span`1)
   at System.Reflection.MethodBaseInvoker.InvokeWithOneArg(Object obj, BindingFlags invokeAttr, Binder binder, Object[] parameters, CultureInfo culture)
```

### Last recorded decision state
- decision_index: 17
- player: NECROBINDER hp=61/66 block=17 energy=1 turn=1 round=1 stepIndex=1
- relics: BOUND_PHYLACTERY, SCROLL_BOXES, SWORD_OF_STONE, REPTILE_TRINKET, BAG_OF_PREPARATION
- playerPowers: SHROUD_POWER:3
- hand: DEFEND_NECROBINDER[1], INFECTION[0], RATTLE[1]
- draw(top<=10): DEATHBRINGER[2], STRIKE_NECROBINDER[1], DEFEND_NECROBINDER[1], INFECTION[0], INFECTION[0], STRIKE_NECROBINDER[1], BODYGUARD[1], GRAVE_WARDEN[1], INFECTION[0], NEGATIVE_PULSE[1]
- discard(top<=10): (empty)
- exhaust(top<=10): DIRGE[0], DEFILE[1], ASCENDERS_BANE[0], SOUL[0], WISP[0]
- pendingChoice: null
- enemy[0]: PHROG_PARASITE hp=42/68 block=0 intent=LASH_MOVE
- enemy[1]: WRIGGLER hp=22/22 block=0 intent=WRIGGLE_MOVE

### Legal actions at last recorded decision
- id=0 | system | End Turn
- id=1 | card | DEFEND_NECROBINDER | card=DEFEND_NECROBINDER | targetType=Self | cost=1
- id=3 | card | RATTLE | card=RATTLE | targetType=AnyEnemy | cost=1

### Selected action at last recorded decision
- action_id=1 label=DEFEND_NECROBINDER type=card selected_enemy_index=None

### Candidate scores at last recorded decision
- id=1 label=DEFEND_NECROBINDER enemy=None score=33.03636363636363
- id=3 label=RATTLE enemy=0 score=32.86969696969697
- id=3 label=RATTLE enemy=1 score=32.86969696969697
- id=0 label=End Turn enemy=None score=None

### Previous decision
- decision_index: 16
- action_id=2 label=WISP type=card selected_enemy_index=None
- warnings: `(none)`

### Quality summary
- unique_state_count: 18
- repeated_state_count: 0
- enemy_hp_progression: [90, 90, 83, 77, 77, 77, 77, 77, 77, 77, 77, 70, 64, 64, 64, 64, 64, 64, 64]
- player_hp_progression: [66, 66, 66, 66, 66, 66, 66, 66, 66, 61, 61, 61, 61, 61, 61, 61, 61, 61, 61]
- last_10_actions: ['End Turn', 'GRAVE_WARDEN', 'UNLEASH', 'GRAVEBLAST', 'End Turn', 'NEGATIVE_PULSE', 'DEATHBRINGER', 'SOUL', 'WISP', 'DEFEND_NECROBINDER']

## fixed50:6314-24

- data_usage: `exclude_emulator_issue`
- termination_reason: `step_exception:TimeoutException`
- warnings: `step_exception:TimeoutException:candidate_evaluation`
- decision_count: 22
- cycle_detected: False
- no_progress_detected: False
- final_outcome: `in_progress`

### Relics
BOUND_PHYLACTERY, LEAD_PAPERWEIGHT, ANCHOR, NUNCHAKU, ROYAL_STAMP, DRIFTWOOD, BING_BONG, CENTENNIAL_PUZZLE

### Exception summary
- classification: `emulator_step_timeout`
- failing candidate: action_id=0 label=End Turn enemy_index=None
- exception_type: `TimeoutException`
```text
Timed out waiting for the next decision point or settlement.
   at Sts2Emulator.Api.GameInstance.WaitUntilChoiceOrSettled() in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 2468
   at Sts2Emulator.Api.GameInstance.Step(Int32 actionId) in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 1895
   at InvokeStub_GameInstance.Step(Object, Span`1)
   at System.Reflection.MethodBaseInvoker.InvokeWithOneArg(Object obj, BindingFlags invokeAttr, Binder binder, Object[] parameters, Cultur
```
```text
Traceback (most recent call last):
  File "C:\STS2_RL\Combat\heuristic_agent.py", line 126, in choose_action_with_detail
    resulting = self.emulator.apply_action(
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\STS2_RL\Combat\battle_emulator.py", line 721, in apply_action
    next_state = self.step_live_action(
                 ^^^^^^^^^^^^^^^^^^^^^^
  File "C:\STS2_RL\Combat\battle_emulator.py", line 650, in step_live_action
    result = game.Step(action["action_id"])
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
System.TimeoutException: Timed out waiting for the next decision point or settlement.
   at Sts2Emulator.Api.GameInstance.WaitUntilChoiceOrSettled() in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 2468
   at Sts2Emulator.Api.GameInstance.Step(Int32 actionId) in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 1895
   at InvokeStub_GameInstance.Step(Object, Span`1)
   at System.Reflection.MethodBaseInvoker.InvokeWithOneArg(Object obj, BindingFlags invokeAttr, Binder binder, Object[] parameters, CultureInfo culture)
```

### Last recorded decision state
- decision_index: 21
- player: NECROBINDER hp=60/66 block=26 energy=0 turn=1 round=1 stepIndex=1
- relics: BOUND_PHYLACTERY, LEAD_PAPERWEIGHT, ANCHOR, NUNCHAKU, ROYAL_STAMP, DRIFTWOOD, BING_BONG, CENTENNIAL_PUZZLE
- playerPowers: WEAK_POWER:2, SHROUD_POWER:2, NEUROSURGE_POWER:3, DOOM_POWER:6, SLEIGHT_OF_FLESH_POWER:13
- hand: STRIKE_NECROBINDER[1], STRIKE_NECROBINDER[1], STRIKE_NECROBINDER[1], UNLEASH[1], BOLAS[0]
- draw(top<=10): DEFY[1], REAP+[3], ENFEEBLING_TOUCH[1], GREED[0], BONE_SHARDS[1], BODYGUARD[1], SHROUD[1], DEFY[1]
- discard(top<=10): DEFEND_NECROBINDER[1], OMNISLICE[0], DEFEND_NECROBINDER[1], BONE_SHARDS[1], ENFEEBLING_TOUCH[1], ENFEEBLING_TOUCH[1], MELANCHOLY[3], DEATHBRINGER+[2], DEFY[1], MELANCHOLY[3]
- exhaust(top<=10): DISTRACTION+[0], PUTREFY[1], ASCENDERS_BANE[0], WISP[0], ENFEEBLING_TOUCH[1], DEFY[1], JACK_OF_ALL_TRADES[0]
- pendingChoice: null
- enemy[0]: BOWLBUG_EGG hp=4/24 block=0 intent=BITE_MOVE
- enemy[1]: BOWLBUG_ROCK hp=6/39 block=0 intent=HEADBUTT_MOVE
- enemy[2]: BOWLBUG_SILK hp=14/49 block=0 intent=THRASH_MOVE

### Legal actions at last recorded decision
- id=0 | system | End Turn
- id=5 | card | BOLAS | card=BOLAS | targetType=AnyEnemy | cost=0

### Selected action at last recorded decision
- action_id=5 label=BOLAS type=card selected_enemy_index=0

### Candidate scores at last recorded decision
- id=5 label=BOLAS enemy=0 score=52.27077922077922
- id=5 label=BOLAS enemy=1 score=52.27077922077922
- id=5 label=BOLAS enemy=2 score=52.27077922077922
- id=0 label=End Turn enemy=None score=None

### Previous decision
- decision_index: 20
- action_id=1 label=JACK_OF_ALL_TRADES type=card selected_enemy_index=None
- warnings: `(none)`

### Quality summary
- unique_state_count: 22
- repeated_state_count: 0
- enemy_hp_progression: [156, 156, 124, 124, 124, 124, 124, 91, 91, 91, 91, 91, 91, 91, 91, 91, 78, 78, 37, 24, 24, 24, 22]
- player_hp_progression: [66, 66, 66, 66, 66, 66, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 64, 60, 60, 60, 60, 60, 60]
- last_10_actions: ['MELANCHOLY', 'End Turn', 'SLEIGHT_OF_FLESH', 'DEFY', 'End Turn', 'NEGATIVE_PULSE', 'DEFY', 'DEFEND_NECROBINDER', 'JACK_OF_ALL_TRADES', 'BOLAS']

## fixed50:1046-32

- data_usage: `exclude_emulator_issue`
- termination_reason: `step_exception:TimeoutException`
- warnings: `step_exception:TimeoutException:candidate_evaluation`
- decision_count: 36
- cycle_detected: False
- no_progress_detected: False
- final_outcome: `in_progress`

### Relics
BOUND_PHYLACTERY, LEAFY_POULTICE, PENDULUM, CENTENNIAL_PUZZLE, VAMBRACE, POCKETWATCH, PAELS_CLAW, UNDYING_SIGIL, BONE_FLUTE, PERMAFROST, MINIATURE_CANNON, MR_STRUGGLES, BAG_OF_PREPARATION

### Exception summary
- classification: `emulator_step_timeout`
- failing candidate: action_id=0 label=End Turn enemy_index=None
- exception_type: `TimeoutException`
```text
Timed out waiting for the next decision point or settlement.
   at Sts2Emulator.Api.GameInstance.WaitUntilChoiceOrSettled() in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 2468
   at Sts2Emulator.Api.GameInstance.Step(Int32 actionId) in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 1895
   at InvokeStub_GameInstance.Step(Object, Span`1)
   at System.Reflection.MethodBaseInvoker.InvokeWithOneArg(Object obj, BindingFlags invokeAttr, Binder binder, Object[] parameters, Cultur
```
```text
Traceback (most recent call last):
  File "C:\STS2_RL\Combat\heuristic_agent.py", line 126, in choose_action_with_detail
    resulting = self.emulator.apply_action(
                ^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "C:\STS2_RL\Combat\battle_emulator.py", line 721, in apply_action
    next_state = self.step_live_action(
                 ^^^^^^^^^^^^^^^^^^^^^^
  File "C:\STS2_RL\Combat\battle_emulator.py", line 650, in step_live_action
    result = game.Step(action["action_id"])
             ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
System.TimeoutException: Timed out waiting for the next decision point or settlement.
   at Sts2Emulator.Api.GameInstance.WaitUntilChoiceOrSettled() in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 2468
   at Sts2Emulator.Api.GameInstance.Step(Int32 actionId) in C:\STS2_Emulator\Sts2Emulator\Api\GameInstance.cs:line 1895
   at InvokeStub_GameInstance.Step(Object, Span`1)
   at System.Reflection.MethodBaseInvoker.InvokeWithOneArg(Object obj, BindingFlags invokeAttr, Binder binder, Object[] parameters, CultureInfo culture)
```

### Last recorded decision state
- decision_index: 35
- player: NECROBINDER hp=9/54 block=14 energy=1 turn=1 round=1 stepIndex=1
- relics: BOUND_PHYLACTERY, LEAFY_POULTICE, PENDULUM, CENTENNIAL_PUZZLE, VAMBRACE, POCKETWATCH, PAELS_CLAW, UNDYING_SIGIL, BONE_FLUTE, PERMAFROST, MINIATURE_CANNON, MR_STRUGGLES, BAG_OF_PREPARATION
- playerPowers: SURROUNDED_POWER:1, LETHALITY_POWER:75, SLEIGHT_OF_FLESH_POWER:9, STRENGTH_POWER:-2, WEAK_POWER:2, FRAIL_POWER:2, COUNTDOWN_POWER:6
- hand: REAP[3], DEBILITATE[1], STRIKE_NECROBINDER[1], SCOURGE[1], SCOURGE[1], AFTERLIFE[1]
- draw(top<=10): DEFEND_NECROBINDER[1], BODYGUARD[1], NO_ESCAPE+[1], UNLEASH[1], DEFY+[1]
- discard(top<=10): MISERY+[0], DEFEND_NECROBINDER[1], DEFEND_NECROBINDER[1]
- exhaust(top<=10): THE_SCYTHE+[2], DIRGE[0], SHARED_FATE[0]
- pendingChoice: null
- enemy[0]: CRUSHER hp=96/219 block=0 intent=BUG_STING_MOVE
- enemy[1]: ROCKET hp=151/209 block=0 intent=CHARGE_UP_MOVE

### Legal actions at last recorded decision
- id=0 | system | End Turn
- id=2 | card | DEBILITATE | card=DEBILITATE | targetType=AnyEnemy | cost=1
- id=3 | card | STRIKE_NECROBINDER | card=STRIKE_NECROBINDER | targetType=AnyEnemy | cost=1
- id=4 | card | SCOURGE | card=SCOURGE | targetType=AnyEnemy | cost=1
- id=5 | card | SCOURGE | card=SCOURGE | targetType=AnyEnemy | cost=1
- id=6 | card | AFTERLIFE | card=AFTERLIFE | targetType=Self | cost=1

### Selected action at last recorded decision
- action_id=2 label=DEBILITATE type=card selected_enemy_index=0

### Candidate scores at last recorded decision
- id=2 label=DEBILITATE enemy=0 score=164.68535825545172
- id=2 label=DEBILITATE enemy=1 score=164.68535825545172
- id=3 label=STRIKE_NECROBINDER enemy=0 score=163.70404984423675
- id=3 label=STRIKE_NECROBINDER enemy=1 score=163.70404984423675
- id=4 label=SCOURGE enemy=0 score=164.18442367601247
- id=4 label=SCOURGE enemy=1 score=164.18442367601247
- id=5 label=SCOURGE enemy=0 score=164.18442367601247
- id=5 label=SCOURGE enemy=1 score=164.18442367601247
- id=6 label=AFTERLIFE enemy=None score=163.35358255451715
- id=0 label=End Turn enemy=None score=None

### Previous decision
- decision_index: 34
- action_id=7 label=DEFEND_NECROBINDER type=card selected_enemy_index=None
- warnings: `(none)`

### Quality summary
- unique_state_count: 36
- repeated_state_count: 0
- enemy_hp_progression: [428, 412, 400, 390, 390, 390, 386, 386, 386, 377, 373, 364, 364, 364, 360, 360, 360, 360, 347, 334, 325, 325, 325, 323, 323, 323, 308, 295, 282, 273, 273, 273, 260, 247, 247, 247, 228]
- player_hp_progression: [54, 54, 54, 54, 54, 54, 34, 34, 34, 34, 27, 27, 27, 27, 27, 27, 27, 27, 27, 18, 18, 18, 18, 18, 18, 18, 18, 18, 9, 9, 9, 9, 9, 9, 9, 9, 9]
- last_10_actions: ['MISERY', 'End Turn', 'DEFY', 'DEFEND_NECROBINDER', 'DEFEND_NECROBINDER', 'End Turn', 'MISERY', 'DEFEND_NECROBINDER', 'DEFEND_NECROBINDER', 'DEBILITATE']

## fixed50:4228-34

- data_usage: `exclude_cycle`
- termination_reason: `cycle_detected`
- warnings: `cycle_detected`
- decision_count: 4
- cycle_detected: True
- no_progress_detected: False
- final_outcome: `in_progress`

### Relics
BURNING_BLOOD, LARGE_CAPSULE, GREMLIN_HORN, VENERABLE_TEA_SET, CANDELABRA, LETTER_OPENER, PARRYING_SHIELD, SEA_GLASS, ANCHOR, ODDLY_SMOOTH_STONE, RIPPLE_BASIN, KUSARIGAMA

### Last recorded decision state
- decision_index: 3
- player: IRONCLAD hp=80/80 block=20 energy=0 turn=1 round=1 stepIndex=1
- relics: BURNING_BLOOD, LARGE_CAPSULE, GREMLIN_HORN, VENERABLE_TEA_SET, CANDELABRA, LETTER_OPENER, PARRYING_SHIELD, SEA_GLASS, ANCHOR, ODDLY_SMOOTH_STONE, RIPPLE_BASIN, KUSARIGAMA
- playerPowers: DEXTERITY_POWER:1, ENERGY_NEXT_TURN_POWER:2
- hand: GLOW[1]
- draw(top<=10): DISCOVERY[1], SETUP_STRIKE+[1], DEFEND_IRONCLAD[1], STRIKE_IRONCLAD[1], HEADBUTT[1], CHARGE[1], UPPERCUT+[2], STRIKE_IRONCLAD[1], STRIKE_IRONCLAD[1], STOKE[1]
- discard(top<=10): HEGEMONY[2], TRUE_GRIT+[1]
- exhaust(top<=10): ANGER[0], KNOW_THY_PLACE[0]
- pendingChoice: null
- enemy[0]: DEVOTED_SCULPTOR hp=157/172 block=0 intent=FORBIDDEN_INCANTATION_MOVE

### Legal actions at last recorded decision
- id=0 | system | End Turn
- id=3 | potion | TOUCH_OF_INSANITY | targetType=AnyPlayer

### Selected action at last recorded decision
- action_id=3 label=TOUCH_OF_INSANITY type=potion selected_enemy_index=None

### Candidate scores at last recorded decision
- id=0 label=End Turn enemy=None score=9.162790697674417
- id=3 label=TOUCH_OF_INSANITY enemy=None score=28.816279069767443

### Previous decision
- decision_index: 2
- action_id=1 label=KNOW_THY_PLACE type=card selected_enemy_index=None
- warnings: `(none)`

### Quality summary
- unique_state_count: 4
- repeated_state_count: 0
- enemy_hp_progression: [172, 157, 157, 157, 157]
- player_hp_progression: [80, 80, 80, 80, 80]
- last_10_actions: ['HEGEMONY', 'TRUE_GRIT', 'KNOW_THY_PLACE', 'TOUCH_OF_INSANITY']
