# RL中間報告: 未使用500 Scenario評価 Batch 1/5 (100件) — 2026-07-24

**このbatch完了後、ユーザー指示により残り4 batchは自動開始していない。以降は本報告と
判断を待つ。**

---

## 0. 結論

停止条件（5節）は**いずれも発生しなかった**。100件の範囲では合格水準を満たしている。
ただし、時間内訳（4節）から**`--measure-agreement`のshadow Heuristic評価が全体の
約38%を占める最大のコスト要因**であることが判明した——残り400件の実行方式を
判断する上で重要な材料のため、先に提示する（7節）。

---

## 1. 実行条件・出力保全

* manifest: `Combat/evaluation/online_eval/unused500_batch01_manifest.jsonl`
  （SHA256: `de13fc496dd8b79e422ee8aa593ac10531623e20306a085f5510aafad15f6a2b`、
  元の500件manifestのindex[0:100]、`batch_split_manifest.json`に対応関係を保存済み）
* Emulator commit（評価時点）: `0d1613050e3d6396004d328ba77f72177b6e872d`
* Policy checkpoint: `Training/checkpoints/policy_teacher2000_seed_20260724/best.pt`
* Value checkpoint: `Training/checkpoints/value_teacher2000_seed_20260724/best.pt`
* 実行引数: `--max-decisions 150 --max-wall-seconds 240 --measure-agreement`
  （設定変更なしで最後まで完走）
* provenance: 前回報告の`.NET Decimal`変換修正（`Combat/emulator_bridge.py::
  to_plain()`）を引き続き使用、`test_scenario_v2.py` 32/32 合格を記録済み
* 出力先: `Combat/evaluation/reports/unused500_batch01/`
  （`summary.json`, `combats.jsonl`, `decisions_policy.jsonl`, `decisions_heuristic.jsonl`,
  `branch_points.jsonl`）。全てmanifest・SHA256・commit・checkpoint・実行引数・
  集計結果・branch points・provenanceを`summary.json`へ自動記録済み。
* **error log**: 例外は`combats.jsonl`の`step_exception`、
  `decisions_policy.jsonl`の`policy_exception`/`value_exception`/
  `heuristic_shadow_exception`/`action_mapping_mismatch`/`illegal_action`
  フィールドに直接記録される設計。今回は全件走査して**該当0件**を確認した
  （3節）ため、別ファイルとしての空errorログは作成していない。
* 実行中・完了後ともコード変更なし（本報告作成中も`online_policy_eval.py`等は無変更）。

---

## 2. 集計結果（100件、quarantined 3件を除く97件が評価対象）

| | Policy | Heuristic |
|---|---|---|
| victory | 76 (78.4%) | 78 (80.4%) |
| defeat | 20 (20.6%) | 19 (19.6%) |
| truncated | 1 (1.0%) | 0 (0%) |
| **正常完走率** | **98.97%** | **100%** |
| 平均decision数 | 28.81 | 27.30 |
| 中央値decision数 | 23 | 22 |
| 平均戦闘時間(instrumentation除く) | **3.45s** | 8.47s |
| **速度比** | **2.46倍高速** | — |

**Heuristic比勝率**: 76/78 = **97.44%**

quarantined 3件は全て既知の`missing_mad_science_state`ギャップ（Policy/adapter
起因ではない、前回・前々回報告と同一カテゴリ）。

---

## 3. 停止条件チェック

| 項目 | 結果 | 判定 |
|---|---|---|
| illegal action | 0 | 発生なし |
| mapping mismatch | 0 | 発生なし |
| 再現性のあるPolicy/Value/Emulator例外 | 0（`policy_exception`/`value_exception`/`heuristic_shadow_exception`/`step_exception`を全件走査） | 発生なし |
| 正常完走率95%未満 | Policy 98.97% / Heuristic 100% | 該当なし |
| Heuristic比勝率90%未満 | 97.44% | 該当なし |
| 速度比2倍未満 | 2.46倍 | 該当なし |
| timeoutの明確な増加 | `truncated_at_time_budget`理由の件数は**0**（唯一のtruncatedは`max_decisions=150`到達、wall=102.4s——240s予算には未到達） | 該当なし |
| 出力欠落・重複 | combats.jsonl 100行=manifest 100件と一致、trajectory_id重複0件を確認 | 該当なし |

**停止条件はいずれも発生しなかった。**

---

## 4. fallback / low-confidence / target_ambiguous / 行動一致率

| 指標 | 値 |
|---|---|
| fallback件数 | **1件**（0.036%、2,795decision中） |
| fallback理由 | `choice_action_type:['choice_card', 'choice_skip']`（`unused:1988-4`decision0、`EQUILIBRIUM`選択） |
| low-confidence率 | **27.26%**（762/2,795、`confidence<0.5`） |
| target_ambiguous率 | **8.01%**（224/2,795） |
| Policy対Heuristic行動一致率 | **77.39%**（2,795decision中） |
| action_type別一致率 | card 72.6% / potion 77.9% / system 96.3% / choice_card 100%(n=1) |

いずれも前回（unused200: fallback 0.067%、low-conf 29.7%、target_ambiguous 7.9%、
一致率75.4%）とほぼ同水準——100件規模でも傾向は安定しており、急激な悪化や新規
パターンは確認されなかった。

---

## 5. 新しい再現性のあるバグ

**なし。** `combats.jsonl`/`decisions_policy.jsonl`の例外系フィールドを全件走査し
0件を確認済み（3節）。前回発見・修正した`.NET Decimal`複製バグの再発もなし。

---

## 6. 時間内訳（100件の総処理時間の分解）

**総処理時間（実測、コンソールログの各Scenario処理時間の合計）**: 1,879.6秒
（31.3分、100Scenario、model読み込み等の起動コスト・Scenario間のJSON書き込みI/Oは
含まない——後述）

| 区分 | 時間 | 割合 | 算出方法 |
|---|---|---|---|
| **Policy arm 合計**（Heuristic shadow評価込み） | 1,051.6s | 55.9% | 実測（`policy`側`wall_seconds`の合計） |
| ├─ Policy net推論 | 11.0s | 0.6% | 実測（`policy_latency_ms`合計） |
| ├─ Value推論 | 8.5s | 0.5% | 実測（`value_latency_ms`合計） |
| ├─ **Shadow Heuristic**（`--measure-agreement`用） | **717.2s** | **38.2%** | 実測（`heuristic_shadow_latency_ms`合計） |
| ├─ fallback時のHeuristic評価 | 0.1s | ~0% | 実測（fallback発生1件のみ） |
| └─ 残差（Emulator Step実行＋adapter内その他処理） | 314.8s | 16.7% | 推定（Policy arm合計からの差分） |
| **Heuristic arm 合計** | 821.4s | 43.7% | 実測（`heuristic`側`wall_seconds`の合計） |
| ├─ Heuristic自身の候補評価 | 571.8s | 30.4% | 実測（`heuristic_latency_ms`合計） |
| └─ 残差（Emulator Step実行） | 249.6s | 13.3% | 推定（Heuristic arm合計からの差分） |
| preflight_validate + episode setup（`clone_state`×2等）+ ループ諸経費 | 6.6s | 0.35% | 推定（総処理時間 − Policy arm − Heuristic arm の残差） |
| branch-point処理 | 実質ゼロ | ~0% | **未個別計測**——分岐は8件のみで、各回は軽量なdict構築のみ（他項目の残差に埋没する規模と判断） |
| その他集計・入出力（JSONL書き込み等） | 未計測 | — | **上記の総処理時間1,879.6sの外側**（Scenarioごとの計測窓の外で発生するため）。5,551行のJSONL書き込みだが、経験的には数秒規模と推定 |

**最大の所要時間要因は Shadow Heuristic（38.2%）**。これを除いたPolicy arm実質処理は
334.4s（`wall_seconds_excluding_shadow`の合計、速度比算出にはこちらを使用済み）。
次いでHeuristic自身の候補評価が571.8s（30.4%）で、Heuristic armを完全に実行する
限り縮小できない支配的コスト。

---

## 7. 残り400件の実行方式判断への示唆（判断はユーザーに委ねる）

6節の内訳から、各選択肢の概算削減効果:

| 選択肢 | 想定される総時間への影響 |
|---|---|
| 1. 現行の完全比較を継続 | 基準（400件で約7,500秒≒125分と推定） |
| 2. `--measure-agreement`を外す | Shadow Heuristic分（約38%）を削減——約4,650秒≒78分と推定。ただし行動一致率・action_type別一致率・分岐点分析が取得不能になる |
| 3. Policy-onlyを中心にHeuristicは標本のみ | Heuristic arm分（約44%）を標本比率に応じて削減可能。Heuristic比勝率の精度は標本サイズに依存 |
| 4. 残り評価を保留しchoiceデータ生成へ | 500件完了を待たず次工程へ移行 |

数値面の判断材料は以上の通り。**どの選択肢を採るかはユーザーの判断を待つ**
（指示により、次batchは開始していない）。
