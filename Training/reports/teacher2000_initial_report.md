# Training 初回報告 (teacher2000 データセット)

生成日: 2026-07-24
対象: `C:\STS2_RL\Combat\data\teacher2000_20260723_dataset`(1,964 usable trajectories, 51,173 decision rows)

## 1. 使用export

- `export_training_dataset.py` を拡張し、旧500-scenario版のネスト形式(`scenario_results.jsonl` の `result.decisions`)に加えて、teacher2000 のフラット形式(`trajectories.jsonl` 一行一decision、`final_outcome`/`termination_reason`/`data_usage_classification`/`truncation_classification` が各行に既に非正規化済み)を読めるようにした(`--source-format {auto,legacy,flat}`、既定 auto)。
- 出力: `exports/teacher2000_20260723_dataset_export_v1/`(Data Contract v1 のファイルレイアウトは変更なし)。`export_script_version: v3`。
- `validate_export.py` 実行結果: complete/partial とも `missing_counts` はほぼ空、`duplicate_decision_ids` / `teacher_missing_from_legal` / `teacher_unavailable` / `selected_index_out_of_range` / `empty_legal_actions` は全て 0 件。manifest split 間の重複も 0 件。
  - 例外: partial 10,788行中 15行で `termination_reason` が欠落(元データの `termination_reason: null` トラジェクトリ1件に対応。RL側 `dataset_summary.json` の `termination_reason_counts.null: 1` と整合)。partial は今回 Policy/Value いずれの主学習にも使っていないため実害なし。

**運用上の注記**: 実行環境が全体で 8GB RAM・ディスク残り一時 17MB という制約下にあったため、旧exporterの「全行をメモリに保持してから書き出す」実装ではOOMで途中終了した。各decision行が既に trajectory 単位の結果を非正規化済みであることを利用し、1行ずつ読んで即座に書き出すストリーミング実装に書き換えて解決(`export_training_dataset.py` の `RowAccumulator` / `iter_flat_decisions` 等)。Combat/Emulator 側のデータやコードは変更していない。

## 2. train / validation / test 件数

| dataset_kind | split | trajectories | decisions |
|---|---|---|---|
| complete | train | 1,394 | 32,027 |
| complete | validation | 174 | 4,114 |
| complete | test | 175 | 4,200 |
| partial | train | 174 | 8,607 |
| partial | validation | 22 | 1,100 |
| partial | test | 22 | 1,081 |

complete 合計 1,743 trajectories / 40,341 decisions、partial 合計 218 trajectories / 10,788 decisions(quarantine 36件 + `exclude_emulator_issue` 3件は元々除外)。

## 3. choice行の件数と扱い

- complete: `choice_card` 39件 (train 27 / validation 8 / test 4)、全40,341行中 0.10%。
- partial: `choice_card` 4件。
- 扱い: RL側から choice の目的・source zone・発生元(choice context)が渡されていないため、正解ラベルを推測せず、Policy の主学習データセットから機械的に除外(`teacher_action_type == "choice_card"` で除外、`sts2_training/dataset.py`)。除外件数はデータセット側でカウント保持。
- Value学習では観測state自体は通常のcombat stateと変わらないため除外していない(状態のみに依存し、行動を予測しないため)。
- 推論時: `PolicyDecision` が legal_actions 中に `choice_card` を検出した場合 `recommend_heuristic_fallback=True` を返す(action_typeからの構造的判定のみ。choice の意味内容は一切推測していない)。実データでの確認: テスト200件中1件が該当。

## 4. Policy対象・Value対象のeligibility件数

- Policy学習対象: complete decisions から choice_card を除いた件数 = train 32,000 / validation 4,106 / test 4,196。
- Value学習対象(`value_training_eligible`): usable_complete かつ勝敗確定行のみ = train 32,027 / validation 4,114 / test 4,200(choice_cardも含む、全件eligible)。
- usable_partial(218 trajectories, 10,788 decisions)は Policy・Value いずれの主学習からも除外(v1)。確定した最終HP・勝敗・残決定数が得られないため。将来的な bootstrapped/TD ラベリングは未着手(`exports/.../derived/README.md` に明記)。

## 5. モデル構成

- Policy: `CandidatePolicyNet`(既存アーキテクチャ変更なし)。state_dim=4560, action_numeric_dim=7, hidden_dim=64, embedding_dim=32(既定)。500-scenario版から state_dim が増加(4433→4560)したのは、より大きいデータセットで discover されたカード/ポーション/レリック/エネミー語彙が増えたため(card 548, potion 64, power 225, relic 291, enemy 105 tokens)。
- Value: 新規 `ValueNet`(`sts2_training/model.py`)。共有MLPトランク(state_dim=4560→hidden 64×2層)+ 独立3ヘッド(win probability / expected final HP fraction / expected remaining decisions)。Policy側とは別ネットワーク(重み共有なし)。損失は BCE(win) + SmoothL1(final HP) + SmoothL1(remaining decisions) の単純和(v1、重み付けは未調整)。

## 6. 学習結果

### Policy (`checkpoints/policy_teacher2000_seed_20260724`)

| | train | validation | test |
|---|---|---|---|
| top-1 accuracy | 0.8203 | 0.7755 | 0.7762 |
| top-3 accuracy | 0.9710 | 0.9623 | 0.9628 |
| top-5 accuracy | 0.9951 | 0.9917 | 0.9952 |
| illegal action rate | 0.0 | 0.0 | 0.0 |

action_type別 (test): card 0.735 (2278/3099), potion 0.782 (201/257), system 0.926 (778/840)。
500-scenario版 `official_baseline`(test top-1平均0.739 / top-3 0.937 / top-5 0.979)と比較し、データ量4倍化により全指標で改善。best_epoch=6, training_time≈1,265秒(単一seed)。

### Value (`checkpoints/value_teacher2000_seed_20260724`)

| | train | validation | test |
|---|---|---|---|
| win accuracy | 0.930 | 0.894 | 0.872 |
| final HP MAE (fraction of maxHp) | 0.139 | 0.199 | 0.174 |
| remaining decisions MAE | 6.57 | 6.66 | 7.23 |

win accuracy by outcome (test): victory 0.944 (n=3215) / defeat 0.636 (n=985) — 勝敗の不均衡(train内 victory:defeat ≈ 3.5:1)を反映し defeat 側の精度が低い。best_epoch=0(patience=3で早期停止)。training_time≈991秒(単一seed、value target生成込み)。

いずれも今回は1 seedのみ(500-scenario版は3 seed平均を報告)。複数seedでの安定性確認は次回課題。

## 7. 推論速度

`run_inference_demo.py` で test split 200 decisions を1件ずつ推論(CPU, バッチなし):

| | mean | p50 | p95 | max |
|---|---|---|---|---|
| PolicyDecision | 3.47ms | 2.99ms | 4.13ms | 86.7ms(初回呼び出し起因の外れ値) |
| ValueDetermination | 2.34ms | 2.25ms | 3.32ms | 7.71ms |

Emulator/pythonnet 不使用、チェックポイント単体から読み込み。

## 8. 保存したcheckpointとprovenance

- `checkpoints/policy_teacher2000_seed_20260724/best.pt`(37MB)
- `checkpoints/value_teacher2000_seed_20260724/best.pt`(5.5MB)
- 両チェックポイントとも `id_dictionaries.v1.json` の内容をそのまま埋め込み済み(`checkpoint["dictionaries"]`)。加えて `config` に `emulator_commit`, `emulator_dll_sha256`, `heuristic_version`, `dictionary_version`, `contract_version`, `export_script_version`, `export_root` を保存。単一ファイルで provenance が追跡可能。
  - emulator_commit: `163bf040027abca2754393a949e612e42f46a3e7`
  - emulator_dll_sha256: `673778A6452C7F066F5C0989345B1BA56504428BA249C121DA6E68F7BC8C88B0`
  - heuristic_version: `greedy_v1_default_weights`

## 9. RL担当へ渡すinference package

- `sts2_training/inference.py`
  - `PolicyDecision(checkpoint_path)(observation, legal_actions) -> {selected_action_index, confidence, ranked_action_indices, recommend_heuristic_fallback, provenance}`
  - `ValueDetermination(checkpoint_path)(observation) -> {win_probability, expected_final_hp_fraction, expected_final_hp, expected_remaining_decisions, provenance}`
- どちらもEmulator/pythonnet起動不要、checkpointファイル1つのみに依存。
- 使用例: `run_inference_demo.py`(exportの`complete_test.jsonl`から読み込んでの動作確認・レイテンシ計測スクリプト)。
- 探索時の重み付け(win_probability / expected_final_hp / expected_remaining_decisions の合成方法)はTraining側で固定していない。RL側で用途に応じて調整すること。

## 10. Data Contract上の不足事項

1. **choice_card context 不足(既知)**: 目的・source zone・発生元がexportに含まれないため、choice Policyの主学習ができない。本レポート4節の通り除外・fallback対応済み。RL側でchoice contextを追加できるか検討をお願いしたい(具体的なフィールド案は別途相談)。
2. **`observation.source.encounter` が本データセットで実質空**: teacher2000の `state` オブジェクトには `source` キー自体が存在せず(500-scenario版との差異は未確認)、exporterの `encounter_counts_top50` はほぼ `unknown` になる。Training側では `sts2_training/dataset.py` の `encounter_key()`(enemies の id 集合から合成)で代替しており学習・評価には支障ないが、RL側で `source.encounter` が意図的に省略されているのか確認をお願いしたい。
3. **usable_partial の Value 学習除外は今回のスコープ限定の判断**(Data Contract自体の不足ではない): 確定した勝敗・最終HPがないため。将来的にbootstrapped/TD学習で活用する場合は別途設計が必要。

## 付録: 生成物一覧

- `exports/teacher2000_20260723_dataset_export_v1/`(complete/partial × all/train/validation/test, manifests, id_dictionaries.v1.json, export_metadata.json, quality_report.json, REPORT.md, derived/value_targets_*.jsonl)
- `checkpoints/policy_teacher2000_seed_20260724/`, `checkpoints/value_teacher2000_seed_20260724/`
- `reports/teacher2000_policy_baseline/seed_20260724_metrics.json`(+ misclassified jsonl)
- `reports/teacher2000_value_baseline/seed_20260724_metrics.json`
- `sts2_training/value_targets.py`, `sts2_training/inference.py`, `train_value.py`, `run_inference_demo.py`
