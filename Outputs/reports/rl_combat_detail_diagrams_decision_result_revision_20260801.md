# RL担当 作業報告 — Combat詳細図の修正(状態取得API・Pending管理・Sequence実行・RNG判断基準) (2026-08-01)

対象: レビュー結果「Combat詳細図 再レビュー結果」で指摘された修正必須6点・改善推奨4点への対応。
`C:\STS2_Mermaid\`配下のCombat詳細図7点すべてを修正した。

RL HEAD(この報告のcommit直前): `f5a8392396efe01004543aadaab3f9543966299e`

**本ラウンドはMermaid図の修正と報告のみ。ランタイムコードの変更は一切行っていない。**

## 修正必須6点への対応

### 1. GetObservation／GetLegalActions旧経路の除去

`mermaid_combat_main_loop_detail.mermaid`の`READ`ノード(`GetObservation／GetLegalActionsで現在の
Combat状態を取得`)を削除し、`CUR_RESULT`(Current Decision Resultを保持)へ置き換えた。
StartCombat／Restore／直前StepのいずれかがBoundary・Observation・Choice候補・Choice種別/制約・
Terminal/Fault・CombatSessionId/StepIndex等のmetadataをまとめて返す一括戻り値(Decision Result)
のみをdecision情報源とし、Step後もStepResultをCurrent Decision Resultへ直接差し替える形にした
(`READ`ノードへ戻って別APIを再呼び出す経路は完全に削除)。

連動して以下も修正した:
- `mermaid_combat_candidate_pipeline_detail.mermaid`: 「現Decision ContextのLegalActionsを取得」→
  「Current Decision Result／Choice Payloadに含まれるchoice_kind等を参照」に修正。
- `mermaid_combat_snapshot_replay_detail.mermaid`: Prefix再生・候補解決の各箇所を
  「現在のDecision Result／Choice Payloadの候補へ再解決」「RestoreResultまたは直前StepResultに
  含まれる候補のみを参照」という表現へ統一。
- `mermaid_combat_main_loop_detail.mermaid`・`mermaid_combat_commit_detail.mermaid`の「最新
  LegalActions」という表現を「Current Decision Result／Choice Payloadの候補」に統一し、別APIによる
  再取得ではなく保持中の戻り値を指すことを明記した。

`CaptureSnapshot`・`ValidateRestoreSnapshotJson`・`RestoreSnapshotJson`・`Step`は引き続き正式APIとして
維持している。

### 2. MainがPendingにいる場合のDecision Context管理

`mermaid_combat_main_loop_detail.mermaid`にMain Decision Contextの保持ロジックを追加した。

- **Stable到達時**: `STABLE_CAPTURE`でCaptureSnapshotを実行し、Held Stable SnapshotとSequence
  Prefix(空)を確定させる。次のStepを行う前に必ず再現元を確保する(指示の通り、StableからDirect
  実行してPendingになる可能性に備え、Stepの前に保持を完了させる)。
- **Pending到達時**: `PENDING_HOLD`で新たにCaptureせず、直前のHeld Stable Snapshotとそこから
  現在までのSequence Prefixを維持したまま、Current ResultをPending Choice Payloadとする。

`mermaid_combat_snapshot_replay_detail.mermaid`の`WHO`分岐も、「Main ProcessがStableで探索開始」
(新規Capture)と「Main ProcessがPendingで探索開始」(新規Capture不要、Mainが保持済みのHeld Stable
Snapshot＋Prefix＋Current Pending Choice Payloadをそのまま使用)を明確に分離し、
「MainからCaptureする時点は必ずStable」という前回の記述と、Main LoopでStable/Pending双方から
Searchへ進む経路との不整合を解消した。

### 3. MainのSequence実行ループをBoundary種別に依存しない形へ再構成

`mermaid_combat_main_loop_detail.mermaid`のEXEC_LOOPを指示された疑似コードの順序通りに再構成した。

```
Step → StepResultを受領 → Main Decision Contextを更新 → 想定した遷移との一致を検証(VERIFY_TRANSITION)
→ Terminalか
    Yes: Combat終了(計画通りの終端の場合のみ)
    No: Sequenceに残りがあるか
        Yes: 次Semantic Actionを現在のChoice Payloadへ再解決
        No: 現在のStable／Pending地点から新しいdecisionを開始(NEW_DECISION_POINT)
```

StableになっただけでSequenceを終了する(旧`CONSUME`が`Stable`のみをトリガーにしていた)動作、および
「Sequenceが空になった時点でPendingは常に異常」という扱いを撤廃した。Sequence破棄条件も指示の4条件
(再解決不能／想定Decision Signatureと不一致／予定外のTerminal・Fault／Main正本が前提と不一致)に
限定し、`NOTE_DISCARD`として明記した。

あわせて、Commit側からMainへ渡す`Planned Sequence`に各手の`Expected Decision Signature`を含める
構造へ変更した(下記4番と共通のDecision Signature定義を使用)。Direct(自己決定)の場合はExpected
Signatureを付与せず、検証をスキップする(探索によって形成された計画ではないため)。

### 4. Prefix Replayの逐次検証化

`mermaid_combat_snapshot_replay_detail.mermaid`のSub Branch側処理を、Prefix全体再生後に最終Boundary
だけを確認する方式から、**各手ごとにStep→StepResult受領→中間Expected Decision Signatureと照合**する
方式(`REPLAY_STEP → REPLAY_ONE_STEP → REPLAY_SIG_CHECK`のループ)へ変更した。

比較対象のDecision Signatureは指示の通り以下で構成し、`action_id`は同一性比較に使わないことを明記した。

```
Decision Signature
- Boundary種別
- Choice種別
- min/max selection
- target制約
- 候補のSemantic Key集合
- Choice scope／continuation識別情報
```

この定義を`mermaid_combat_main_loop_detail.mermaid`のVERIFY_TRANSITION、`mermaid_combat_snapshot_replay_detail.mermaid`の
REPLAY_SIG_CHECK、`mermaid_combat_commit_detail.mermaid`のBUILD_PLANNEDで共通して参照する形に揃えた。
Sub Branch候補には`mermaid_combat_candidate_pipeline_detail.mermaid`のSPLITノードでExpected Decision
Signatureを付与するようにした。

### 5. Stable境界を跨いだWorker再利用の格下げ

`mermaid_combat_commit_detail.mermaid`から`WORKER_STILL_ALIVE`／`REUSE_LIVE`という実装済み経路としての
分岐を削除した。基本経路は指示通り次に統一した。

```
Pending地点内: State-Holding Worker(Lease)を再利用する
Stable評価境界: Child Snapshotへ正規化し、次Decision Contextの正本はChild Snapshotとする
```

`RELEASE_LEASE`ノードを追加し、Stable評価境界に到達した時点で当該Decision ContextのLeaseを解放する
ことを明記した。Stable境界を跨いだ再利用の可能性自体は`NOTE_FUTURE_REUSE`という設計注記として残したが、
「未決定・将来の任意最適化候補であり、実測結果を踏まえた監督者判断を経てから正式採用する」という
位置づけへ明確に格下げした。図上のノードとしては到達不能な注記(点線接続のみ)にとどめている。

### 6. RNGを伏せる方針とPASSTHROUGH経路の矛盾解消

`mermaid_combat_rng_hypothesis_detail.mermaid`の判断基準を、「RNG仮説が必要か」から
「この探索範囲で未来の非公開RNGを消費し得るか」(`CONSUME_CHECK`)へ変更した。

```
消費しないことが保証される → Root RNGをそのまま使用してよい(PASSTHROUGH)
消費する可能性がある → 必ず仮説RNG入りDerived Snapshotを使用(MODE以降)
真のRNGを使用してよい用途 → 厳密Replay／determinism test／デバッグ再現／
                              実際にMainで採用Actionを実行する場合(TRUE_RNG_OK)
```

また、State-Holding Worker Leaseを`(Decision Context, RNG Hypothesis ID)`の組に紐づけ、RNG仮説H1で
到達したWorkerのlive stateをH2のDecision Contextへ転用してはならないことを明記した
(`mermaid_combat_branch_scheduler_detail.mermaid`のLEASE_MODEL・NOTE_RNG_LEASE、
`mermaid_combat_rng_hypothesis_detail.mermaid`のNOTE_LEASE_BOUNDARY)。共通仮説集合(H1..Hn)を使う場合も
Hごとに別個のLease/Branch stateとして扱う。

## 改善推奨4点への対応

1. **Decision ContextとWorker Leaseの分離**: `mermaid_combat_snapshot_replay_detail.mermaid`の
   DECISION_CONTEXT subgraphを「Canonical Decision Context」として再定義し(Stable Root Snapshot・
   Sequence Prefix・Current Decision Result／Choice Payload・Expected Decision Signature・RNG
   Hypothesis IDのみで構成)、State-Holding Workerをこの正本から除外した。Worker側は
   `mermaid_combat_branch_scheduler_detail.mermaid`に新設した`LEASE_MODEL` subgraph
   (worker_id・worker_generation・context_id・RNG Hypothesis ID・state signatureで構成)として分離し、
   Lease解放条件(枝刈り・Holder候補不使用・Stable/Terminal正規化・Fault/Timeout・Search終了)を明記した。
2. **Choice分類はPayloadの明示情報を優先**: `mermaid_combat_candidate_pipeline_detail.mermaid`を、
   Search Coordinatorが種別を推定する読み方から、Choice Payloadの`choice_kind`等の明示フィールドを
   第一の分類根拠とする読み方へ修正した。また、通常1つのDecision Resultは単一Choice種別のみを含む
   前提で、Primary/Target/Continuationの3つの評価器へ直接routingし、常に単一リストへ混在させる
   従来の`MERGE`ノードを削除して「通常は結合不要、契約上まれに複数種別が同時提示される場合のみ結合」
   という注記に留めた。
3. **TerminalでChild SnapshotをCaptureしない**: `mermaid_combat_snapshot_replay_detail.mermaid`の
   `RETURN_BOUNDARY`分岐を、Stable→`CHILD_CAP`(CaptureSnapshot)、Terminal→`TERMINAL_RESULT_ONLY`
   (CaptureSnapshotを行わずTerminal Resultのみ作成)に分離し、`mermaid_combat_branch_scheduler_detail.mermaid`
   と同じ分離を実現した。
4. **全Branch失敗とMain Combat Faultの区別**: `mermaid_combat_fault_worker_detail.mermaid`の
   `ALL_FAULT`を「Search Evaluation Failure」(全Branchが評価不能であることのみを意味し、Main
   GameInstance自体がFaultしたとは限らない)として再定義し、Mainの意思決定層への報告経路
   (`SEARCH_FAIL_LOG`)とした。別途`MAIN_FAULT_SRC` subgraphを新設し、MainのStepResult自体がFault
   境界を返した場合の`MAIN_COMBAT_FAULT`を独立した経路として分離した(暗黙のDirect fallback禁止方針は
   両経路とも維持)。

## 承認済み扱いとした箇所(今回変更なし)

指示にある「現時点で承認可能」の3点(Fault／Timeoutの大枠、ActionContinuationの1 Step単位化)は
構造として維持した。ただしFault詳細図は改善推奨4に伴い`ALL_FAULT`のラベル・接続を変更しているため、
Timeout/Restart/Retry等の大枠構造(`SOURCES`/`WORKER`/`WM`各subgraph)自体には手を加えていない。

## 修正後の中心契約への準拠状況

指示にある8原則を全図の共通前提として反映した。

1. Emulatorの各Start／Step／Restore戻り値が、そのdecisionに必要な情報を一括して返す → 全図で
   Decision Result／Choice Payloadという語彙に統一。
2. RLは戻り値からDecisionFrameを構築する → Main LoopのCUR_RESULT／STEP_RESULT、Snapshot Replayの
   RestoreResult/StepResult扱いに反映。
3. GetObservation／GetLegalActionsによる再取得はしない → 修正必須1で対応。
4. MainもBranchも、次Actionは直前の戻り値に含まれるChoice候補へSemantic Actionを再解決する →
   全図で「現在のDecision Result／Choice Payloadの候補へ再解決」という表現に統一。
5. PendingはStable Root＋Prefixで表現し、Pending Snapshotは作らない → 従来通り維持、Main側にも
   同じ原則を明示的に適用(修正必須2)。
6. State-Holding Workerは最適化用の一時leaseであり、Decision Contextの正本ではない → 改善推奨1で
   Canonical Decision ContextとLeaseを分離。
7. Stable境界ではChild Snapshotを正本とする → 修正必須5でREUSE_LIVEを格下げし徹底。
8. RNGを消費し得るInGame探索では、Mainの真のRNGではなく仮説RNGを使用する → 修正必須6で
   CONSUME_CHECKによる判断基準へ変更。

## 未解決事項・判断保留(継続)

1. **Stable境界を跨いだLease再利用の正式採否**: `NOTE_FUTURE_REUSE`として保留を維持。監督者判断が
   必要な項目として引き続き残る。
2. **Decision Context系列内のContinuation深さ上限の具体値**: 前回から継続する未決事項、今回は変更なし。
3. **カスケードコスト対応方針**: 前回から継続、現状(常にStable Root Snapshotから再生)を維持する設計を
   図示。実装後の計測結果に基づいて判断する方針を維持。
4. **必須受け入れテストの契約上重要な項目の具体的フィールド一覧**: 今回Decision Signatureの構成要素
   (Boundary種別・Choice種別・min/max・target制約・候補Semantic Key集合・Choice scope/continuation
   識別情報)を明記したことで前進したが、既存Restore契約v0.8の各フィールドとの対応表は未作成であり、
   実装設計時に必要。

## 停止

指示の通り、上記7図の修正とこの報告のみを行い、ランタイムコードの変更は行っていない。
`git status`でSTS2_RLリポジトリの作業ツリーはこの報告ファイルの追加のみであることを確認済み。
