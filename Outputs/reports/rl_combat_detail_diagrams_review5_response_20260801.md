# RL担当 作業報告 — Combat詳細図の再検討6点への対応 (2026-08-01)

対象: 6図再確認後の6点の指摘への検討・回答と図の修正。6点とも実害のある正当な指摘と判断し、
却下した項目はない。`C:\STS2_Mermaid\`配下のCombat詳細図7点全てを修正した。

RL HEAD(この報告のcommit直前): `2e557ca18224fb8fb04b7df971b63430ec8f97c6`

**本ラウンドはMermaid図の修正と見解回答のみ。ランタイムコードの変更は一切行っていない。**

## 1. Candidate PipelineとRNG処理の順序統一、二重経路の解消、PENDING_STATICをWorkerへ送らない

**判断: 妥当。実害のある構造バグだった。採用した。**

これまでの図は、`mermaid_combat_snapshot_replay_detail.mermaid`のRoot Snapshot確定直後に
RNG Hypothesis決定(`RNG_SUB`)を行い、その結果を`mermaid_combat_candidate_pipeline_detail.mermaid`の
CLASSIFYへ渡す一方、Candidate PipelineのSPLITも独立して`mermaid_combat_branch_scheduler_detail.mermaid`へ
直接WorkItemを送っており、実質的に「RNG展開が候補分類より先に、かつ独立した2経路でSchedulerへ到達する」
という矛盾した設計になっていた。

修正として、順序を「Candidate Pipeline(分類・事前評価・枝刈り)が先、RNG Hypothesis決定(枝刈り後の
生存候補に対してのみ)が後」に統一し、Search CoordinatorからBranch Worker Poolへの投入経路を
`Candidate Pipeline → RNG Hypothesis → Scheduler`の1本に一本化した(`mermaid_combat_snapshot_replay_detail.mermaid`の
`TO_RNG`/`FROM_RNG`、`mermaid_combat_candidate_pipeline_detail.mermaid`の`OUT_RNG`、
`mermaid_combat_rng_hypothesis_detail.mermaid`のIN/`NOTE_ORDER`)。

また、`mermaid_combat_snapshot_replay_detail.mermaid`のWHOに残っていた「Main ProcessがPendingで
探索開始」(`MAIN_HELD`)という経路を削除した。これは前々回導入した`PENDING_STATIC`
(Main-observed Pendingからの新規decisionは静的評価のみで完結し、Search Coordinator/Branch Worker Poolへは
一切送らない)によって既に到達不能になっていたにもかかわらず、削除し忘れていた死んだ経路だった。
指摘通り「PENDING_STATICはWorkerへ送らない」という要件は元々`main_loop`側では満たされていたが、
`snapshot_replay`側に矛盾する記述が残っていたため、これを除去して整合性を取った。

## 2. Decision SignatureとState Identityの分離

**判断: 妥当。設計上の弱点だった。採用した。**

これまでLeaseの`state signature`フィールドを、Choiceの意味的同一性を表すDecision Signatureと
同一構造として説明していた。しかし意味的に同一のChoiceが、実際には異なる(あるいは信頼できない)
underlying stateから生じている可能性を排除できないため、この2つは別軸で検証すべきという指摘は正しい。

`mermaid_combat_branch_scheduler_detail.mermaid`のLease構成要素を**State Identity**として再定義し、
`context_id`・`worker_generation`(Worker再起動検出)・`state epoch`(Step成功ごとに単調増加、欠落/重複
検出)・`CombatSessionId`/`StepIndex`(Emulator自身のStepResult/DecisionFrameが返す権威ある識別情報)・
`Decision Result digest`(Decision Resultそのもののハッシュ、Decision Signatureより強い同一性証明)で
構成するようにした。`LEASE_VERIFY`もState Identityで判定し、Decision Signatureでは判定しないことを
明記した。Main不変性検証(`mermaid_combat_commit_detail.mermaid`の`VERIFY`)も同様にState Identity
(CombatSessionId/StepIndex/Decision Result digest)で判定するよう修正した。
`mermaid_combat_snapshot_replay_detail.mermaid`のDC_SIGNATUREにも、Decision Signatureが
「意味的同一性の検証専用」であり「Worker/Leaseの信頼性検証には使わない」ことを明記した。

## 3. Bootstrap到達だけではLeaseを確立しない、Scheduler 20行目の残存記述削除

**判断: 妥当。構造は概ね正しかったが、表現の曖昧さと1箇所の残存記述ミスがあった。修正した。**

`LEASE_ESTABLISH`の条件自体(「Stepを実行し、その結果Pendingへ到達したこと」)は前回既に正しく
定義されていたが、`BOOTSTRAP_STEP`ノードのラベルに「(以後このWorkerが...Leaseされる)」という
曖昧な注記が残っており、Restore+Replayで親Contextへ到達しただけの段階でLeaseが確立するかのように
誤読されうる状態だった。この注記を削除し、「Restore+Replayで親Contextへ到達しただけの段階では
Leaseは確立しない。確立するのは候補StepがPendingを返した場合のみ」と明記した。

`mermaid_combat_branch_scheduler_detail.mermaid`の`SPLIT_IN`(旧20行目)に残っていた
「Sub Branch候補にはExpected Post-Step Signatureが付与済み」という記述も、前々回の修正で本文中の
`SUB_ROUTE`は直したものの冒頭の`SPLIT_IN`ラベルの修正漏れがあったため、指摘通り削除し、
「Current Context Signature＋Candidate Semantic Actionのみを持つ」という正しい記述へ統一した。

## 4. RNG集約の二段階化、共通H集合・最低coverage・欠損ペナルティ、独立仮説集合の診断専用化

**判断: 妥当。二段階の明示化と偏りのある集約規則の是正は必要だった。採用した。**

`mermaid_combat_commit_detail.mermaid`に第1段階(`STAGE1`: 各(Root Action, H)ペア内でCLASSIFY/PRUNE/
CONTEXT_UPDATEの連鎖(制御可能なContinuation Branch)を経て収束した最終結果を、そのペアの代表スコアと
する)と第2段階(`GROUP_BY_ROOT_ACTION`: Root Action単位でH1..Hnの代表スコアを集約する)を明示的に
分離した。第1段階自体は既存のDecision Context系列機構が自然に生成する結果であり、新たな処理の追加は
不要と判断した。

集約規則については、指摘の3案(共通H集合・最低coverage・欠損ペナルティ)を検討し、**併用**を提案した。

- **共通H集合**: GRID生成の時点で全Root Actionに同じ名目上のH1..Hnが割り当てられており、これは
  既に構造的に保証されている(欠損はFault由来のみ)。
- **最低coverage**: 有効サンプル数がnの過半数(例: ceil(n/2))未満のRoot Actionは評価不能として除外する。
- **欠損ペナルティ**: 最低coverageを満たすRoot Actionについても、有効サンプルの単純平均ではなく、
  欠損したHをそのRoot Actionの有効サンプル中「最低スコア」で補完してから平均する悲観的補完とした。
  理由: 単純平均は「たまたま不利なHでFaultした」候補を不当に高評価してしまう偏りがあるため、
  指摘の「Actionごとに異なる有効Hだけを平均すると比較が偏る」という懸念に直接対応する。

独立仮説集合(診断専用)については、`mermaid_combat_rng_hypothesis_detail.mermaid`で標準比較用と
診断専用のWorkItemに別タグ(RNG Hypothesis IDへの「標準」/「診断」タグ付け)を導入し、
`mermaid_combat_commit_detail.mermaid`のIN(標準比較対象の入力)には診断タグの結果を一切含めないことを
明記した。既存の`COMPARE_STD`/`COMPARE_IND`分離を、より明確な「物理的に別集合」という形へ強化した。

## 5. MainのStepResultがFaultなら追記前に即Main Combat Faultへ

**判断: 妥当。実害のある欠陥だった。採用した。**

前回までの`EXEC_LOOP`は、StepResult受領後に無条件で`APPEND_RECORD`(Replay Prefix/Plan Pathへの記録)を
行っており、Faultの場合もこの記録がまず行われてから`VERIFY_TRANSITION`で不一致と判定されて
`DISCARD`/`RESYNC`という「通常の計画不一致」と同じ経路を通ってしまっていた。しかしFaultは
Emulator側の実行時異常であり、Replay Prefix/Plan Pathへ記録して将来Replayの対象にすべきものではなく、
再探索によって解決する性質のものでもない。

`mermaid_combat_main_loop_detail.mermaid`のEXEC_LOOPに`STEP_FAULT_CHECK`を追加し、StepResult受領直後、
`APPEND_RECORD`より前にBoundary=Faultを判定し、Faultの場合はTransition Recordへの追記もDISCARD/RESYNCも
行わず、即座にMain Combat Faultへ遷移するよう修正した。

## 6. Fault後のWorker再利用条件をFault種別ごとに明示

**判断: 妥当。従来の「Worker Process自体は継続可能か」という二値判定は粗すぎた。採用した。**

`mermaid_combat_fault_worker_detail.mermaid`のFault発生源を、指摘の4種別
(validation rejection／replay mismatch／post-teardown Restore failure／action fault)へ明示的に分類し、
種別ごとに再利用可否の既定方針を定めた。

| Fault種別 | GameInstanceの状態 | 既定方針 |
|---|---|---|
| validation rejection | 変更なし(Validateは非破壊的照会) | 再利用可(再起動不要) |
| replay mismatch | 何らかの妥当な状態だが想定と異なる | 再起動/強制再初期化(安全側の既定) |
| post-teardown Restore failure | 明確に信頼できない(process-singleton stateのロールバック不可) | 再起動/強制再初期化(必須) |
| action fault | Emulator内部の実行時異常 | 再起動/強制再初期化(安全側の既定) |

replay mismatchとaction faultを「安全側の既定(再起動)」としたのは、GameInstanceが
process-singleton stateであり、前セッションの完全なロールバックが保証されないという本エンゲージメント
初期からの既存知見(`SnapshotRestoreFailedException`のdoc comment)を踏まえた判断であり、
Restoreが真に完全な状態リセットを提供することが実測(決定性テスト等)で確認されるまでは、
安易に「再利用可」側へ倒すべきではないと考えた。この2種別の既定緩和可否は未解決事項として残した。

## 修正した図

7図全てを修正した(`mermaid_combat_main_loop_detail.mermaid`・`mermaid_combat_candidate_pipeline_detail.mermaid`・
`mermaid_combat_snapshot_replay_detail.mermaid`・`mermaid_combat_branch_scheduler_detail.mermaid`・
`mermaid_combat_rng_hypothesis_detail.mermaid`・`mermaid_combat_commit_detail.mermaid`・
`mermaid_combat_fault_worker_detail.mermaid`)。

## 却下した項目

なし。6点とも実害のある正当な指摘であり、全て採用した。

## 未解決事項・監督者判断/Emulator担当確認が必要な事項(継続・追加)

1. **replay mismatch/action faultのWorker再利用既定緩和可否**(新規、点6由来): RestoreSnapshotJsonの
   完全な状態リセット保証を実測で検証してから判断する。
2. **action faultのEmulator側例外分類**(新規、点6由来): 「既知の軽微な業務例外」を区別する分類基準が
   Emulator側に必要。
3. **限定的な境界RNG override**、**RNG Counter差分のStepResult露出**、**汎用確率的Beam Searchの正式設計**、
   **Stable境界を跨いだLease再利用の正式採否**、**Decision Context系列内のContinuation深さ上限の具体値**、
   **Evaluator入力の型/インターフェース分離の実装設計**: いずれも前回までの継続事項。

## 停止

指示の通り、上記7図の修正とこの報告のみを行い、ランタイムコードの変更は行っていない。
`git status`でSTS2_RLリポジトリの作業ツリーはこの報告ファイルの追加のみであることを確認済み。
