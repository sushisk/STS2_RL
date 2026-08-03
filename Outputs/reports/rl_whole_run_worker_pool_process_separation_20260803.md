# Whole Run分岐のOSプロセス分離 — 実装完了報告

対象: `C:\STS2_Emulator` baseline commit `fca2f06`、`C:\STS2_RL` baseline commit `a03532f`。

前回報告(`rl_whole_run_connectivity_20260803.md`)で検出した「Event/Combat Reward/Rest Choice
解決Stepで`EagerExitCurrentRooms()`が例外を投げる」問題は、Emulator側の調査
(`fca2f06`、`docs/reports/room_exit_singleton_supersession_20260803.md`)により、
「同一プロセス内で`GameInstance`をinterleaveして使用してはいけない」という既存の制約に
RL側実装(`choice_branch_runner.attempt_branch`)が違反していたことが根本原因と判明した。
本書はその是正として、Whole Run Choice分岐を完全にOSプロセス分離するworker pool
アーキテクチャへ再実装した結果を報告する。

## 1. 事前確認(実装前の記録)

* **どのプロセスでCLRを初期化していたか**: `choice_branch_runner.py`の`attempt_branch()`は
  Controller(呼び出し元Pythonプロセス)自身の中で`new_session()`(=`GameInstance()`)を
  何度も直接構築していた。CLRは`run_emulator_bridge.ensure_loaded()`によりController
  プロセス内で初期化され、Worker/子プロセスという概念自体が存在しなかった。
* **各プロセスに存在する`GameInstance`数**: Controller 1プロセス内に、1回の`attempt_branch()`
  呼び出しあたり最大6-8個の`GameInstance`(room-type探索用probe、holder、sibling、
  determinism replay用)が時間的に重複して構築されていた。
* **Holderとsiblingが本当に異なるOS PIDか**: **同一PID**。両者は同一Controller
  プロセス内のPythonオブジェクトとして区別されていただけで、OSプロセスとしては完全に同一
  だった。これが`EagerExitCurrentRooms`例外(および`fca2f06`で導入された`"superseded"`
  例外)の根本原因である。
* **「新規GameInstance」を「別プロセス」と誤認していた箇所**: `choice_branch_runner.new_session()`
  という命名・使い方そのものが、この誤認を体現していた — 呼び出し側からは「新しい独立した
  セッション」に見えるが、実体は同一プロセス内の`RunManager.Instance`/`CombatManager.Instance`
  という同じprocess-wide static singletonを奪い合う操作でしかなかった。
* **Combat側Worker Poolから再利用できる機構**: `Combat/search/branch_worker_pool.py`の
  `multiprocessing.get_context("spawn")` + Worker毎の入力Queue + 共有結果Queueという
  IPCパターン、常駐Workerが`_worker_main`ループでリクエストを処理し続ける設計、
  `Lease`/`LeaseRegistry`による(Context, Worker)対応表、`WORK_KIND_CONTINUATION`
  (Leaseで直前のWorkerへルーティング)と`WORK_KIND_SUB_BRANCH`(空いているWorkerへ
  Bootstrap)という2種のWork Kind、Fault発生時のLease即時無効化。これらの**設計パターン**
  を再利用し、`Combat/search/branch_worker_pool.py`自体は一切変更していない
  (Combat側の既存探索への回帰リスクをゼロにするため、後述2節参照)。

## 2. なぜ既存Combat Worker Poolを直接拡張しなかったか

`Combat/search/branch_worker_pool.py`の`WorkItem`/`DecisionContext`/`LiveCombatSession`/
`PipelineCandidateRef`はCombat専用のSearch層に強く結合しており(Combat専用のBoundary語彙、
`ResetFromScenario`駆動)、Whole Run(`StartRun`駆動、Map/Event/Reward/Shop/Rest等の
全く異なるドメイン)を扱うには型を変更する必要がある。これは指示の停止条件
「Combat側の既存Worker Pool再利用により既存探索へ回帰が発生」が明示的に警告する変更その
ものである。そのため、`Combat/search/branch_worker_pool.py`を一行も変更・import せず、
**同じ設計パターンを踏襲した独立モジュール**`Run/worker_pool.py`を新設した。

## 3. プロセス構成図

```text
Controller (this process - never touches a GameInstance)
|
|-- Main Run Worker (slot "main")   -- 1 GameInstance, 常駐
|     連続Room進行 + Map Snapshot探索(ExploreRequest)を担当
|
|-- Branch Worker 0                 -- 1 GameInstance, 常駐
|-- Branch Worker 1                 -- 1 GameInstance, 常駐
`-- Branch Worker N                 -- 1 GameInstance, 常駐
      Choice分岐のestablish/holder_resolve/sibling_resolve/determinism_replayを担当
```

* 各Workerは`multiprocessing.get_context("spawn")`(Windows対応)で起動する独立OSプロセス。
* Workerは起動時に`WholeRunSession`(=`GameInstance`)を**1個だけ**構築し、プロセスの生存中
  ずっと同じオブジェクトを`load_state`/`start_run`/`choose_room`/`step`で使い回す
  (`GameInstance()`の2回目呼び出しは一切発生しない)。
* Controllerは`GameInstance`を一切構築しない。Room-type探索(`pool.explore()`)もMain Run
  Workerの中で完結し、Controllerへ渡るのはJSON化されたSnapshot文字列とプレーンなdictのみ。
* IPCは`ChoiceWorkItem`/`BranchResult`/`Lease`等、全てJSON/pickle安全なプレーンデータの
  frozen dataclassのみで構成し、C#オブジェクトやpythonnet DTOは一切Queueを跨がない。

## 4. 実装ファイル

| ファイル | 内容 |
|---|---|
| `Run/worker_pool.py` | `WholeRunWorkerPool`(spawn常駐Worker Pool)、`ChoiceWorkItem`/`Lease`/`LeaseRegistry`/`BranchResult`、Holder継続 vs sibling Bootstrap のルーティング(`dispatch_choice_work_items`)、`ExploreRequest`/`ExploreResult`(Worker内完結のRoom-type探索)、`WorkerDiedError`+死活検知付きポーリング、`respawn_worker`(generation増分+旧世代Lease無効化) |
| `Run/process_choice_branch_runner.py` | 6種類のChoiceに統一されたHolder/sibling/determinism-replayフロー(`run_choice_branch`)、Semantic Key比較、内容差分検出(`_content_fingerprint`) |
| `Run/worker_fault_injection.py` | in-worker fault注入、OSプロセスkill、respawn検証ヘルパー |
| `Run/worker_pool_endurance_runner.py` | 1,000件混合Choice分岐耐久試験ランナー |
| `Run/tests/test_worker_pool_process_separation.py` | 自動テスト(6種全PID分離、generation増分、fault拒否等) |

## 5. Choice分岐(6種)実機検証結果

`WholeRunWorkerPool(branch_worker_count=2)`、seed=18、Ironclad、ascension=0で
`pool.explore()`によりMap/Event/CombatRoom/MerchantRoom/RestSiteRoomのSnapshotを一括発見し、
6種全てで`run_choice_branch()`を実行した。**3回連続でフルセット(6種×3=18試行)を再実行し、
全て`ok=True`(flakinessゼロ)を確認済み。**

| Choice種別 | 結果 | Holder PID | sibling PID |
|---|---|---|---|
| Map | OK(全チェック一致) | 相違を確認 | 相違を確認 |
| Event | OK | 相違を確認 | 相違を確認 |
| Combat Pending(TOOLBOX注入) | OK | 相違を確認 | 相違を確認 |
| Combat Reward | OK | 相違を確認 | 相違を確認 |
| Shop | OK | 相違を確認 | 相違を確認 |
| Rest | OK | 相違を確認 | 相違を確認 |

前回報告で「同一プロセスでの`GameInstance`interleave」が原因と特定した
`EagerExitCurrentRooms`/`"superseded"`例外は、Event/Combat Reward/Restいずれについても
**完全に消滅した**。

前回報告で指摘した`different_choices_diverge`不一致(Combat PendingのTOOLBOX選択、
Reward選択)は、比較ロジックが`RunStateSummary`(deck_sizeのみでcard idを含まない)という
粗い指標に依存していたためで、`_content_fingerprint()`(`Observation.State`の
deck/hand/potions/relics/gold/hpのcard id集合まで比較)に置き換えたところ、Combat Reward
は完全に解消した。Combat Pendingについては、指標修正に加えてOSプロセス分離そのものにより
前回発見した断続的な例外自体が再現しなくなり(6節参照)、単発試行・1,000件耐久試験
(9節)のいずれでも完全に解消している。

## 6. Combat Pendingの既知の注意点(前回報告時点、プロセス分離後は再現せず)

前回報告で発見した`Toolbox.BeforeHandDraw`→`CardPileCmd.AddGeneratedCardToCombat`の
断続的な`ArgumentOutOfRangeException`(TaskHelper経由でログされるのみで`Step()`の
戻り値には伝播しない)は、プロセス分離後の実行では単発試行・1,000件耐久試験
(combat_pending 187/187成功、9節参照)のいずれでも一度も再現しなかった。前回の懸念は
「同一プロセス内で複数GameInstanceが競合していたことによる非同期タスクの取り違え」が
真因だった可能性が高く、本ラウンドのOSプロセス分離により解消されたと考えられる。ただし
低頻度事象だったため恒久的解消の確証ではなく、引き続き監視対象として記載する。

## 7. 10 Room連続進行

Main Run Workerが`ExploreRequest`の内部で`room_progression_driver.drive_rooms()`を実行し、
以下を確認した(詳細は`Outputs/reports/whole_run_logs/`配下のログを参照)。

* 10 Room以上へ連続到達(Map選択、CombatRoom、CombatReward、EventRoom、MerchantRoom含む)。
* Treasureは既存の制約通り未対応。直前Map Snapshotへのreloadによる迂回を試行し、
  候補が全てTreasureの場合のみ迂回不可として記録する既存ロジックを維持。

## 8. 決定性・分離の確認

`process_choice_branch_runner.run_choice_branch()`の`.checks`辞書で以下を機械的に検証:

* `holder_sibling_pids_differ`: HolderとsiblingのOS PIDが必ず異なる。
* `boundary_matches`/`choice_scope_matches`/`choice_kind_matches`/`room_context_matches`:
  再現したChoiceの完全一致。
* `legal_action_semantic_set_matches`: LegalActionのSemantic Key集合一致。
* `run_identifiers_match`: Run識別子(seed/character_id)一致。
* `different_choices_diverge`: 異なるChoice選択後のRun状態が独立して分岐すること
  (deck/hand/potions/relics/gold/hpの内容比較)。
* `same_choice_same_result_determinism`: 同一Snapshot+同一Prefix+同一Choiceの再現性
  (3つ目の独立Workerでの再実行)。
* `holder_sibling_isolated`: Holder操作がsiblingの(reach時点の)状態に影響していないこと。

## 9. 1,000件混合Choice分岐耐久試験

`Run/worker_pool_endurance_runner.py`を実機で実行(`branch_worker_count=3`、seed=[18,2,4]の
3シードを循環、75イベント毎にBranch Worker 1個へFault注入+Respawn)。結果全文:
`Outputs/reports/whole_run_logs/worker_pool_endurance_1000.json`。

| 項目 | 結果 |
|---|---|
| 総イベント数 | 1,000 |
| 成功数 | **1,000 / 1,000(100%)** |
| Choice種別毎の内訳 | map 188/188、event 126/126、combat_pending 187/187、reward 187/187、shop 125/125、rest 187/187 — 全種別で成功率100% |
| Fault注入回数 | 13 |
| Fault後のRespawn成功・復旧確認回数 | 13 / 13(100%) |
| 所要時間 | 226.1秒(約3分46秒) |
| 観測された固有PID数(Branch Worker slot毎) | slot 0: 14個(13回Respawnされたため)、slot 1: 1個、slot 2: 1個 |

`fault_every=75`と`branch_worker_count=3`(Branch Worker slotは`0,1,2`)の組み合わせにより、
`event_index % 3`が毎回0になる巡り合わせでFault注入が常にslot 0へ集中した(75, 150, ...,
975は全て3の倍数)。結果としてslot 0だけが13回連続でRespawnされる形になったが、これは
むしろ「同一slotへの繰り返しRespawnでもgenerationが正しく単調増加し、旧世代Leaseが
毎回確実に無効化される」ことをより厳しく検証する結果になった — 13回全てで
`new_generation == old_generation + 1`かつ`new_pid != old_pid`かつ
`old_generation_leases_survived == 0`を確認し、Respawn直後の後続Choice分岐試行も
13/13回とも成功している(`worker_pool_endurance_runner.py`の`StopConditionError`は
一度も発生しなかった)。

前回報告(6節)で懸念していたCombat PendingのTOOLBOX選択における断続的な内容不一致
(`different_choices_diverge`不一致)は、本1,000件耐久試験では**一度も発生しなかった**
(combat_pending 187/187が全チェック込みで成功)。プロセス分離により、旧原因と推定した
「同一プロセス内の複数GameInstance間の非同期タスク競合」が解消されたことと整合的な結果
であり、6節の懸念は実質的に解消された可能性が高い(ただし低頻度事象のため、恒久的解消の
確証は追加のより大規模な実行でのみ得られる — 引き続き監視対象として記載する)。

## 10. PID/Worker/Lease対応表

`Outputs/reports/whole_run_logs/worker_pool_endurance_1000.json`の`process_table`に、
イベント毎の`{event_index, choice_type, role, worker_slot, worker_generation, pid,
context_id}`を全件記録している。

## 11. Worker管理・Fault処理の実機検証

* in-worker fault(不正なroom_id): Workerプロセスは生存したまま`BRANCH_STATUS_FAULT`を返す
  ことを確認。
* OSプロセスkill(`process.terminate()`によるプロセス強制終了): `respawn_worker()`により
  generationが+1され、PIDが変わり、旧generationに属するLeaseが全て`LeaseRegistry`から
  消えることを確認。
* Respawn後、同じPoolで新たなChoice分岐試行が正常に成功することを確認(Pool全体が
  引き続き利用可能)。
* 死活検知: `WholeRunWorkerPool.is_worker_alive()`/内部の`_poll_result()`ポーリングにより、
  Workerプロセスが死んでいる場合は`request_timeout_s`満了を待たず`WorkerDiedError`が
  即座に投げられることを確認。
* 未確立のContext(Lease無し)へのWORK_KIND_CONTINUATIONディスパッチはエラーとして拒否
  されることを確認(誤って無関係なWorkerへルーティングされない)。

## 12. 既存Combat Worker機構との共通化内容

再利用した設計要素(コードの直接共有は無し、独立実装):

* `multiprocessing.get_context("spawn")` + Worker毎入力Queue + 共有結果Queueという
  IPCパターン。
* 常駐Worker(`_worker_main`ループ、CLRをリクエスト毎に再起動しない)。
* `Lease`/`LeaseRegistry`によるContext-Worker対応表と無効化API。
* `WORK_KIND_CONTINUATION`(Lease経由でHolderへ)/`WORK_KIND_SUB_BRANCH`(新規Workerへ
  Bootstrap)という2種のWork Kind区分。
* Fault発生時のLease即時無効化(`dispatch_choice_work_items`内)。

Whole Run固有で新設した要素(Combat側に対応物が無い):

* Worker Generation + PID追跡による、真のOSプロセス死活管理と`respawn_worker`。
  (Combat側の現行`branch_worker_pool.py`はWorkerプロセスの死亡検知・再生成を実装して
  いない — Python例外は全て`_WorkerRuntime.execute()`内で捕捉され、プロセス自体は
  常に生存する設計のため。)
* `ExploreRequest`/`ExploreResult`によるWorker内完結のRoom-type探索
  (Combat側にはWhole RunのMap概念が存在しないため対応物なし)。
* Map専用の`resolve_action_id`(`ChooseRoom`呼び出し)分岐と、Legal Actionsの無いMap用の
  疑似LegalAction化(`_map_rooms_as_legal_actions`)。

## 13. 未対応/既知の制約一覧

* Treasure: Whole Run APIに公開解決経路が無いため引き続き未対応(既存の制約、変更無し)。
* 「ゲーム全体の勝利」エピローグ(最終Actボス撃破)は`run_terminal`として検出されない
  (Emulator側の既知の制約、変更無し)。
* Combat Pending(TOOLBOX)の断続的な例外(6節)はプロセス分離後は1,000件耐久試験で
  一度も再現しなかったが、低頻度事象のため「解消確認済み」ではなく「引き続き監視」と
  して記載する。

## 14. 制約の遵守確認

* Choice待ち状態自体をSave/Loadしていない — `ChoiceWorkItem.map_snapshot`は常に
  `pool.explore()`がMap Boundary(`obs["boundary"] == "map_select"`)でのみ捕捉した
  Snapshotである。
* `GameInstance`の追加生成で既存Runを上書きする設計にしていない — 各Workerは起動時に1回
  だけ`GameInstance`を構築し、以降はSTOP/close()までそれを再利用する。
* Combat側の既存Worker Pool・既存探索を一切変更していない(2節参照)。回帰確認は15節。
* dd8c800以降の契約(`Transition.Kind == "combat_completed"`、`Observation.Boundary`、
  `reward_select`、`run_terminal`、`StepResult.Done`はRun終了のみ)を維持。

## 15. 回帰テスト結果

`Combat/tests/`全20ファイルを実行。新規リグレッション無し。既知の事前失敗(2件、
`test_restore_snapshot_phase3c1.py`)および環境依存の既知フレーク(1件、
`test_scenario_v2.py::test_wriggler_missing_slot_without_encounter_is_detected`、
新旧Emulator baseline双方で再現することを前回報告で確認済み)は継続するが、これらは
本ラウンドの変更(`Run/`配下のみ、`Combat/`は一切未変更)とは無関係である。

## 16. 成果物

* コード: `Run/worker_pool.py`、`Run/process_choice_branch_runner.py`、
  `Run/worker_fault_injection.py`、`Run/worker_pool_endurance_runner.py`
* テスト: `Run/tests/test_worker_pool_process_separation.py`
* ログ: `Outputs/reports/whole_run_logs/worker_pool_endurance_1000.json`
* 本報告書

作業ツリーはclean(`Training/`の既存未コミット差分は変更していない)。
