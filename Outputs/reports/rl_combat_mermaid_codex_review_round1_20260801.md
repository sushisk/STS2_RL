# RL担当×Codex共同レビュー 第1ラウンド報告 — Combat Mermaid Diagram (2026-08-01)

対象: `C:\STS2_Mermaid\mermaid_rough_combat.mermaid`(上位契約)＋7詳細図。

使用したcommit(レビュー開始時点): `01d03cbe8bbffb24fa6b62551712f5c81a4ea23d`

**本ラウンドはMermaid図の修正と報告のみ。ランタイムコードの変更は一切行っていない。**

## 手順

`codex exec`(codex-cli 0.145.0、read-onlyサンドボックス)にプロジェクト概要・現在の設計判断一覧・
8図・最新報告書の要約を渡し、8図横断で矛盾・欠落・循環・責任境界・古いAPI依存をレビューさせた。
Codexは全8図を実際に読み込み(`rg`によるgrep探索込み)、12件の指摘を返した。

## Codexの指摘一覧とRL担当の採否判断

| # | 指摘 | 採否 | 理由(要約) |
|---|---|---|---|
| 1 | Rough図のBody Branchが固定役割としてLease契約(候補StepがPendingに到達した時点で確立)と矛盾 | **採用** | Rough図が旧Body/Sub事前確保モデルのまま更新されておらず、実装者が誤って旧モデルを実装しうる実害あり |
| 2 | Rough図が「現在のCombat状態と選択肢を取得」という旧状態取得経路を示唆 | **採用** | Decision Result一元化方針と矛盾。実装時にGetObservation等の別API誤用を招きうる |
| 3 | SchedulerのTOP_TO_BOOTSTRAPが「Restore+Replayで到達 かつ 新規Lease確立」と読める文言のまま | **採用** | 前回(第5ラウンド)にBOOTSTRAP_STEP側は修正したが、TOP_TO_BOOTSTRAP自身のラベル修正漏れ。実際に矛盾する記述が残っていた |
| 4 | Lease済みlive継続経路(LIVE_CONTINUE)にSemantic Action再解決が明示されていない | **採用** | Restore/Replay経路はRESOLVE_CANDIDATEを経るが、live経路だけ省略されて見える非対称性は実装バグの温床 |
| 5 | COMMIT_FIRST_ONLY後の「必ず再探索」とMain-observed Pendingの静的評価限定方針が文言上衝突 | **採用(表現修正のみ)** | 実際の到達点はStable/Pending双方あり得るため、「Search Coordinatorを必ず呼ぶ」という言い回しが誤り。ルーティング自体(Stable→Search、Pending→PENDING_STATIC)は既存の分岐で正しく処理されるため、方針転換ではなく文言統一で解消可能と判断。「PENDING_STATIC方針を撤回すべき」という代案は不要と判断し却下(RNG非公開の実害が生じる) |
| 6 | task timeout時、process生存確認だけでWorkerをReadyへ戻す経路は危険 | **採用** | health probeはWorkItem処理の完全性を証明しない。timeoutは既定で強制再起動とすべき |
| 7 | Search Evaluation FailureのMain側受け口が未定義 | **採用** | main_loop図にSearchの失敗を受け取る分岐が実際に欠落していた。暗黙fallback禁止方針と組み合わせると実行不能になる欠陥 |
| 8 | Commit図のdeterministic violation(INVARIANT_VIOLATION)がFault図のSourcesに入口を持たない | **採用** | Fault図のSOURCESに該当ノードが無く、矢印が宙に浮いていた実際の欠落 |
| 9 | RNG非消費と判定した候補がPendingへ継続した場合、その先のContinuationでもRNG非消費が保証されない | **採用** | PASSTHROUGH判定は「その1手」のみに対する判定であり、後続Continuationへ無条件で継承すると実RNGがEvaluatorへ漏洩しうる実害のある欠陥 |
| 10 | Rough図のNEXT_ROOTSがHypothesis由来Beam継続を示唆し、単一ラウンド制限と矛盾 | **採用** | 指摘1・2と同根(Rough図の更新遅れ) |
| 11 | Worker再起動後のRetry中候補とFault確定候補の区別が弱く、非同期実装で二重集計しうる | **採用** | 具体的な実装上の競合状態を指摘しており、WorkItem状態モデルの明示が必要 |
| 12 | Training担当向けログ/Result schemaが未定義で、低評価と評価不能を区別できない | **採用** | レビュー観点に明記された「Training担当へ渡すログ・Replay・結果を生成可能な構造か」に直接該当する欠落 |

**却下した項目**: なし(指摘5の副次的代案「PENDING_STATIC方針の撤回」のみ不採用、理由は上記)。
**一部採用**: 指摘5(方針は維持、表現のみ修正)。

## 修正した図

8図全てを修正した。

- `mermaid_rough_combat.mermaid`: Body/Sub固定役割・旧状態取得経路・Hypothesis-Beam継続の矛盾を解消し、
  Decision Result／Lease／Replay Prefix・Plan Path／Root Action集約／単一ラウンドCommit方針等、
  詳細図の語彙・構造に全面的に合わせて再構成した。Search Evaluation Failureの明示的な出口も追加した。
- `mermaid_combat_branch_scheduler_detail.mermaid`: TOP_TO_BOOTSTRAPの文言を修正(点3)。
- `mermaid_combat_snapshot_replay_detail.mermaid`: LIVE_CONTINUE前にLIVE_RESOLVE(Semantic Action再解決)
  と再解決不能時のFault経路を追加(点4)。
- `mermaid_combat_rng_hypothesis_detail.mermaid`: Prefix非空Pending継続時に、継承元がPASSTHROUGH由来の
  Noneであれば`RECHECK_CONSUME`でCONSUME_CHECKを再実行する分岐を追加(点9)。実際のH継承の場合は
  従来通りINHERIT_H(再判定不要)。
- `mermaid_combat_commit_detail.mermaid`: RESEARCHの文言を「Search Coordinatorを必ず呼ぶ」から
  「Mainの通常の決定ループへ戻る(到達点に応じてSearch/PENDING_STATICへルーティング)」へ修正(点5)。
  INVARIANT_VIOLATIONの接続先をSRC_MAIN_INVARIANTへ明記(点8)。IN(集約入力)にWorkItem状態モデルへの
  参照を追加(点11)。
- `mermaid_combat_main_loop_detail.mermaid`: NEED_SEARCHの戻り値を`SearchSuccess`/`SearchEvaluationFailure`
  の明示的分岐にし、失敗時はMain Combat Faultへ遷移する経路を追加(点7)。
- `mermaid_combat_fault_worker_detail.mermaid`: SRC_TIMEOUTを既定で強制再起動する経路へ変更(点6)、
  MAIN_FAULT_SRCにSRC_MAIN_INVARIANT(Branch Worker再利用判断を経由しない独立経路)を追加(点8)、
  WorkItem状態モデル(Running/Retrying/FinalSuccess/FinalFault)を明記しCOORD_COLLECTの対象を限定(点11)、
  Training担当向けログ/Result schema(status／root_action_key／hypothesis_id／plan_path／
  replay_prefix_digest)を明記(点12)。
- `mermaid_combat_candidate_pipeline_detail.mermaid`: 変更なし(Codexからの直接指摘なし。cross-check用に
  他図の変更に伴う矛盾がないか確認済み)。

## 図間整合性の再確認結果(この時点での自己点検)

- `TOP_TO_BOOTSTRAP`(branch_scheduler)と`BOOTSTRAP_STEP`(branch_scheduler)・`LEASE_ESTABLISH`
  (branch_scheduler)・`NOTE_NO_LEASE_ON_ARRIVAL`(snapshot_replay)の記述は、いずれも「親Contextへの
  到達だけではLease未確立、候補StepがPendingを返した時点でのみ確立」で一致することを確認した。
- `SEARCH_RESULT`/`SEARCH_FAIL_HANDLE`(main_loop)と`ALL_FAULT`/`SEARCH_FAIL_LOG`(fault_worker)の
  接続を確認し、Search失敗がMain Combat Faultとして一貫して扱われることを確認した。
- `INVARIANT_VIOLATION`(commit)と`SRC_MAIN_INVARIANT`(fault_worker)の接続を確認した。
- Rough図の新しい語彙(Decision Result・Replay Prefix・Plan Path・Lease・Root Action・単一ラウンド
  Hypothesis・PENDING_STATIC)が、対応する詳細図の定義と矛盾しないことを確認した。

次ラウンドでCodexに全8図を再度渡し、横断的な再確認(局所修正による新たな不整合の有無)を依頼する。

## 継続する未解決事項

前回までの継続事項(Stable境界を跨いだLease再利用、Continuation深さ上限の具体値、Replayカスケード
コストへの将来対応、RNG Counter差分の露出可否、Pending境界での限定的RNG override、汎用確率的Beam
Search、Evaluator入力型の分離、Restore失敗時の例外分類の一部緩和可否)は、いずれも「図の構造には
影響しない実装時決定事項」であり、Mermaid完成の妨げにはならないとCodexも同意した(次ラウンドで
最終確認する)。

## 使用したcommit・ランタイムコード無変更・作業ツリー状態

- 開始時点commit: `01d03cbe8bbffb24fa6b62551712f5c81a4ea23d`
- ランタイムコードの変更: なし
- 作業ツリー: 本報告のcommit時点でclean(Mermaid図はSTS2_RLのgit管理対象外、STS2_Mermaid配下)
