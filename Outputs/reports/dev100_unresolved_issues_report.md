# dev100 unresolved issues

Source run dir: `C:\STS2_RL\Combat\data\trajectories_dev100_20260722_stepindex_w4`

This report lists every unresolved issue from the 100-scenario workers=4 batch,
along with a dedicated replay wrapper script for each case.

## 4861-21 - step_exception:TimeoutException

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_4861_21_step_exception_timeoutexception.py`
- status: `ok`
- data_usage: `exclude_emulator_issue`
- classification: `None`
- character: `None`
- encounter: `None`
- floor: `None`
- elapsed_s: `47.542`
- decision_count: `46`
- termination_reason: `step_exception:TimeoutException`
- warnings: `['step_exception:TimeoutException:candidate_evaluation']`
- final_outcome: `in_progress`
- stepIndex in saved spec: `None`
- first observed stepIndex: `0`
- last observed stepIndex: `48`
- last action: `{'action_id': 6, 'action_type': 'card', 'label': 'NIGHTMARE', 'is_available': True, 'parameters': {'cardId': 'NIGHTMARE', 'cost': 2, 'targetType': 'Self'}}`
- last enemy hps: `[149]`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_9728.log`
- relics:
- stderr excerpt:
```text
[INFO] ModelIdSerializationCache initialized. Categories: 20 Entries: 1654 Epochs: 57 Properties: 47 Hash: 4232096141
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card NEUTRALIZE (targeting  (index 1))
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card SURVIVOR (no target)
[INFO] Player 1 chose cards [NEUTRALIZE]
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card DEFEND_SILENT (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card BACKFLIP (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card STRIKE_SILENT (targeting  (index 1))
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 using potion EXPLOSIVE_AMPOULE (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 using potion LIQUID_BRONZE (targeting  (index 0))
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card SURVIVOR (no target)
[INFO] Player 1 chose cards [NEUTRALIZE]
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card DEFEND_SILENT (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card BACKFLIP (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card STRIKE_SILENT (targeting  (index 1))
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 using potion EXPLOSIVE_AMPOULE (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 using potion LIQUID_BRONZE (targeting  (index 0))
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card BACKFLIP (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card DEFEND_SILENT (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card STRIKE_SILENT (targeting  (index 1))
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card EXPERTISE (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card BLADE_OF_INK (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 using potion EXPLOSIVE_AMPOULE (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 using potion LIQUID_BRONZE (targeting  (index 0))
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card DEFEND_SILENT (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card STRIKE_SILENT (targeting  (index 1))
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card EXPERTISE (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 playing card BLADE_OF_INK (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 using potion EXPLOSIVE_AMPOULE (no target)
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNCING_FLASK]
[INFO] Player 1 using potion LIQUID_BRONZE (targeting  (index 0))
[INFO] Player 1 chose cards [DEADLY_POISON,DAGGER_THROW,BOUNC
...<truncated>...
```

## 3122-10 - truncated_at_time_budget:120.0s

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_3122_10_truncated_at_time_budget_120.0s.py`
- status: `ok`
- data_usage: `usable_partial`
- classification: `A_normal_long_combat`
- character: `None`
- encounter: `None`
- floor: `None`
- elapsed_s: `120.497`
- decision_count: `30`
- termination_reason: `truncated_at_time_budget:120.0s`
- warnings: `['truncated_at_time_budget:120.0s']`
- final_outcome: `in_progress`
- stepIndex in saved spec: `None`
- first observed stepIndex: `0`
- last observed stepIndex: `37`
- last action: `{'action_id': 0, 'action_type': 'system', 'label': 'End Turn', 'is_available': True, 'parameters': {}}`
- last enemy hps: `[6]`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_19832.log`
- relics:
- stderr excerpt:
```text
[INFO] Player 1 playing card GLITTERSTREAM (no target)
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 1))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 2))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 3))
[INFO] Player 1 playing card KNOCKOUT_BLOW (targeting  (index 1))
[INFO] Player 1 playing card KNOCKOUT_BLOW (targeting  (index 2))
[INFO] Player 1 playing card KNOCKOUT_BLOW (targeting  (index 3))
[INFO] Player 1 playing card RESONANCE (no target)
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 1))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 2))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 3))
[INFO] Player 1 using potion SPEED_POTION (targeting  (index 0))
[INFO] Player 1 using potion LUCKY_TONIC (targeting  (index 0))
[INFO] Player 1 using potion SWIFT_POTION (targeting  (index 0))
[INFO] Player 1 playing card GLITTERSTREAM (no target)
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 1))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 2))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 3))
[INFO] Player 1 playing card RESONANCE (no target)
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 1))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 2))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 3))
[INFO] Player 1 using potion SPEED_POTION (targeting  (index 0))
[INFO] Player 1 using potion LUCKY_TONIC (targeting  (index 0))
[INFO] Player 1 using potion SWIFT_POTION (targeting  (index 0))
[INFO] Player 1 using potion SPEED_POTION (targeting  (index 0))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 1))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 2))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 3))
[INFO] Player 1 playing card RESONANCE (no target)
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 1))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 2))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 3))
[INFO] Player 1 using potion LUCKY_TONIC (targeting  (index 0))
[INFO] Player 1 using potion SWIFT_POTION (targeting  (index 0))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 1))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 1))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 2))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 3))
[INFO] Player 1 using potion LUCKY_TONIC (targeting  (index 0))
[INFO] Player 1 using potion SWIFT_POTION (targeting  (index 0))
[INFO] Player 1 playing card FALLING_STAR (targeting  (index 1))
[INFO] Player 1 using potion LUCKY_TONIC (targeting  (index 0))
[INFO] Player 1 using potion SWIFT_POTION (targeting  (index 0))
[INFO] Player 1 using potion SWIFT_POTION (targeting  (index 0))
[INFO] Player 1 using potion LUCKY_TONIC (targeting  (index 0))
[INFO] Player 1 using potion LUCKY_TONIC (targeting  (index 0))
[INFO] Player 1 playing card HIDDEN_GEM (no target)
[INFO] Player 1 playing card DEFEND_REGENT (no target)
[INFO] Player 1 playing card VENERATE (no target)
[INFO] Player 1 playing card BEAT_INTO_SHAPE (targeting  (index 1))
[INFO] Player 1 playing card BEAT_INTO_SHAPE (targeting  (index 2))
[INFO] Player 1 playing card BEAT_INTO_SHAPE (targeting  (index 3))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 1))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 2))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 3))
[INFO] Player 1 playing card DEFEND_REGENT (no target)
[INFO] Player 1 playing card HIDDEN_GEM (no target)
[INFO] Player 1 playing card VENERATE (no target)
[INFO] Player 1 playing card BEAT_INTO_SHAPE (targeting  (index 1))
[INFO] Player 1 playing card BEAT_INTO_SHAPE (targeting  (index 2))
[INFO] Player 1 playing card BEAT_INTO_SHAPE (targeting  (index 3))
[INFO] Player 1 playing card STRIKE_REGENT (targeting  (index 1))
[INFO] Player 1
...<truncated>...
```

## 2080-15 - unsupported_pending_choice_type:Unsupported

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_2080_15_unsupported_pending_choice_type_unsupported.py`
- status: `quarantined`
- data_usage: `exclude_emulator_issue`
- classification: `None`
- character: `NECROBINDER`
- encounter: `ENCOUNTER.SLIMED_BERSERKER_NORMAL`
- floor: `4`
- elapsed_s: `0.623`
- decision_count: `None`
- termination_reason: `None`
- warnings: `[]`
- final_outcome: `None`
- stepIndex in saved spec: `None`
- first observed stepIndex: `None`
- last observed stepIndex: `None`
- last action: `None`
- last enemy hps: `None`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_19832.log`
- relics:
  - `BOUND_PHYLACTERY`
  - `BOOMING_CONCH`
  - `CHOSEN_CHEESE`
  - `WAR_PAINT`
  - `MERCURY_HOURGLASS`
  - `BAG_OF_PREPARATION`
  - `STURDY_CLAMP`
  - `GOLDEN_COMPASS`
  - `LOST_WISP`
  - `ORICHALCUM`
  - `THE_COURIER`
  - `REPTILE_TRINKET`
  - `TOXIC_EGG`
  - `TOOLBOX`
  - `VAJRA`
  - `REGAL_PILLOW`
  - `THROWING_AXE`
- stderr excerpt:
```text
=== END 2080-15 ===
```

## 2986-17 - unsupported_pending_choice_type:Unsupported

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_2986_17_unsupported_pending_choice_type_unsupported.py`
- status: `quarantined`
- data_usage: `exclude_emulator_issue`
- classification: `None`
- character: `NECROBINDER`
- encounter: `ENCOUNTER.EXOSKELETONS_NORMAL`
- floor: `14`
- elapsed_s: `0.043`
- decision_count: `None`
- termination_reason: `None`
- warnings: `[]`
- final_outcome: `None`
- stepIndex in saved spec: `None`
- first observed stepIndex: `None`
- last observed stepIndex: `None`
- last action: `None`
- last enemy hps: `None`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_9728.log`
- relics:
  - `BOUND_PHYLACTERY`
  - `ARCANE_SCROLL`
  - `STONE_CALENDAR`
  - `LANTERN`
  - `POTION_BELT`
  - `TOOLBOX`
  - `PAELS_TEARS`
  - `GORGET`
- stderr excerpt:
```text
=== END 2986-17 ===
```

## 3109-22 - missing_mad_science_state

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_3109_22_missing_mad_science_state.py`
- status: `quarantined`
- data_usage: `exclude_state_mismatch`
- classification: `None`
- character: `DEFECT`
- encounter: `ENCOUNTER.TEST_SUBJECT_BOSS`
- floor: `15`
- elapsed_s: `0.088`
- decision_count: `None`
- termination_reason: `None`
- warnings: `[]`
- final_outcome: `None`
- stepIndex in saved spec: `None`
- first observed stepIndex: `None`
- last observed stepIndex: `None`
- last action: `None`
- last enemy hps: `None`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_9728.log`
- relics:
  - `CRACKED_CORE`
  - `CURSED_PEARL`
  - `FESTIVE_POPPER`
  - `STRAWBERRY`
  - `CENTENNIAL_PUZZLE`
  - `JUZU_BRACELET`
  - `MOLTEN_EGG`
  - `RED_MASK`
  - `PAELS_TEARS`
  - `TINY_MAILBOX`
  - `ODDLY_SMOOTH_STONE`
  - `FAKE_LEES_WAFFLE`
  - `FAKE_HAPPY_FLOWER`
  - `HAPPY_FLOWER`
  - `DISTINGUISHED_CAPE`
  - `GOLD_PLATED_CABLES`
  - `ETERNAL_FEATHER`
  - `HORN_CLEAT`
  - `FORGOTTEN_SOUL`
- stderr excerpt:
```text
=== END 3109-22 ===
```

## 780-17 - init_exception:ArgumentNullException

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_780_17_init_exception_argumentnullexception.py`
- status: `quarantined`
- data_usage: `exclude_state_mismatch`
- classification: `None`
- character: `REGENT`
- encounter: `ENCOUNTER.CONSTRUCT_MENAGERIE_NORMAL`
- floor: `5`
- elapsed_s: `0.042`
- decision_count: `None`
- termination_reason: `None`
- warnings: `[]`
- final_outcome: `None`
- stepIndex in saved spec: `None`
- first observed stepIndex: `None`
- last observed stepIndex: `None`
- last action: `None`
- last enemy hps: `None`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_6824.log`
- relics:
  - `DIVINE_RIGHT`
  - `LEAFY_POULTICE`
  - `FESTIVE_POPPER`
  - `HORN_CLEAT`
  - `PAELS_TEARS`
  - `GIRYA`
  - `LASTING_CANDY`
  - `PANTOGRAPH`
  - `STONE_CRACKER`
  - `DUSTY_TOME`
- stderr excerpt:
```text
=== END 780-17 ===
```

## 5944-3 - no_legal_actions

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_5944_3_no_legal_actions.py`
- status: `quarantined`
- data_usage: `exclude_state_mismatch`
- classification: `None`
- character: `REGENT`
- encounter: `ENCOUNTER.PHANTASMAL_GARDENERS_ELITE`
- floor: `7`
- elapsed_s: `0.061`
- decision_count: `None`
- termination_reason: `None`
- warnings: `[]`
- final_outcome: `None`
- stepIndex in saved spec: `None`
- first observed stepIndex: `None`
- last observed stepIndex: `None`
- last action: `None`
- last enemy hps: `None`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_6824.log`
- relics:
  - `DIVINE_RIGHT`
  - `GOLDEN_PEARL`
  - `DREAM_CATCHER`
- stderr excerpt:
```text
[ERROR] System.InvalidOperationException: No valid next state found.
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.ConditionalBranchState.GetNextState(Creature _, Rng __) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\ConditionalBranchState.cs:line 53
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine.FindNextMoveState(IEnumerable`1 targets, Creature owner, Rng rng, Boolean logMove) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\MonsterMoveStateMachine.cs:line 67
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine.RollMove(IEnumerable`1 targets, Creature owner, Rng rng) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\MonsterMoveStateMachine.cs:line 36
   at MegaCrit.Sts2.Core.Models.MonsterModel.RollMove(IEnumerable`1 targets) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Models\MonsterModel.cs:line 418
   at MegaCrit.Sts2.Core.Combat.CombatManager.AfterCreatureAdded(Creature creature) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 906
   at MegaCrit.Sts2.Core.Combat.CombatManager.StartCombatInternal() in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 418
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 24
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 24
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task)
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.RunSafely(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 17
   at MegaCrit.Sts2.Core.Combat.CombatManager.AfterCombatRoomLoaded() in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 404
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.StartCombat(IRunState runState) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\CombatRoom.cs:line 230
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.StartCombat(IRunState runState)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.EnterInternal(IRunState runState, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\CombatRoom.cs:line 141
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.EnterInternal(IRunState runState, Boolean isRestoringRoomStackBase)
   at MegaCrit.Sts2.Core.Rooms.AbstractRoom.Enter(IRunState runState, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\AbstractRoom.cs:line 60
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoomInternal(AbstractRoom room, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Runs\RunManager.cs:line 1194
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoomInternal(AbstractRoom room, Boolean isRestoringRoomStackBase)
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoom(AbstractRoom room) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Runs\RunManager.cs:line 1228
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMach
...<truncated>...
```

## 4755-5 - no_legal_actions

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_4755_5_no_legal_actions.py`
- status: `quarantined`
- data_usage: `exclude_state_mismatch`
- classification: `None`
- character: `SILENT`
- encounter: `ENCOUNTER.PHANTASMAL_GARDENERS_ELITE`
- floor: `12`
- elapsed_s: `0.034`
- decision_count: `None`
- termination_reason: `None`
- warnings: `[]`
- final_outcome: `None`
- stepIndex in saved spec: `None`
- first observed stepIndex: `None`
- last observed stepIndex: `None`
- last action: `None`
- last enemy hps: `None`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_9728.log`
- relics:
  - `RING_OF_THE_SNAKE`
  - `SCROLL_BOXES`
  - `VAJRA`
- stderr excerpt:
```text
[INFO] Player 1 chose cards [LEADING_STRIKE,PREPARED,ACROBATICS]
[ERROR] System.InvalidOperationException: No valid next state found.
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.ConditionalBranchState.GetNextState(Creature _, Rng __) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\ConditionalBranchState.cs:line 53
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine.FindNextMoveState(IEnumerable`1 targets, Creature owner, Rng rng, Boolean logMove) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\MonsterMoveStateMachine.cs:line 67
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine.RollMove(IEnumerable`1 targets, Creature owner, Rng rng) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\MonsterMoveStateMachine.cs:line 36
   at MegaCrit.Sts2.Core.Models.MonsterModel.RollMove(IEnumerable`1 targets) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Models\MonsterModel.cs:line 418
   at MegaCrit.Sts2.Core.Combat.CombatManager.AfterCreatureAdded(Creature creature) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 906
   at MegaCrit.Sts2.Core.Combat.CombatManager.StartCombatInternal() in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 418
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 24
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 24
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task)
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.RunSafely(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 17
   at MegaCrit.Sts2.Core.Combat.CombatManager.AfterCombatRoomLoaded() in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 404
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.StartCombat(IRunState runState) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\CombatRoom.cs:line 230
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.StartCombat(IRunState runState)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.EnterInternal(IRunState runState, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\CombatRoom.cs:line 141
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.EnterInternal(IRunState runState, Boolean isRestoringRoomStackBase)
   at MegaCrit.Sts2.Core.Rooms.AbstractRoom.Enter(IRunState runState, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\AbstractRoom.cs:line 60
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoomInternal(AbstractRoom room, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Runs\RunManager.cs:line 1194
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoomInternal(AbstractRoom room, Boolean isRestoringRoomStackBase)
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoom(AbstractRoom room) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Runs\RunManager.cs:line 1228
   at System.Runtime.CompilerServices.Asy
...<truncated>...
```

## 2641-8 - no_legal_actions

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_2641_8_no_legal_actions.py`
- status: `quarantined`
- data_usage: `exclude_state_mismatch`
- classification: `None`
- character: `DEFECT`
- encounter: `ENCOUNTER.PHANTASMAL_GARDENERS_ELITE`
- floor: `15`
- elapsed_s: `0.035`
- decision_count: `None`
- termination_reason: `None`
- warnings: `[]`
- final_outcome: `None`
- stepIndex in saved spec: `None`
- first observed stepIndex: `None`
- last observed stepIndex: `None`
- last action: `None`
- last enemy hps: `None`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_9728.log`
- relics:
  - `CRACKED_CORE`
  - `STONE_HUMIDIFIER`
  - `REGAL_PILLOW`
  - `JUZU_BRACELET`
- stderr excerpt:
```text
[ERROR] System.InvalidOperationException: No valid next state found.
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.ConditionalBranchState.GetNextState(Creature _, Rng __) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\ConditionalBranchState.cs:line 53
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine.FindNextMoveState(IEnumerable`1 targets, Creature owner, Rng rng, Boolean logMove) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\MonsterMoveStateMachine.cs:line 67
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine.RollMove(IEnumerable`1 targets, Creature owner, Rng rng) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\MonsterMoveStateMachine.cs:line 36
   at MegaCrit.Sts2.Core.Models.MonsterModel.RollMove(IEnumerable`1 targets) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Models\MonsterModel.cs:line 418
   at MegaCrit.Sts2.Core.Combat.CombatManager.AfterCreatureAdded(Creature creature) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 906
   at MegaCrit.Sts2.Core.Combat.CombatManager.StartCombatInternal() in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 418
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 24
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 24
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task)
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.RunSafely(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 17
   at MegaCrit.Sts2.Core.Combat.CombatManager.AfterCombatRoomLoaded() in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 404
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.StartCombat(IRunState runState) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\CombatRoom.cs:line 230
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.StartCombat(IRunState runState)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.EnterInternal(IRunState runState, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\CombatRoom.cs:line 141
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.EnterInternal(IRunState runState, Boolean isRestoringRoomStackBase)
   at MegaCrit.Sts2.Core.Rooms.AbstractRoom.Enter(IRunState runState, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\AbstractRoom.cs:line 60
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoomInternal(AbstractRoom room, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Runs\RunManager.cs:line 1194
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoomInternal(AbstractRoom room, Boolean isRestoringRoomStackBase)
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoom(AbstractRoom room) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Runs\RunManager.cs:line 1228
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMach
...<truncated>...
```

## 659-6 - no_legal_actions

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_659_6_no_legal_actions.py`
- status: `quarantined`
- data_usage: `exclude_state_mismatch`
- classification: `None`
- character: `SILENT`
- encounter: `ENCOUNTER.PHANTASMAL_GARDENERS_ELITE`
- floor: `12`
- elapsed_s: `0.128`
- decision_count: `None`
- termination_reason: `None`
- warnings: `[]`
- final_outcome: `None`
- stepIndex in saved spec: `None`
- first observed stepIndex: `None`
- last observed stepIndex: `None`
- last action: `None`
- last enemy hps: `None`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_19832.log`
- relics:
  - `RING_OF_THE_SNAKE`
  - `SILVER_CRUCIBLE`
  - `TINY_MAILBOX`
  - `ODDLY_SMOOTH_STONE`
- stderr excerpt:
```text
[ERROR] System.InvalidOperationException: No valid next state found.
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.ConditionalBranchState.GetNextState(Creature _, Rng __) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\ConditionalBranchState.cs:line 53
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine.FindNextMoveState(IEnumerable`1 targets, Creature owner, Rng rng, Boolean logMove) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\MonsterMoveStateMachine.cs:line 67
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine.RollMove(IEnumerable`1 targets, Creature owner, Rng rng) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\MonsterMoveStateMachine.cs:line 36
   at MegaCrit.Sts2.Core.Models.MonsterModel.RollMove(IEnumerable`1 targets) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Models\MonsterModel.cs:line 418
   at MegaCrit.Sts2.Core.Combat.CombatManager.AfterCreatureAdded(Creature creature) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 906
   at MegaCrit.Sts2.Core.Combat.CombatManager.StartCombatInternal() in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 418
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 24
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 24
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task)
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.RunSafely(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 17
   at MegaCrit.Sts2.Core.Combat.CombatManager.AfterCombatRoomLoaded() in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 404
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.StartCombat(IRunState runState) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\CombatRoom.cs:line 230
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.StartCombat(IRunState runState)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.EnterInternal(IRunState runState, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\CombatRoom.cs:line 141
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.EnterInternal(IRunState runState, Boolean isRestoringRoomStackBase)
   at MegaCrit.Sts2.Core.Rooms.AbstractRoom.Enter(IRunState runState, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\AbstractRoom.cs:line 60
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoomInternal(AbstractRoom room, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Runs\RunManager.cs:line 1194
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoomInternal(AbstractRoom room, Boolean isRestoringRoomStackBase)
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoom(AbstractRoom room) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Runs\RunManager.cs:line 1228
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMach
...<truncated>...
```

## 5021-11 - no_legal_actions

- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_5021_11_no_legal_actions.py`
- status: `quarantined`
- data_usage: `exclude_state_mismatch`
- classification: `None`
- character: `SILENT`
- encounter: `ENCOUNTER.MYTES_NORMAL`
- floor: `5`
- elapsed_s: `0.046`
- decision_count: `None`
- termination_reason: `None`
- warnings: `[]`
- final_outcome: `None`
- stepIndex in saved spec: `None`
- first observed stepIndex: `None`
- last observed stepIndex: `None`
- last action: `None`
- last enemy hps: `None`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_19832.log`
- relics:
  - `RING_OF_THE_SNAKE`
  - `LARGE_CAPSULE`
  - `KUSARIGAMA`
  - `ETERNAL_FEATHER`
  - `TINY_MAILBOX`
  - `BOWLER_HAT`
  - `SEAL_OF_GOLD`
- stderr excerpt:
```text
[ERROR] System.InvalidOperationException: No valid next state found.
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.ConditionalBranchState.GetNextState(Creature _, Rng __) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\ConditionalBranchState.cs:line 53
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine.FindNextMoveState(IEnumerable`1 targets, Creature owner, Rng rng, Boolean logMove) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\MonsterMoveStateMachine.cs:line 67
   at MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine.MonsterMoveStateMachine.RollMove(IEnumerable`1 targets, Creature owner, Rng rng) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine\MonsterMoveStateMachine.cs:line 36
   at MegaCrit.Sts2.Core.Models.MonsterModel.RollMove(IEnumerable`1 targets) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Models\MonsterModel.cs:line 418
   at MegaCrit.Sts2.Core.Combat.CombatManager.AfterCreatureAdded(Creature creature) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 906
   at MegaCrit.Sts2.Core.Combat.CombatManager.StartCombatInternal() in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 418
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 24
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 24
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.LogTaskExceptions(Task task)
   at MegaCrit.Sts2.Core.Helpers.TaskHelper.RunSafely(Task task) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Helpers\TaskHelper.cs:line 17
   at MegaCrit.Sts2.Core.Combat.CombatManager.AfterCombatRoomLoaded() in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat\CombatManager.cs:line 404
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.StartCombat(IRunState runState) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\CombatRoom.cs:line 230
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.StartCombat(IRunState runState)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.EnterInternal(IRunState runState, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\CombatRoom.cs:line 141
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Rooms.CombatRoom.EnterInternal(IRunState runState, Boolean isRestoringRoomStackBase)
   at MegaCrit.Sts2.Core.Rooms.AbstractRoom.Enter(IRunState runState, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Rooms\AbstractRoom.cs:line 60
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoomInternal(AbstractRoom room, Boolean isRestoringRoomStackBase) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Runs\RunManager.cs:line 1194
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMachine)
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoomInternal(AbstractRoom room, Boolean isRestoringRoomStackBase)
   at MegaCrit.Sts2.Core.Runs.RunManager.EnterRoom(AbstractRoom room) in C:\STS2_Emulator\Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Runs\RunManager.cs:line 1228
   at System.Runtime.CompilerServices.AsyncMethodBuilderCore.Start[TStateMachine](TStateMachine& stateMach
...<truncated>...
```
