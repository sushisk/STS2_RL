# Start-of-Combat Pending探索 実装完了報告 (2026-08-03)

基準commit: `5ece1e2`(Pending/Leaseエンデュランス試験 完了時点)。
実装commit: `831de69`(コア配線)・`3767a07`(mermaid図)・`5847ce5`(テスト・実機検証ランナー)。

## 1. 方針の実現方法

Start-of-Combat Pending(TOOLBOX/CHOICES_PARADOX/GAMBLING_CHIP等)は`ResetFromScenario`/
`start_combat()`直後に発生し、**直前のStable境界が一度も存在しない**という点で、これまで
実装してきたAction Continuation Pending(前回タスク)とは根本的に異なる。前回のPending探索は
「直前のStable SnapshotをRestoreしてReplayで再現する」機構だったが、Start-of-Combat Pendingには
そもそも「直前のStable Snapshot」が存在しない。

この差を埋めるため、**Combat Start Replay Root**という新しい「根」の種別を導入した。
`DecisionContext.root_snapshot`は、これまでの`CombatStateSnapshot`(Capture済みSnapshot)に加え、
`CombatStartReplayRoot`(このCombat開始に使った`scenario_spec`辞書 — Scenario／RNG Seed／Deck／
Relicそのもの。`build_scenario_from_spec()`の入力データであり、**Captureされたものではない**)を
保持できるようにした。sibling Workerは、Pending SnapshotをRestoreするのではなく、**同じ
scenario_specで`start_combat()`を再実行**することで同じPendingへ独立に到達する。

## 2. 実装した変更

| ファイル | 変更内容 |
|---|---|
| `Combat/search/decision_context.py` | `CombatStartReplayRoot`(dataclass、`scenario_spec: dict`)新設。`DecisionContext.from_combat_start_pending()`。`replay_decision_context()`が`isinstance(root_snapshot, CombatStartReplayRoot)`の場合`session.start_combat(scenario_spec)`を呼ぶ分岐(Restoreは一切呼ばない)。Phase G由来の`PendingSnapshotRestoreViolationError`ガードはこの種別に対しては何もしない(そもそもRestoreしないため検査対象がない)。 |
| `Combat/search/branch_worker_pool.py` | `_snapshot_identity_json()`/`_snapshot_ipc_json()`が`CombatStartReplayRoot`を最初にチェックする分岐(JSON-safeなdictそのものなのでCLR変換不要、trivially picklable)。`derive_context_id()`/Lease/dispatchルーティングは無変更で正しく機能する(root種別を意識しないduck-typed設計のため)。 |
| `Combat/search/main_loop.py` | `MainLoopState.combat_start_replay_root`(既定None、後方互換)。`initialize_main_loop_state(..., combat_start_replay_root=...)`。`build_combat_start_decision_context()`(`build_main_decision_context()`のStart-of-Combat版)。`run_until_terminal_or_fault()`内で`PendingSearchNotAllowedError`は、`held_stable_snapshot is None and combat_start_replay_root is not None`のとき(=genuine Start-of-Combat Pending)のみ回避されSearchへ進む。それ以外(mid-combatのPending)は従来通り例外。 |
| `Combat/search/rng_hypothesis.py` | `compute_public_multiset_for_combat_start()`(Scenario specの可視piles分をデッキ全体から差し引く — CombatHistoryが存在しない時点のため生成カード考慮は不要)。`derive_substituted_replay_root()`(`scenario_spec["draw_pile"]`のみをhypothesisの信念順で置換 — CombatScenarioには単一streamの差し替え点[SnapshotのRng.RunRng["Shuffle"]相当]が無いため)。`CombatStartHypothesisGridCell`/`build_grid_for_combat_start()`。 |
| `Combat/search/belief_coverage.py` | `assess_public_multiset_coverage_for_combat_start()`/`compute_public_multiset_with_coverage_for_combat_start()`(既存の`CONFIRMED_COVERED_GENERATION_SOURCES`/`UNCERTAIN_GENERATION_SOURCES`テーブルを、Snapshotの代わりにScenario specの`relics`/`player_powers`から読む — テーブル自体は変更なし)。 |
| `Combat/search/search_coordinator.py` | `_hypothesis_work_items_with_coverage()`がgenesis根の場合に上記genesis版へ分岐。`_check_main_invariant()`のheld_snapshotチェックが、genesis DecisionContextの場合は`live_state.held_stable_snapshot is None`であることのみを確認する(digest比較はしない — 指示の「Start-of-Combat PendingをHeld Stable Snapshotとして扱わない」を Main不変条件検証コードでも徹底)。 |

## 3. Mermaid図の更新

`mermaid_combat_snapshot_replay_detail.mermaid`・`mermaid_combat_main_loop_detail.mermaid`・
`mermaid_combat_branch_scheduler_detail.mermaid`の3図を更新した(詳細は
`docs/architecture/combat/SVG_RENDER_LOG.md`第6回検証セクション参照)。

* `DC_COMBAT_START`(Combat Start Replay Rootの形)・`WHO_COMBAT_START`(Start-of-Combat
  Pendingのみの新規入口)・`ROOT_KIND`/`COMBAT_START_RUN`(SUB_REPLAYサブグラフ内、Restoreの
  代わりに`start_combat()`を再実行する分岐)を`snapshot_replay_detail`へ追加。
* `NEED_SEARCH_COMBAT_START`(`held_stable_snapshot is None`の場合のみのSearch入口)・
  `NOTE_NO_HELD_SNAPSHOT_AT_GENESIS`(Start-of-Combat PendingをHeld Stable Snapshotとして扱わない
  ことの明記)を`main_loop_detail`へ追加、`NOTE_PENDING_FUTURE`を実装済み内容へ更新。
* `branch_scheduler_detail`は視覚的なノード/エッジ変更なし(コメントのみ、「Root Snapshot」が
  Combat Start Replay Rootも含む総称であることの注記を追加)。

`@mermaid-js/mermaid-cli` v11.16.0で3図とも実レンダリング成功を確認、SVG再生成、
`MANIFEST.sha256`を全8図分再計算し`sha256sum -c`で検証済み(5図は前回commitから不変)。

## 4. 実機検証

### 4-1. 標準ライブラリのみのアドホック検証(監督者が直接実行、リポジトリには含めない)

* TOOLBOX: `start_combat()`直後に`Boundary=pending`(`choiceType=ToolboxChooseCard`,
  `scope=StartOfCombat`)。2つの異なる`choice_card`候補をそれぞれ`WorkItem`化し同時dispatch →
  両方とも`execution_mode=bootstrap_step`(まだLease未確立)で、異なる`combat_session_id`・
  異なる`resolved_card_id`に独立到達。
* CHOICES_PARADOX / GAMBLING_CHIP: それぞれ`choiceType=ChoicesParadoxAddToHand`/
  `GamblingChipDiscard`, `scope=StartOfCombat`で正しくPendingを発生。
* `run_until_terminal_or_fault()`を実`build_search_strategy()`・実`routing_policy`
  (`boundary=="pending" and held_stable_snapshot is None`のとき`ROUTE_SEARCH`)で実行した
  end-to-end検証: genesis Pendingが正しくSearchへ回り、Search呼び出し中`held_stable_snapshot`は
  `None`のまま、Search完了後Mainが実際に行動を実行して初めて実Snapshotで確定し、その後戦闘は
  正常にTerminalまで完走した。

### 4-2. 正式テスト・実機検証ランナー(commit `5847ce5`、リポジトリに追加)

`Combat/tests/test_decision_context.py`・`test_branch_worker_pool.py`・`test_main_loop.py`・
`test_search_coordinator.py`・`test_rng_hypothesis.py`・`test_belief_coverage.py`へ新規テストを
追加(実Emulator経由、モック無し)。Codexへコーディングを委任し(`git add`/`commit`禁止設定で
実行)、返された差分を全文レビューした上で本体ツリーへ反映し、全テストを自分の環境で再実行して
結果を確認した。

新規`Combat/search/combat_start_pending_verification_runner.py`: TOOLBOX/CHOICES_PARADOX/
GAMBLING_CHIP各34回・合計102件のStart-of-Combat Pendingイベントを、1つの共有
`BranchWorkerPool`(worker_count=3)+`LeaseRegistry`で処理する実機検証ランナー。Codexの実行結果を
そのまま信頼せず、監督者自身の環境で独立に再実行し、同一の結果を確認した。

| 指標 | 値 |
|---|---|
| Start-of-Combat Pendingイベント数 | 102(TOOLBOX 34 / CHOICES_PARADOX 34 / GAMBLING_CHIP 34) |
| 各Choiceの分岐確認 | 全102件、2つの異なる候補がそれぞれ独立に異なる`combat_session_id`・異なる解決結果(カードIDまたはboundary)へ到達したことを確認 |
| Fault後の再生成確認 | 意図的に`scenario_spec["character_id"]`を不正化して実`action_fault`を注入・分類、直後に正しいscenario_specでのリトライが成功(=同一Combat Start Replay Rootからの`start_combat()`再実行によるFault後再生成)。合計24件のFault注入、全件リトライ成功 |
| Worker/Session混線確認 | 214件の`combat_session_id`が完全にユニーク(重複無し)、`worker_generation`が全Workerで単調非減少 |
| GAMBLING_CHIPの複数選択パターン | 可変selectのGAMBLING_CHIPでLease確立/Holder継続の実発生を確認(`gambling_chip_lease_observed=true`) |
| 所要時間 | 約16秒(6.4イベント/秒、worker_count=3) |

`lease_issues`(37)と`lease_releases`(20)の差は、ランナーの追跡ロジックがGAMBLING_CHIPの
follow-up(2手目のchoice_confirm/choice_skip)経由でのみ`lease_releases`をカウントする設計に
起因する診断上の非対称であり(TOOLBOX/CHOICES_PARADOX同様1手で即Stableへ到達するケースは
そもそもLeaseを確立しないため両カウンタとも増えない)、`StopConditionError`としては検証して
いない参考値である。実際のLease生存期間・cross-contamination不在は`unique_combat_session_ids`と
`worker_generation_non_decreasing`で直接検証済み。

## 5. 制約の遵守確認

* **Pending Snapshotは直接Capture／Restoreしない**: `replay_decision_context()`の
  `CombatStartReplayRoot`分岐は`session.start_combat(scenario_spec)`のみを呼び、
  `restore_snapshot()`/`capture_snapshot()`は一切呼ばない(テストで`restore_snapshot`呼び出し
  回数が0であることを明示的に確認済み)。
* **Combat Start Replay Rootとして保持**: `scenario_spec`辞書自体がScenario／RNG Seed／Deck／
  Relicを含む完全な入力データであり、追加の変換・再構成は不要だった。
* **Holder Workerが開始処理で到達したPending状態を保持して1つのChoiceを実行**:
  `EXECUTION_MODE_HOLDER_STEP`は前回タスクで実装済みの機構をそのまま再利用(root種別を意識しない
  設計のため無変更で機能した)。
* **sibling Workerが同じReplay RootからCombatを開始し直し、別Choiceを実行**:
  `EXECUTION_MODE_BOOTSTRAP_STEP`が`ROOT_KIND`分岐経由で`start_combat()`再実行に切り替わる形で
  実現、実機検証で確認済み。
* **再現後のChoiceScope/ChoiceKind/候補Semantic Key集合の照合**: 既存の`REPLAY_SIG_CHECK`
  (`DecisionSignature.matches_for_replay()`、Pending時は`choice_scope`/`choice_kind`/
  `candidate_semantic_keys`も比較対象)がroot種別を意識せず動作するため、追加コード不要で
  自動的に適用される。
* **Start-of-Combat PendingをHeld Stable Snapshotとして扱わない**: `MainLoopState.
  held_stable_snapshot`はgenesis Search呼び出し中`None`のまま(実機end-to-end検証で確認)。
  `_check_main_invariant()`もこの区別を徹底し、genesis DecisionContextに対してSnapshotの
  digest比較を行わない。
* **Mainの真のOrderedDrawPileや将来RNGを評価へ直接渡さない**: `derive_substituted_replay_root()`
  はhypothesisの信念順のみを`scenario_spec["draw_pile"]`へ書き込み、Main自身の元の
  `scenario_spec`オブジェクトは変更しない(テストで確認済み)。既存のSearch Hypothesis／DrawPile
  Belief規則(`consume_check()`の分類ルール・GRID不変条件・COMMIT_FIRST_ONLY等)はすべて無変更。

## 6. 全既存回帰スイート

16ファイル全て再実行(自分の環境で、Codexの自己申告のみに依拠せず):
`test_decision_context.py`(19)・`test_main_loop.py`(15)・`test_search_coordinator.py`(15)・
`test_multi_round_search.py`(7)・`test_shadow_adapter.py`(7)・`test_belief_coverage.py`(6)・
`test_candidate_pipeline.py`(10)・`test_branch_worker_pool.py`(11)・`test_rng_hypothesis.py`(10)・
`test_fault_taxonomy.py`(10)・`test_multi_combat_continuous_execution.py`(1)・
`test_shadow_evaluation_batch.py`(1)・`test_endurance_runner.py`(1)・
`test_multi_hypothesis_stress_runner.py`(1)・`test_live_combat_session_step.py`(3)・
`test_restore_snapshot_phase3c1.py`(26/28、既知の未関連2件のみ失敗、回帰ではない)。

**新規の回帰は無し。**

## 7. Git commit・作業ツリー状態

| commit | 内容 |
|---|---|
| `831de69` | Combat Start Replay Rootのコア配線(6ファイル) |
| `3767a07` | Mermaid図3枚の更新・再レンダリング・MANIFEST再計算 |
| `5847ce5` | テスト(6ファイル)+実機検証ランナー新規追加 |
| (本報告と同時) | 本報告書 |

`git status --short`はclean(作業ツリーに未commit変更無し)。

## 8. 結論

Start-of-Combat Pending(TOOLBOX/CHOICES_PARADOX/GAMBLING_CHIP)を`PENDING_STATIC`限定から外し、
Combat Start Replay Rootという新しい根の種別を通じて通常のSearch対象とした。前回タスクで実装した
Action Continuation Pending向けのHolder/Lease/sibling-Bootstrap機構は、root種別を意識しない
設計になっていたため、ほぼ無変更で再利用できた。実機で102件のStart-of-Combat Pendingイベントを
通じ、各Choiceの分岐・Fault後の再生成・Worker/Session混線の不在を確認し、重大な問題は検出
されなかった。コードとMermaid図は一致した状態でcommit済み。ここで停止する。
