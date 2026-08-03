# RL担当 総合報告：公開DTO監査・Combat Worker Respawn・Branch Cancel（Phase N）

- RL基準commit（作業開始点）: `235eb8b`
- Emulator基準commit: `fca2f06`
- 本コミット: `cc13130`
- 対象指示: 「RL担当指示：公開DTO監査・Combat Worker Respawn・Branch Cancel」（A/B/Cの3作業＋ユーザー個別注記A/B/C）

本レポートは今回のPhase全体（A・B・C三作業＋各種試験＋全回帰）を一枚で俯瞰できるようにまとめたものです。詳細な一次資料は以下の2本の個別レポートに分けて提出済みです。

* Part A詳細: `Outputs/reports/rl_dto_exposure_audit_20260803.md`
* Part B/C詳細＋試験結果: `Outputs/reports/rl_worker_respawn_branch_cancel_20260804.md`

---

## 0. 全体サマリ

| 区分 | 作業内容 | 状態 | 一次資料 |
|---|---|---|---|
| A | Emulator DTO公開範囲の事前監査（フィールド単位表＋8Decision種別ペイロード例） | 完了・報告済み・**承認待ち**（実装は未着手） | `rl_dto_exposure_audit_20260803.md` |
| B | Combat WorkerのOSレベルRespawn（PID/generation追跡、hang時kill+respawn、旧Lease無効化） | 完了・全試験PASS | `rl_worker_respawn_branch_cancel_20260804.md` §1 |
| C | Branch Cancel/Release内部API（状態機械、cancel/release/status、branch数上限） | 完了・全試験PASS | 同上 §2 |
| - | 1000件混在耐久試験（Combat Branch、Cancel/Fault/Kill/Timeout/Respawn混在） | 完了・violation 0件 | 同上 §3、生ログ `inference_removal_logs/branch_manager_endurance_1000.json` |
| - | 全回帰（Combat 26ファイル＋Whole Run 6ファイル） | 完了・既知baseline失敗以外は全PASS | 同上 §4 |

停止条件（7項目）は**いずれも検出されなかった**（詳細は §4 参照）。指示どおり、公開Decision DTO本体・Training向け通信APIの実装には一切進んでおらず、`Training/`配下の既存の未コミット差分にも一切触れていません。

---

## 1. 経緯・ユーザーからの個別注記への対応

今回の指示には、正式な作業指示に先立ってユーザーから3点の個別注記（A/B/C）があり、それぞれ以下のように反映しました。

* **注記A**「Training側とのDTOを作成する。まず何を公開し何を伏せるかのリスト提出を求める」
  → Part Aとして、Observation/State・LegalAction・StepResult・TransitionOutcome・RoomContext・MapRoomOption・RunResetResult/RoomEnterResult・Combat Observation・Pending Choice/Target情報・Card/Enemy/Relic/Potion/Deck/Reward・Map/Room/Event/Shop/Rest・Run Summary・SaveState/Run Snapshot関連DTO・RNG関連DTO・Worker/Replay/Lease関連のRL内部DTOを、フィールド単位で5分類（そのまま公開／公開だがマスク・削減／RL内部限定／Training非公開／判断保留）した表と、8 Decision種別ごとのペイロード例を提出。実装には進まず、承認待ちの状態で本パスを終えています。

* **注記B**「停止時の安全用のfallbackをコードしてほしい。何回停止でエラーとするかは任せる」
  → Part Bとして実装。Worker単位で即座に（リトライ回数を待たず）kill+respawnする設計を採用しました。理由は、Combat WorkerのEmulator呼び出しは同期的でハング中は協調的な中断手段がなく、「N回失敗したらエラー」という累積カウンタ方式よりも「1回のtimeout/クラッシュで即respawnし、既存のfault_taxonomy側の`decide_retry()`が持つ既存のリトライ上限（既存実装、変更なし）に処理を委ねる」方式の方が、1 Workerの障害でPool全体を止めないという要件に対して安全かつシンプルだったためです。

* **注記C**「branchを消せるようにする。3 APIは用意することになっているが設計は任せる。安全用にbranch数上限も」
  → Part Cとして `cancel_branches` / `release_branches` / `get_branch_status` の3 APIを実装し、`BranchManager(max_branches=...)`（デフォルト256）で同時アクティブ（queued+running）Branch数の上限を設け、上限超過時は `submit()` の時点で例外を送出して拒否する設計にしました。

---

## 2. Part A: Emulator DTO公開範囲の事前監査（要旨）

判断基準は「Emulatorの内部状態か」ではなく「実際のプレイヤーがそのDecision時点で知り得るか」。主な結論（詳細は一次資料参照）:

* `Observation.State.deck`／`map` はそのまま公開可（`map`はC#の`BuildMapDict`実装を直接確認・実際にseed=18のAct1で12/66ノードが`"Unknown"`のまま保たれることをライブ確認済みで、実ゲームのマップ画面同等＝Hidden Information漏洩ではない）。
* `drawPile`（順序付き）はTrainingへ非公開（カテゴリ4）。`discardPile`／`exhaustPile`は公開するがMultiset化してのマスクを推奨（`OrderMaskedObservation`という既存パターンをテンプレートとして参照）。
* SaveState/Run SnapshotのRNGストリーム・事前生成Event/EncounterIDリストはRL内部限定（カテゴリ4）であり、これらは`Observation.State`には一切漏れ出ていないことを確認済み。
* Worker/Replay/Lease関連DTOはすべてRL内部専用。
* 未解決6件（`Metrics`/`Extras`自由形式内容の未検証、`playPile`のUI可視性、`discardPile`/`exhaustPile`の順序アクセス可否、`StepResult.Info`のキー単位再マッピング要否、`StepResult.Reward`のCombat専用1.0/-1.0意味論、実クライアント逆コンパイル未突合という一般的留保）を明記。stop相当の重大な曖昧性はなし。

**この監査結果は実装前の承認待ちであり、本パスでは公開Decision DTO・Training向けAPIの実装には進んでいません。**

---

## 3. Part B: Combat Worker OS-level Respawn（要旨）

既存の `search/fault_taxonomy.py` が `FAULT_TASK_TIMEOUT`/`FAULT_WORKER_PROCESS_CRASH` を既に `FORCE_RESTART`＋generation増分にマッピング済みだったため、`search_coordinator.py`側は無変更。`branch_worker_pool.py` に以下を追加:

* `_WorkerHandle.pid` によるPID追跡、`worker_pids`/`is_worker_alive()`プロパティ
* `respawn_worker(worker_id, lease_registry=None)`: kill→旧`in_queue`のclose/cancel_join_thread→同じslotへgeneration+1で再spawn→`lease_registry.invalidate_worker()`
* `execute()`/`dispatch_work_items()`: timeout時に`TimeoutError`を投げる代わりに該当Workerをrespawnし、`fault_kind="task_timeout"`の正常なfault結果を返す。旧generationからの遅延結果は破棄。

**副次的に発見・修正したバグ**: 全回帰実行中、respawnを多用する2ファイル（`test_branch_manager.py`／`test_worker_respawn.py`）が全アサーションPASSした後にプロセス終了時ハング（exit=124）していました。原因は旧Workerの`in_queue`を放置していたことによるmultiprocessing終了時クリーンアップのブロックで、`close()`/`respawn_worker()`双方に`in_queue.close()`+`cancel_join_thread()`を追加して解消しました（機能的な正しさには影響なし、リソースクリーンアップのみの修正）。

試験: `test_worker_respawn.py`（6 tests全PASS、うち1つは**100回以上**の強制kill×2 Worker交互でPID重複なし・generation単調増加・Pool slot数不変を検証）。

---

## 4. Part C: Branch Cancel/Release内部API（要旨）

新規 `Combat/search/branch_manager.py`。既存の`dispatch_work_items()`がバッチ全体ブロッキング型で「今実行中のBranch」を個別Cancelできない構造だったため、`submit()`（queued登録のみ）と`poll()`（実dispatch＋結果回収）を分離した非同期設計を新規導入。これにより「1 Workerは常に高々1 requestしか同時処理しない」という既存のWorker affinity保証から、running状態のBranchをkillしても他のBranch/Leaseを巻き込まないことが構造的に保証されます。

Branch状態: `queued`/`running`/`completed`/`partial`/`cancelled`/`faulted`/`released`。Cancel契約（queued即時除外／running kill+respawn／completed結果破棄／cancelled・released冪等／release後結果参照は`BranchReleasedError`で明示拒否／親Cancelの子への伝播／Episode close時`close_all()`で全Cancel+Release）を実装し、`max_branches`（デフォルト256）で安全上限を設定。

試験: `test_branch_manager.py`（11 tests全PASS、queued/running/Holder/sibling cancel、近接完了レース、二重cancel、release後参照拒否、Worker kill併用cancel、episode close cleanup、非対象Branchの継続完了、上限enforcement、未知ID例外を実際のPool/Emulatorに対して検証）。

---

## 5. 1000件混在耐久試験（要旨）

持続的な1 Pool（worker_count=3）+ 1 `BranchManager`に対し、通常完了／Pending・Lease確立＋siblingReplay／queued cancel／極小timeoutによるrespawn／Pool外からの生kill+respawnを1000イベント巡回混在。

```
event_count=1000, elapsed_s=551.7
normal_complete=333, pending_lease_and_sibling_replay=167, queued_cancel=167,
tiny_timeout_respawn=167, raw_worker_kill_respawn=166, faults_total=167,
cancelled_result_reuse_rejections=167
violation_count=0, final_pool_worker_slot_count=3, final_lease_registry_size=0
```

333回のrespawnを経てもPoolは常に3 Worker slotを維持しfull restartは一度も発生せず、PID重複（cross-contamination）・Lease leakage・released結果の再利用はいずれも0件でした。

---

## 6. 全回帰結果（要旨）

* **Combat（26ファイル）**: 新規2ファイル（`test_worker_respawn.py` 6 tests、`test_branch_manager.py` 11 tests）含め全PASS。既知の事前既存失敗2件（`test_restore_snapshot_phase3c1.py`の2件、`test_scenario_v2.py`の1件、いずれも本フェーズ以前から確認済み・無関係）以外に新規regressionなし。
* **Whole Run（Run/tests、6ファイル）**: 既知baseline失敗1件（`test_choice_branch_shop_holder_sibling_reproduction`、旧式単一プロセスrunner起因、無関係）以外は全PASS。

---

## 7. 停止条件チェック結果

| 条件 | 判定 |
|---|---|
| per-worker respawnに全面的IPC再設計が必要 | 該当なし（既存fault_taxonomy設計の配線のみで完結） |
| Main Workerを殺す必要がある | 該当なし（MainはこのPoolのWorkerではない） |
| CancelがほかのBranch/Mainを変える | 該当なし（Worker affinity保証により構造的に不可能、試験でも確認） |
| 旧generation結果が安全に識別できない | 該当なし（request_id/worker_generationで確実に識別・破棄） |
| Lease所有者が判定できない | 該当なし（`Lease.worker_id`/`invalidate_worker()`で明確） |
| Emulator DTO公開に重大な曖昧性 | 該当なし（未解決6件のみ、stop相当ではない） |
| Hidden Information除去にEmulator契約変更が必要 | 該当なし |

---

## 8. 成果物・コミット

コミット `cc13130`（1 commit、8 files changed, 1806 insertions(+), 17 deletions(-)）:

* `Combat/search/branch_worker_pool.py`（変更・Part B）
* `Combat/search/branch_manager.py`（新規・Part C）
* `Combat/search/branch_manager_endurance_runner.py`（新規・耐久試験）
* `Combat/tests/test_worker_respawn.py`（新規）
* `Combat/tests/test_branch_manager.py`（新規）
* `Outputs/reports/rl_dto_exposure_audit_20260803.md`（Part A）
* `Outputs/reports/rl_worker_respawn_branch_cancel_20260804.md`（Part B/C詳細）
* `Outputs/reports/inference_removal_logs/branch_manager_endurance_1000.json`（耐久試験生ログ）

`Training/`配下の既存の未コミット差分（本Phaseの開始前から存在）には一切触れていません。作業ツリーはそれ以外の範囲でclean（`git status`で`Training/`以外の変更なしを確認済み）です。

## 9. 今後の予定（未着手・承認待ち）

* Part Aの監査結果に対するTraining側の承認
* 承認後: 公開Decision DTO本体の実装
* Training向け通信API（`cancel_branches`等の内部APIを外部から呼べるようにする層）の実装
