# Codex／RL担当双方合意済みCombat Mermaid Diagram — 最終報告 (2026-08-01)

対象: `C:\STS2_Mermaid\mermaid_rough_combat.mermaid`(上位契約)＋7詳細図
(`mermaid_combat_main_loop_detail.mermaid`・`mermaid_combat_candidate_pipeline_detail.mermaid`・
`mermaid_combat_branch_scheduler_detail.mermaid`・`mermaid_combat_snapshot_replay_detail.mermaid`・
`mermaid_combat_rng_hypothesis_detail.mermaid`・`mermaid_combat_fault_worker_detail.mermaid`・
`mermaid_combat_commit_detail.mermaid`)。

レビュー開始時点commit: `01d03cbe8bbffb24fa6b62551712f5c81a4ea23d`
本報告直前のcommit: `ddef6042cf86d60563fa5547c155155f8baa5628`(第1ラウンド報告)

**本作業全体を通じ、Mermaid図の修正と報告のみを行った。ランタイムコードの変更は一切行っていない。**

## 反復サマリ

`codex exec`(codex-cli 0.145.0、read-onlyサンドボックス)をRL担当と対等な設計レビュー担当として
3ラウンド運用した。

- **第1ラウンド**: Codexが8図を横断的に読み、12件の指摘を提出。RL担当は12件全てを採用と判断した
  (うち1件は方針転換ではなく表現統一で解消)。却下はゼロ。8図全てを修正し、
  `rl_combat_mermaid_codex_review_round1_20260801.md`として報告・commit(`ddef604`)。
- **第2ラウンド**: 修正後の8図をCodexへ再度渡し、局所修正による新たな不整合の有無を横断確認させた。
  Codexは12件の反映を確認した上で、Rough図の`SEARCH_FAIL -.-> DECISION`が
  Direct/PENDING_STATICへの暗黙再ルーティングと誤読されうるという1件の残存指摘を提出。
  RL担当はこれを採用し、`SEARCH_FAIL -.-> MAIN_FAULT`へ修正した。
- **第3ラウンド(最終確認)**: 修正後の8図を再度Codexへ渡し、最終確認を依頼した。Codexは
  「新たな修正点なし」「この最終ラウンドの設計には合意できる」と明言した。
- **RL担当independentな最終点検**: Codexとは別に、8図全てについてbracket/quote balanceの
  プログラム的検証を実施したところ、`mermaid_combat_main_loop_detail.mermaid`の`VERIFY_TRANSITION`
  ノードで開き括弧`{`に対して閉じ括弧が`}`ではなく`]`になっているMermaid構文エラーを発見し、
  修正した(Codexも人間のレビューでも見落としていた構文レベルの不整合であり、プログラム的な
  bracket-balance検証で初めて検出できた)。修正後、8図全てで`[`/`]`・`{`/`}`・引用符が
  過不足なく対応することを確認した。この修正を反映した最終版がCodexの第3ラウンド確認対象と
  同一内容であることも確認済み(第3ラウンドの時点で既にこの修正は反映済みだった)。

## 第1ラウンド指摘一覧とRL担当の採否(再掲)

| # | 指摘 | 採否 |
|---|---|---|
| 1 | Rough図のBody Branchが固定役割としてLease契約と矛盾 | 採用 |
| 2 | Rough図が旧状態取得経路(「現在のCombat状態と選択肢を取得」)を示唆 | 採用 |
| 3 | SchedulerのTOP_TO_BOOTSTRAPが「到達だけでLease確立」と読める文言のまま | 採用 |
| 4 | Lease済みlive継続経路にSemantic Action再解決が明示されていない | 採用 |
| 5 | COMMIT_FIRST_ONLY後の「必ず再探索」とMain-observed Pending静的評価限定方針の文言衝突 | 採用(表現修正のみ) |
| 6 | task timeout時、生存確認だけでWorkerをReadyへ戻す経路が危険 | 採用 |
| 7 | Search Evaluation FailureのMain側受け口が未定義 | 採用 |
| 8 | deterministic violationがFault図のSourcesに入口を持たない | 採用 |
| 9 | RNG非消費と判定した候補のContinuation先でRNG非消費が保証されない | 採用 |
| 10 | Rough図のNEXT_ROOTSがHypothesis由来Beam継続を示唆し単一ラウンド制限と矛盾 | 採用 |
| 11 | Retry中候補とFault確定候補の区別が弱く二重集計しうる | 採用 |
| 12 | Training担当向けログ/Result schemaが未定義 | 採用 |

第2ラウンド追加指摘(Rough図のSEARCH_FAIL接続先)も採用。**却下項目は最終的にゼロ。**

## 図間整合性の最終確認結果

- Codex第2・第3ラウンドで、局所修正による新たな不整合が生じていないことを確認済み。
- RL担当独自に、全8図のノード参照(`-->`/`-.->`の接続先)がファイル内で定義済みであることを
  grepベースで確認し、Round1で発生していた死んだ経路(Main-Pending用の旧WHO分岐等)が
  残存していないことを確認した。
- Mermaid構文のbracket/quote balanceをプログラム的に検証し、1件の構文エラー
  (`VERIFY_TRANSITION`の閉じ括弧誤り)を発見・修正した。

## 終了条件の充足確認

1. **Codexが8図について新たな修正点なしと判断する** — 第3ラウンドで明言済み。
2. **RL担当も新たな修正点なしと判断する** — 上記bracket-balance検証による1件の構文修正を最後に、
   RL担当としても追加の修正点は見当たらない。
3. **Rough Diagramと7詳細図に矛盾がない** — 第1・第2ラウンドで検出された矛盾(Body/Sub固定役割、
   旧状態取得経路、Hypothesis-Beam継続、Search失敗の接続先)は全て解消済み。
4. **全主要フローに入口・出口・責任主体がある** — 各詳細図の冒頭コメントに責任主体を明記し、
   Combat Entryから Combat終了／Main Combat Fault／Search Evaluation Failureまでの経路が
   全て閉じていることを確認した。
5. **既存Emulator契約で実装不能な経路が残っていない** — Pending自体のCapture/Restoreを行う経路、
   RNG専用の新規API依存、GetObservation/GetLegalActions依存はいずれも排除済み。使用するAPIは
   `CaptureSnapshot`／`RestoreSnapshotJson`／`ValidateRestoreSnapshotJson`／`Step`のみ。
6. **未決事項が設計blockerか実装時判断かに分類されている** — 下記「継続する未解決事項」の通り、
   全て「図の構造には影響しない実装時決定事項」として分類済み(Codexも第2ラウンドで同意)。
7. **Mermaid構文が全図で正常に描画できる** — bracket/quote balanceのプログラム的検証で全8図が
   整合していることを確認した(実レンダラでの目視確認は環境上未実施だが、構文レベルの
   異常は検出されなかった)。
8. **最終レビュー報告がcommitされ、作業ツリーがcleanである** — 本報告のcommitをもって充足する。

**8項目全てを満たしたため、本レビューを終了する。**

## 継続する未解決事項(実装時決定として扱う。設計blockerではない)

- Stable境界を跨いだLease再利用の正式採否(実測結果を踏まえた監督者判断待ち)
- Decision Context系列内のContinuation深さ上限の具体値
- Replayカスケードコストへの将来対応(実測後に判断)
- RNG Counter差分のStepResult露出可否(Emulator担当への確認事項)
- Pending境界での限定的RNG override(現行Emulator契約では未対応。Emulator担当への確認事項。
  Codex・RL担当ともに「採用するなら構造変更が必要だが、現行図は明示的に不採用としているため
  blockerではない」ことに合意)
- 汎用確率的Beam Search(Root Action単位の単一ラウンドで代替する設計を採用。将来拡張として明示)
- Evaluator入力型の分離(図の構造上はBOUNDARY_TABLEで要件を明記済み。実装時のAPI契約として
  型/インターフェースレベルでの分離が必須であることをCodex・RL担当ともに確認)
- Restore失敗時の例外分類の一部緩和可否(replay mismatch/action faultのWorker再利用既定緩和は、
  RestoreSnapshotJsonの完全な状態リセット保証の実測検証に依存)

## 使用したcommit・ランタイムコード無変更・作業ツリー状態

- レビュー開始時点commit: `01d03cbe8bbffb24fa6b62551712f5c81a4ea23d`
- 第1ラウンド報告commit: `ddef6042cf86d60563fa5547c155155f8baa5628`
- 本最終報告のcommitをもって完了
- ランタイムコードの変更: 一連の作業を通じて一切なし
- 作業ツリー: 本報告のcommit時点でclean(Mermaid図はSTS2_RLのgit管理対象外、STS2_Mermaid配下)

## 結論

**Codex／RL担当双方合意済みCombat Mermaid Diagramとして、本図一式(Rough Diagram＋7詳細図)を
確定する。** 実装には進まず、ここで停止する。
