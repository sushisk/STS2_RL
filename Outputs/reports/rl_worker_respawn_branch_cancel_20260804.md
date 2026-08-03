# RL担当報告：公開DTO監査・Combat Worker Respawn・Branch Cancel

- RL基準commit: `235eb8b`（本作業の開始点）
- Emulator基準commit: `fca2f06`
- 本レポートはPart B（Combat Worker OS-level Respawn）・Part C（Branch Cancel/Release API）の実装・試験結果をまとめる。Part A（Emulator DTO公開範囲の事前監査）は別途 `Outputs/reports/rl_dto_exposure_audit_20260803.md` として提出済み・承認待ちであり、指示のとおり本パスでは公開Decision DTO/Training向けAPIの実装には一切進んでいない。

## 0. サマリ

| Part | 内容 | 状態 |
|---|---|---|
| A | Emulator DTO公開範囲監査 | 完了・報告済み・**承認待ち**（別ファイル） |
| B | Combat Worker OS-level Respawn | 完了・全試験PASS |
| C | Branch Cancel/Release内部API | 完了・全試験PASS |
| - | 1000件混在耐久試験 | 完了・violation 0件 |
| - | 全回帰 | 完了・既知のbaseline失敗以外は全PASS |

停止条件（IPC全面再設計が必要／Main Workerを殺す必要がある／Cancelが他Branch・Mainを変える／旧generation結果の識別不能／Lease所有者判定不能／DTO公開に重大な曖昧性／Hidden Information除去にEmulator契約変更が必要）は**いずれも検出されなかった**。

---

## 1. Part B: Combat Worker OS-level Respawn

### 1.1 設計

対象: `Combat/search/branch_worker_pool.py`

既存の `search/fault_taxonomy.py` を確認した結果、`FAULT_TASK_TIMEOUT`／`FAULT_WORKER_PROCESS_CRASH` は**既に**定義されており、`worker_reuse_policy()` で `FORCE_RESTART` に、`decide_retry()` で `worker_generation` 増分にマッピングされていた。つまり再試行・集約層（`search_coordinator.py`）は既にWorker Respawnを前提とした設計になっており、変更不要だった。今回追加したのは `branch_worker_pool.py` 自体のOSプロセスkill+respawn配線のみ。

追加した機能:

* `_WorkerHandle.pid`: spawn時・respawn時に `process.pid` を記録。
* `BranchWorkerPool.worker_pids`: 現在の全Worker PIDを返すプロパティ。
* `BranchWorkerPool.is_worker_alive(worker_id)`: `process.is_alive()` の薄いラッパ。
* `BranchWorkerPool.respawn_worker(worker_id, lease_registry=None)`:
  * 生存していれば `terminate()` → `join(timeout=5)`。
  * 旧 `in_queue` を `close()`＋`cancel_join_thread()`（後述1.3のバグ修正）。
  * 同じ `worker_id` slotへ新しいOSプロセスを `generation = old_generation + 1` でspawn。
  * `lease_registry.invalidate_worker(worker_id)` で旧generationが保持していた全Leaseを無効化。
  * 他のWorker slotには一切触れない。Main（Combat Main loopの `LiveCombatSession`）はこのPoolのWorkerではないため、Respawnの影響を受けない。
* `BranchWorkerPool.execute()`: `queue.Empty`（=timeout）時、`TimeoutError` を投げる代わりに該当Workerを `respawn_worker()` し、`fault_kind="task_timeout"` の正常な `BRANCH_STATUS_FAULT` `BranchResult` を返すよう変更。
* モジュール関数 `dispatch_work_items()`: バッチ全体がtimeoutした場合、まだ未応答の全requestについて、対応する各Workerを重複排除して個別にrespawnし、各requestごとにfault結果を合成する。すでに片付いた `request_id` と異なる `received_id`（旧generationからの遅延結果）は黙って破棄する（従来の `RuntimeError("...out-of-order result...")` は撤去）。
* `WorkerDiedError`: 内部ブックキーピング専用の例外クラス（`dispatch_work_items`/`execute` の呼び出し境界を絶対に越えない設計であることをdocstringで明記）。

### 1.2 契約への適合

* 1 Workerの障害・ハングでPool全体は終了しない（他のWorker slotに一切触れないコードパス）。
* Emulator呼び出しがtimeoutを超えた場合、応答を待ち続けず即座にkill+respawn。
* 旧Workerからの遅延結果はPID/generation/request ID不一致として破棄（`request_id` ベースの突合わせで実現、旧generationの結果はそもそも`remaining`/待機対象集合に存在しないため自動的に無視される）。
* respawn前のLeaseは全て無効化（`lease_registry.invalidate_worker`）。
* siblingはStable Root／Replay Rootから再生成可能（既存の `_route_work_item` のbootstrap経路をそのまま利用、変更なし）。
* Pending SnapshotのRestoreは禁止のまま維持（変更していない）。

### 1.3 副次的に発見・修正したバグ（資源クリーンアップ）

全回帰実行中、`test_branch_manager.py`・`test_worker_respawn.py` は**全アサーションPASSした上でプロセス終了時にハング**していた（`exit=124`＝per-file 200sタイムアウト到達）。原因はrespawn時に旧Workerの `in_queue`（`multiprocessing.Queue`）を一切close/cancel_join_threadせず放置していたため、インタプリタ終了時のmultiprocessing内部クリーンアップが「誰も読まなくなったQueue」のフラッシュ待ちでブロックしていたこと。`respawn_worker()` と `close()` の両方で `in_queue.close()` + `in_queue.cancel_join_thread()`（`close()`では `_result_queue` にも同様の処理）を追加して解消した。機能的な正しさ（全アサーション結果）には影響していなかったが、放置すればTraining側の長時間稼働プロセスでファイルディスクリプタ/スレッドリークとして顕在化しうる実バグであり、この場で修正した。

### 1.4 試験結果

`Combat/tests/test_worker_respawn.py`（6 tests, 全PASS）:

| テスト | 検証内容 |
|---|---|
| `test_single_forced_kill_respawns_with_new_pid_and_incremented_generation` | 単発kill→respawnでPID変化・generation+1・sibling無傷 |
| `test_respawn_invalidates_only_that_workers_leases` | respawnしたWorkerのLeaseのみ無効化、他Workerの別Leaseは無傷 |
| `test_100_forced_kills_across_both_workers_never_needs_full_pool_restart` | **100回**の強制kill（2 Worker交互）でPID重複なし・generation単調増加・毎回他方のWorker生存・Pool slot数不変（no restart） |
| `test_old_generation_holder_step_is_rejected_after_respawn` | 実Pending Lease確立→respawn後、旧LeaseのworkerGenerationが新generationと一致せず `is_valid_for()` がFalseになることを実際のLIQUID_MEMORIESシナリオで確認 |
| `test_execute_timeout_surfaces_as_fault_result_and_respawns_hung_worker` | `request_timeout_s=0.001` の実Bootstrap Stepが確実にtimeoutし、`TimeoutError`が外部に漏れず `fault_kind="task_timeout"` のfault結果として返り、該当Workerが自動respawnされることを確認 |
| `test_sibling_can_still_bootstrap_independently_after_a_neighbor_respawn` | 隣のWorkerのrespawn後も別Workerでの新規bootstrapが正常動作 |

---

## 2. Part C: Branch Cancel/Release内部API

### 2.1 設計

新規ファイル `Combat/search/branch_manager.py`、クラス `BranchManager`。

**非同期submit/poll設計を選択した理由**: 既存の `dispatch_work_items()` はバッチ全体をブロッキングで待つ設計であり、これをそのまま使うと「今まさにEmulator呼び出し中のBranch」を個別にCancelする方法がない（どのWorkerが何を実行中か、呼び出し側から見えない）。そこで `BranchManager` は:

* `submit(work_items, parent_branch_id=None) -> list[branch_id]`: `queued` として登録するのみ（即時dispatchしない）。
* `poll(timeout) -> dict[branch_id, BranchResult]`: `queued` な全Branchを実際にWorkerへ提出（`running`）し、共有result Queueをドレインして完了を待つ。timeoutした未応答requestは、対応するWorkerを重複排除してrespawnし、fault結果を合成する（Part Bのロジックを再利用）。

この設計により「1 Workerは常に高々1 requestしか同時に処理しない」というWorker affinity保証（`_worker_main` のシングルスレッドread-loop、既存実装）から、「running状態のBranch＝そのWorkerの唯一の占有者」が構造的に成り立ち、running Branchをkillしても他のBranchを巻き込まないことが保証される。

### 2.2 Branch状態

`queued` / `running` / `completed` / `partial`（型は用意、本Combat用途では現状未使用＝WorkItem単位のBranchは常にsuccess/faultの二値でpartialに該当するケースが生じないため）/ `cancelled` / `faulted` / `released`。

### 2.3 Cancel契約の実装

| 状態 | Cancel時の挙動 |
|---|---|
| `queued` | routing/submit前に単に `cancelled` へ。Workerには一切送信されない。 |
| `running` | 協調停止手段がない（同期的Emulator呼び出し中）ため、`respawn_worker()` で即kill+respawn。Worker affinityにより他Branchは無関係。 |
| `completed`/`partial`/`faulted` | 結果を破棄（`established_lease` があれば `lease_registry.invalidate_lease()`）、`cancelled` へ遷移。 |
| `cancelled`/`released` | 冪等（no-op、例外なし）。 |

* **Holder Cancel**: completed結果が `established_lease` を保持していれば、Cancel時に即座に `invalidate_lease()`。
* **Sibling Cancel**: 対象Branch以外のLease/Branchには一切触れない（`cancel_branches` は指定ID＋その `child_branch_ids` のみを訪問）。
* **親子カスケード**: `submit(..., parent_branch_id=...)` で登録した子は `parent.child_branch_ids` に記録され、親のCancelは深さ優先で全子孫に伝播する。
* **use-after-release拒否**: `get_branch_result()` は `released` 状態のBranchに対し `BranchReleasedError` を送出する。
* **release**: 非終端状態のBranchは自動的に内部で `cancel` してから `released` へ（呼び出し側が状態を気にせず常に安全に呼べるようにするため）。冪等。
* **Episode close/Training切断cleanup**: `close_all()` が既知の全Branchを `cancel_branches` → `release_branches` する。
* **Branch数上限**: `BranchManager(max_branches=...)`（デフォルト256）。`active_branch_count()`（queued+running）が上限を超える `submit()` は `BranchLimitExceededError` を送出し、登録前に拒否する（安全設計として、ユーザー指示の「逆に安全用にbranch数の上限も設定する」に対応）。

### 2.4 試験結果

`Combat/tests/test_branch_manager.py`（11 tests, 全PASS）:

queued cancel／running cancel（kill+respawn実証、PID変化を確認）／Holder cancel（Lease無効化確認）／sibling cancel（他方無傷を確認）／二重cancel冪等／release後参照拒否／release冪等＋active branchの暗黙cancel／episode close cleanup／cancel対象外Branchの継続完了／max_branches上限／未知branch_id例外、を実際の `BranchWorkerPool`（実プロセス・実Emulator呼び出し）に対して検証。

---

## 3. 1000件混在耐久試験

`Combat/search/branch_manager_endurance_runner.py`。持続的な1 Pool（worker_count=3）+ 1 `BranchManager` に対し、1000イベントを6種類のシナリオで巡回混在させた：

1. 通常完了（DEFEND_IRONCLAD/BASHのBootstrap Step）
2. Pending/Lease確立＋sibling Replay（LIQUID_MEMORIES、実Lease確立→pending_pipeline_resultから同一親のsiblingを`parent_branch_id`付きで追加submit）
3. queued cancel（submit直後にcancel、実際に一度もdispatchされないことを確認）
4. 極小timeout（`poll(timeout=0.001)`）によるrunning/timeout-respawn強制発生
5. Pool外からの生kill（`process.terminate()`）＋ `respawn_worker()` 直接呼び出し
6. 通常完了（type 0の別バリエーション）

各イベント後に以下を継続検証:
* 現在生存中の全WorkerのPIDに重複がない（cross-contamination検知）
* Pool のWorker slot数が常に3のまま（no restart）
* Lease registryのサイズがWorker数を超えない（leakage検知）

### 結果

```
event_count: 1000, worker_count: 3, elapsed_s: 551.7
counters: normal_complete=333, pending_lease_and_sibling_replay=167,
          queued_cancel=167, tiny_timeout_respawn=167,
          raw_worker_kill_respawn=166, faults_total=167,
          cancelled_result_reuse_rejections=167
violation_count: 0
final_lease_registry_size: 0
final_pool_worker_slot_count: 3
```

**violation 0件**。Main（テスト内の独立した `main_session`、Pool/Managerに一度も渡していない）は構造的にどのコードパスからも参照されず不変。released Branchの結果再利用は167回すべて `BranchReleasedError` で正しく拒否された。生kill＋タイムアウトrespawnあわせて333回（166+167）のrespawnを経てもPoolは常に3 Worker slotを維持し、full restartは一度も発生していない。

（`running_cancel_kill=0`／`tiny_timeout_respawn=167` は、0.001秒のpoll timeoutが実IPCラウンドトリップに対して常に短すぎ、「本当にrunning状態でcatchできた」ケースが1件もなかったことを示す統計上の偏り。timeout-respawnの機構自体はPart Bの機構と同一であり、真にrunning状態でのcancel-kill経路は `test_branch_manager.py::test_running_cancel_kills_and_respawns_only_that_worker` で別途直接検証済み。）

生ログ: `Outputs/reports/inference_removal_logs/branch_manager_endurance_1000.json`

---

## 4. 全回帰結果

### Combat（26ファイル）

Part B/C関連の新規ファイル・修正ファイルを含め全PASS。既知の事前既存失敗（本フェーズ以前から確認済み、無関係）以外に新規の失敗・regressionなし:

* `test_restore_snapshot_phase3c1.py`: `test_official_json_example_restores_successfully`／`test_real_6546_21_rejected_via_public_api` の2件失敗（既知baseline、26 passed / 2 failed）
* `test_scenario_v2.py`: `test_wriggler_missing_slot_without_encounter_is_detected` の1件失敗（既知の環境依存flake、31 passed / 1 failed）
* その他24ファイルは全PASS（`test_branch_manager.py` 11 passed、`test_worker_respawn.py` 6 passed 含む）

### Whole Run（Run/tests、6ファイル）

* `test_whole_run_connectivity.py`: `test_choice_branch_shop_holder_sibling_reproduction` の1件失敗（既知baseline。旧式の単一プロセス `Run/choice_branch_runner.py` を使用しており、Emulator `fca2f06` の `EnsureNotSuperseded()` により意図的に `"...superseded..."` で失敗する。本フェーズの変更とは無関係）
* その他5ファイルは全PASS（`test_worker_pool_process_separation.py` の5 testsを含む）

---

## 5. 停止条件チェック

以下はいずれも**検出されなかった**：

* per-worker respawnに全面的なIPC再設計が必要 → 不要だった（既存の `fault_taxonomy.py` の設計が既にrespawnを前提としており、`branch_worker_pool.py` へのプロセスkill+respawn配線のみで完結）。
* Main Workerを殺す必要がある → Main（Combat Main loopの `LiveCombatSession`）はこのPoolのいかなるWorkerでもなく、常に無関係。
* CancelがほかのBranchやMainを変える → Worker affinity保証により構造的に不可能であることを設計・試験の両方で確認。
* 旧generation結果が安全に識別できない → `request_id`／`worker_generation`の突合わせで確実に識別・破棄可能。
* Lease所有者が判定できない → `Lease.worker_id`／`LeaseRegistry.invalidate_worker()`で明確。
* Emulator DTO公開に重大な曖昧性 → Part A（別ファイル）で「未解決6件」を明示したのみで、stop相当の重大な曖昧性はなし。
* Hidden Information除去にEmulator DTO契約変更が必要 → 該当なし（Part Aの範囲内）。

---

## 6. 今回のパスで実施していないこと（指示どおり）

* 公開Decision DTO本体・Training向け通信APIの実装には一切進んでいない。Part B/C は「将来外部からBranch IDを指定して呼べる内部契約」レベルに留めている。
* `Training/` 配下の既存の未コミット差分には一切触れていない。

## 7. 変更ファイル一覧

* `Combat/search/branch_worker_pool.py`（Part B respawn機構＋資源クリーンアップ修正）
* `Combat/search/branch_manager.py`（新規、Part C）
* `Combat/search/branch_manager_endurance_runner.py`（新規、1000件混在耐久試験）
* `Combat/tests/test_worker_respawn.py`（新規、6 tests）
* `Combat/tests/test_branch_manager.py`（新規、11 tests）
* `Outputs/reports/rl_dto_exposure_audit_20260803.md`（Part A、既存・別途提出済み）
* `Outputs/reports/inference_removal_logs/branch_manager_endurance_1000.json`（新規、耐久試験生ログ）
* `Outputs/reports/rl_worker_respawn_branch_cancel_20260804.md`（本レポート）
