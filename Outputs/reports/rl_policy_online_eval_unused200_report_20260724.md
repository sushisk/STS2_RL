# RL次工程報告: 未使用200 Scenario評価 (2026-07-24)

前段: `Outputs/reports/rl_policy_online_eval_initial_report_20260724.md`（known10 + unused50）

## 0. 結論

**500件評価へ進めることを推奨する。** 合格条件（5節）を全て満たした。実行中に
Combat側の未検証コードパスで1件バグを発見・修正したが、Emulator/Trainingは無変更。

---

## 1. 実行条件（指示通り）

* `max_decisions = 150`
* `max_wall_seconds = 90.0`（既定値のまま。4節で後述する通り、次段階では
  引き上げを推奨）
* PolicyとHeuristicは同一の初期Scenario（`preflight_validate`→`clone_state`）から
  独立に1戦闘ずつ実行
* quarantinedは両者共通で評価対象から除外（4件、全てPolicy/adapterと無関係の既知ギャップ）
* Valueは毎decisionで推論・ログのみ、行動選択には不使用
* AnyEnemyはfirst-alive方式を維持、`target_ambiguous`として記録
* choice系（`choice_card`/`choice_skip`/`choice_confirm`）はHeuristic fallbackを維持
* `--measure-agreement` 有効化（PolicyとHeuristicの行動一致率、および分岐点分析に必要なため）
* Emulator・Trainingは無変更（2節のバグ修正はCombat側のみ）

manifest: `Combat/evaluation/online_eval/unused_200_manifest.jsonl`
（`unused_50`の入れ子上位集合、validation/test/benchmark splitから抽出、
teacher2000生成に一度も使われていない）

出力: `Combat/evaluation/reports/online_eval_unused200_20260724/`
（`combats.jsonl`, `decisions_policy.jsonl`, `decisions_heuristic.jsonl`,
`branch_points.jsonl`, `summary.json`）

---

## 2. 実行中に発見・修正したバグ（Combat側のみ、Emulator/Training無変更）

初回実行（200件中153件処理後）が `TypeError: cannot pickle 'Decimal' object` で
異常終了した。原因は `Combat/battle_emulator.py::clone_state()`
（本評価harnessが初めて呼び出したメソッド——既存コードで`clone_state`/
`with_shuffle_seed`と同じdeepcopyパターンを使うのは`LookaheadSearcher`のみだが、
教師データ生成は`lookahead_searcher=None`の greedy path しか使っておらず、この
経路は本評価harnessが最初の実exerciseだった）が `engine_state` を
`copy.deepcopy` する際、DEFECT系のorb値（`basePassiveValue`/`baseEvokeValue`）が
`Combat/emulator_bridge.py::to_plain()` を通っても生の .NET `Decimal` オブジェクトの
ままになっており、`deepcopy`がこれを複製できなかったため。

**修正**: `to_plain()` に `System.Decimal` → Python `float` への変換を追加した
（`battle_emulator.py`の`_decimal_value()`と同じstr経由変換パターン）。
`Combat/tests/test_scenario_v2.py`のフルスイート（32件）で回帰なしを確認
（修正前は既存の`test_orbs_survive_restore_with_order_and_mutable_values`が
そもそもこの経路を通っておらず「31 passed」だったが、`clone_state`を初めて
実運用したことで**別の**同型バグ——`float(value)`が pythonnet の Decimal
ラッパーで動作しない——を新たに踏んだため、修正を2段階で行い最終的に
「32 passed, 0 failed」）。`C:\STS2_Emulator`・`Training/`は一切変更していない。

修正後、200件を最初からクリーン再実行し、以降クラッシュなし。

---

## 3. 結果

### 3.1 victory / defeat / truncated

| | Policy | Heuristic |
|---|---|---|
| n（評価対象、quarantined除く） | 196 | 196 |
| victory | 154 (78.6%) | 160 (81.6%) |
| defeat | 38 (19.4%) | 33 (16.8%) |
| truncated | 4 (2.0%) | 3 (1.5%) |
| **正常完走率** | **97.96%** | **98.47%** |

quarantined 4件（`missing_mad_science_state`×3、`card_state_mismatch`×1）は
いずれも既存の既知ギャップ（前回報告4節参照）で、Policy/adapter起因ではない。

### 3.2 速度・decision数

| | Policy | Heuristic |
|---|---|---|
| 平均decision数 | 30.4 | 28.1 |
| 平均戦闘時間（instrumentation除く） | **4.23s** | 10.78s |
| **速度比** | **2.55倍高速** | — |

### 3.3 illegal action / Policy exception / action mapping mismatch

**全て0**（両arm、196戦闘・約5,950decisionを通じて）。

### 3.4 fallback件数と理由

**4件**（0.067%、5,954decision中）。既知10件・未使用50件では実発生ゼロだったが、
200件規模で初めてchoice系decisionが出現し、設計通りHeuristicへ委譲された。

| 理由 | 件数 |
|---|---|
| `choice_action_type:['choice_card', 'choice_skip']` | 3 |
| `choice_action_type:['choice_card']` | 1 |

### 3.5 low-confidence率

**29.70%**（1,767/5,950decision、`confidence<0.5`）。前回報告（known10 31.0%、
unused50 29.9%）とほぼ同水準——200件規模でも安定した傾向であり、規模拡大に伴う
悪化はない。指示通り行動は変更していない。

### 3.6 target_ambiguous率

**7.88%**（469/5,950decision）。AnyEnemyかつ生存敵2体以上でPolicyがターゲットを
指定できない decisionの割合。2節のfirst-alive方式を維持。

### 3.7 PolicyとHeuristicの行動一致率

**75.38%**（5,950decision中、Heuristicの反実仮想選択との比較）。known10 84.8%、
unused50 76.1%、unused200 75.4%——規模が増えるにつれて緩やかに低下しているが、
悪化ペースは小さく、下げ止まりつつあるように見える。

### 3.8 encounter別勝率

72種の異なるencounter（敵構成）が出現。**n≥3のencounterでPolicy勝率が0%になった
ケースはゼロ**（致命的崩れなし）。サンプル数が多いencounter（n≥5、22種類）でも
両arm近い水準で推移しており、特定encounterへの系統的な崩れは確認されなかった。
唯一両arm共に苦戦したのは `THE_INSATIABLE`（policy 1/6, heuristic 2/6）——
Policy固有ではなく、両方にとって難しい相手と判断できる。

### 3.9 残存HP・Potion残量

| | Policy | Heuristic |
|---|---|---|
| 平均残存HP | 34.7 | 34.9 |
| 平均Potion残量 | 0.026 | 0.031 |

ほぼ同水準。

### 3.10 Policyだけが敗北したScenario（13件）

`unused:1781-12`, `unused:4044-7`, `unused:5009-22`, `unused:6885-6`,
`unused:5513-4`, `unused:6261-16`, `unused:6421-11`, `unused:2685-11`,
`unused:1867-4`, `unused:6442-21`, `unused:6194-20`, `unused:2677-6`,
`unused:4537-12`

### 3.11 Heuristicだけが敗北したScenario（8件）

`unused:3637-6`, `unused:2047-11`, `unused:6166-6`, `unused:1094-8`,
`unused:5906-18`, `unused:7444-10`, `unused:5791-13`, `unused:1138-22`

---

## 4. 追加分析: 最初に行動が分岐したdecision（branch point）

結果が異なった22 Scenario全てで、Policyが実際に訪れた状態でのHeuristic反実仮想選択
（`--measure-agreement`のshadow評価）と初めて一致しなくなったdecisionを記録した
（`branch_points.jsonl`、各エントリに observation・legal actions・Policy ranking/
confidence・Heuristic選択・Value出力・両arm最終結果を保存）。

* 分岐decision_indexの分布: 最小0、**中央値2**、最大33——**多くの分岐は戦闘のごく
  早期（1〜数手目）で起きている**。早い分岐ほどその後の展開全体に影響しやすいため、
  終盤の僅差ではなく序盤の選択差が最終結果を左右するケースが多いことを示唆する。

代表例（詳細は`branch_points.jsonl`参照）:

* `unused:1781-12`（decision 0、**Policyが敗北した例**）: Policy が confidence
  0.907 で `SPEED_POTION` を選択、Heuristicは`NEGATIVE_PULSE`を選択。
  結果: Policy defeat(HP0) / Heuristic victory(HP27)。Policyが高確信度で選んだ
  初手が実際には悪手だった実例——mismatch/exceptionではなく、純粋な意思決定品質の
  差。
* `unused:3637-6`（decision 33、**Policyが勝利した例**）: Policyが`NO_ESCAPE`を
  confidence 0.999で選択、Heuristicは`End Turn`を選択。結果: Policy
  victory(HP11) / Heuristic defeat(HP0)——逆にPolicyがHeuristicより良い判断を
  した実例。

両方向の実例が存在し、一方的にPolicyが劣っているわけではないことが確認できる。

---

## 5. 合格判断（500件評価へ進めるか）

| 条件 | 結果 | 判定 |
|---|---|---|
| illegal / exception / mapping mismatchが全て0 | 0 / 0 / 0 | **合格** |
| 正常完走率95%以上 | Policy 97.96% / Heuristic 98.47% | **合格** |
| Heuristic比で明確な高速化を維持 | 2.55倍高速 | **合格** |
| 勝率がHeuristicの90%以上 | 154/160 = **96.25%** | **合格** |
| 特定action typeやencounterに致命的な崩れがない | n≥3で勝率0%のencounterなし、fallback理由もchoice系のみ | **合格** |

**全条件を満たしたため、500件評価へ進めることを推奨する。**

---

## 6. 500件評価に向けた推奨事項

1. **`--max-wall-seconds` の引き上げ推奨**: 4件のtruncatedのうち3件は
   `truncated_at_time_budget:90.0s` が原因（うち1件は`--measure-agreement`の
   shadow評価オーバーヘッドがPolicy側の実効時間予算を圧迫したケース）。
   正常完走率は95%基準を満たしているため500件評価をブロックするものではないが、
   500件では長期戦の絶対数が増える可能性があるため、`--max-wall-seconds`を
   180〜240s程度へ引き上げることを推奨する。
2. **`--measure-agreement` の扱い**: 行動一致率・分岐点分析にはshadow評価
   （Heuristicのフル候補評価をPolicyの全decisionでも実行）が必要だが、これは
   Policy arm自体の実測速度を押し下げる（本報告の速度比2.55倍は shadow時間を
   除外した値）。500件では全体の実行時間が大きくなるため、時間節約を優先する
   場合は`--measure-agreement`を外す（行動一致率・分岐点分析は今回までのデータで
   十分な傾向が掴めている）か、両方の値を報告する運用を続けるかは判断事項とする。
3. コード変更は不要（1節の実行条件のまま500件評価に進められる）。
