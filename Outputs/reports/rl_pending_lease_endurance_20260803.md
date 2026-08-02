# Pending／Lease耐久試験の再開 完了報告 (2026-08-03)

基準commit: `af4c27f`(Pending Snapshotの誤Restore修正 完了時点)。
Emulator側 最終commit: `201b7c2`(`C:\STS2_Emulator`、ビルド済み)。
本タスクの実装commit: `32fb1ca`・`d08c432`。耐久試験コードcommit: 本報告書と同時にcommit。

## 1. 経緯 - なぜF2(Pending/Lease耐久試験)が実施不能だったか

前回のF2試験は、「Action Continuation由来の複数候補Pendingが`LiveCombatSession`から一度も
外部へ観測されたことがない」という点で頓挫した。Emulator側commit `201b7c2`の調査
(`C:\STS2_Emulator\docs\reports\pending_choice_exposure_20260803.md`)により、原因の所在が
確定した:

* **`GameInstance`自身はPendingを一切隠していない。** `InteractiveCardSelector.
  GetSelectedCards`は、候補が1件でも複数件でも常に`PendingChoice`を発行する。
  TOOLBOX/SURVIVOR/`LIQUID_MEMORIES`いずれも実機で`Boundary=pending_choice`が正しく
  返ることを確認済み。
* 唯一の自動解決は実エンジン自身の`CardSelectCmd`層にある「候補数が選択必要数以下で、
  選ぶ余地が無い場合」(`CardSelectorPrefs.RequireManualConfirmation`)のみで、この場合
  `PendingChoice`自体が一切発行されない。
* `StepResult.Info`が、Stepの結果として新しいPendingが発生した場合に`choiceScope`/
  `choiceKind`を直接キーとして持つようになった。
* Pending SnapshotのRestore対応は追加されていない(`SupportsPendingChoice == false`のまま、
  指示通り)。

**しかし** RL側`Combat/live_combat_session.py`の`LiveCombatSession.step()`は、Stepの結果が
真の複数候補ActionContinuation Pendingである限り、既定の`continuation_resolver`
(ヒューリスティック)で**自動的に選択を確定し続けてから**呼び出し元へ返す構造になっており、
Search側にもMain側にも真のPendingが一度も`BOUNDARY_PENDING`として見えたことがなかった。
`Combat/search/branch_worker_pool.py`には既にHolder Worker継続・Lease確立・
Bootstrap+Replayによるsibling再現の仕組み(`EXECUTION_MODE_HOLDER_STEP`/
`EXECUTION_MODE_BOOTSTRAP_STEP`/`BranchResult.pending_decision_context`/
`established_lease`、Phase A実装)が既にあったが、一度も`BOUNDARY_PENDING`な
`BranchResult`が実際に生成されたことがないため、この経路は未検証のまま残っていた。

独自に実Emulatorで検証し、上記を確認した(SURVIVOR discard・`LIQUID_MEMORIES`捨て札2枚は
`stop_at_pending=True`で真にPendingへ到達、`LIQUID_MEMORIES`捨て札1枚はエンジン自身の
自動解決で`PendingChoice`が一切発行されないことを実測)。

## 2. Mermaid設計契約の確認結果

`docs/architecture/combat/mermaid_combat_branch_scheduler_detail.mermaid`・
`docs/architecture/combat/mermaid_combat_main_loop_detail.mermaid`とも、**既に正しく**
本件の設計を規定していることを確認した(**図の修正は不要、無変更**)。

* `mermaid_combat_branch_scheduler_detail.mermaid`の`LEASE_ESTABLISH`/`CONTEXT_UPDATE`
  ノードは「あるWorkerが候補Stepを実行し、その結果Pendingへ到達したこと」をLease確立の
  唯一の条件として明記し、`CONTEXT_UPDATE`の遷移条件には「ActionContinuationの連鎖含む」
  と明示的に記載されている。
* `mermaid_combat_main_loop_detail.mermaid`の`NOTE_PENDING_FUTURE`は、PENDING_STATICの
  静的評価限定という制約が「Main-observed Pending(Mainの実RNG直下)という限定された経路
  にのみ適用される」ものであり、「`choice_scope=ActionContinuation`も原則探索対象である」
  という上位方針を撤回するものではないと明記している。

つまり本件は、既に正しい設計契約に対する**純粋な実装ギャップ**であり、前回の
「Pending Snapshotの誤Restore修正」と同じパターンだった。

## 3. 実装した修正

| ファイル | 修正内容 |
|---|---|
| `Combat/live_combat_session.py` | `LiveCombatSession.step()`に`stop_at_pending: bool = False`を追加(commit `32fb1ca`)。`True`の場合、初回StepまたはActionContinuation継続ループ中のいずれかのStepが真の複数候補PendingChoiceを生んだ時点で、`continuation_resolver`を呼ばずに即座にその`BattleState`を返す。エンジン自身の透過的な単一候補自動確定は、`PendingChoice`自体を一切発行しないためこの引数の有無に関わらず無影響。 |
| `Combat/search/branch_worker_pool.py` | `_resolve_and_step()`の`session.step(...)`に`stop_at_pending=True`を追加。Search側は常に最初のPendingで停止する。副次的に、Branch Worker内でIPC経由のJSON文字列snapshotから`derive_context_id()`する際の正規化欠落(`_snapshot_identity_json()`に`str`分岐が無く、Lease確立側とcontext_idが食い違っていた)も修正 - これが無いとPending後のHolder Leaseの継続ラウンドが機能しなかった。 |
| `Combat/search/decision_context.py` | `replay_decision_context()`の`session.step(...)`に`stop_at_pending=True`を追加。sibling WorkerがStable Root Snapshotをrestoreし直前のReplay Prefixを再生する際、途中でPendingへ到達するエントリも正確に再現する。既存のREPLAY_SIG_CHECK(`DecisionSignature`の`choice_scope`/`choice_kind`/`candidate_semantic_keys`比較込み)がそのまま「再現されたPendingの一貫性検証」を兼ねるため、別途の検証コードは不要だった。 |
| `Combat/search/main_loop.py` | `_run_exec_loop()`の`session.step(...)`に`stop_at_pending=True`を追加。SearchがPlan Pathの途中でPendingへ到達するSemantic Actionを含めてMainへcommitした場合、MainがEXEC_LOOPで同じ行動を実行した際も同じくPendingで停止しないと、Search側が記録した`expected_signature`(boundary=pending含む)とMainが観測する`observed_signature`が食い違い、VERIFY_TRANSITIONが誤ってDISCARD/RESYNCしてしまうため。 |
| `Combat/search/multi_round_search.py` | Beam探索のラウンド継続処理が`BOUNDARY_PENDING`を`non_stable_boundary`として打ち切っていたのを、`round_index + 1 < config.max_rounds`であれば`BranchResult.pending_decision_context`(既に`_build_success_result()`が完全に構築済み、追加のRestore/Step不要)を次ラウンドのDecisionContextとして採用しラウンドを継続するよう変更。これがまさにPhase A由来のHolder/Lease/sibling-Bootstrap機構を初めて実行させる変更となる。 |

コーディングはCodex(GPT-5.5、`git add`/`git commit`禁止設定で実行)へ委任し、返された差分を
自身の事前診断(3ファイル+1ファイルの計4箇所、独自に特定済み)と突き合わせて全文確認した上で
本体ツリーへ反映・commitした。

## 4. テスト

新規/拡張したテスト(全て実Emulator経由、モック無し):

| ファイル | 内容 |
|---|---|
| `Combat/tests/test_live_combat_session_step.py`(新規) | `stop_at_pending=True`/`False`双方の挙動、単一候補`LIQUID_MEMORIES`が`PendingChoice`を一切発行しないことを確認。3件pass。 |
| `Combat/tests/test_decision_context.py` | `replay_decision_context()`がPending到達エントリを含むReplay Prefixを正しく再現できることを確認。17件pass(既存含む)。 |
| `Combat/tests/test_branch_worker_pool.py` | 実`LIQUID_MEMORIES`シナリオで、WorkItemのStepが真のPendingへ到達した場合に`BranchResult.result_signature.boundary == "pending"`、`pending_decision_context`/`pending_pipeline_result`/`established_lease`が全て設定されることを確認。10件pass(既存含む)。 |
| `Combat/tests/test_multi_round_search.py` | Beam探索がPendingへ到達する候補を含む場合にBeamを打ち切らず次ラウンドへ継続し、`EXECUTION_MODE_HOLDER_STEP`と`sub_branch`+`EXECUTION_MODE_BOOTSTRAP_STEP`の双方が実際に発生することを確認。7件pass(既存含む)。 |

全て私自身が独立に実行し(Codexの自己申告のみに依拠せず)、実行結果を確認した。

### 全既存回帰スイート(15ファイル、Codex差分を本体ツリーへ反映後に自分で再実行)

`test_decision_context.py`(17)・`test_main_loop.py`(13、`test_pending_boundary_cannot_route_to_search`
含めPending関連の既存不変条件も無変更で保持を確認)・`test_search_coordinator.py`(14)・
`test_multi_round_search.py`(7)・`test_shadow_adapter.py`(7)・`test_belief_coverage.py`(5)・
`test_candidate_pipeline.py`(10)・`test_branch_worker_pool.py`(10)・`test_rng_hypothesis.py`(8)・
`test_fault_taxonomy.py`(10)・`test_multi_combat_continuous_execution.py`(1)・
`test_shadow_evaluation_batch.py`(1)・`test_endurance_runner.py`(1)・
`test_multi_hypothesis_stress_runner.py`(1)・`test_live_combat_session_step.py`(3)・
`test_restore_snapshot_phase3c1.py`(26/28、既知の未関連2件のみ失敗、回帰ではない)。

**新規の回帰は無し。**

## 5. Pending／Lease耐久試験(実1,000イベント)

新規スクリプト`Combat/search/pending_lease_endurance_runner.py`を作成し、1つの共有
`BranchWorkerPool`(worker_count=3)+`LeaseRegistry`を1,000件の真のPending境界イベントに
わたって使い回す耐久試験を実施した。

### 手法

各イベントは以下の手順を直接`dispatch_work_items()`経由で実行する(`multi_round_search.py`の
Beam層は経由しない - カード/Potion系候補は保守的にHypothesis必須と分類され、その場合の
Pending到達は既存の`_completed_hypothesis_round`が同一の`_build_success_result`/Lease機構を
経由して1ラウンドで完結する設計のため、耐久試験としてはLease/Worker機構そのものを直接・
決定論的に検証する本方式を採用した):

1. SURVIVOR(手札discard、3候補)/`LIQUID_MEMORIES`(捨て札2枚からretrieve、2候補)を
   交互にStable根から1件Stepし、真のPending境界へ到達させる(Lease発行)。
2. **Holder継続**: Pending Decision Contextから継続候補(index 0)を、確立直後のLeaseと
   同一のcontext_idでWorkItem化しdispatch。`EXECUTION_MODE_HOLDER_STEP`かつ
   **Lease確立時と同一のworker_id**で実行されることを確認。
3. **Sibling再現**: 別候補(index 1、Holderとは異なるカード)を同一context_idの
   `sub_branch`としてdispatch。`EXECUTION_MODE_BOOTSTRAP_STEP`(直前のStable Root
   SnapshotをRestoreしReplay Prefixを再生 - Pending Snapshot自体は一度もRestoreしない、
   Phase Gのガードと自然に整合)で実行され、Holderとは異なる`combat_session_id`・
   異なる解決カードで独立にStableへ到達することを確認。
4. **Lease解放確認**: Holder継続後、`LeaseRegistry`に当該Leaseがもう存在しないことを
   直接確認。
5. **Cross-context誤用拒否**(全体の約5%のイベントで実施): 確立直後の実Leaseに対し、
   同一のcontext_id/search_hypothesis_idだが異なる`combat_session_id`を主張する
   WorkItemを構築し、`Lease.is_valid_for()`が識別子の一致だけでなく内容(state identity)
   の不一致を理由に正しく`False`を返すことを確認。
6. **Fault注入とLease無効化**(全体の約10%のイベントで実施): sibling側のRoot Snapshotへ
   意図的にdangling `CombatHistory`参照を注入して実Faultを発生させ、
   `dispatch_work_items()`の既存の`lease_registry.invalidate_worker()`が働くこと、
   直後のリトライが正常に成功することを確認。
7. `worker_generation`が各Workerごとに単調非減少であること、Emulatorが独立restoreの
   たびに発行する`combat_session_id`が1,000件を通じて一度も重複しないこと
   (State-Holding Worker間のcross-contaminationが無いことの直接的な証拠)を全イベントで
   確認。

### 結果(1,000/1,000イベント完走、`STOP_CONDITION`無し)

| 指標 | 値 |
|---|---|
| Pending境界イベント数 | 1,000(SURVIVOR 500 / LIQUID_MEMORIES 500) |
| Lease発行数 | 1,000(イベントごとに厳密1件) |
| Lease解放数 | 1,000(イベントごとに厳密1件、Holder解決直後に消滅を確認) |
| Holder Step実行数 | 1,000(全件、Lease確立時と同一worker_idで実行) |
| sibling Bootstrap+Replay成功数 | 1,000(直接成功、またはFault後リトライ経由での成功を含む) |
| 意図的Fault注入・実発生数 | 100(`post_teardown_restore_failure`、全件正しく分類) |
| Fault後Lease無効化+リトライ成功確認数 | 100(全件成功) |
| Cross-context誤用拒否確認数 | 50(全件、内容不一致により正しく拒否) |
| ユニークな`combat_session_id`数 | 1,000(イベント数と一致 - 重複無し、cross-contamination無し) |
| `worker_generation`単調性 | 全Workerで単調非減少を維持 |
| 所要時間 | 66.0秒(15.2イベント/秒、worker_count=3) |

重大なcross-contamination・Main状態変異・不公平なHypothesis適用・Lease誤用は一切検出されなかった。

## 6. 確認済みの無変更範囲

* `Combat/search/candidate_pipeline.py`・`Combat/search/fault_taxonomy.py`・
  `Combat/search/search_coordinator.py`・`Combat/combat_state_snapshot.py`は無変更
  (指示通り、触らずに済んだ)。
* Mermaid diagram群は無変更(契約は既に正しかった、2節参照)。
* `branch_worker_pool.py`の`_build_success_result()`のPending到達時のroot_snapshot継承
  ロジック自体は無変更 - Phase G以来の設計通り正しく機能した。

## 7. 未対応事項・今後の課題

* 本耐久試験はSURVIVOR/`LIQUID_MEMORIES`という2種類のAction Continuation Pendingに限定した。
  TOOLBOX等のStart-of-Combat Pendingは、既存の`PendingSearchNotAllowedError`により
  そもそもSearchへ到達しない設計(Main自身の`PENDING_STATIC`が処理)であり、本タスクの
  対象外(F2/前回報告の既存整理通り)。
* カード/Potion系のPending選択候補は`consume_check()`により保守的にHypothesis必須と
  分類されるため、`multi_round_search.py`のBeam継続(plain path)を実際に経由するのは、
  `choice_confirm`/`choice_skip`のみで構成される中間確定ステップに限られる
  (`test_multi_round_search.py`の新規テストは`_requires_hypothesis`を一時的にFalse固定
  することでこのplain path自体を単体検証している)。Hypothesis必須経路でのPending到達は
  既存の`_completed_hypothesis_round`が同一のLease機構を経由して正しく1ラウンドで完結する
  設計であり、これは本タスクで変更しておらず、独立に動作を妨げない。

## 8. Git commit・作業ツリー状態

| commit | 内容 |
|---|---|
| `32fb1ca` | `LiveCombatSession.step()`への`stop_at_pending`追加 |
| `d08c432` | Search/Replay/Main配線 + `multi_round_search.py`のPending継続 + テスト |
| (本報告と同時) | `pending_lease_endurance_runner.py` + 本報告書 |

`git status --short`はclean(作業ツリーに未commit変更無し)。

## 9. 結論・Training接続への準備状況

前回のShadow評価・耐久試験報告(`b612543`)およびF1/F2報告(`9f1e3f1`)で指摘された2件の
ギャップのうち、残っていた「Pending/Lease耐久試験が実施不能」という問題は、今回の
`stop_at_pending`配線と`multi_round_search.py`のPending継続実装により解消した。
1,000件の実Pending境界イベントを通じて、Holder Worker継続・sibling Bootstrap+Replay・
Lease発行/消費/解放・Fault後の無効化・Cross-context誤用拒否・worker_generation単調性・
CombatSessionId一意性のいずれにも重大な問題は検出されなかった。

Pending Snapshot自体を直接Restoreする経路は本タスクを通じて一度も発生しなかった
(既存のPhase Gガード`PendingSnapshotRestoreViolationError`は今回一度も発火しておらず、
それは想定通りの結果である - 全てのsibling再現は正しくStable Root+Replay Prefix経由だった)。

Main-observed Pending(Start-of-Combat等)は引き続きSearchへ到達しない設計のまま、
Action Continuation Pendingへの探索対応が実証されたことで、Combat Search
Vertical SliceのTraining接続に向けた既知の設計・実装ギャップは本報告時点で解消されたと
判断する。ここで停止する。
