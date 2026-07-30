# RL初回報告: Policy / Valueオンライン評価 (2026-07-24)

## 0. まとめ

Training担当から引き渡された teacher2000 Policy/Value checkpoint を、先読みなしで
Combat に接続し、`known10`(既知) と `unused50`(未使用・held-out) の2段階評価を完了した。

* illegal action: **0**、Policy exception: **0**、action mapping mismatch: **0**（両段階とも）
* 勝率比: known10 = Heuristic比 **100%**、unused50 = Heuristic比 **97.2%**（決定数上限を
  適切な値に補正後は **100%**、詳細は3.3節）
* 速度: Heuristicの **約2.7〜5倍高速**（instrumentation負荷を除く）
* fallback（choice_card等）: 両段階とも実発生 **0件**（想定通り低頻度。ルーティング自体は
  合成テストで別途検証済み、3.5節）
* **200件評価へ進めることを推奨**（4節）。ただし `max_decisions` を60→150程度へ引き上げること
  を前提条件とする（3.3節で判明した唯一の要修正点）。

---

## 1. 使用checkpointとprovenance

| | Policy | Value |
|---|---|---|
| checkpoint | `Training/checkpoints/policy_teacher2000_seed_20260724/best.pt` | `Training/checkpoints/value_teacher2000_seed_20260724/best.pt` |
| dataset_version / contract_version | v1 / v1 | v1 / v1 |
| export_script_version | v3 | v3 |
| heuristic_version (teacher) | `greedy_v1_default_weights` | 同左 |
| checkpoint内 emulator_commit | `163bf040027abca2754393a949e612e42f46a3e7` | 同左 |
| checkpoint内 emulator_dll_sha256 | `673778A6...C88B0` | 同左 |

**評価時に実際に使用したEmulator**: `C:\STS2_Emulator` の現在のHEAD、commit
`0d1613050e3d6396004d328ba77f72177b6e872d`（今回の初期指示で指定された choice context
対応版。`163bf04`→`12df954`→`0d16130` の順で choice_card の operation/source/origin を
`parameters` へ追加する2コミットが進んでいる）。

**バージョン差分の影響評価**: 追加されたのは `choice_card` legal_action の
`parameters` 内の新規キーのみで、Policy/Value が読む既存キー（`hp`, `hand`,
`drawPile`, `enemies` 等）のスキーマは変わっていない。かつ `choice_card` を含む
decisionは本adapterが構造的にHeuristic fallbackへ回す（3.5節）ため、Policyはこの
新規フィールドを一切参照しない。したがって今回の評価スコープでは
`163bf04`(学習時) と `0d16130`(評価時) の差はPolicy/Valueの入出力に影響しない。
念のため明記して報告する（checkpointを学習し直す必要はないと判断）。

checkpointは読み取り専用で使用（上書きなし）。

---

## 2. adapter実装内容

新規ファイル（すべて `Combat/` 配下、`Training/`・`Emulator/` は無変更）:

* **`Combat/policy_agent.py`** — `PolicyAgent` クラス。`sts2_training.inference.
  PolicyDecision`/`ValueDetermination` をprocess起動時に1回だけロードし、
  `decide(battle_state, legal_actions, deadline)` で1decision分の判断＋ログレコードを返す。
  * **ルーティング**: `legal_actions` の `action_type` が `{system, card, potion}` の
    みなら Policy、`choice_card`/`choice_skip`/`choice_confirm`（またはその他未知の
    action_type）が1つでも混在すれば既存の `HeuristicAgent.choose_action_with_detail`
    へ全委譲（Heuristic側は既に汎用的にどのaction_typeも評価できるため、再実装なし）。
  * **action mapping（正規化）**: `normalize_legal_action()` が生の
    `{action_id, action_type, label, is_available, parameters}` を、Training側
    `export_training_dataset.py::normalize_action()` と**構造的に同一**な
    `{action_id, action_type, label, is_available, card_id, potion_id, target_type,
    target_enemy_index, raw_parameters}` へ変換する。`Training/` を直接import・依存は
    せず（作業範囲外のため）、この9行のマッピングを独立に再実装している——2つの実装が
    将来ズレた場合は例外にならない静かな train/serve skew になるため、Training側の
    実装を変更する際は必ずこちらも合わせて確認すること、とdocstringに明記した。
  * **ターゲット解決**: `AnyEnemy` 系のcard/potionで生存敵が2体以上の場合、Policyの
    `legal_actions` はどの候補にも解決済みenemy targetを持たない（学習データ自体、
    teacherの選択ですら target_enemy_index が None のまま——検証済み）。今回は
    追加探索（apply_actionによる候補スコアリング）を行わず、`target_index=0`
    （最初の生存敵）に固定する設計とし、`target_ambiguous`/`alive_enemy_count` を
    ログへ残す。理由: 初期指示の「探索は低信頼局面／教師生成時のみ」を字義通り
    守るため。既知の限定事項として4節・5節で扱う。
  * **低信頼**: `confidence < 0.5` の場合、`low_confidence=True` を記録するのみで
    行動は変更しない（指示通り）。
  * **Value**: 毎decisionで推論・ログに保存するが、行動選択には一切使わない
    （指示通り、初回評価でValueにPolicy行動を上書きさせていない）。
  * **例外・mismatch処理**: Policy推論自体の例外、`selected_action_index` が
    `legal_actions` の範囲外、選択actionの `is_available=False` の3ケースを
    `action_mapping_mismatch`/`policy_exception` として記録し、いずれも
    Heuristic fallbackへ落とす（サイレントに補正しない）。
* **`Combat/evaluation/online_eval/build_unused_manifests.py`** — teacher2000生成が
  一度も触っていない `floor_states_{validation,test,benchmark}.jsonl`
  （`run_trajectory_batch.py::load_reconstructed_sample()` 自身のコメント通り、
  dev sampling はtrainのみ使用）から、既存の `reconstruct_floor_state.
  encounter_to_scenario_spec()` を再利用して unused50/200/500 manifest
  （`unused_{50,200,500}_manifest.jsonl`、50⊂200⊂500の入れ子）を生成。
* **`Combat/evaluation/online_eval/online_policy_eval.py`** — 評価ハーネス本体。
  同一初期状態（`preflight_validate` → `emulator.clone_state()`）からPolicy側と
  Heuristic側を独立に1戦闘ずつ走らせ、必須ログ（5節）をper-decision/per-combatで
  JSONL保存し、`summary.json` に集計を出力する。

---

## 3. 評価結果

### 3.1 既知10件（`fixed_50_scenarios.json[:10]`、長年の回帰用固定セット）

出力: `Combat/evaluation/reports/online_eval_known10_20260724/`

| 指標 | Policy+fallback | Heuristic baseline |
|---|---|---|
| 勝率 | 9/10 (90%) | 9/10 (90%)（同一シナリオで敗北） |
| illegal action | 0 | 0 |
| exception | 0 | 0 |
| action mapping mismatch | 0 | 0 |
| 非終端LegalActions空 | 0 | 0 |
| 正常進行率 | 100% | 100% |
| 平均decision数 | 14.5 | 16.0 |
| 平均戦闘時間(instrumentation除く) | 1.92s | 9.50s（**4.95倍高速**） |
| fallback使用 | 0 | — |
| Heuristicとの同一行動率 | 84.8%（145 decisions中） | — |

### 3.2 未使用50件（`unused_50_manifest.jsonl`、validation/test/benchmark splitから抽出、
teacher2000生成に一度も使われていない）

出力: `Combat/evaluation/reports/online_eval_unused50_20260724/`

* 50件中2件が `preflight_validate` により `missing_mad_science_state` で
  quarantine（MAD_SCIENCEカードの `tinker_time_type`/`rider` が復元データに
  欠落——既存の既知ギャップ、Policy/adapterとは無関係、13.5節相当の既知事項）。
  評価対象は48件。

| 指標 | Policy+fallback | Heuristic baseline |
|---|---|---|
| 勝率 | 35/48 (72.9%) | 36/48 (75.0%) |
| illegal action | 0 | 0 |
| exception | 0 | 0 |
| action mapping mismatch | 0 | 0 |
| 非終端LegalActions空 | 0 | 0 |
| 正常進行率 | 93.75%（3件が`max_decisions=60`到達） | 93.75%（同3件中2件が重複） |
| 平均decision数 | 26.8 | 26.9 |
| 平均戦闘時間(instrumentation除く) | 2.81s | 7.72s（**2.75倍高速**） |
| fallback使用 | 0 | — |
| Heuristicとの同一行動率 | 76.1%（1287 decisions中） | — |

Policy/Heuristicで最終結果が食い違ったのは5件（Policy優位2件、Heuristic優位3件）。

### 3.3 正常進行率が95%未達だった件の原因切り分け（重要）

3.2節の「正常進行率93.75%」は初期指示の合格条件（95%以上）を僅かに下回った。
該当4シナリオ（`unused:256-18`, `unused:5012-13`, `unused:6213-7`, `unused:2047-11`）
のみ `--max-decisions 150 --max-wall-seconds 240` で再実行したところ、
**両arm・4件全てが正常に終端まで到達**した（正常進行率100%、illegal/exception/
mismatchは引き続き0）。すなわち原因は **本ハーネスの `max_decisions=60` という
設定値が一部の長期戦（実データ由来の27枚デッキ級デッキ等）に対して単純に小さすぎた
だけ**であり、Policy固有の不安定性・ループ・例外ではないと確認できた
（両arm対称に発生している点も一致）。

この4件を高予算版の結果に差し替えた場合の unused50 正味の結果:

| 指標 | Policy+fallback | Heuristic baseline |
|---|---|---|
| 勝率（補正後） | 36/48 (**75.0%**) | 36/48 (75.0%) |
| 正常進行率（補正後） | **100%** | **100%** |

補正後は勝率が完全に一致する。**200件評価からは `max_decisions` を150程度へ
引き上げることを推奨**（`generate_heuristic_trajectories.py` 側の既存デフォルトが
時間予算主体・decision数50〜だったことを踏まえても、reconstructed scenarioには
より長い決着を要する個体が一定数含まれる）。

### 3.4 Policy / Value 推論速度

| | mean | p50 | p95 | n |
|---|---|---|---|---|
| Policy decision（known10） | 5.11ms | 4.00ms | 12.55ms | 145 |
| Value（known10） | 3.95ms | 3.14ms | 9.39ms | 145 |
| Policy decision（unused50） | 3.38ms | 3.16ms | 4.98ms | 1287 |
| Value（unused50） | 2.82ms | 2.69ms | 3.88ms | 1287 |

Training報告の単体ベンチ（Policy 3.47ms / Value 2.34ms、バッチなしCPU）と同じ
オーダー。harness側のログ処理オーバーヘッドを含んでもなお、Heuristicの
1decisionあたり評価コスト（候補×対象ごとにapply_actionで仮実行）より大幅に軽い。

### 3.5 fallback発生件数と理由

**両段階合計で実発生0件**（choice_card等は母集団で約0.1%とTraining報告にある通り
低頻度で、58戦闘中には出現しなかった）。ルーティング自体が正しく機能することは、
実戦闘とは別に合成legal_actions（`choice_card`×2 + `choice_skip`×1）を渡す直接テストで
確認済み——`decision_source == "heuristic_fallback"`、
`fallback_reason == "choice_action_type:['choice_card', 'choice_skip']"` を確認。

### 3.6 illegal / exception / mismatch件数

既知10件・未使用50件・3.3節の再検証4件、全55戦闘・約1,750decisionを通じて:

* illegal action: **0**
* Policy exception: **0**
* action mapping mismatch: **0**
* 非終端でLegalActions空: **0**

### 3.7 Heuristicとの速度差

* known10: **4.95倍高速**（instrumentation=`--measure-agreement`のシャドウ評価時間を
  除外した実測）
* unused50: **2.75倍高速**

---

## 4. 既知の限定事項・要フォローアップ

1. **AnyEnemyターゲット解決**: Policyはターゲットを予測しない（学習データ自体
   target_enemy_indexを一切含んでいない、teacherの実選択すら）。現在は「最初の
   生存敵」固定で、追加探索はしていない（2節）。多体戦での取りこぼしが疑われる
   場合は、専用ターゲットモデルの追加、または限定的な1手先読みの許可をTraining/RL
   間で協議のうえ検討する余地がある。
2. **低信頼(confidence<0.5)の発生率がやや高い**: known10で31.0%(45/145)、
   unused50で29.9%(385/1287)。指示通り行動は変更せず記録のみに留めているが、
   softmax分布が合法アクション数に応じて自然に平坦化する影響もあり、
   閾値0.5が適切かはキャリブレーション観点で再検討の価値がある。
3. **`max_decisions` は150以上を推奨**（3.3節）。
4. **`missing_mad_science_state` によるquarantine**（2/50）は既存の既知ギャップで
   本adapter起因ではない。頻度は低いが200/500件評価でも一定数発生しうる。
5. **checkpoint学習時のEmulator commit(`163bf04`)と評価時(`0d16130`)の差分**は
   1節で評価済み・影響なしと判断。choice教師データ生成着手前にAzure側を
   `0d16130` へ同期する必要がある点は初期指示の通り未着手（本タスクのスコープ外）。

---

## 5. 必須ログ実装

各decisionで以下を保存済み（`decisions_policy.jsonl`/`decisions_heuristic.jsonl`）:
selected action, confidence, ranked actions, Policy latency, Value出力, Value latency,
fallback有無と理由, inference exception, action mapping mismatch。モデルは
`build_policy_agent()` によりprocess起動時に1回のみロードされる
（`PolicyAgent.__init__`）。

---

## 6. 200件評価へ進めるか

**進めることを推奨する。** 根拠:

* illegal action / Policy exception / action mapping mismatch: 全て0（合格）
* 正常進行率: `max_decisions`を150へ引き上げれば100%（3.3節で実証済み、要件反映のみ）
* 勝率: 補正後 Heuristic比 **100%**（known10・unused50とも） — 「90%以上=有望」を
  明確にクリア
* 速度: Heuristicの2.75〜4.95倍高速
* fallbackルーティングは実戦闘での発生こそ0件だったが、独立した合成テストで
  正しく機能することを確認済み

次段階で反映すべき変更は `--max-decisions 150`（または同等の緩和）のみ。
コード変更は不要。
