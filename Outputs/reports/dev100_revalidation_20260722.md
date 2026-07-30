# dev100再検証報告

作成日: 2026-07-22

## 使用Emulator

- Commit: `2c6dc8844cb5940f8b450b8f8f621ef5f3735a57`
- DLL: `C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll`
- SHA256: `67D4ABD46E5F1987E22184E01349A7A969A41F11C97EB48BCBAFBA0BEE5FFA69`

## RL側修正

### 変更ファイル

- `C:\STS2_RL\Combat\battle_emulator.py`
- `C:\STS2_RL\Combat\data\preflight_validate.py`
- `C:\STS2_RL\Combat\data\README.md`
- `C:\STS2_RL\Combat\tests\test_scenario_v2.py`

### 対応内容

- Observation -> Scenario 復元で `playerPowers[].associatedCard` を
  `PowerStack.AssociatedCard` へ引き渡すよう修正
- `NIGHTMARE_POWER` に `associatedCard` が無い場合は推測せず
  `missing_associated_card:NIGHTMARE_POWER` として隔離
- state key に `playerPowers[].associatedCard` を反映
- preflight で `player_powers` の associated card 差分を比較対象に追加
- StartOfCombat choice (`ToolboxChooseCard` を含む) は既存の
  `PendingChoice` / `LegalActions` 処理へそのまま統合
- `MAD_SCIENCE` の `tinker_time_type` / `tinker_time_rider` 欠損は
  引き続き `missing_mad_science_state` で固定隔離
- README に将来収集データで保持すべき動的状態を追記

## associatedCard欠損時ルール

- 対象: 現時点では `NIGHTMARE_POWER`
- ルール:
  - `playerPowers[].associatedCard` が観測に存在する場合のみ復元
  - 欠損時は補完・推測しない
  - preflight で `missing_associated_card:NIGHTMARE_POWER` として隔離

## 個別再確認結果 (fresh process)

source run dir:
`C:\STS2_RL\Combat\data\trajectories_dev100_20260722_stepindex_w4`

### 解消した件

- `4861-21`
  - 旧: `step_exception:TimeoutException`
  - 新: `ok`, `decision_count=50`, `truncated_at_max_decisions:50`
  - Timeout / ArgumentException は再発せず
- `2080-15`
  - 旧: `unsupported_pending_choice_type:Unsupported`
  - 新: `ok`, `11 decisions`, `victory`
- `2986-17`
  - 旧: `unsupported_pending_choice_type:Unsupported`
  - 新: `ok`, `30 decisions`, `victory`
- `780-17`
  - 旧: `init_exception:ArgumentNullException`
  - 新: `ok`, `20 decisions`, `defeat`
- `5944-3`
  - 旧: `no_legal_actions`
  - 新: `ok`, `31 decisions`, `defeat`
- `4755-5`
  - 旧: `no_legal_actions`
  - 新: `ok`, `19 decisions`, `victory`
- `2641-8`
  - 旧: `no_legal_actions`
  - 新: `ok`, `44 decisions`, `victory`
- `659-6`
  - 旧: `no_legal_actions`
  - 新: `ok`, `48 decisions`, `victory`
- `5021-11`
  - 旧: `no_legal_actions`
  - 新: `ok`, `33 decisions`, `victory`

### 意図どおり残した件

- `3109-22`
  - 新: `quarantined`
  - 理由: `missing_mad_science_state`
  - 方針どおり教師データ対象外

## dev100再実行

新run directory:
`C:\STS2_RL\Combat\data\trajectories_dev100_20260722_assoc_toolbox_w4`

manifest:

- 元: `C:\STS2_RL\Combat\data\trajectories_dev100_20260722_stepindex_w4\scenario_manifest.jsonl`
- 新: `C:\STS2_RL\Combat\data\trajectories_dev100_20260722_assoc_toolbox_w4\scenario_manifest.jsonl`
- SHA256 両方一致:
  `FA2301A9F4E560E4FC039367D7267A97E13660D3AD0D10961B7A5BCF36474A00`

### 集計

- total: `100`
- ok: `99`
- quarantined: `1`
- usable_complete: `89`
- usable_partial: `10`
- exclude_state_mismatch: `1`
- illegal_action: `0`
- heuristic_exception: `0`
- emulator_step_exception: `0`
- timeout: `0`
- cycle: `0`
- no_progress: `0`
- determinism: `5/5`
- truncated: `10`
- truncation classification: `A_normal_long_combat: 10`

### 残存隔離

- `3109-22`
  - `missing_mad_science_state`

### error summary

- total_error_events: `1`
- error_kind_counts:
  - `quarantine: 1`
- error_type_counts:
  - `missing_mad_science_state: 1`

### repro wrapper

- `C:\STS2_RL\Combat\data\trajectories_dev100_20260722_assoc_toolbox_w4\generated_repros\repro_3109_22.py`

## resume確認

同一 out dir に対して再度 `--resume` を実行し、以下を確認:

- `already_completed_before_resume = 100`
- `newly_executed_this_invocation = 0`

## 判断

`unsupported_pending_choice`、`no_legal_actions`、`step_exception:TimeoutException`
は解消。残存は方針どおりの `missing_mad_science_state` 1件のみで、全100件は
成功または説明可能な隔離へ分類済み。

この状態で、500〜1,000戦闘の小規模教師生成準備へ進行可能。
