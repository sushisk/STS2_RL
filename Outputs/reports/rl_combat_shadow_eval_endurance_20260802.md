# Combat Search: Shadow評価・耐久試験 報告 (2026-08-02)

基準commit: `376f321`(Combat Search Vertical Slice v1完了時点)。
実装はCodex(`codex exec`)へ委任し、評価設計・差分レビュー・完成判定はRL担当が行った。

## 1. Shadow評価結果

**再現コマンド**:
```
python Combat/search/shadow_evaluation_runner.py --combats 100 --worker-count 3 --json-out <path>
```
設定: `single_round build_search_strategy`、`width=1`、`hypothesis_count=1`、`max_retries=0`、`worker_count=3`。
実行時間545.7秒(N=100、旧経路はEmulator+ModelDbを毎回再初期化するspawnサブプロセスのため1件あたり数百ms級)。
Commit: `a052032`

### 記録した指標(全て実施)

| 指標 | 結果 |
|---|---|
| 選択Action一致率 | 80/100(80%)。不一致20件は体系的(FIRE_POTION系10件・BASH系10件で旧がPotion/BASHを選び新がSTRIKE_IRONCLADを選ぶ傾向) |
| 評価値 | 旧: 平均8.21/中央値6.65。新: 平均24.10/中央値24.00(スケールが異なる別指標のため直接比較不可、旧はStateEvaluator、新は候補/探索の集約スコア) |
| 勝敗・残HP | 旧新とも残HP平均51.04で完全一致(選択直後1手のみの実結果) |
| Potion消費 | 旧: 平均0.10(10件がPotion使用)。新: 平均0.00(Potion選択候補が新経路の1位候補にならなかった) |
| 1決定時間 | 旧: 平均432.7ms(サブプロセス起動込み)。新: 中央値25.3ms(warmup込み平均84.0ms) |
| Step/Restore/Replay回数 | 旧: Step平均3.6・Restore平均4.6。新: Step一定2・Restore一定3・Replay一定1(width=1のため毎回同一パターン) |
| Worker使用率・Fault・Retry回数 | Worker使用率一定0.333(3ワーカー中1使用)。Fault/Retryとも全件0(width=1の単純シナリオでは自然発生無し) |
| Hypothesis数・探索ラウンド数・Plan Path長 | 全件 Hypothesis数=1・探索ラウンド数=1・Plan Path長=1(single-round設定のため) |

### Main Session不変性検証

別建てのMain参照セッションの`get_legal_actions()`/`get_observation()`を100戦闘の実行前後でJSON完全一致比較し、**変更無し**を確認。

### 限界(RL担当所見)

`width=1`設定のため新経路のCandidate Pipelineは毎回上位1候補のみを評価しており、複数候補比較・Fault/Retry発生・Hypothesis複数生成といった新経路の本来の能力は本run内では十分にストレスされていない。より高い`width`/`hypothesis_count`での追加比較が今後望ましい。

## 2. 耐久試験結果

**再現コマンド**:
```
python Combat/search/endurance_runner.py --combats 1000 --worker-count 3 --width 1 \
  --hypothesis-count 1 --max-retries 1 --fault-fraction 0.1 --checkpoint-interval 100 \
  --use-beam-every 10 --json-out <path>
```
Commit: `ab9e4da`

### 規模

1,000戦闘/1,000決定に到達(実行時間39.3秒)。全戦闘victory到達。

### Fault/Retry注入

決定の10%(100件)に意図的な不正SemanticActionを注入し、実際にaction_fault 200件・retry round 200件を、既存のFault taxonomy分類・retry loopが正しく処理することを確認。

### 状態混線検査(いずれも実行中に継続的に検証、事後集計ではない)

| 検査項目 | 結果 |
|---|---|
| CombatSessionId一意性 | PASS(1000/1000で重複無し) |
| worker_generation単調性 | PASS(全Workerでgeneration=1のまま、regressionは`StopConditionError`を即座に送出する設計で検証、違反無し) |
| Lease不正消費(誤ったcontext向けLease使用) | PASS(0件) |
| Snapshot整合性(dangling参照・重複InstanceId) | PASS(100戦闘ごとの定点チェック全てeligible=true・dangling_references=0。Task5で修正されたEmulator側バグの再発無し) |
| Main参照セッション不変性 | PASS(前後でget_legal_actions/get_observationが完全一致) |

### メモリ・処理時間の推移

| combat | elapsed_s | coordinator WS(MB) | worker WS範囲(MB) | 直近decision中央値(ms) |
|---|---|---|---|---|
| 100 | 12.75 | 101.5 | 103.0-105.6 | 107.6 |
| 500 | 24.35 | 102.7 | 108.5-110.9 | 17.2 |
| 1000 | 34.80 | 103.4 | 120.4-122.8 | 15.7 |

Worker Working Setは1000戦闘で約103MB→123MB程度の緩やかな増加のみ(急激なリークは無し)。決定latencyはWorker warmup後、中央値15ms前後で安定。

### 重要な限界(隠さず記録)

本1000戦闘データセットは全てStable/Terminal境界へ直接到達する構成で、**Pending境界(State-Holding Worker Lease確立の前提条件)が一度も発生しなかった**ため、`lease_issues=0`・`lease_consumptions=0`であった。Lease発行・消費の照合ロジック自体(`_assert_established_lease`等)は実装・組み込み済みで、発火すれば即座に`StopConditionError`を送出する設計だが、**Lease再利用そのものの混線耐性は本run内では実質的にストレステストされていない**。Pending境界を含む戦闘構成(複数ターン継続選択・Action Continuation等)での追加耐久試験が必要。

## 3. STOP条件の検出結果

**Shadow評価・耐久試験のいずれにおいても、重大な状態混線・Main改変・再現不能なFaultは検出されなかった。** 全て既存のFault taxonomyで正しく分類・処理され、CombatSessionId/Lease/Snapshotの整合性は維持された。作業は停止せず完了まで実施した。

## 4. 性能ボトルネックと推奨設定

- **旧経路(HeuristicAgent)の1決定あたりコスト(平均432.7ms)は、Emulator/ModelDbをプロセスごとに再初期化するspawnサブプロセス構成に起因**する測定上のオーバーヘッドが支配的であり、旧経路そのものの計算コストを正確に代表する数値ではない(公平な比較には旧経路もウォームプロセスで評価する改良が今後望ましい)。
- **新経路はWorker warmup後、中央値15-25ms程度の低latencyで安定**しており、1000戦闘規模でメモリの急激な増加や処理時間の劣化は見られなかった。
- **`worker_count=3`・`max_retries=1`は、今回検証した単純戦闘構成に対して安定して機能する**が、Pending境界を伴うより複雑な戦闘、および`width`/`hypothesis_count`を上げた高負荷構成での追加検証が推奨される。

## 5. 新経路を次工程へ進められるかの判定

**RL担当所見(判断材料の提供、最終的な移行判断は監督者に委ねる)**:

- 状態混線・Main改変・分類不能Faultは検出されず、1000戦闘規模での基本的な安定性は確認できた。
- ただし(a) Shadow評価は`width=1`の単純構成に限定されており新経路の複数候補比較能力を十分に検証していない、(b) 耐久試験はPending境界(Lease再利用)を一度も経験しておらず、この経路の混線耐性は未検証、という2点の重要な検証ギャップが残っている。
- これらのギャップを埋める追加検証(より高いwidth/hypothesis_countでのShadow評価、Pending境界を含む戦闘構成での耐久試験)を行った上での判断を推奨する。既存Agent/Trainingへの正式置換は本タスクの指示通り実施していない。

## 6. 再現コマンド・commit ID・作業ツリー状態

| 項目 | 内容 |
|---|---|
| Shadow評価 commit | `a052032` |
| 耐久試験 commit | `ab9e4da` |
| Shadow評価 再現コマンド | `python Combat/search/shadow_evaluation_runner.py --combats 100 --worker-count 3 --json-out <path>` |
| 耐久試験 再現コマンド | `python Combat/search/endurance_runner.py --combats 1000 --worker-count 3 --width 1 --hypothesis-count 1 --max-retries 1 --fault-fraction 0.1 --checkpoint-interval 100 --use-beam-every 10 --json-out <path>` |
| 作業ツリー状態 | `git status --short` clean。全変更はmainブランチへ直接commit済み、pushはしていない |
| 変更範囲 | `Combat/search/search_coordinator.py`(計装拡張)・`Combat/search/shadow_adapter.py`(計装拡張)・`Combat/search/shadow_evaluation_runner.py`(新規)・`Combat/search/endurance_runner.py`(新規)・対応テスト4ファイル(新規)のみ。`decision_context.py`/`main_loop.py`/`candidate_pipeline.py`/`branch_worker_pool.py`/`rng_hypothesis.py`/`fault_taxonomy.py`/`multi_round_search.py`/`belief_coverage.py`/`live_combat_session.py`/`combat_state_snapshot.py`/既存Agent実装/Training/Commonへの変更は無し |

## 結論

Shadow評価(実100戦闘、Action一致率80%)と耐久試験(実1000戦闘、Fault/Retry注入込み、状態混線無し)を完了した。重大な状態混線・Main改変・再現不能なFaultは検出されず、作業は停止せず完了まで実施した。ただし上記5節に記載した2つの検証ギャップ(高width/hypothesis_countでのShadow評価未実施、Pending境界を伴うLease再利用の耐久試験未実施)が残っており、これらを踏まえた上での次工程判断を推奨する。既存Agent・Trainingへの正式置換は行っていない。
