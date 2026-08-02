# Pending Snapshotの誤Restore修正 報告 (2026-08-03)

基準commit: `9f1e3f1`(高負荷探索ストレス試験 完了時点)。修正commit: `69b225a`。

## 発見の経緯

F2(Pending/Lease耐久試験)着手時、TOOLBOX等が発生させる実Pending境界のSnapshotを
Branch Worker経由でRestoreしようとしたところ、Emulator側から
`unsupported_capture_boundary:published_choice`／`pending_choice_present`で
明示的に拒否されることを発見し、当該タスクを一旦停止して報告した。

## Mermaid設計契約の確認結果

`docs/architecture/combat/mermaid_combat_snapshot_replay_detail.mermaid`は
**既に正しく**以下を規定していることを確認した(図の修正は不要、無変更)。

- ファイル冒頭`%% 前提`: 「Pending状態そのものをCapture／Restoreの対象にしない。
  Pendingを別Workerで再現する場合は、直前のStable SnapshotをRestoreし、
  そこから実行済みのSemantic Action SequenceをReplayして同じPendingへ
  自然に再到達させる。」
- `NOTE_NO_REGEN`: 「PendingはStable Root SnapshotからのReplay Prefix再生の
  『自然な結果』として到達するものであり、独立した再生成処理は存在しない。
  Pending自体をCapture／Restoreの対象にもしない。」
- `WHO`: Main-observed Pending(StartOfCombat Pendingを含む)はこの図(Search/
  Restore経路)に一切到達せず、Main Loopの`PENDING_STATIC`で完結する設計。

バグは実装側がこの契約から逸脱していた3箇所に限定して特定・修正した。

## 実装した修正

| ファイル | 修正内容 |
|---|---|
| `Combat/combat_state_snapshot.py` | `restore_input_eligibility()`に`Metadata.CaptureBoundary`チェックを追加。`RESTORE_ELIGIBLE_CAPTURE_BOUNDARY_VALUES = {"normal_player_decision"}`のみをRestore適格と判定。4種のCaptureBoundary全てについて実Emulatorで実測(下記参照)。docstringに「これは高速な静的近似であり、正本は`LiveCombatSession.validate_restore_snapshot*()`」と明記。 |
| `Combat/search/decision_context.py` | `PendingSnapshotRestoreViolationError`を新設。`replay_decision_context()`の`session.restore_snapshot()`呼び出し直前に、root_snapshotのCaptureBoundaryを検査するガードを追加(dataclass/dict/JSON文字列/CLRオブジェクトいずれの形状も処理)。Restore不適格なら明示的な設計違反として例外を送出。この関数は`branch_worker_pool.py`のBootstrap+Step経路からも内部的に呼ばれるため、単一箇所の修正で全呼び出し元をカバーする。 |
| `Combat/search/main_loop.py` | `run_until_terminal_or_fault()`内の「genesis bootstrap exception」(戦闘がPending境界から開始する場合[TOOLBOX等]にPending Snapshotを`held_stable_snapshot`として代用captureしていたコード)を完全に削除。Pending境界からのSearchは`PendingSearchNotAllowedError`により既に構造的に禁止されているため、この暫定措置は元々不要だった。`held_stable_snapshot`は本物のStable境界に到達するまで`None`のまま。 |

## CaptureBoundary別のRestore適格性(実Emulatorで実測)

| CaptureBoundary | 適格性 | 実際のRestore結果 |
|---|---|---|
| `normal_player_decision` | 適格 | Restore成功 |
| `published_choice` | 不適格 | `unsupported_capture_boundary:published_choice`, `pending_choice_present`で拒否 |
| `published_target` | 不適格 | `unsupported_capture_boundary:published_target`で拒否 |
| `terminal` | 不適格 | `unsupported_capture_boundary:terminal`, `enemy_count:0`で拒否 |

## 確認済みの無変更範囲

- `Combat/search/branch_worker_pool.py`の`_build_success_result()`(Pending到達時、
  親のroot_snapshotをそのまま継承しReplay Prefixのみ延長する実装)は再確認の上、
  既に設計通り正しいことを確認し、無変更。
- Mermaid diagram群は無変更(契約は既に正しかった)。
- 本タスクの範囲外で見つかった追加のPending Snapshot Restore違反は無し。

## Action Continuation Pendingについて(範囲・限界)

現状の`LiveCombatSession.step()`は、調査した全ての継続選択メカニズムについて
内部で自動解決を行い、外部へPendingとして一度も返さないことを確認済み
(前回のF2調査で判明)。このため、Action Continuation Pending由来のPending
Snapshotを実際に構築・Restoreしようとする具体的な再現経路は現状存在しない。
本修正の防御的ガード(`PendingSnapshotRestoreViolationError`)はStart-of-Combat
PendingとAction Continuation Pendingの両方に等しく適用される設計だが、後者の
実トリガーによる統合テストは、指示の通りEmulator側のPending公開対応完了後に
別途実施する。本タスクでは実施していない。

## テスト結果

| ファイル | 結果 |
|---|---|
| `test_decision_context.py` | 16/16 pass(新規2件: `restore_input_eligibility`のPending拒否確認、`replay_decision_context`の`PendingSnapshotRestoreViolationError`確認) |
| `test_main_loop.py` | 13/13 pass(新規: genesis PendingでCaptureが起きず本物のStableまで`held_stable_snapshot=None`のまま、その後も正常に戦闘完走することを確認) |
| `test_search_coordinator.py` | 14/14 pass |
| `test_multi_round_search.py` | 6/6 pass |
| `test_shadow_adapter.py` | 7/7 pass |
| `test_belief_coverage.py` | 5/5 pass |
| `test_candidate_pipeline.py` | 10/10 pass |
| `test_branch_worker_pool.py` | 9/9 pass |
| `test_rng_hypothesis.py` | 8/8 pass |
| `test_fault_taxonomy.py` | 10/10 pass |
| `test_multi_combat_continuous_execution.py` | 1/1 pass |
| `test_shadow_evaluation_batch.py` | 1/1 pass |
| `test_endurance_runner.py` | 1/1 pass |
| `test_multi_hypothesis_stress_runner.py` | 1/1 pass |
| `test_restore_snapshot_phase3c1.py` | 26/28 pass(既知の未関連2件のみ失敗、回帰ではない) |

合計: 新規/拡張テスト全てpass、新規の回帰無し。`decision_context.py`は多数のモジュール
(`branch_worker_pool.py`・`search_coordinator.py`・`multi_round_search.py`・
`shadow_adapter.py`・各種runnerスクリプト)が依存する基盤ファイルであるため、
今回は特に全既存テストスイートを漏れなく再実行し、クリーンな回帰結果を確認した。

## Git commit・作業ツリー状態

修正commit: `69b225a`。`git status --short`はclean(作業ツリーに未commit変更無し)。

## 結論

Pending SnapshotをRestore対象として扱っていた実装上の設計違反を、Mermaid設計契約
(既に正しく規定済み、無変更)に沿って3箇所の最小限の修正で是正した。修正は
`combat_state_snapshot.py`・`decision_context.py`・`main_loop.py`という、
これまで変更対象外としてきた基盤ファイルに及ぶが、変更範囲は厳密に本件の3箇所に
限定し、他の挙動は一切変更していない。全既存テストスイートで新規の回帰無し。
Action Continuation Pendingの実トリガーによる統合テストは、Emulator側の対応完了後に
別途実施する(本タスクでは未実施、指示通り)。
