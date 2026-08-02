# Combat Search Vertical Slice v1 実装報告 (2026-08-02)

## 概要

「Combat実行基盤」(Phase 2〜8、前回報告済み・commit `1808096`)を土台に、監督者より
指示された6タスク+複数戦闘連続実行検証を完了した。実装はCodex(`codex exec`)へ
委任し、作業分解・Codexへの指示・差分レビュー・テスト実行・デバッグ・完成判定は
RL担当が一貫して担当した。全経路の実Emulatorテストと複数戦闘連続実行テストまで
RL担当がデバッグを完了している。

**コード変更範囲**: `Combat/search/`(新規4ファイル追加、既存2ファイル拡張)と
`Combat/tests/`(新規テスト7ファイル)のみ。既存のランタイムコード
(`live_combat_session.py`・`combat_state_snapshot.py`・`beam_search.py`・
`lookahead.py`・`heuristic_agent.py`・`choice_semantics.py`・`state_evaluator.py`・
`policy_agent.py`・`choice_policy_agent.py`・`Combat/env/`・`Combat/data/`・
`Combat/evaluation/`)・`Training/`・`Common/` への変更は一切無い
(`git diff --stat f7aa3b8..HEAD -- <それらのパス>` は全て空出力で確認済み)。
Trainingへの接続は行っていない。

## タスク別の実装内容とcommit

| # | 指示内容 | 実装 | Commit |
|---|---|---|---|
| 1 | Fault taxonomyのリトライループをSearch Coordinatorへ結線 | `search_coordinator.py`に`_dispatch_work_items_until_final()`を追加。Phase7の`WorkItemAttempt`/`decide_retry()`を実際に駆動し、`FinalSuccess`/`FinalFault`確定まで再送信するループへ変更。BranchWorkerPoolがLease無効化・世代不一致による再ルーティングは行うが、OSプロセスのkill/respawnは持たないことを発見し、その範囲でFORCE_RESTART方針を実装(真のプロセス再起動は別スコープと明記) | `84fe784` |
| 2 | `verify_main_invariant`を実State Identity／Held Snapshot／Replay Prefix検証へ置換 | `main_state_provider`パラメータを追加(省略時は従来動作で後方互換)。供給時はCommit直前にState Identity・Held Snapshot内容・Replay Prefix構造を実際に比較し、不一致は`MainInvariantViolatedError`として送出(`SearchStrategy`の戻り値型制約のため例外経由、将来のMain Loop統合での捕捉を想定) | `743e9f6` |
| 3 | `PREV_CHILD`による複数ラウンド探索(Plan Path継続+枝刈り) | `Combat/search/multi_round_search.py`を新規追加。Phase2実装済みだが呼び出し元が無かった`DecisionContext.from_prev_child()`を初めて実際に駆動。RNG非依存(PASSTHROUGH)ラウンドのみ継続、Hypothesis必須到達で単発commitして終了、beam_width件のみ次ラウンドへ枝刈り。**実機での発見**: 現実的な選択のほとんどがカード使用でHypothesis必須になるため、実Emulatorのみで長さ2以上のPREV_CHILD継続に至るクリーンなシナリオは見つからなかった(実装の不備ではなくゲーム性質上の制約、正しさは注入テストで検証) | `75cd74e` |
| 5 | Emulator側`source_live_state_inconsistency`修正確認、サニタイザ削除+関連テスト更新 | **実機診断でEmulator側バグが修正されたことを確認**(新規Combat captureがdangling参照0件・restore-eligible)。Phase8で追加した暫定サニタイザを削除し、この修正に暗黙依存していた既存テスト3件を、意図的にdangling参照を注入する明示的構成へ修正。修正済みSnapshotがサニタイズ無しでrestore-eligibleであることと、意図的に壊れたSnapshotが正しく拒否されることの両方をテストで確認 | `8e9dacd` |
| 4 | 旧経路/新経路のshadow実行比較Adapter | `Combat/search/shadow_adapter.py`を新規追加。旧経路(`HeuristicAgent`+`BattleEmulator`+`StateEvaluator`)は使い捨てspawnサブプロセスで評価、新経路は呼び出し元プロセスのシングルトンGameInstanceを退避・復元しながら同一プロセス内で評価。どちらも呼び出し元の実セッションに一切影響しないことをテストで実証。既存の実行経路は無変更のまま、意味的等価性による一致/不一致の記録のみを行う純粋な観測ツール | `73a866b` |
| 6 | PUBLIC_MULTISET生成カード網羅性の検証+不完全Belief記録 | vendored Emulator source(2026-07-23時点)を実際に調査し、コンバット山への生成カード追加が`CardPileCmd.AddGeneratedCardsToCombat`と`CardCmd.Transform`のコンバット内経路の2箇所のみを通り、いずれも`History.CardGenerated`書き込みを伴うことを確認。カード36種・Relic13種・Power12種の生成経路全てがこの2経路を通ることを確認し、未記録の生成経路は発見されなかった(`UNCERTAIN_GENERATION_SOURCES`は空)。実機でCOLLISION_COURSEのカード生成をクロスチェックし裏付けた。`Combat/search/belief_coverage.py`を新規追加し、`search_coordinator.py`のHypothesis関与ラウンドのdecision log entryへ`public_multiset_coverage`診断情報を付与するよう拡張 | `f7aa3b8` |
| (追加) | 複数戦闘の連続実行テスト | `test_multi_combat_continuous_execution.py`を新規追加。1つの共有BranchWorkerPool(worker_count=3)を使い回しながら異なるキャラ構成/Relic/Potion/敵HP/戦略種別(単発ラウンド・PREV_CHILD beam両方)の実戦闘5件を連続実行し、全てCombatTerminalOutcome(victory)へ到達、combat_session_id重複無し、5戦闘完了後もWorker Poolが正常応答することを確認。デバッグ過程で発見した複数敵/特定Terminal到達パターンでのEmulator側非同期ログ例外(NullReferenceException等)は、RL側変更対象ファイル外の既知の外部要因として明記し、該当シナリオ形状を回避 | `b2ec718` |

## テスト結果(全て実Emulator、モック無し)

| ファイル | 結果 |
|---|---|
| `test_decision_context.py` | 14/14 pass |
| `test_main_loop.py` | 12/12 pass |
| `test_candidate_pipeline.py` | 10/10 pass |
| `test_branch_worker_pool.py` | 9/9 pass |
| `test_rng_hypothesis.py` | 8/8 pass |
| `test_fault_taxonomy.py` | 10/10 pass |
| `test_search_coordinator.py` | 14/14 pass |
| `test_multi_round_search.py` | 6/6 pass |
| `test_shadow_adapter.py` | 7/7 pass |
| `test_belief_coverage.py` | 5/5 pass |
| `test_multi_combat_continuous_execution.py` | 1/1 pass(実5戦闘連続完走) |
| `test_restore_snapshot_phase3c1.py`(既存、回帰確認用) | 26/28 pass — 既知の未関連2件失敗(`test_official_json_example_restores_successfully`、`test_real_6546_21_rejected_via_public_api`。いずれも本タスクで個別調査済みでVertical Slice v1とは無関係と確認済み) |

合計: 新規/拡張テスト96件全てpass。既存回帰確認スイートに新規の失敗無し。

## 実装過程での重要な発見

1. **Emulator側`source_live_state_inconsistency`バグの修正を実機で検出**(Task5)。
   Phase8完了時点では未修正で暫定サニタイザが必要だったが、本セッション中に
   修正されたことを実機診断で確認し、暫定コードを撤去。これに暗黙依存していた
   既存テスト4件(3ファイル)を、意図的な合成データ注入による明示的検証へ
   置き換えた。
2. **PREV_CHILDによる複数ラウンド継続は、この対戦ゲームの性質上、実プレイでは
   稀にしか発生しない**(Task3)。ほとんどの意思決定がカード使用を伴い
   Hypothesis必須に分類されるため。ロジック自体の正しさ(枝刈り・Plan Path蓄積・
   Hypothesis境界での正しい終了)は注入テストで実証済み。
3. **PUBLIC_MULTISETの生成カード網羅性は、調査した範囲(vendored source
   2026-07-23時点)では完全**であることを確認(Task6)。未網羅の生成経路は
   発見されなかったが、将来のEmulator変更への保証ではない旨をコード内に明記。
4. **複数戦闘連続実行でWorker Pool共有時の劣化・リークは検出されなかった**が、
   複数敵/特定のTerminal到達パターンを含むより広いシナリオでEmulator側の
   非同期ログ処理に起因する例外(NullReferenceException等)を発見した。
   これはRL側の変更対象ファイル外の既知の外部要因であり、対応は範囲外。

## 既知の制限(隠さず明記)

- Phase7のリトライループはBranchWorkerPoolのLease無効化・再ルーティングとして
  実装されているが、真のOSプロセスレベル再起動(kill/respawn)機構は無い
  (`branch_worker_pool.py`自体の拡張が必要な別スコープ)。
- `verify_main_invariant`の実装はSearch Coordinator呼び出し1回=1同期batch前提。
  将来Main活動と並行するコーディネータを構築する場合は別途検証が必要。
- PREV_CHILDによる複数ラウンド継続は、上記の通り実プレイでの発生頻度が
  低いことが判明した(設計・実装上の不備ではない)。
- Emulator側の非同期ログ処理に起因する例外(複数敵/特定Terminal到達パターン)
  は既知の外部要因として残存。RL側では対応不能(Emulator側の修正待ち)。
- Trainingへの接続は本タスクの指示通り未実施。

## Git commit

上記表の「Commit」列を参照。全てmainブランチへ直接commit済み、pushはしていない。

## 結論

監督者から指示された6タスク全てを完了し、複数戦闘の連続実行テストまで
実Emulatorでデバッグ・検証済みである。既存のランタイムコード・Training実装
には一切変更を加えていない。「Combat Search Vertical Slice v1」として
既存実装の上に積み上げる形で完成させ、旧経路への即時置換は行っていない
(shadow Adapterによる比較のみ)。Trainingへの接続は行わず、ここで停止する。
