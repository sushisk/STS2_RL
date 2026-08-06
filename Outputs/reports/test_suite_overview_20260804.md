# テストスイート概要（整理後）

- 整理実施日: 2026-08-04
- 整理前: 40ファイル（Combat 26／Run 6／TrainingAPI 8）
- 整理後: **35ファイル**（Combat 22／Run 6／TrainingAPI 7）

## 削除したファイルと理由

| ファイル | 理由 |
|---|---|
| `Combat/tests/test_endurance_runner.py` | 独立した耐久試験runnerスクリプトの小規模(N=6)スモークテスト。runner自体は本番コードパスではなく、他のテストが本番コードを既に厳密にカバーしているため限界的価値が低い。 |
| `Combat/tests/test_multi_hypothesis_stress_runner.py` | 同上パターン（独立stress runnerスクリプトの小規模(N=3)スモークテスト）。 |
| `Combat/tests/test_shadow_evaluation_batch.py` | 同上パターン。`test_shadow_adapter.py`（詳細版・実Emulator）と内容が重複しており、そちらを残す。 |
| `Combat/tests/test_multi_combat_continuous_execution.py` | 非常に低速なsoak test。`test_main_loop.py`／`test_search_coordinator.py`／`test_branch_worker_pool.py`／`test_multi_round_search.py`が個別にカバーしている範囲を横断的に再実行しているだけで、単体では新しい保護を提供しない。 |
| `TrainingAPI/tests/test_event_rng_hypothesis_integration.py` | 実Emulatorを最大150ステップ×15テスト分駆動する非常に低速なファイル。同じ導出ロジック／Registry契約は`test_event_rng_hypothesis.py`（純粋Python、CLR不要、高速）で十分にカバーされており、実エンジンでの疎通確認自体は`test_e2e.py`のWhole Runシナリオでも部分的に担保される。「時間のかかるテストより短いテストを残す」方針により削除。 |

上記以外は、各ファイルが固有のコード経路・契約（過去に発見された具体的なバグの再発防止を含む）を保護しており、削除すると保護が失われると判断し、そのまま残した。

---

## 現在保持しているテスト一覧

### Combat/tests/（22ファイル）

| ファイル | テスト数 | 内容 |
|---|---|---|
| `test_action_fault_contract.py` | 9 | Console I/O異常時のFault検出（2種類の障害注入手法） |
| `test_battle_emulator_transition_outcome.py` | 3 | `StepResult.Transition`（Combat終了の正式シグナル）の変換ロジック |
| `test_belief_coverage.py` | 6 | `belief_coverage.py`のPUBLIC_MULTISETカバレッジ評価 |
| `test_branch_manager.py` | 11 | Branch Cancel/Release状態機械（queued/running/Holder/sibling/二重cancel等） |
| `test_branch_worker_pool.py` | 11 | Branch Worker Poolのルーティング・ディスパッチ |
| `test_candidate_pipeline.py` | 10 | 候補生成パイプライン（抽出・ランキング・スコアリング） |
| `test_choice_semantics.py` | 20 | Choice Semanticsテーブル（実DLLケース＋辞書ロジックの単体ケース） |
| `test_decision_context.py` | 19 | `DecisionContext`／`DecisionSignature`の境界判定・Replay・Restore適格性 |
| `test_execution_mode.py` | 8 | `external_control`／`zero_index`実行モード（Combat側） |
| `test_external_control_decision_types.py` | 4 | External Control下でのstale／重複／不正Action安全性（Combat側） |
| `test_fault_injection_additional.py` | 2 | 追加Fault経路（Episode close時の保持Lease、無指示進行の拒否） |
| `test_fault_taxonomy.py` | 10 | Fault分類・リトライ・Commit集約ロジック |
| `test_inference_removal_audit.py` | 4 | `external_control`／`zero_index`経路がlegacy推論を一切importしないことの監査（Combat側） |
| `test_live_combat_session_step.py` | 3 | `LiveCombatSession.step(stop_at_pending=...)`の挙動 |
| `test_main_loop.py` | 15 | Main Process決定ループの状態機械 |
| `test_multi_round_search.py` | 7 | Beam Search（`multi_round_search.py`） |
| `test_restore_snapshot_phase3c1.py` | 28 | Snapshot Restore APIの網羅的検証（本スイート中もっとも低速：テストごとに独立Pythonサブプロセスを起動） |
| `test_rng_hypothesis.py` | 10 | RNG Hypothesis／DrawPile Belief（Method-B置換のRound Trip） |
| `test_scenario_v2.py` | 32 | CombatScenario入力仕様（カード強化状態・ポーション・null許容HP等） |
| `test_search_coordinator.py` | 15 | Search Coordinatorの最終組み立て |
| `test_shadow_adapter.py` | 7 | 旧経路と新経路の比較（Shadow比較） |
| `test_worker_respawn.py` | 6 | Combat Worker OSレベルRespawn（PID／generation変化、旧Lease無効化等） |

### Run/tests/（6ファイル）

| ファイル | テスト数 | 内容 |
|---|---|---|
| `test_whole_run_connectivity.py` | 5 | Whole Run基盤（Session／Driver／Branch Runner）への実Emulator疎通 |
| `test_worker_pool_process_separation.py` | 5 | Whole Run Worker PoolのOSプロセス分離（実プロセス・実PID・Lease/generation） |
| `test_execution_mode.py` | 7 | `external_control`／`zero_index`実行モード（Whole Run側） |
| `test_external_control_decision_types.py` | 6 | External Control下でのDecision安全性（Map／Combat／Start-of-Combat Pending） |
| `test_inference_removal_audit.py` | 2 | legacy filler picker不使用の監査（Whole Run側） |
| `test_fault_injection_additional.py` | 3 | 追加Fault経路（Snapshot Load失敗、Episode close等） |

### TrainingAPI/tests/（7ファイル）

| ファイル | テスト数 | 内容 |
|---|---|---|
| `test_dto_validation.py` | 16 | Request DTOのSchema／必須項目検証（純粋Python、CLR不要） |
| `test_mask_audit.py` | 4 | masked DTOの再帰的Hidden Information監査（純粋Python、CLR不要） |
| `test_event_rng_hypothesis.py` | 12 | Active Event RNG Hypothesis導出関数／Registryの単体契約（純粋Python、CLR不要） |
| `test_rng.py` | 8 | `rng_id`とHypothesisの対応関係（Combat／Whole Run双方の配線） |
| `test_root_protection.py` | 10 | root不変性契約（get_decision／emulate_action／commit_actionの境界） |
| `test_fault_lifecycle.py` | 6 | Worker Fault／Cancel／CloseがWire層のResponseへ正しく反映されること |
| `test_e2e.py` | 2 | Mock Training Client経由のフルシーケンスE2E（Combat／Whole Run） |

**合計: 35ファイル、約320 test関数**

---

## 実行方法

いずれもpytest非依存の独自Assertion Runnerで、各ファイルは単独で直接実行できる（`PASS test_x` / `FAIL test_x`を1行ずつ出力し、最後に`N passed, M failed`を表示、失敗があれば終了コード1）。

### 個別ファイルの実行

```bash
# Combat配下は Combat/ をカレントディレクトリにして実行
cd Combat
python tests/test_branch_manager.py

# Run配下は Run/ をカレントディレクトリにして実行
cd Run
python tests/test_execution_mode.py

# TrainingAPI配下はリポジトリルートから実行
cd C:\STS2_RL
python TrainingAPI/tests/test_dto_validation.py
```

### ディレクトリ一括実行（推奨: 出力が多いため`grep -v`でINFOログを除去し、`tail`で末尾のみ確認）

```bash
cd Combat
for f in tests/*.py; do
  echo "=== $f ==="
  python "$f" 2>&1 | grep -v "^\[INFO\]" | tail -10
done
```

`Run/tests/`・`TrainingAPI/tests/`も同様のループで一括実行できる。

### 実行時の注意

* いずれも実Emulator（CoreCLR経由のpythonnet）を初期化するため、**pytest等の外部テストランナーではなく、このファイル単体を`python`で直接実行すること**（複数ファイルを同一プロセスでimportするとCoreCLRの二重初期化やGameInstanceのsupersede例外が発生する）。
* `TrainingAPI/tests/test_dto_validation.py`・`test_mask_audit.py`・`test_event_rng_hypothesis.py`の3ファイルのみCLR非依存の純粋Pythonテストであり、他のツールから安全にimportしても問題ない。
* 個々のファイルは数秒〜数分（`test_restore_snapshot_phase3c1.py`は28個の独立サブプロセスを起動するため特に低速）。一括実行時にハングを避けるため、環境によっては1ファイルごとに`timeout`でラップし、さらにスイープ全体を外側の`timeout`でも囲むことを推奨する（本プロジェクトでは長時間スイープが環境起因で無応答になる事例が過去に複数回発生したため）。

```bash
timeout 1800 bash -c '
for f in tests/*.py; do
  echo "=== $f ==="
  timeout 200 python "$f" 2>&1 | grep -v "^\[INFO\]" | tail -10
done
'
```

### 既知の事前既存失敗（新規failureではない）

全回帰実行時、以下は本整理と無関係な既知のbaseline失敗として扱ってよい。

* `Combat/tests/test_restore_snapshot_phase3c1.py`: `test_official_json_example_restores_successfully`／`test_real_6546_21_rejected_via_public_api`（2件）
* `Combat/tests/test_scenario_v2.py`: `test_wriggler_missing_slot_without_encounter_is_detected`（環境依存のflake、1件）
* `Run/tests/test_whole_run_connectivity.py`: `test_choice_branch_shop_holder_sibling_reproduction`（旧式単一プロセスrunner起因、1件）
