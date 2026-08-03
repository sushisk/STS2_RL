# 推論処理撤去と受動実行基盤への整理 — 実装完了報告

対象: `C:\STS2_RL` baseline commit `e342f70`。Emulator側の変更は無し(既存`fca2f06`のまま)。

新しい責任分界: 今後の判断主体はTraining。RLはEpisode/Run進行、Emulator操作、
Observation/LegalAction公開、指定Actionの実行、指定Branchの生成、Snapshot/Replay/Worker/
Lease管理、Combat/Whole Runの状態遷移、Trajectory/診断情報の記録のみを担当する。

## 1. 現状調査(推論・選択経路一覧)

Combat/・Run/全体を調査した結果、以下を確認した。

| 箇所 | 内容 | 分類 |
|---|---|---|
| `Combat/policy_agent.py` | `Training/checkpoints/policy_teacher2000_seed_20260724/best.pt`・`value_teacher2000_seed_20260724/best.pt`をロードするPolicyNet/ValueNet推論 | **撤去**(本番importから隔離、`legacy/`へ) |
| `Combat/choice_policy_agent.py` | `Training/checkpoints/choice_policy_8token_best/best.pt`をロードするChoice Policy推論 | **撤去**(同上) |
| `Combat/heuristic_agent.py` | `StateEvaluator`スコアによるHeuristic候補採点・選択 | **撤去**(同上) |
| `Combat/state_evaluator.py` | 重み付き評価関数(`DEFAULT_WEIGHTS`)によるBoard評価 | **撤去**(同上) |
| `Combat/potion_value_table.py` | Potion固定価値テーブル(評価関数の一部) | **撤去**(同上) |
| `Combat/beam_search.py`/`Combat/lookahead.py` | `StateEvaluator`スコアに基づくビームサーチ/先読み探索 | **撤去**(同上) |
| `Combat/main.py`/`Combat/_bench_abc.py` | 上記Agent群を使うCLIデモ・ベンチマーク | **撤去**(同上) |
| `Combat/search/candidate_pipeline.py`の`_card_score`/`_hand_choice_score`等 | 全LegalActionを暗黙展開しHeuristicスコアで上位`width`件へ剪定 | **Training API実装まで停止**(既存呼び出し元との後方互換のため関数自体は維持、新規に明示的候補リスト版を追加。5節参照) |
| `Combat/search/multi_round_search.py`の`best = sorted(completed, key=_rank_completed)[0]` | ビームサーチ結果からスコア最良の1系列を自動選択しMainへcommit | **Training API実装まで停止**(撤去するとCombat Searchの実行基盤自体が機能しなくなるため、既存動作のまま維持。停止条件に該当するため推測修正せず維持のみ) |
| `Combat/search/main_loop.py`の`pending_static_select`(旧: 常時使用) | Pending解決時のtier付き簡易選択(choice_card/confirm優先、skip劣後) | **強制処理として維持**(ただし本ラウンドで注入可能化、zero_index専用の代替実装を新設) |
| `Combat/search/main_loop.py`の`first_candidate_direct_selector` | 常にlegal_actions[0]を選択 | **強制処理として維持**(既にzero_index相当。`Combat/execution_mode.py`で正式にzero_index実装として位置づけ直した) |
| `Combat/search/shadow_adapter.py` | 旧HeuristicAgent vs 新Search Coordinatorの比較専用ツール(Mainへ非commit) | **Training API実装まで停止/offline_analysis相当**(import先を`legacy.`へ修正のみ、モジュール自体は診断専用として維持) |
| `Combat/data/generate_heuristic_trajectories.py`、`Combat/evaluation/online_eval/*.py`(8ファイル) | 上記legacy Agentを使う教師データ生成・監査スクリプト | **Training API実装まで停止**(import先を`legacy.`へ修正、動作は維持。将来Trainingの`external_control`が本物の中間決定を供給できるようになった時点で置き換えるべき残存依存として6節に明記) |
| `Combat/search/branch_worker_pool.py` | Worker/Lease/Fault/Bootstrap+Step機構 | **Branch実行基盤として維持**(無変更) |
| `Run/worker_pool.py`/`Run/process_choice_branch_runner.py` | OSプロセス分離Worker Pool、Holder/sibling/Lease | **Branch実行基盤として維持**(無変更) |
| `Run/whole_run_session.py`の`pick_default_action`/`Run/room_progression_driver.py`の`pick_room` | Action種別優先順位・Treasure回避という小さな内蔵方針 | **Training API実装まで停止**(zero_indexの定義上使用不可と明記、Branch prefix自動発見等の内部フィラー用途に限定して維持。7節参照) |
| Mock evaluator/固定スコア | 検索した範囲では存在せず(該当なし) | - |
| 自動Choice resolver | `LiveCombatSession`/`WholeRunSession`の`continuation_resolver`引数はデフォルトで`_default_choose_action_continuation_live`(既存、ActionContinuationの多候補ケースでの内蔵解決)を使う。これは真の複数選択肢がある場合に暗黙選択している点でHeuristic的だが、mid-effect ActionContinuationの継続そのものはEngine自身の一部として「強制処理」寄りに近い。本ラウンドでは変更対象としなかったが、6節の残存依存として明記 | **Training API実装まで停止(残存依存として明記)** |

## 2. 残す実行モード

### `Combat/execution_mode.py` / `Run/execution_mode.py`

- `MODE_EXTERNAL_CONTROL`: Training(または相当のテスト)が`direct_selector`/`pending_selector`
  (Combat)、`action_picker`/`room_picker`(Run)を明示的に注入する。RLはそれらのコールバックが
  返すaction_idを現在のLegalActionsへ解決するだけで、複数の実候補があっても自ら比較・選択
  しない。`make_external_action_selector`/`make_external_room_selector`
  (Combat/Run双方)は「指定されたActionの実行」のみを行う薄いラッパーであり、判断はしない。
- `MODE_ZERO_INDEX`: `first_candidate_direct_selector`(Combat、既存)、
  `zero_index_pending_selector`(Combat、新設、tierなし)、`zero_index_action`/
  `zero_index_room_picker`(Run、新設)。全て`legal_actions[0]`/`rooms[0]`を無条件選択、
  並べ替え・score・logit・value・tier・heuristic参照は一切行わない。RL単独動作確認用の
  接続確認専用フォールバックであり、強さを目的としたPolicyではないことをdocstringに明記した。

## 3. 残す強制処理(無変更)

- `LiveCombatSession`/`WholeRunSession`のBoundaryまでのsettle処理(`stop_at_pending`等)。
- Action Continuationの公開と継続(Engine自身の実質選択余地なし自動確定を含む)。
- Fault処理とWorker再生成(`Combat/search/branch_worker_pool.py`、`Run/worker_pool.py`の
  `respawn_worker`)。
- Snapshot/ReplayによるChoice再現、Holder/sibling/Lease管理。

## 4. 撤去・隔離の実施内容

`Combat/heuristic_agent.py`・`policy_agent.py`・`choice_policy_agent.py`・`beam_search.py`・
`lookahead.py`・`state_evaluator.py`・`potion_value_table.py`・`main.py`・`_bench_abc.py`の
9ファイルを`Combat/legacy/`へ移動した(`git mv`、履歴保持)。`Combat/legacy/__init__.py`に
本ラウンドの責任分界とこのパッケージの位置づけを明記。相互import・外部呼び出し元
(`Combat/tests/test_scenario_v2.py`、`Combat/data/generate_heuristic_trajectories.py`、
`Combat/evaluation/online_eval/`配下8ファイル、`Combat/search/shadow_adapter.py`)のimport文を
`from legacy.X import ...`へ機械的に修正し、動作は完全に維持した(5節の検証結果参照)。

`Combat/search/main_loop.py`・`search_coordinator.py`・`candidate_pipeline.py`の
本番探索実行パス(`build_search_strategy`、`build_candidate_pipeline_result`、
`build_beam_search_strategy`)は**撤去していない** — 6節参照。

## 5. Branch基盤の扱い(新設、追加のみ・既存動作は無変更)

`Combat/search/candidate_pipeline.py`に
`build_candidate_pipeline_result_for_explicit_candidates(decision_context,
candidate_legal_action_indices)`を新設した。既存の`build_candidate_pipeline_result`
(Heuristicスコアによる暗黙展開)とは独立に、指定indexの候補のみからPipelineを構築する。
候補リストが空の場合は`ValueError`(全Legal Actionへのフォールバックはしない)。不正な
indexも`ValueError`。

`Combat/search/search_coordinator.py`に`dispatch_explicit_candidates(decision_context,
candidate_legal_action_indices, *, pool, config, lease_registry,
combat_start_deck_multiset, metrics=None)`を新設した。既存の`_dispatch_work_items_until_final`
(Fault retry/Lease/Worker機構)をそのまま再利用しつつ、`build_search_strategy`の
score集約(`aggregate_hypothesis_results`/`aggregate_plain_results`)・
`build_commit_decision`(自動採用判定)は一切通らない。戻り値`ExplicitBranchDispatchResult`
は`work_item`/`branch_result`/`work_item_state`のみを持ち、評価値・採用判定フィールドは
存在しない。

実機検証(`Combat/tests/test_execution_mode.py::test_dispatch_explicit_candidates_never_selects_a_winner`):
指定した2候補(index 0, 2)のみがBranch実行され(Hypothesis Grid展開で8 WorkItemsになったが、
distinctな候補は指定通り2つのみ)、`ExplicitBranchDispatchResult`にaggregate_score/
best_action属性が存在しないことを確認。

## 6. 停止条件の判断(推測修正せず維持)

`Combat/search/multi_round_search.py`の`build_beam_search_strategy`
(`sorted(completed, key=_rank_completed)[0]`によるWinner自動選択・Main自動commit)、
および`Combat/search/candidate_pipeline.py`の`build_candidate_pipeline_result`
(Heuristicスコアによる暗黙全展開)は、**現時点でCombat Searchの唯一の動作する実行経路**
である。これを撤去すると`ROUTE_SEARCH`が機能しなくなり、既存のSearch Coordinator/
Candidate Pipeline/Branch Worker Poolの実行基盤自体が失われる
(「推論処理の撤去により、Branch、Replay、Worker、Combat Searchの実行基盤まで失われる
場合は停止して報告してください」に該当)。

このため、これらは**撤去せず現状のまま維持**し、5節の明示的候補リストAPIを
**追加**することで、Training API実装後にそちらへ段階的に移行できる経路を用意した。
既存のスコアリング関数・自動commit経路自体の削除は本ラウンドの範囲外とし、次のアクション
として6節・10節に明記する。

`Combat/live_combat_session.py`/`Run/whole_run_session.py`のActionContinuation
`continuation_resolver`のデフォルト実装(`_default_choose_action_continuation_live`)も
同様の理由で維持した — mid-effect Pendingの継続そのものは既存のBranch/Replay機構と
密結合しており、単独で撤去するとPending解決の実行経路が失われる。

## 7. Run/における発見(重要): zero_indexは実戦闘を終結できない

`zero_index_action`(常にlegal_actions[0])をCombatの意思決定へ適用したところ、この
Emulatorの`BuildLegalActions()`は常に`system`/`End Turn`をindex 0として構築する
(`Sts2Emulator/Api/GameInstance.cs`、Whole Run API文書の「Combat」節が記載する構築順序と
一致)。そのため、`zero_index`は原理的に一度もカードを出さずEnd Turnし続けることになり、
戦闘に勝利する手段を持たない。GodMode有効時はPlayerも死なないため、戦闘は`max_steps`まで
`stable`のまま停滞し続ける。

これは実装上の不具合ではなく、「合法手順序をそのまま使用し並べ替えない」という`zero_index`
の定義そのものから導かれる正直な帰結である。10 Room連続進行の要件についてはこの制約を
そのまま報告し(8節)、Choice種別ごとの`zero_index`個別動作確認(Map/Event/Combat Pending/
Reward/Shop/Rest)は別途、各Choiceへ到達した時点でindex 0が選択されることを直接検証する
形で満たした(`Combat/tests/test_execution_mode.py`、`Run/tests/test_execution_mode.py`)。

## 8. 10 Room実行ログ

| ログ | モード | 結果 |
|---|---|---|
| `Outputs/reports/whole_run_logs/zero_index_10room_attempt.json` | `zero_index` | Map選択→Neow Event選択→Pending(choice_card)選択まではindex 0で正常に進行し、最初のCombatRoomで`stable`のままEnd Turnのみを60 stepsまで継続、7節の理由により1 Room目で停滞(`rooms_entered=1`)。全Stepでaction_id=0を選択したことを記録。 |
| `Outputs/reports/whole_run_logs/external_control_10room_demo.json` | `external_control`(Training役の簡易スクリプトで代用) | 同一seed=18で**10 Room到達**(`rooms_entered=10`, `final_boundary=map_select`, `CombatRoom`/`EventRoom`/`MerchantRoom`/`TreasureRoom`を含む)。RL側の実行基盤(`drive_rooms`/`step`/`choose_room`)が、指定されたActionをそのまま実行するだけで10 Room連続進行を正しく処理できることを実証した。 |

`external_control_10room_demo.json`の「Training役」スクリプトは`make_external_action_selector`/
`make_external_room_selector`でラップされた**単純な固定優先順位の代用関数**であり、これ自体は
RL側の本番実行モードではない(Training API実装後は、この代用関数の代わりにTrainingからの
実際の指示が入る)。この代用は「RL自身が判断しない実行経路がそもそも動作すること」を示す
接続確認のみを目的とする。

## 9. DTO整理

Run/(`WholeRunSession`のObservation/LegalAction/StepResult/RoomContext等)は元々すべて
plain dict構成で、score/logit/value/winner/ranking相当のフィールドは元から存在しない
(変更不要)。

Combat側で唯一該当した`Combat/search/candidate_pipeline.py`の`PipelineCandidateRef.score`/
`ScoredCandidate.score`は既にoptional(デフォルト`0.0`)であり、必須フィールドから除外
済みの状態だった。明示的候補リストAPI(5節)はこの`score`に常に`0.0`・
`evaluator_name="explicit"`を設定し、実質的な評価値としては使用しない形にした。

必須DTOフィールド(episode ID/decision ID/state version/decision type/observation/
legal actions/action IDs/transition/branch results/execution statistics/
terminated/truncated/schema version相当)は、既存の`DecisionSignature`/`StepResult`/
`ChoiceReachResult`/`ChoiceStepResult`/`BranchResult`等に元々含まれており、追加の
必須フィールド定義変更は不要だった。

## 10. 残存するTraining/model依存一覧(今後の対応が必要な項目)

- `Combat/legacy/policy_agent.py`/`choice_policy_agent.py`: `Training/checkpoints/`配下の
  `.pt`ファイルを直接ロードする。本番importからは隔離済みだが、ファイル自体は削除して
  いない(履歴・検証用)。
- `Combat/data/generate_heuristic_trajectories.py`、`Combat/evaluation/online_eval/`配下
  8ファイル: `Combat/legacy/`のHeuristic/Policy Agentを使い続けている(import先のみ修正)。
  教師データ生成・監査という用途自体はTraining側の責務に近づいており、将来的にはRL側の
  `external_control`実行基盤を経由してTrainingが直接actionを供給する形へ置き換えるべき
  残存タスクである。
- `Combat/search/multi_round_search.py`の自動Winner選択・Main自動commit経路
  (`build_beam_search_strategy`)、`Combat/search/candidate_pipeline.py`の暗黙Heuristic
  スコアリング(`build_candidate_pipeline_result`): 6節で説明した通り、Training APIが
  明示的候補リスト経由の意思決定を供給できるようになるまでの間、現状のまま維持。
- `Run/whole_run_session.py`の`pick_default_action`、`Run/room_progression_driver.py`の
  `pick_room`: Branch prefix自動発見(`worker_pool.py`の`discover_prefix=True`)専用の内部
  フィラー方針として維持(7節)。Training側の`external_control`が実際の中断combat内
  Action系列を供給できるようになれば置き換えるべき。
- `Combat/live_combat_session.py`/`Run/whole_run_session.py`のActionContinuation既定
  `continuation_resolver`(`_default_choose_action_continuation_live`): 真に複数候補が
  ある場合の内蔵解決ロジックが残っている(6節参照)。

## 11. 回帰テスト結果

`Combat/tests/`全21ファイル(新設`test_execution_mode.py`含む)を個別実行(逐次、並行負荷を
排除した状態で最終確認)。

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
| test_execution_mode.py(新設) | 8 passed |
| test_fault_taxonomy.py | 10 passed |
| test_live_combat_session_step.py | 3 passed |
| test_main_loop.py | 15 passed |
| test_multi_combat_continuous_execution.py | 1 passed |
| test_multi_hypothesis_stress_runner.py | 1 passed |
| test_multi_round_search.py | 7 passed |
| test_restore_snapshot_phase3c1.py | 26 passed, 2 failed(既知の事前失敗、下記) |
| test_rng_hypothesis.py | 10 passed |
| test_scenario_v2.py | 31 passed, 1 failed(既知のWriggler flake、下記) |
| test_search_coordinator.py | 15 passed |
| test_shadow_adapter.py | 7 passed |
| test_shadow_evaluation_batch.py | 1 passed |

**新規リグレッション: 無し。**

既知の事前失敗(`test_restore_snapshot_phase3c1.py`の
`test_official_json_example_restores_successfully`/`test_real_6546_21_rejected_via_public_api`)
および環境依存の既知フレーク(`test_scenario_v2.py::test_wriggler_missing_slot_without_encounter_is_detected`、
過去のラウンドで新旧Emulator baseline双方で再現すると確認済み)は、いずれも本ラウンドの
変更(`Combat/legacy/`移動、`main_loop.py`/`candidate_pipeline.py`/`search_coordinator.py`
への追加のみの変更)とは無関係である。

回帰実行中、システムへの強い並行負荷(本タスク中に多数のCLRロード・複数のBranch Worker Pool
生成を同時並行させたこと)に起因すると見られる一過性の失敗を複数観測した
(`test_fault_taxonomy.py`のTimeoutError、`test_restore_snapshot_phase3c1.py`の追加5件の失敗、
1プロセスが49分間応答しないハング)。いずれも該当テストを負荷の無い状態で個別に再実行した
結果、全て成功(exit=0)することを確認しており、コード変更由来のリグレッションではないと
判断した。

`Run/tests/`は以下の通り。

| ファイル | 結果 |
|---|---|
| test_whole_run_connectivity.py | 4 passed, 1 failed |
| test_worker_pool_process_separation.py | 5 passed |
| test_execution_mode.py(新設) | 7 passed |

`test_whole_run_connectivity.py::test_choice_branch_shop_holder_sibling_reproduction`の
失敗は、本ラウンドで一切変更していない旧`Run/choice_branch_runner.py`
(前フェーズの報告書で「OSプロセス分離未対応、Emulator commit `fca2f06`の
supersededガードに抵触しうる」と既に説明・置き換え済みの旧モジュール)が原因であり、
実際にエラーメッセージも`"...superseded..."`だった。新しい`Run/worker_pool.py`/
`process_choice_branch_runner.py`ベースの`test_worker_pool_process_separation.py`は
5/5全て安定して成功しており、本ラウンドの変更に起因するリグレッションではない。

## 12. 成果物

- コード: `Combat/legacy/`(9ファイル移動)、`Combat/execution_mode.py`(新設)、
  `Combat/search/main_loop.py`(pending_selector注入可能化)、
  `Combat/search/candidate_pipeline.py`(明示的候補リストAPI追加)、
  `Combat/search/search_coordinator.py`(`dispatch_explicit_candidates`追加)、
  `Combat/search/shadow_adapter.py`(import修正)、`Run/execution_mode.py`(新設)、
  `Run/whole_run_session.py`(`zero_index_action`/`make_external_action_selector`追加)、
  `Run/room_progression_driver.py`(action_picker/room_picker注入可能化)
- テスト: `Combat/tests/test_execution_mode.py`(8 tests)、
  `Run/tests/test_execution_mode.py`(7 tests)
- ログ: `Outputs/reports/whole_run_logs/zero_index_10room_attempt.json`、
  `Outputs/reports/whole_run_logs/external_control_10room_demo.json`
- 本報告書

作業ツリーはclean(`Training/`の既存未コミット差分は変更していない)。Training APIの
実装には進んでいない。
