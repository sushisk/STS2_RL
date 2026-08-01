# Combat Mermaid 監督者指示対応レビュー報告 (2026-08-01)

対象: `C:\STS2_Mermaid\mermaid_rough_combat.mermaid`(上位契約)＋7詳細図
(`mermaid_combat_main_loop_detail.mermaid`・`mermaid_combat_candidate_pipeline_detail.mermaid`・
`mermaid_combat_branch_scheduler_detail.mermaid`・`mermaid_combat_snapshot_replay_detail.mermaid`・
`mermaid_combat_rng_hypothesis_detail.mermaid`・`mermaid_combat_fault_worker_detail.mermaid`・
`mermaid_combat_commit_detail.mermaid`)、および`mermaid_combat_target_worker_pool.mermaid`の要否判断。

背景: 前回の「Codex／RL担当双方合意済みCombat Mermaid Diagram」確定(commit `5a3a96f`)後、
監督者による最終確認が行われ、実装契約として確定する前の限定修正指示(11項目)が出された。
本報告はその対応記録である。全面レビューの再開ではなく、指示された11項目の限定修正と、
その差分がもたらす横断的な整合性への影響のみを対象とする。

**本作業全体を通じ、Mermaid図の修正と報告のみを行った。ランタイムコードの変更は一切行っていない。**

前回最終報告commit: `5a3a96f`

## 指示項目ごとの対応

### 1. Rough Diagramと詳細図の契約関係

**採用。** `mermaid_rough_combat.mermaid`冒頭コメントと`ROUGH_NOTE`ノードを修正。
「矛盾があれば詳細図を正とする」という自動優先規定を削除し、「Rough Diagram=アーキテクチャ方針・
責任境界・不変条件を示す上位契約、詳細図=それを具体化する詳細契約。両者が矛盾した場合はどちらも
自動的に優先せず、実装を停止して監督者判断を求める」という関係へ修正した。Roughが意図的に省略する
フィールド・例外分類・内部手順は詳細図を補足契約として扱う旨も明記した。

### 2. Main Sequence実行中のStable境界処理

**採用。実害のあるバグとして修正。** `mermaid_combat_main_loop_detail.mermaid`のEXEC_LOOPを修正。
従来は`SEQ_REMAIN`(Planned Sequenceに残りがあるか)の判定のみで、Sequenceに残りがある限り
Boundary処理(Held Stable SnapshotのCaptureSnapshot・更新・Replay Prefixリセット)がSequence完了まで
遅延される構造になっていた。指示通り、各Step成功後に必ずBoundary判定(`STEP_BOUNDARY`)を行い、
Stable到達時は都度`STEP_STABLE_CAPTURE`でHeld Stable Snapshotを更新しReplay Prefixをリセットし、
Pending到達時は`STEP_PENDING_HOLD`で維持するよう再構成した(`SEQ_REMAIN_S`/`SEQ_REMAIN_P`)。
Sequenceに残りがなくなった時点(`SEQ_REMAIN_S`/`SEQ_REMAIN_P`の「No」)は、既存の`MAIN_DC`/`MAIN_DC2`
ノード(Held Stable Snapshot＋Replay Prefix＋Current Resultの確定)へ直接接続することで、
新しいdecisionへ二重のCaptureSnapshotを行わずに移行する構造とした。旧`NEW_DECISION_POINT`ノードは
この再配線により不要となったため削除した。

### 3. Main-observed Pendingの探索制限

**採用。** `PENDING_STATIC`の制限は初期実装時点の安全策であり最終方針ではないことを明記する
`NOTE_PENDING_FUTURE`を追加した。将来、候補実行が未来RNGを消費しないことをEmulator契約または
明示的なmetadataで保証できる場合はWorker展開を許可する余地を残す一方、現段階では判定機構自体が
存在しないため全て静的評価とすることを明記。「choice_scope=ActionContinuationも原則探索対象で
ある」という上位方針(candidate_pipeline_detailのNOTE_KIND_VS_SCOPE)を撤回するものではなく、
Main-observed Pendingという限定経路にのみ適用される制限であることも明記した。

### 4. Search Evaluation FailureとMain Combat Faultの分離

**採用。実害のある設計矛盾として修正。** 従来`main_loop_detail`の`SEARCH_FAIL_HANDLE`は
「Search Evaluation FailureをMain Combat Faultとして扱い、FAULT_OUTへ遷移する」という記述になっており、
`fault_worker_detail`側で既に確立していた「ALL_FAULTはMain GameInstance自体がFaultしたとは限らない」
という区別と矛盾していた。`SEARCH_FAIL_HANDLE`を「Main自身は正常でありMain Combat Faultへは変換しない」
という記述へ修正し、新たに`ABORT_POLICY`→`COMBAT_ABORTED`(CombatAbortedByDecisionFailure)という
第三の終了種別を追加した。あわせて、以前欠落していた`FAULT_OUT --> RETURN`・`COMBAT_ABORTED --> RETURN`
の辺を追加し、両終了経路が呼び出し元への返却まで到達することを保証した。暗黙のDirect fallback禁止方針は
維持している。

### 5. Current Context Signatureの説明修正

**採用。実害のある誤記として修正。** `mermaid_combat_snapshot_replay_detail.mermaid`のDC_SIGNATURE
ノードに、「Current Context Signatureは未実行の候補には存在しない」という誤記があった
(本来この性質はExpected Post-Step Signatureのものであり、Current Context Signatureは候補実行前から
存在し親Decision Contextへの到達確認に使うものである)。正しい説明へ修正した。他の6箇所の
Current Context Signature言及は元々正しい記述だったため修正不要であることを確認した。

### 6. BranchResultの正規化形式

**採用。** `mermaid_combat_branch_scheduler_detail.mermaid`のNORMALIZEノードと
`mermaid_combat_commit_detail.mermaid`のINノードを、「Observation・Plan Path・status・diagnostics・
Child Snapshot(Stable時のみ)・Terminal Result(Terminal時のみ)」という共通BranchResult形式として
明示した。「Sequence」という用語は排し、Plan Pathへ統一した。Terminal結果にChild Snapshotが
含まれるという誤読を招く表現がないことも確認した(既存の`snapshot_replay_detail`のCHILD_CAP/
TERMINAL_RESULT_ONLYは元々Stable/Terminalを正しく分離していた)。

### 7. (Root Action, RNG Hypothesis)内のContinuation評価

**採用。実害のある曖昧表現として修正。** `mermaid_combat_commit_detail.mermaid`のSTAGE1ノードの
「収束した最終Stable/Terminal結果」という表現は、(Root Action,H)ペア内でCLASSIFY/PRUNE/SPLITにより
継続候補とSub Branch候補が枝分かれし複数のControllable Continuation Branchが生じうる実態を反映して
いなかった。「これらのうち有効な(Fault/評価不能を除く)最善スコアを持つものを代表値とする」という
明示的な表現へ修正した。Root Action単位の集約規則(AGGREGATE_RULE、共通H集合＋最低coverage＋
欠損ペナルティ)は既に明確だったため変更不要と判断した。

### 8. 「単一ラウンド」用語の整理

**採用。** RNG Hypothesisの評価範囲を指す「単一ラウンド」を「Root Action Evaluation Segment
(Root Action評価区間)」へ統一した。修正箇所: `mermaid_rough_combat.mermaid`(ROOT_ACTION_GROUP)、
`mermaid_combat_rng_hypothesis_detail.mermaid`(目的・前提コメント、NOTE_ROOT_ONLY)、
`mermaid_combat_commit_detail.mermaid`(目的・前提コメント、COMPARE_HYP、NOTE_SINGLE_ROUND→
NOTE_SINGLE_SEGMENTへリネーム)、`mermaid_combat_snapshot_replay_detail.mermaid`(PREV_CHILD)。
あわせて、RNG非依存のBeam Search自身の深度反復(`commit_detail`のEXPAND、「次ラウンド」と表現されて
いた箇所)についても、RNG HypothesisのRoot Action Evaluation Segmentとは別概念である旨を明記する
形で表現を修正した(こちらは指示の直接対象ではないが、同じ「ラウンド」という語による混同を避ける
ため、指示の趣旨に沿って併せて修正した)。

### 9. post-teardown Restore failure後のWorker方針

**採用。** `mermaid_combat_fault_worker_detail.mermaid`のREUSE_UNSAFEノードと
NOTE_REUSE_TABLEを、「Emulator契約上の必須事項」と「初期実装の運用ポリシー」を区別する形へ修正した。
Emulator契約上はclean Restore成功による回復可能性を否定しないが、現時点で未実測のため、初期運用では
安全側にWorker Processを再起動する運用ポリシーを既定とする、という指示通りの構造とした。
性能上必要になった場合は決定性受け入れテストを追加した上で緩和を検討できる旨も明記した。

### 10. `mermaid_combat_target_worker_pool.mermaid`の要否確認

**調査結果:**
1. 最新8図からの参照: 4箇所(fault_worker_detailのREJECT_TASK方針、rng_hypothesis_detailの
   §13解決済み参照、main_loop_detailのRULE_MAIN、commit_detailのRULE_COMMIT・MAIN_SIGNATURE)。
   いずれも「同趣旨の参考」としての言及であり、各図自体が当該方針を自己完結的に定義済みであることを
   確認した(依存関係ではなく由来の記録)。
2. README・報告書・設計文書からの参照: `Outputs/reports/`配下の過去の複数報告書
   (`rl_combat_target_worker_pool_review_20260731.md`ほか)から参照されているが、いずれも
   当時のレビュー対象としての歴史的記録であり、現在も有効な参照である(過去の報告書自体は修正しない)。
3. ランタイムコード・テスト・生成スクリプトからの参照: 検索の結果、該当なし。
4. 現在の仕様にしか存在しない有効な情報: BranchWorkItem batch単位の一括処理・固定Body/Sub構造・
   MAIN_SIGNATURE単独検証・Sequence Prefix用語など、いずれも現行8図でより正確な形
   (State-Holding Worker Lease・Decision Context・State Identity・Plan Path等)に置き換え済みで
   あり、現行契約として有効な情報の欠落はない。
5. Git履歴からの復元可能性: `C:\STS2_Mermaid`はSTS2_RLのgitリポジトリ管理下になく、独立した
   gitリポジトリでもない(`git rev-parse --is-inside-work-tree`が失敗することを確認)。
   このためGit履歴による復元は保証できない。

**判断: 削除せず、`deprecated/`へ移動した(履歴資料として必要な場合に該当)。**
Git履歴による復元が保証できない以上、削除は不可逆的なリスクを伴うため採用しない。一方、旧世代の
設計として一定の歴史的価値(現行設計がどこから発展したかの記録)を持つため、`deprecated/`ディレクトリへ
移動し、ファイル冒頭に指示通りのDEPRECATEDヘッダ
(「履歴参照専用。現在の実装契約として使用禁止。正本はmermaid_rough_combat.mermaidおよび7詳細図」)を
追加した。現行8図からの4箇所の参照は、いずれも「参考: 当該図は現在deprecatedであり、本方針は本図
自体が正本として定義する」という表現へ修正し、現行図が旧図に依存しているという誤読を防いだ。

### 11. Training接続に向けた記録

**継続課題として記録する(図の構造変更は行わない)。** Training担当へ渡すログ契約で将来必要となる
以下の項目を継続課題として記録する。今回はログschemaの詳細設計やランタイム実装には進んでいない。

- schema version
- run／episode／combat／decision／workitem ID
- parent context ID
- Emulator commitまたはDLL hash
- Snapshot schema version
- evaluator／model version
- Root Snapshot digest
- RNG hypothesis設定
- immutable raw log manifest

既存の`fault_worker_detail.mermaid`のNOTE_LOG_SCHEMA(status・root_action_key・hypothesis_id・
plan_path・replay_prefix_digest)と合わせて、Training接続時に本格設計する対象として引き継ぐ。

## Codexによる限定差分レビュー

全面レビューの再開ではなく、上記11項目の修正が正しく・矛盾なく反映されているかの限定差分レビューを
`codex exec`(codex-cli 0.145.0、read-onlyサンドボックス)へ依頼した。

- **第1ラウンド**: Codexが8図を横断的に確認し、2件の指摘を提出。
  1. `mermaid_rough_combat.mermaid`が`SEARCH_FAIL -.-> MAIN_FAULT`のままで、
     `main_loop_detail`/`fault_worker_detail`で確立した「SearchEvaluationFailureはMain Combat Fault
     ではなくCombatAbortedByDecisionFailureへ至る」という整理と矛盾している。
  2. `mermaid_combat_commit_detail.mermaid`のVERIFYノードが、`target_worker_pool.mermaid`の
     MAIN_SIGNATURE検証への参照のまま、deprecated化後の表現に更新されていない。
  
  両方ともRL担当の見落としであり、正当な指摘として**採用**した。
  - Rough Diagramに`COMBAT_ABORTED`(CombatAbortedByDecisionFailure)ノードを追加し、
    `SEARCH_FAIL -.-> COMBAT_ABORTED`へ配線を修正した。
  - `commit_detail`のVERIFYノードを、「参考: deprecated/mermaid_combat_target_worker_pool.mermaidの
    MAIN_SIGNATURE検証と同趣旨。当該図は現在deprecatedであり、本検証方式は本図自体が正本として
    定義する」という表現へ修正した。

  なお、STEP_BOUNDARY/STEP_STABLE_CAPTURE/STEP_PENDING_HOLD/SEQ_REMAIN_S/SEQ_REMAIN_Pの再配線、
  FAULT_OUT→RETURN、COMBAT_ABORTED→RETURNの妥当性、NEW_DECISION_POINTの残存なしについては、
  第1ラウンドの時点で既に「妥当」と確認されている。

- **第2ラウンド(最終確認)**: 上記2件の修正を反映した8図を再度渡し、確認を依頼した。Codexは
  「今回の限定差分レビュー範囲では**新たな修正点なし**です。この8図の現在状態に合意できます」と
  明言した。

## 横断整合性の再確認

- 全ての修正・再配線について、node ID参照の完全性をプログラム的に検証した(各エッジの接続先IDが
  ファイル内で少なくとも1箇所shape定義されていることを確認。8図全てで未定義参照なし)。
- Codexの指摘を反映する過程で、`commit_detail.mermaid`のRESEARCHノードが旧`NEW_DECISION_POINT`を
  参照したままになっている追加のダングリング参照をRL担当が自己発見し、
  `main_loop_detail.mermaidのSTEP_BOUNDARY経由でMAIN_DC/MAIN_DC2からNEW_DECISION/
  NEW_DECISION_PENDINGへ`という現状の構造に合わせて修正した。

## Mermaid構文の機械的検証

全8図(`mermaid_rough_combat.mermaid`＋7詳細図)についてbracket/brace/quote balanceのプログラム的
検証を実施し、全て整合していることを確認した(`[`/`]`・`{`/`}`・引用符が過不足なく対応)。

```
mermaid_rough_combat.mermaid: [39]39 {7}7 quotes=92 -> OK
mermaid_combat_branch_scheduler_detail.mermaid: [29]29 {3}3 quotes=64 -> OK
mermaid_combat_candidate_pipeline_detail.mermaid: [21]21 {2}2 quotes=46 -> OK
mermaid_combat_commit_detail.mermaid: [26]26 {3}3 quotes=54 -> OK
mermaid_combat_fault_worker_detail.mermaid: [40]40 {4}4 quotes=88 -> OK
mermaid_combat_main_loop_detail.mermaid: [38]38 {14}14 quotes=104 -> OK
mermaid_combat_rng_hypothesis_detail.mermaid: [28]28 {5}5 quotes=66 -> OK
mermaid_combat_snapshot_replay_detail.mermaid: [47]47 {11}11 quotes=116 -> OK
```

## 完了条件の充足確認

- 上記方針修正(1〜11項目)は全て反映済みまたは根拠付きで整理済み(11項目は図の構造変更ではなく
  継続課題としての記録という対応で完了)。
- Rough図と7詳細図の矛盾: Codexによる限定差分レビュー2ラウンドで「新たな修正点なし」の合意に
  達しており、矛盾は解消されている。
- `mermaid_combat_target_worker_pool.mermaid`の判断: `deprecated/`へ移動して確定。参照切れなし
  (現行8図からの4箇所の参照は全て新しいパス・表現へ更新済み)。
- Mermaid構文検証: 成功(全8図でbracket/brace/quote整合)。
- Codexの差分レビュー: 新たな設計blockerなし(2ラウンド目で明示的に合意)。
- 最終報告書: 本報告のcommitをもって充足する。
- 作業ツリー: 本報告のcommit後にcleanであることを確認する。

## 使用したcommit・ランタイムコード無変更・作業ツリー状態

- 前回最終報告commit: `5a3a96f`
- 本報告のcommitをもって完了
- ランタイムコードの変更: 本作業を通じて一切なし
- 作業ツリー: 本報告のcommit時点でclean(Mermaid図はSTS2_RLのgit管理対象外、STS2_Mermaid配下。
  `C:\STS2_Mermaid`自体もgitリポジトリではないため、Mermaid図側の変更履歴はファイルシステム上の
  変更のみで、git差分としては残らない)

## 結論

監督者指示の11項目全てについて対応(反映または継続課題としての記録)を完了し、Codexによる限定差分
レビューでも新たな修正点なしの合意に達した。8図一式は実装契約として確定可能な状態にある。

実装には進まず、ここで監督者へ報告して停止する。
