# RL担当 作業報告 — Combat詳細フロー図の作成 (2026-08-01)

対象: `mermaid_rough_combat.mermaid`(合意済み・`rl_combat_rough_worker_pool_review_20260801_v2.md`で合意表明)を
上位設計として、7つの詳細Mermaid図を`C:\STS2_Mermaid\`配下に新規作成した。

RL HEAD: `6f37e358f895372130a07cca4301d10567f984b9` (この作業報告のcommit以前の時点)

**本ラウンドはMermaid図の新規作成のみ。ランタイムコードの変更は一切行っていない。**

## 前提として反映した設計方針(指示より)

- Mainは採用Sequenceのみ実行し、仮説展開を一切行わない。
- 仮説遷移は全てBranch Workerで行う。
- Bodyは当該ラウンド内のみ同一Instanceを継続利用する(ラウンドを跨いだ引き継ぎはしない)。
- Body/Subは評価境界でObservation・Child Snapshot・Sequenceへ正規化し、同列評価する。
- 勝者のChild Snapshotを次ラウンドのRootとする。
- Pendingから直接Restoreせず、必ずStable Root SnapshotからSequence Prefixを再生する。
- ActionContinuationも他の選択と同列に分類・事前評価・枝刈りの対象とする(自動解決ループを持たない)。
- RNGはRestore前に派生Snapshotへ置換する方式B(新規Emulator API不要)。

## 作成した図と意図

| ファイル | 意図 |
|---|---|
| `mermaid_combat_main_loop_detail.mermaid` | Mainが「読み取り→境界判定→Direct or Search要求→採用Sequence再解決→Step→記録」のループのみを行い、仮説評価用のRestore/Reset/Stepを一切行わないことを明示。Search要求・結果受領は他図への接続点として表現。 |
| `mermaid_combat_candidate_pipeline_detail.mermaid` | Primary Action／Target／Continuation Choiceの3分類、分類別の軽量(非Restore)事前評価関数、統合後の枝刈りを詳細化。ActionContinuationが自動解決されず他候補と同列パイプラインに入ることを明示。ラウンド内で新しい選択が発生するたびに本パイプラインへ再突入するループ構造も明記。 |
| `mermaid_combat_branch_scheduler_detail.mermaid` | Body/Sub Worker割当、Bodyの「ラウンド内継続 vs 初回Restore」の分岐、Sub Workerの都度Restore+Replay、Branch境界での正規化までを詳細化。カスケードコスト増大と、Body連続Step結果とRestore+Replay結果の一致検証(決定性テスト)を設計注記として明記。 |
| `mermaid_combat_snapshot_replay_detail.mermaid` | Stable Root Snapshot取得(Main初回 or 前ラウンドのChild Snapshot)、Worker側でのValidate→Restore→Sequence Prefix再生→Pending再生成→候補Action適用→Child Snapshot取得までを詳細化。PendingからのRestoreを行わない方針を明示的な設計注記として記載。 |
| `mermaid_combat_rng_hypothesis_detail.mermaid` | Main RNGを「特別な真実」として扱わない(CaptureSnapshotで含まれるが、Main Instance自体は不変に保たれる)ことの明示、仮説RNG導入が必要な場合のみRoot Snapshot JSONを複製・該当ストリームのみ置換して派生Snapshotを作るフロー、同一Non-RNG状態下での比較が可能になる点を詳細化。方式Bとして新規Emulator API不要である点を注記。 |
| `mermaid_combat_fault_worker_detail.mermaid` | `mermaid_rough_combat.mermaid`の`ROUGH_NOTE`が指す「詳細図」に相当。Restore拒否・Branch Fault・Timeout・Process異常・Continuation深さ上限到達・候補0件の6つの発生源から、Worker側の自己判定→Worker Managementでのリトライ/再起動→Search Coordinatorでの集約→全滅時のMain報告までを一本の図にまとめた。暗黙のDirect fallbackを行わない方針を維持。 |
| `mermaid_combat_commit_detail.mermaid` | Body/Subを出自に関わらず同一評価関数で比較すること、Beam continueの場合のNEXT_ROOTS(複数形、真のBeam幅維持)、終了時のBest Sequence確定、MainでのSequence再解決(1手ずつ都度LegalActionsへ再解決)と実行、Main側の想定乖離時のresync経路を詳細化。Branch状態を一切コピーしない方針を明記。 |

## 各図の相互接続方針

各図はノードラベルに`→ ファイル名`形式で次に読むべき図を明記し、上位図(`mermaid_rough_combat.mermaid`)の
どのノード群に対応するかを冒頭コメントに記載した。これにより、7図を通しで読むと
`main_loop → snapshot_replay → candidate_pipeline → branch_scheduler → (rng_hypothesis, fault_worker)
→ commit → main_loop`という一巡の経路を追える構成にした。

## 責任主体の明示

各図の`subgraph`見出しに`(責任: ...)`を付記し、Main Process／Search Coordinator／Branch Worker／
Worker Management のいずれが当該処理を担うかを明示した。Emulator API境界(`Step`・`CaptureSnapshot`・
`RestoreSnapshotJson`・`ValidateRestoreSnapshotJson`)を呼び出すノードにはその旨を注記し、
Python側(Search Coordinator)がGameInstanceそのものに触れないことが図から読み取れるようにした。

## 指示された2点の明示箇所

- **カスケードコストの注記**: `mermaid_combat_branch_scheduler_detail.mermaid`の`NOTE_CASCADE`ノードに、
  ラウンド内のContinuation連鎖が深いほどSub Worker側のSequence Prefix再生コストが線形に増加する旨、
  および現段階では設計変更せず実装後の性能計測項目とする旨を明記した。
- **Body/Restore一致の必須受け入れテスト**: 同図の`NOTE_DETERMINISM`ノードに、Bodyの連続Step結果と
  同一Root Snapshotから同一SequenceをRestore＋Replayした結果がCaptureSnapshotレベルでバイト一致する
  ことを実装時の必須受け入れテストとする旨を明記した(round-2レビュー§2-Cの提案をそのまま反映)。

## 未解決事項・今後の検討課題

1. **Continuation深さ上限の具体的な数値**: `mermaid_combat_fault_worker_detail.mermaid`の`NOTE_DEPTH_CAP`で
   概念(ラウンド内連鎖選択回数の上限)のみ明記し、具体的な数値(現行実装の`MAX_CONTINUATION_STEPS=50`に
   相当する値をそのまま使うか、ラウンド内選択という異なる単位で再設計するか)は未決定。実装設計時に決定が必要。
2. **カスケードコスト対応方針**: round-2レビューで提示した2案(現状維持=ラウンドRootから毎回再生 / 連鎖発生時点の
   生きた状態からその場でSub分岐)のうち、今回もいずれか一方を図に固定していない。`mermaid_combat_branch_scheduler_detail.mermaid`は
   案1(現状維持)の経路のみを描画しているが、これは「まず単純な方から実装し実測する」という前回合意に基づく暫定選択であり、
   実測結果次第で図の更新が必要になる可能性がある。
3. **VERIFY(Main不変性検証)の実装要否**: `mermaid_combat_commit_detail.mermaid`の`VERIFY`ノードは、本設計では
   Mainが探索中に一切Step/Restoreされないため構造的に不変性が保証されるはずだが、`mermaid_combat_target_worker_pool.mermaid`の
   `MAIN_SIGNATURE`検証を踏襲する形で「念のための検証」として残した。実装コストとのトレードオフで省略可能か、
   今後の判断が必要。
4. **Worker再起動時のBody継続不能**: `mermaid_combat_fault_worker_detail.mermaid`は主にSub Branch/一般的なWorker障害を
   想定しており、Body Worker自体がラウンド途中でProcess異常終了した場合の扱い(Bodyの「ラウンド内継続」という前提が
   崩れる)を明示的なノードとして描いていない。現状は`WM_RESTART`→`WM_POLICY`の一般経路に合流させているが、
   Body固有の対応(例えば当該ラウンドをそのままFault扱いにするのか、Sub Snapshotから代替Bodyを再構築するのか)は
   未検討であり、次回以降の論点として残る。
5. **RNG派生Snapshotの選定基準**: `mermaid_combat_rng_hypothesis_detail.mermaid`は「どのRNGストリームに
   どういう仮説値を割り当てるか」の決定ロジック自体(何本の仮説を作るか、既存の`sample_future_draw_orders`相当の
   ロジックを流用するか)を対象外としている。方式Bの適用箇所と契機は明示したが、仮説生成アルゴリズムそのものは
   別途の検討が必要。

## 停止

指示の通り、Mermaid図7点の新規作成とこの報告のみを行い、ランタイムコードの変更は行っていない。
`git status`でSTS2_RLリポジトリの作業ツリーはこの報告ファイルの追加のみであることを確認済み。
