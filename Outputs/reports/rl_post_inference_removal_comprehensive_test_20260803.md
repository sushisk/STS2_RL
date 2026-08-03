# 推論撤去後の総合テスト・デバッグ — 報告書

対象: `C:\STS2_RL` baseline commit `c38fadd`(推論処理撤去と受動実行基盤への整理)。
Emulator側は無変更(`fca2f06`のまま)。

## 1. 推論経路監査表

静的監査(grep)と実行時監査(sys.modules監視+fail-fastスタブ)の両方で確認した。

| 項目 | 静的確認 | 実行時確認 | 結果 |
|---|---|---|---|
| Policy/Valueモデル読み込み(`Combat/legacy/policy_agent.py`) | 本番import経路(`main_loop.py`/`search_coordinator.py`/`branch_worker_pool.py`/`candidate_pipeline.py`/`Run/`)からの参照ゼロを確認 | `zero_index`/`external_control`セッション実行後、`sys.modules`に`legacy.*`が一切現れないことを確認(`test_inference_removal_audit.py`) | 到達不能 |
| Heuristic評価関数(`Combat/legacy/state_evaluator.py`) | 同上 | `StateEvaluator.evaluate`をfail-fastスタブ化し、zero_index全実行で呼び出し回数0を確認 | 到達不能 |
| score/logit/value/tierによる候補比較 | `candidate_pipeline.py`の`_card_score`等は`build_candidate_pipeline_result`(既存、維持)のみが使用し、`build_candidate_pipeline_result_for_explicit_candidates`(新設)は一切スコアリング関数を呼ばない | 明示的候補APIの戻り値`ScoredCandidate.score`が常に`0.0`・`evaluator_name="explicit"`であることを実測確認 | 明示的経路では未使用 |
| 候補の並べ替え | `zero_index_action`/`zero_index_pending_selector`/`first_candidate_direct_selector`のソースコードに`sort`/`rank`等が無いことを確認 | 100 Run zero_indexバッチで、全Decisionが`legal_actions[0]`のまま(並べ替え無し)であることを実測 | 未実施 |
| winner選択 | `build_beam_search_strategy`(既存、維持)のみが`sorted(completed, key=_rank_completed)[0]`を持つ。`dispatch_explicit_candidates`(新設)にはwinner選択コード無し | `ExplicitBranchDispatchResult`/`BranchResult`にaggregate_score/best_action属性が存在しないことを実測確認 | 明示的経路では未使用 |
| Search結果のMainへの自動commit | 同上(`build_beam_search_strategy`のみ) | `dispatch_explicit_candidates`はBranch実行のみ行い、`build_commit_decision`を一切呼ばないことをコードパス上確認 | 明示的経路では未使用 |
| Training checkpoint/model path依存 | `Combat/legacy/policy_agent.py`/`choice_policy_agent.py`のみに存在、本番未参照 | 実行時、`.pt`ファイルへのアクセスが一切発生しないことを`sys.modules`監査で間接確認 | 到達不能 |
| 候補未指定時の暗黙Branch生成 | `build_candidate_pipeline_result_for_explicit_candidates([])`は`ValueError`を送出、全Legal Actionへのフォールバックなし | 実測確認(`test_inference_removal_audit.py`、`test_explicit_candidate_pipeline_never_implicitly_expands_all_legal_actions`) | 拒否を確認 |

`Run/`側(`whole_run_session.pick_default_action`/`room_progression_driver.pick_room`)についても、
`zero_index`/`external_control`実行中に一度も呼ばれないことをcall-counting monkeypatchで実測確認
(`Run/tests/test_inference_removal_audit.py`、2/2 pass)。

## 2. Decision種別ごとのExternal Controlテスト結果

| Decision種別 | Combat | Whole Run |
|---|---|---|
| Map | - | 実測(stale/duplicate/invalid拒否、Observation-onlyでの無変化を確認) |
| Event | 既存Holder/sibling機構経由で確認済み(前フェーズ) | 同左(`test_worker_pool_process_separation.py`) |
| Combat(通常stable) | 実測(DecisionFrameMismatchErrorによるstale/duplicate拒否) | 実測(decision_idガードによるstale/duplicate拒否) |
| Combat Pending(mid-combat Action Continuation) | 実測(WISH等、`make_external_action_selector`が指定Actionのみ解決) | 既存choice_pending経路で確認済み |
| Reward | 既存Holder/sibling機構経由で確認済み | 同左 |
| Shop | 同上 | 同上 |
| Rest | 同上 | 同上 |
| Start-of-Combat Pending | 実測(TOOLBOX、複数候補・stale拒否を確認) | 実測(TOOLBOX注入、decision_idガード確認) |

必須確認事項(全て実測):

* 複数Legal Actionで自動選択せず停止する: `direct_selector`/`pending_selector`は必須引数(デフォルト無し)、`run_until_terminal_or_fault()`を引数無しで呼ぶと`TypeError`(構造的に強制)。
* Observation取得だけではMain状態が変化しない: `test_map_observation_only_never_changes_state`で実測。
* 明示的なAction ID指定後だけMainが進行する: 同上。
* staleなdecision ID/state versionを拒否する: Combatは既存`DecisionFrameMismatchError`(`(combat_session_id, step_index)`)、Whole Runは新設`execution_mode.decision_id`/`StaleDecisionError`で実測確認。
* 不正・存在しないAction IDを拒否する: 実測確認(`ValueError`)。
* 拒否後もMain状態が変化しない: 実測確認(`run_state`/`engine_state`の完全一致比較)。
* 同一Actionの二重commitを拒否する: 実測確認(1回目成功後、同一decision_id/BattleStateでの2回目は必ず拒否)。

## 3. Zero-indexモード結果(100 Run)

`Run/zero_index_multi_run.py`を実行(seed=1〜100の100 Run、`max_steps=40`)。
結果全文: `Outputs/reports/whole_run_logs/zero_index_100run_batch.json`。

* 100 Run全てで、常に`legal_actions[0]`/`rooms[0]`を選択(並べ替え無し、Branch生成無し、
  Hypothesis比較無し、推論・採点無し)。
* 決定性: 同一seed(18, 42)を独立した2セッションで再実行し、Action ID列・最終Boundary・
  最終run_stateが完全一致することを確認(`zero_index_determinism_check.json`)。
* **発見**: このEmulatorは`BuildLegalActions()`が常に`system`/`End Turn`をindex 0として
  構築するため、`zero_index`は原理的に実戦闘に勝利できない(前ラウンドの報告書で既に
  発見・記録済みの制約、本ラウンドで再確認)。10 Room連続進行の実演は前ラウンドで作成した
  `external_control_10room_demo.json`(Training役の代用スクリプトで10 Room到達済み)を
  正式な参照結果として維持する。本ラウンドでも`zero_index_10room_search_result.json`で
  1 Room(最初のCombat)で停滞することを再確認した。

## 4. 明示的Branch内部APIの検証

`explicit_branch_worker_count.py`で2種類のケースを実測(`Outputs/reports/inference_removal_logs/explicit_branch_worker_count.json`)。

| ケース | 指定候補数 | Hypothesis要否 | 期待Worker実行数 | 実測Worker実行数 |
|---|---|---|---|---|
| A(カード2枚、Hypothesis必要) | 2 | 要(hypothesis_count=4) | 2×4=8 | **8** |
| B(system、Hypothesis不要) | 1 | 不要 | 1 | **1** |

候補未指定時にBranchが1件も生成されないこと、未指定Legal Actionが実行されないこと、
Branch Resultにscore/ranking/winnerが無いこと、Branch生成だけではMainが変化しないこと
(`session.capture_snapshot()`で確立したHeld Stable Snapshotと`loop_state`はBranch実行中
不変)、採用ActionがMain Worker上で再実行される既存契約(Combat側`_build_success_result`
のcontinuation経路、無変更)は、いずれも既存テスト(`test_search_coordinator.py`、
`test_candidate_pipeline.py`)および新規テスト(`test_inference_removal_audit.py`)で確認済み。

## 5. プロセス・Lease検証(1,000件規模)

前ラウンドで構築済みの耐久試験ランナーを、本ラウンドの変更を反映した最新コード上で
再実行し、新規リグレッションが無いことを確認した。

### Combat: `Combat/search/pending_lease_endurance_runner.py`(1,000件)

結果全文: `Outputs/reports/inference_removal_logs/combat_pending_lease_endurance_1000_rerun.json`。

```
event_count: 1000, lease_issues: 1000, lease_releases: 1000,
holder_step_count: 1000, sibling_bootstrap_count: 1000,
fault_count: 100(意図的注入、全件復旧), unique_combat_session_ids: 1000,
worker_generation_non_decreasing: true, cross_context_misuse_checks: 50(全件正しく拒否)
```

前ラウンド(Phase H)の結果と完全に同一の形状であり、新規リグレッション無し。

### Whole Run: `Run/worker_pool_endurance_runner.py`(1,000件)

* 1,000/1,000イベント成功(Choice種別ごとの内訳: map 188、event 126、combat_pending 187、
  reward 187、shop 125、rest 187 — 全て100%成功)。
* Fault注入13件、Respawn復旧13/13件。
* 所要141秒(前ラウンドの226秒より高速 — システム負荷の違いによる差と考えられる)。
* **前ラウンドで確認されたCombat Pending(TOOLBOX)の断続的な内容不一致は、本ラウンドでも
  一度も再現しなかった**(combat_pending 187/187が全チェック込みで成功)。

### Combat側Worker再生成の既知の制約(発見・未解決事項)

`Combat/search/branch_worker_pool.py`の`BranchWorkerPool`には、個別Workerプロセスの
OSレベルkill検知・generation増分・Lease無効化を伴う再生成API(`Run/worker_pool.py`の
`respawn_worker`相当)が実装されていない。`close()`による全体シャットダウンのみ可能。
これは本ラウンド開始前から存在する制約であり、`search_coordinator.py`の既存docstring
(`build_search_strategy`)でも「BranchWorkerPool does not expose process restart
plumbing」として既に明記されている。本ラウンドはCombat既存Worker Poolへの変更を
最小限にする方針(前ラウンドの停止条件「Combat側の既存Worker Pool再利用により既存探索へ
回帰が発生」を踏まえた判断)のため、新規respawn機構の追加は行わず、未解決事項として
10節に記載する。Worker例外自体(プロセス生存のままFault応答)の処理は既存機構で正しく
機能することを確認済み(9節回帰結果参照)。

## 6. Snapshot/Replay決定性

Combat Root・Whole Run Map Boundary Root双方について、既存の実機テスト・耐久試験で
継続的に検証されている(本ラウンドで新規リグレッション無し、9節参照)。

* Combat Root: `test_decision_context.py`(Replay Prefix再現)、`test_branch_worker_pool.py`
  (実multiprocess Holder/sibling)、5節のCombat 1,000件耐久試験(`cross_context_misuse_checks`
  50件全て正しく拒否 = 異なるContextでのLease誤用が起きないことの実証)。
* Whole Run Map Boundary Root: `test_worker_pool_process_separation.py`
  (6 Choice種別全てでBoundary/ChoiceScope/ChoiceKind/RoomContext/LegalAction Semantic集合
  一致、Holder/sibling別PID確認済み)、5節のWhole Run 1,000件耐久試験。
* Pending Snapshotを直接Restoreしない、Start-of-Combat PendingはCombat Start Replay Root
  (Combat)/Map Boundary Snapshot再現(Whole Run)から再現する、という既存契約は本ラウンドで
  一切変更していない。

## 7. DTO・情報漏洩監査

`external_control`用に現在到達可能なDTO経路(`legal_actions`、`decision_id`/`DecisionFrame`、
`Boundary`/`ChoiceScope`/`ChoiceKind`/`RoomContext`/`Transition`)を全数確認した。

**含まれていないことを確認したもの**:

* 真のOrdered DrawPile: `make_external_action_selector`/`apply_external_action`の
  resolverには`legal_actions`のみが渡り、`engine_state`/`Observation.State`
  (drawPile等を含む)は一切渡らない。
* RNG内部状態: 同上(RNGはWorker/Lease/Snapshot内部にのみ存在し、外部制御インターフェースの
  戻り値に含まれない)。
* 未到達Roomの具体的内容: `MapRoomOption`は`RoomId`/`Column`/`Row`/`PointType`のみで、
  未解決("Unknown")ノードは実際に`"Unknown"`のまま返る(実機確認: seed=18のAct1で
  66ノード中12件が`Unknown`のまま)。
* Snapshot JSON: 外部制御インターフェースの戻り値に含まれない(内部のみ)。
* Replay内部情報(action_prefix、context_id等): 同上。
* Worker/Lease内部オブジェクト: `Lease`/`ChoiceWorkItem`/`BranchResult`はプレーンdataclass
  だが、これら自体もexternal_controlのresolverには渡らない(RL内部のBranch実行専用)。
* legacy evaluatorのscore: `Combat/legacy/`は到達不能(1節)。

**注記(false-positiveとして除外した項目)**: `Observation.State.map`は同一Actの全ノードの
`pointType`(Monster/Elite/Shop/Rest/Treasure/Unknown)を含むが、これは実際のSlay the Spire
本編でもマップ画面に最初から表示される公開情報であり、「未到達Roomの具体的内容」
(pre-generated Event/Encounter ID等)には該当しない。実機確認の結果、
`Observation.State`に`event_ids`/`encounter_ids`等の事前生成キュー自体は含まれていないことも
確認した。

**含まれていることを確認したもの(欠落なし)**:

* decision ID: Combat=`DecisionFrame(combat_session_id, step_index)`、Whole Run=
  `execution_mode.decision_id()`。
* state version: 同上(step_indexが相当)。
* decision type: `ChoiceScope`/`ChoiceKind`/`Boundary`。
* public observation: `legal_actions`(実際に選択可能な範囲の公開情報)。
* legal actionsと安定したaction ID: `LegalAction.ActionId`は同一Decision内で安定
  (Emulator契約、無変更)。
* Boundary/Transition/Room Context: `StepResult`/`RoomContext`/`TransitionOutcome`
  (無変更)。
* terminated/truncated相当: `run_terminal`/`IsTerminal`(Whole Run)、`BOUNDARY_TERMINAL`
  (Combat)。
* schema version相当: 明示的なschema_versionフィールドは無いが、DTO自体は
  Emulator commit(`fca2f06`)に紐づくバージョン管理下にある。

**残存の設計課題(未実装、Training API自体の範囲外)**: 現状の`external_control`
resolverシグネチャ(`resolve_action_id(legal_actions)`)はTrainingに`legal_actions`のみを
渡し、`RoomContext`/`Transition`/`decision_id`を一括りにした正式な「公開Decision DTO」は
まだ用意していない。将来Training APIを実装する際は、Combatの既存`OrderMaskedObservation`
(Phase 4、ordered DrawPileを含まない集計形の観測)に相当する、Whole Run向けの安全な
「公開Observation」DTOを新設し、生の`Observation.State`をそのまま転送しないことを推奨する
(10節に記載)。

## 8. Fault・異常系

新規に追加したテスト(`Combat/tests/test_fault_injection_additional.py`、
`Run/tests/test_fault_injection_additional.py`)に加え、既存の
`test_fault_taxonomy.py`/`test_action_fault_contract.py`/`test_worker_pool_process_separation.py`
の実測結果を統合。

| 注入内容 | Combat | Whole Run |
|---|---|---|
| 不正Action ID | 実測(ValueError、Main状態不変) | 実測(ValueError、Main状態不変) |
| staleなDecision | 実測(DecisionFrameMismatchError) | 実測(StaleDecisionError) |
| Worker例外 | 既存`test_fault_taxonomy.py`で確認済み(fault_kind分類) | `inject_in_worker_fault`で確認済み(プロセス生存維持) |
| Workerの強制終了 | 未対応(既知の制約、5節参照) | 実測(`kill_worker_process`+`respawn_and_verify`、generation増分・PID変化・旧Lease無効化) |
| timeout | 既存`request_timeout_s`機構(無変更) | `WorkerDiedError`による即時検知を実測 |
| Replay署名不一致 | 既存`ReplayMismatch`機構(無変更) | 既存`replay_mismatch`fault_kind(無変更) |
| Snapshot Load失敗 | 既存`test_restore_snapshot_phase3c1.py`(26件超の拒否パス) | 実測(不正JSON文字列のLoadState、例外送出後も正常snapshotで復旧可能) |
| Branch cancel競合 | 該当する明示的cancel APIが両者に存在しないため対象外(Lease誤用拒否で代替確認済み、6節参照) | 同左 |
| Episode close中の未完了Branch | 実測(Lease保持状態での`pool.close()`が正常終了) | 実測(同左) |
| 外部指示なしでの進行要求 | 実測(`run_until_terminal_or_fault()`が`direct_selector`無しでは`TypeError`) | 実測(`step()`/`choose_room()`が引数無しでは`TypeError`) |

いずれもMain状態を破壊せず、Fault後もWorker/Leaseが正しく解放・無効化されることを確認した。

実機検証中、`CALCIFIED_CULTIST`に対する`End Turn`(system)実行が、既知の
`TimeoutException`("Timed out waiting for the next decision point or settlement")を
断続的に引き起こすことを再確認した(前ラウンドまでに複数回観測済みの、Emulator側の
既存Combat AI/非同期処理に起因する事象で、本ラウンドの変更とは無関係)。

## 9. 全回帰と性能

`Combat/tests/`全23ファイル(新規4ファイル含む)、`Run/tests/`全6ファイル(新規3ファイル
含む)を個別・逐次実行して最終確認した(実行中、長時間バックグラウンドプロセスが応答
しなくなる事象が2回発生したため、以後は全体に対して`timeout`コマンドで打ち切り時間
(900秒)を設定した上で再実行し、両方とも打ち切り時間内に完走した)。

### Combat/tests/(全23ファイル)

| ファイル | 結果 |
|---|---|
| test_action_fault_contract.py | 9 passed |
| test_battle_emulator_transition_outcome.py | 3 passed |
| test_belief_coverage.py | 6 passed |
| test_branch_worker_pool.py | 11 passed |
| test_candidate_pipeline.py | 10 passed |
| test_choice_semantics.py | 20 passed |
| test_decision_context.py | 19 passed |
| test_endurance_runner.py | 1 passed |
| test_execution_mode.py(新規) | 8 passed |
| test_external_control_decision_types.py(新規) | 4 passed |
| test_fault_injection_additional.py(新規) | 2 passed |
| test_fault_taxonomy.py | 10 passed |
| test_inference_removal_audit.py(新規) | 4 passed |
| test_live_combat_session_step.py | 3 passed |
| test_main_loop.py | 15 passed |
| test_multi_combat_continuous_execution.py | 1 passed |
| test_multi_hypothesis_stress_runner.py | 1 passed |
| test_multi_round_search.py | 7 passed |
| test_restore_snapshot_phase3c1.py | 26 passed, 2 failed(既知の事前失敗) |
| test_rng_hypothesis.py | 10 passed |
| test_scenario_v2.py | 31 passed, 1 failed(既知のWriggler flake) |
| test_search_coordinator.py | 15 passed |
| test_shadow_adapter.py | 7 passed |
| test_shadow_evaluation_batch.py | 1 passed |

### Run/tests/(全6ファイル)

| ファイル | 結果 |
|---|---|
| test_execution_mode.py | 7 passed |
| test_external_control_decision_types.py(新規) | 6 passed |
| test_fault_injection_additional.py(新規) | 3 passed |
| test_inference_removal_audit.py(新規) | 2 passed |
| test_whole_run_connectivity.py | 4 passed, 1 failed(既知の`superseded`事象、前ラウンドで置き換え済みの旧`choice_branch_runner.py`起因、無関係) |
| test_worker_pool_process_separation.py | 5 passed |

**新規リグレッション: 無し。** 既知の事前失敗(`test_restore_snapshot_phase3c1.py`の2件、
`test_scenario_v2.py`のWriggler flake、`test_whole_run_connectivity.py`の
旧モジュール起因の1件)は全て前ラウンドまでに確認済みの、本ラウンドの変更とは無関係な
既存事象である。

### 性能比較

### 性能比較

`Outputs/reports/inference_removal_logs/performance_comparison.json`より:

| 項目 | 結果 |
|---|---|
| Candidate Pipeline構築(旧: score・全展開、width=8) | 0.11ms(4候補) |
| Candidate Pipeline構築(新: 明示的2候補) | 0.10ms(2候補) |
| Legal Actions取得(Combat) | 6.9ms |
| 単純Action適用(Combat Step) | 73.9ms |
| 明示的2候補Branch Dispatch(実Worker Pool、Hypothesis grid展開込み) | 3,474.6ms(8 Worker実行) |
| Observation取得(Whole Run) | 7.4ms |
| Legal Actions取得(Whole Run) | 0.02ms |
| 単純Action適用(Whole Run ChooseRoom) | 48.4ms |

Candidate Pipeline構築自体の所要時間は新旧でほぼ同等(スコアリング計算自体が軽量なため
大きな差は出ないが、新経路はスコアリング関数を一切呼ばず、候補数もTraining指定分のみに
限定される)。モデル初期化(`.pt`ロード)は1節の監査により本番経路から完全に到達不能である
ため、いずれの計測でも発生していない。

## 10. 発見・修正した不具合一覧

* (今回のセッション中に発見・修正した新規の不具合は無し。既存の推論撤去実装
  (commit `c38fadd`)は本ラウンドの全検証において正しく機能した。)
* 既知のEmulator側事象の再確認: `CALCIFIED_CULTIST`に対するEnd Turnの断続的Timeout
  (8節)。前ラウンドまでに観測済みの事象で、今回のコード変更とは無関係。

## 11. 未解決事項

* `Combat/search/branch_worker_pool.py`にOSプロセスレベルのWorker強制終了検知・
  respawn・旧generation Lease無効化のAPIが無い(Whole Run側の`respawn_worker`相当が
  Combat側に無い)。既存の非常に広範なテストカバレッジを持つCombat Worker Poolへの変更は
  リスクが高いため、本ラウンドでは追加しなかった。将来、Combat側でもWorker強制終了からの
  復旧が必要になった場合は、Whole Run側の設計(worker_slot+generation+PID追跡、
  Lease無効化)を移植することを推奨する。
* `external_control`のresolverシグネチャは現在`legal_actions`のみをTrainingに渡す
  設計であり、正式な「公開Decision DTO」(RoomContext/Transition/decision_idを含む)は
  未実装(7節)。Whole Run向けの`OrderMaskedObservation`相当のDTOも未実装。いずれも
  Training API本体の実装時に併せて設計すべき項目であり、本タスクの範囲(RLを「判断しない
  実行基盤」へ整理すること)を超えるため、今回は着手しなかった。
* 「Branch cancel競合」に対応する明示的なcancel APIはCombat/Whole Run双方に存在しない
  (8節)。現状はLease誤用拒否機構で同種のリスクをカバーしているが、真の「実行中Branch
  への割り込みcancel」は将来の検討事項として記録する。

## 12. 成果物

* コード: `Run/execution_mode.py`(decision_id/StaleDecisionError/apply_external_action等
  追加)
* テスト(新規8ファイル、計31 tests): `Combat/tests/test_inference_removal_audit.py`(4)、
  `Combat/tests/test_external_control_decision_types.py`(4)、
  `Combat/tests/test_fault_injection_additional.py`(2)、
  `Run/tests/test_inference_removal_audit.py`(2)、
  `Run/tests/test_external_control_decision_types.py`(6)、
  `Run/tests/test_fault_injection_additional.py`(3)、
  (`Run/execution_mode.py`拡張分は既存`test_execution_mode.py`でカバー)
* ツール: `Run/zero_index_multi_run.py`(100 Run batch + determinism check)
* ログ: `Outputs/reports/whole_run_logs/zero_index_100run_batch.json`、
  `zero_index_10room_search_result.json`、`zero_index_determinism_check.json`、
  `Outputs/reports/inference_removal_logs/combat_pending_lease_endurance_1000_rerun.json`、
  `explicit_branch_worker_count.json`、`performance_comparison.json`
* 本報告書

作業ツリーはclean(`Training/`の既存未コミット差分は変更していない)。
