# ローカル500件教師データ生成試験 報告

作成日: 2026-07-22

## 使用Emulator / Heuristic

- Commit: `2c6dc8844cb5940f8b450b8f8f621ef5f3735a57`
- DLL SHA256: `67D4ABD46E5F1987E22184E01349A7A969A41F11C97EB48BCBAFBA0BEE5FFA69`
- Heuristic: `greedy_v1_default_weights`

## 実行条件

- 件数: `500`
- workers: `4`
- run dir:
  `C:\STS2_RL\Combat\data\trajectories_train500_20260722_w4`
- manifest:
  `C:\STS2_RL\Combat\data\trajectories_train500_20260722_w4\scenario_manifest.jsonl`
- manifest SHA256:
  `4373CEB0EA0CE3258D6FFCFA780F2D947ADAA6F9B73AA0F4F0455940637EED9F`
- dev100 manifest を除外してサンプリング
- fixed50 は既存ロジックで可能な範囲で除外

## 完了確認

- manifest件数: `500`
- scenario_results件数: `500`
- trajectory ID一意件数: `500`
- 重複: `0`
- JSONL破損: なし
- resume確認:
  - `already_completed_before_resume = 500`
  - `newly_executed_this_invocation = 0`

## 全集計

- ok: `490`
- quarantined: `10`
- usable_complete: `418`
- usable_partial: `66`
- exclude_heuristic_exception: `4`
- exclude_state_mismatch: `10`
- exclude_cycle: `1`
- exclude_emulator_issue: `1`

### 戦闘結果

- victory: `353`
- defeat: `65`
- truncated: `66`
- cycle: `1`
- no_progress: `0`

### 実行時間

- 総実行時間: `1781.2s` (`29.7分`)
- 平均/戦闘: `13.521s`
- 平均/decision: `0.4272s`
- 総decision数: `12867`

### 決定論性

- determinism checked: `10`
- matched: `10`
- rate: `100%`

## action分布

### action_type

- `card`: `9603`
- `system`: `2529`
- `potion`: `719`
- `choice_card`: `16`

### 代表的なlabel

- `End Turn`: `2529` (`19.65%`)
- `DEFEND_REGENT`: `592`
- `DEFEND_SILENT`: `572`
- `DEFEND_IRONCLAD`: `414`
- `DEFEND_DEFECT`: `398`
- `DEFEND_NECROBINDER`: `381`

極端な `End Turn` 偏重は見られない。防御カード比率は高いが、長期戦66件を含む戦闘AIとしては不自然な集中ではない。

## エラー・隔離分類

### 既知の意図的隔離

- `missing_mad_science_state`: `9`

### 新規または残存の要確認

- `heuristic_exception: RuntimeError`: `4`
  - `6304-18`
  - `787-23`
  - `2365-21`
  - `4419-24`
  - 内容: `Every legal-action candidate failed to evaluate ... ValueError: Cannot build a scenario with no living enemies`
- `step_exception:TimeoutException`: `1`
  - `5362-18`
- `init_exception:ArgumentException`: `1`
  - `7678-9`
- `cycle_detected`: `1`
  - `6588-3`
- `truncated_at_time_budget:120.0s`: `2`
  - `3412-14`
  - `6561-18`

## repro wrapper

生成先:
`C:\STS2_RL\Combat\data\trajectories_train500_20260722_w4\generated_repros`

生成数: `18`

主な対象:

- `repro_6304_18.py`
- `repro_787_23.py`
- `repro_2365_21.py`
- `repro_4419_24.py`
- `repro_5362_18.py`
- `repro_6588_3.py`
- `repro_7678_9.py`
- `repro_3286_20.py`
- `repro_4106_17.py`
- `repro_5929_20.py`
- `repro_7316_19.py`
- `repro_4708_25.py`
- `repro_2363_18.py`
- `repro_1847_18.py`
- `repro_5816_16.py`
- `repro_5051_19.py`
- `repro_3412_14.py`
- `repro_6561_18.py`

## 判定

### 使用可能な部分

- `usable_complete = 418` は模倣学習の予備データとして使用可能
- `usable_partial = 66` は理由付きで保持可能だが、終端教師としては慎重に扱う

### 本格生成前に必要な修正

1. Heuristic candidate 全滅 (`RuntimeError`) 4件の原因切り分けと修正
2. `step_exception:TimeoutException` 1件の再現確認
3. `init_exception:ArgumentException` 1件の初期化入力確認
4. `cycle_detected` 1件の再現確認
5. `missing_mad_science_state` 9件は収集データ側での保存項目補完が必要

## 結論

500件はローカルで安定実行でき、`workers=4`、`resume`、逐次保存、repro生成、
決定論性確認は成立した。

ただし、未解決の Heuristic / Timeout / init / cycle が少数残っているため、
Azure Spot VMでの本格大量生成へ進む前に、これらの再現と分類を先に片付けるのが妥当。

予備データとしては、少なくとも `usable_complete 418件` は利用可能。
