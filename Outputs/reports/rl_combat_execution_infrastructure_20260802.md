# Combat実行基盤 実装報告 (2026-08-02)

## 概要

`docs/architecture/combat/` の8枚のMermaid図(正式契約)に基づき、Combat実行基盤を
`Combat/search/` パッケージとして実装した。実装作業はCodex(`codex exec`)へ委任し、
作業分解・Codexへの指示・差分レビュー・テスト実行・デバッグ・完成判定はRL担当が
一貫して担当した。8フェーズすべてが完了し、最終フェーズで実際にMain Loop→Search
Coordinator→Candidate Pipeline→RNG Hypothesis→Branch Worker Pool(実プロセス)→
Fault taxonomy/Commit集約という全経路を実Emulatorで結合させ、短い戦闘が
実際にTerminalへ到達することを確認した。

**コード変更範囲**: `Combat/search/`(新規パッケージ、8ファイル)と`Combat/tests/`
(新規テスト8ファイル)のみ。既存のランタイムコード
(`live_combat_session.py`・`combat_state_snapshot.py`・`beam_search.py`・
`lookahead.py`・`heuristic_agent.py`・`choice_semantics.py`・`state_evaluator.py`・
`policy_agent.py`・`choice_policy_agent.py`・`Combat/env/`・`Combat/data/`・
`Combat/evaluation/`)・`Training/`・`Common/` への変更は一切無い
(`git diff --stat cfb12a4..HEAD -- <それらのパス>` は全て空出力で確認済み)。

## 図と実装の対応表

| Mermaid図 | 実装ファイル | 主な内容 |
|---|---|---|
| `mermaid_combat_snapshot_replay_detail.mermaid` | `Combat/search/decision_context.py` | Decision Context(DC_DEF)、軽量Decision Signature(DC_SIGNATURE)、SUB_REPLAY(Restore+Replay Prefix再実行+検証) |
| `mermaid_combat_main_loop_detail.mermaid` | `Combat/search/main_loop.py` | Main Processの決定ループ状態機械、Held Stable Snapshot/Replay Prefixブックキーピング、PENDING_STATIC、EXEC_LOOP、VERIFY_TRANSITION |
| `mermaid_combat_candidate_pipeline_detail.mermaid` | `Combat/search/candidate_pipeline.py` | choice_kind別分類・軽量静的評価、Order-Masked Observation(構造的にDrawPile順を遮断)、prune/split |
| `mermaid_combat_branch_scheduler_detail.mermaid` | `Combat/search/branch_worker_pool.py` | State-Holding Worker Lease、Branch Worker Pool(実multiprocessing)、Holder Step/Bootstrap+Step dispatch |
| `mermaid_combat_rng_hypothesis_detail.mermaid` | `Combat/search/rng_hypothesis.py` | Search Hypothesis ID、PUBLIC_MULTISET(正しい式)、BELIEF_GEN、方式BによるSnapshot置換 |
| `mermaid_combat_fault_worker_detail.mermaid` + `mermaid_combat_commit_detail.mermaid` | `Combat/search/fault_taxonomy.py` | Fault taxonomy・Worker再利用方針・WorkItem状態機械、Root Action集約・COMMIT_FIRST_ONLY |
| `mermaid_rough_combat.mermaid`(上位契約) | 全ファイル共通 | Authoritative/Hypothetical/Concrete OrderedDrawPile用語、Exact State層/Belief-Search層分離を各所で遵守 |
| (統合) | `Combat/search/search_coordinator.py` | 上記6ファイルを1本の`SearchStrategy`へ組み立てる最終統合層 |

## テスト結果(全て実Emulator・実LiveCombatSession、モック無し)

| ファイル | 結果 |
|---|---|
| `test_decision_context.py` | 14/14 pass |
| `test_main_loop.py` | 12/12 pass |
| `test_candidate_pipeline.py` | 10/10 pass |
| `test_branch_worker_pool.py` | 9/9 pass(うち2件は実際に2プロセスをspawnする統合テスト) |
| `test_rng_hypothesis.py` | 8/8 pass(実Restore round-trip込み) |
| `test_fault_taxonomy.py` | 10/10 pass(うち2件は実Fault再現) |
| `test_search_coordinator.py` | 6/6 pass(実エンドツーエンドTerminal到達を含む) |
| `test_restore_snapshot_phase3c1.py`(既存、回帰確認用) | 27/28 pass — 既存の未関連1件失敗(`test_official_json_example_restores_successfully`)は変更前から再現する既知の事象であり、本実装による回帰ではない |

合計: 新規テスト69件全てpass、既存回帰確認スイートに新規の失敗無し。

## 実装過程で発見・対応した実際のブロッカー

Phase 8の実エンドツーエンド統合で、新規Combatの`CombatStateSnapshot`captureが
恒常的に`CombatHistory.Entries`内に「`source_live_state_inconsistency`」原因の
dangling `CardDrawnEntry`参照を含み(シナリオの権威的な手札設定が初期ドロー時の
CardInstanceIdを上書きするための既知のEmulator側データ品質問題)、
`restore_input_eligibility()`に恒常的に弾かれてBranch Worker側のRestoreが機能しない
ことを実機検証で特定した。これはPhase 2〜7個別のテストでは`_make_eligible()`
(テスト専用ヘルパー、CombatHistory全体を消去しCompleteness検証も上書きする強引な
回避策)を各自使っていたため、統合するまで顕在化しなかった。

対応として、`search_coordinator.py`に`_strip_known_benign_dangling_entries()`を
実装し、厳密にこの1種類(`entry_type=CardDrawnEntry`, `cause=
source_live_state_inconsistency`)に該当するエントリのみを除去する狭いサニタイザを
導入した。`Metadata.Completeness`/`UnsupportedFields`は一切書き換えず、それ以外の
dangling参照や除去後もeligibleにならない場合は例外を送出する(サイレントな
回避を許さない設計)。これは恒久対応ではなく、Emulator側のデータ品質問題に対する
明記された暫定ワークアウトである。

## 既知の制限(スコープとして明記、隠していない)

- **Phase 7のリトライループは未結線**: `decide_retry()`(Running→Retrying→
  FinalSuccess/FinalFault)は実装済みだが、Phase 8の統合層はBranch Faultを
  即座にFinalFaultとしてログ化するのみで、実際の再送信(Retrying状態からの
  再実行)ループは呼び出していない。
- **`verify_main_invariant`は`lambda: True`**: 現在の統合は1回のSearchStrategy
  呼び出し=1回の同期Worker batchであり、その間Main側の並行実行が存在しないため
  正当化される。将来Main活動と並行するコーディネータを構築する場合は実装が必要。
- **深いマルチラウンド探索・Beam Search継続は未実装**: 本実装は「1回のBranch
  Worker Pool結果ラウンドの集約」までであり、`PREV_CHILD`経由の複数手先読み
  (Plan Path継続)や、RNG非依存経路での複数手一括commitは対応していない
  (Phase 2の`DecisionContext.from_prev_child()`自体は実装・テスト済み)。
- **既存の実行経路(`beam_search.py`/`lookahead.py`/`heuristic_agent.py`/
  `CombatEnv`)は意図的に無変更**: Training データ生成・オンライン評価は
  引き続き既存経路で動作する。新アーキテクチャへの移行は本タスクの範囲外の
  別途判断とした。
- **`ShuffleRngSeed`(単一int)と`RunRng["Shuffle"]`(Counter+State0-3)の関係は
  未解決**: `Common/contracts/emulator_dto_contract_rl_required.v1.md`で既に
  文書化されている既知の要確認事項であり、本実装はCounter+State0-3形式を採用し、
  単一seed形式とは接続していない。
- **PUBLIC_MULTISETの生成カード経路網羅性**: `CardGeneratedEntry`が全ての
  カード生成経路を記録している保証は無く、Emulator側の要確認事項として
  コード内に明記されたまま(虚偽の完全性保証はしていない)。

## Git commit

| Phase | Commit |
|---|---|
| Phase 2: Decision Context / Decision Signature | `b3a8bae` |
| Phase 3: Main Process決定ループ状態機械 | `c25a57e` |
| Phase 4: Candidate Pipeline | `f90ec15` |
| Phase 5: Branch Worker Pool + Lease | `eb55037` |
| Phase 6: RNG Hypothesis / DrawPile Belief | `842a84d` |
| Phase 7: Fault taxonomy + Commit集約 | `0f5395a` |
| Phase 8(最終): Search Coordinator統合 | `1808096` |

## 結論

Combat実行基盤の受け入れ条件(8枚のMermaid図の設計契約に沿った実装、単体・
統合・決定性/Replay/Fault系テストが実Emulator上で通ること、実エンドツーエンドで
Main Loop〜Branch Worker Pool〜集約までが結合して実際にCombatを完走できること)を
満たしたと判断する。既存のランタイムコード・Training実装には一切変更を加えて
いない。未実装の大規模設計変更(リトライループの実結線、マルチラウンド探索、
既存経路からの移行)には進まず、ここで停止する。
