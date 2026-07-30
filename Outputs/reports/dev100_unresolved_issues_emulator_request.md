# dev100未解決事象 調査・修正依頼

作成日: 2026-07-22

## 対象

100戦闘バッチ実行 (`workers=4`) で確認された未解決事象のうち、`A_normal_long_combat` と判断した 1 件を除く 10 件を、Emulator 側調査対象として依頼します。

- source run dir: `C:\STS2_RL\Combat\data\trajectories_dev100_20260722_stepindex_w4`
- 詳細レポート: `C:\STS2_RL\Outputs\reports\dev100_unresolved_issues_report.md`
- 個別repro script: `C:\STS2_RL\Combat\data\repros_dev100_unresolved`

除外した件:

- `3122-10`
  - `truncated_at_time_budget:120.0s`
  - RL側分類: `A_normal_long_combat`
  - この段階ではEmulator調査対象に含めない

## 依頼概要

以下の 10 件について、添付の個別 repro script を用いて再現確認と原因調査をお願いします。

### 1. step exception / timeout

#### `4861-21`

- issue: `step_exception:TimeoutException`
- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_4861_21_step_exception_timeoutexception.py`
- status: `ok`
- RL側利用区分: `exclude_emulator_issue`
- termination_reason: `step_exception:TimeoutException`
- warnings: `step_exception:TimeoutException:candidate_evaluation`
- decision_count: `46`
- first observed stepIndex: `0`
- last observed stepIndex: `48`
- last action:
  - `action_id=6`
  - `action_type=card`
  - `label=NIGHTMARE`
  - `targetType=Self`
- last enemy hps: `[149]`
- stderr log: `C:\Users\Hatsune Miku\AppData\Local\Temp\sts2_rl_worker_9728.log`

依頼内容:

- 候補評価中の `Step()` が `TimeoutException` で終了する原因調査
- 同一入力での再現性確認
- `NIGHTMARE` 実行近辺での内部待機・非終端タスク・進行停止の有無確認

### 2. unsupported pending choice type

#### `2080-15`

- issue: `unsupported_pending_choice_type:Unsupported`
- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_2080_15_unsupported_pending_choice_type_unsupported.py`
- status: `quarantined`
- RL側利用区分: `exclude_emulator_issue`
- character: `NECROBINDER`
- encounter: `ENCOUNTER.SLIMED_BERSERKER_NORMAL`
- floor: `4`
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

#### `2986-17`

- issue: `unsupported_pending_choice_type:Unsupported`
- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_2986_17_unsupported_pending_choice_type_unsupported.py`
- status: `quarantined`
- RL側利用区分: `exclude_emulator_issue`
- character: `NECROBINDER`
- encounter: `ENCOUNTER.EXOSKELETONS_NORMAL`
- floor: `14`
- relics:
  - `BOUND_PHYLACTERY`
  - `ARCANE_SCROLL`
  - `STONE_CALENDAR`
  - `LANTERN`
  - `POTION_BELT`
  - `TOOLBOX`
  - `PAELS_TEARS`
  - `GORGET`

依頼内容:

- `pendingChoice.choiceType = Unsupported` が返る条件の特定
- StartOfCombat / ActionContinuation のどちらに属する choice かの確認
- 公開API上で復元可能な選択種別へ正規化できるかの確認
- もし未対応 choice 種別が必要なら、識別可能な正式 enum / scope / restorable 情報の提供要否を判断

### 3. MAD_SCIENCE 状態不足

#### `3109-22`

- issue: `missing_mad_science_state`
- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_3109_22_missing_mad_science_state.py`
- status: `quarantined`
- RL側利用区分: `exclude_state_mismatch`
- character: `DEFECT`
- encounter: `ENCOUNTER.TEST_SUBJECT_BOSS`
- floor: `15`
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

依頼内容:

- `MAD_SCIENCE` の内部状態が Observation / Scenario 復元経路で不足する原因調査
- `TinkerTimeType` / `TinkerTimeRider` または同等内部値が、このケースで欠落していないか確認
- 既存修正済みケースとの差分確認

### 4. combat init exception

#### `780-17`

- issue: `init_exception:ArgumentNullException`
- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_780_17_init_exception_argumentnullexception.py`
- status: `quarantined`
- RL側利用区分: `exclude_state_mismatch`
- character: `REGENT`
- encounter: `ENCOUNTER.CONSTRUCT_MENAGERIE_NORMAL`
- floor: `5`
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

依頼内容:

- `ResetFromScenario` / 初期化経路での `ArgumentNullException` 原因特定
- null だった具体的な入力状態・オブジェクトの特定
- 特定レリック・敵編成・room開始処理との関連有無の確認

### 5. no legal actions after scenario restore

以下 5 件は、いずれも LegalActions が空のまま非終端となり、stderr に `System.InvalidOperationException: No valid next state found.` が記録されています。

#### `5944-3`

- issue: `no_legal_actions`
- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_5944_3_no_legal_actions.py`
- character: `REGENT`
- encounter: `ENCOUNTER.PHANTASMAL_GARDENERS_ELITE`
- floor: `7`
- relics:
  - `DIVINE_RIGHT`
  - `GOLDEN_PEARL`
  - `DREAM_CATCHER`

#### `4755-5`

- issue: `no_legal_actions`
- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_4755_5_no_legal_actions.py`

#### `2641-8`

- issue: `no_legal_actions`
- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_2641_8_no_legal_actions.py`

#### `659-6`

- issue: `no_legal_actions`
- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_659_6_no_legal_actions.py`

#### `5021-11`

- issue: `no_legal_actions`
- wrapper: `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_5021_11_no_legal_actions.py`

共通依頼内容:

- `No valid next state found.` に至る MonsterMove / Combat start 復元経路の調査
- 復元直後に LegalActions が空になる原因の特定
- 特定の敵 move state / branching condition / 開始時状態不足との関連確認
- stepIndex, start-of-combat choice, forced move, pending choice のいずれかが未反映でないか確認

補足:

- `5944-3` では stderr 上の stack trace が `MonsterMoveStateMachine.ConditionalBranchState.GetNextState(...)` 起点で記録されています
- 残り 4 件も同種である可能性が高いため、共通調査を優先して問題ありません

## RL側での現時点判断

- `3122-10` は長期戦として許容し、差し戻し対象から除外
- 上記 10 件は RL 側だけでは説明を閉じられないため、Emulator 側調査対象
- すべて保存済み run から個別 repro script を生成済み
- 今回の依頼書作成では Emulator 実行・Emulator コード変更は行っていない

## 参照ファイル

- 詳細レポート: `C:\STS2_RL\Outputs\reports\dev100_unresolved_issues_report.md`
- repro script 一覧:
  - `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_4861_21_step_exception_timeoutexception.py`
  - `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_2080_15_unsupported_pending_choice_type_unsupported.py`
  - `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_2986_17_unsupported_pending_choice_type_unsupported.py`
  - `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_3109_22_missing_mad_science_state.py`
  - `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_780_17_init_exception_argumentnullexception.py`
  - `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_5944_3_no_legal_actions.py`
  - `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_4755_5_no_legal_actions.py`
  - `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_2641_8_no_legal_actions.py`
  - `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_659_6_no_legal_actions.py`
  - `C:\STS2_RL\Combat\data\repros_dev100_unresolved\repro_5021_11_no_legal_actions.py`
