# RL担当報告：Active Event RNG Hypothesis実装

- RL基準commit（作業開始点）: `76c7a7d`
- Emulator基準commit: `fca2f06`
- 対象指示: 「RL担当指示：Active Event RNG Hypothesis実装」

## 0. サマリ

前回の停止報告（`Outputs/reports/rl_whole_run_rng_hypothesis_STOP_20260804.md`）を踏まえ、Whole Runの`rng_id`をActive Event境界に限定して実体化した。Emulatorの`GetEventRngState`/`SetEventRngState`（進行中Event 1つの4ストリームのみを対象とする、意図的に狭いスコープの公開API）だけを根拠とし、Map／Encounter／Boss／Ancient／Act生成には一切着手していない。

実装の過程で、`Run/run_emulator_bridge.py`の既存バグ（`SerializableRng`の実フィールド名が`s0`〜`s3`ではなく`state0`〜`state3`である）を発見・修正した。このAPIは本タスク以前に一度も実Eventに対して実行されたことがなかった（従来はテスト未到達のコードパスだった）。

停止条件はいずれも該当しなかった。

## 1. Capability定義

`TrainingAPI/dto.py::RNG_HYPOTHESIS_CAPABILITIES`：

| ドメイン | 対応状況 |
|---|---|
| `event`（Active Event自身のRNG＋Reward／Shop／Transformation） | **対応** |
| `map` | 未対応 |
| `encounter` | 未対応 |
| `boss_ancient` | 未対応 |

新しい公開Operationは追加していない（既存`emulate_action`の`rng_id`意味論を変更しただけ）。

## 2. Hypothesis Key・管理表

Hypothesis Key: `(parent_branch_id, decision_point_id, rng_id)`（`instance_id`はInstanceごとに独立した`EventRngHypothesisRegistry`インスタンスで暗黙的にスコープされる）。

`TrainingAPI/whole_run_event_rng.py::EventRngHypothesisRegistry`が保持する情報：

| 項目 | 保持内容 |
|---|---|
| Hypothesis Key | `(parent_branch_id, decision_point_id, rng_id)` |
| 内部State | `derive_event_rng_hypothesis()`が導出した4ストリーム分の`{counter, s0, s1, s2, s3}` |
| 対応する復元Root | Key自体が`(parent_branch_id, decision_point_id)`を通じて間接的に指す（Instance側の`_View`/`_BranchBookkeeping`が実際のmap_snapshot/action_prefixを保持） |
| `rng_id` | Key内に含む |
| 参照中Branch | `_branch_refs_by_key: dict[key, set[branch_id]]`（複数Branchが同一Hypothesisを共有可能） |
| 生成世代 | `_generation_counts: dict[key, int]`（`generation_of()`で取得、再生成のたびに増分） |

## 3. Hypothesis状態遷移

```
                 register_branch(key, id)
                        │
   [not created] ──get_or_create()──▶ [live, refcount>=1] ──┐
        ▲                                  │ │              │register_branch(key, id2)
        │                                  │ └──────────────┘ (sibling shares)
        │ release_branch(key, id) with     │
        │ refcount reaching 0              │
        │                                  ▼
        └──────────────────────── [entry dropped from registry]
                                            ▲
              release_all_for_decision(parent, dp) / release_all() ─┘
              (unconditional drop regardless of refcount)
```

* `get_or_create(key, base_state, rng_id)`: KeyがまだLiveでなければ`derive_event_rng_hypothesis()`で導出し`generation`を+1して記録。既にLiveなら記録済みの状態をそのまま返す（`base_state`引数は無視 - 同一Keyなら同一の親Decisionから来ているはずという前提）。
* `register_branch(key, branch_id)`: 参照Branch集合に追加。
* `release_branch(key, branch_id)`: 参照Branch集合から除去。**参照が0になった時点でのみ**Entry自体を破棄（Cancel／Release時のみ呼ぶ - 単一Branchが自分自身のSimulation結果でEvent境界を離れたという理由だけでは呼ばない。理由は§5参照）。
* `release_all_for_decision(parent, dp)` / `release_all()`: 参照数に関わらず無条件破棄（root Decisionのstale化・instance Close用）。

## 4. Whole Run Instance側の統合

`TrainingAPI/instance_whole_run.py::emulate_action`の適用手順（指示§4のとおり）：

1. `_view_for(parent_branch_id)`で親Branchの現在状態を取得（既存のWorker Pool再利用機構で復元済み）。
2. `parent_view.boundary != "event_choice"`なら即座に`rejected`（`fault_kind="rng_hypothesis_unsupported_at_boundary"`、`error="Active Event RNG hypothesis is not available at this boundary."`）。
3. rootが親の場合は`registry.get_or_create(key, parent_view.event_rng_state, rng_id)`でHypothesisを取得／新規生成。非rootが親の場合は親Branch自身の`event_rng_key`／`event_rng_override`／`event_rng_override_at_index`をそのまま継承（新規生成しない - 指示§4のとおり）。
4. `ChoiceWorkItem.event_rng_override`＋`event_rng_override_at_index`としてWorker側へ渡す。
5. Worker側（`Run/worker_pool.py`）が指定Actionを実行する直前に`SetEventRngState`で適用。
6. 停止条件（`next_decision`のみ対応、Whole Run全体で従来から未対応の`combat_end`等は引き続き`rejected`）。
7. Branch状態とHypothesis参照（`event_rng_key`）を`_BranchBookkeeping`へ保存。

### 深いBranchでの継続適用（`event_rng_override_at_index`）

`action_prefix`は親から子へ伸びていく（`parent_action_prefix + [chosen_action]`）。Hypothesis Overrideは**最初にHypothesisが確立された時点の1点だけ**に固定したインデックスで適用し、それ以降のReplayでは自然にRNGを消費・前進させる（`親Branchが到達したHidden Stateから継続する`という指示の要件を、「同じOverride Stateを毎回再適用してリセットする」のではなく「1回だけ適用してあとは自然進行に任せる」ことで満たす）。`Run/worker_pool.py::ChoiceWorkItem.event_rng_override_at_index`のdocstringに設計意図を記載。

## 5. 重要なバグ修正（実装中に発見）

**Sibling Branch間のHypothesis共有が壊れるバグ**: 初期実装では「Branchの結果がEvent境界を離れたら（`new_boundary != "event_choice"`）そのBranch自身のHypothesis参照をrelease」というロジックを入れていたが、これは**同じrng_idを共有する兄弟Branch**（同一親Decisionから複数回`emulate_action`する場合）のうち最初の1件がEvent境界を離れた瞬間に、まだ後続の兄弟が参照する前にレジストリのEntry自体を破棄してしまうバグだった（`test_fairness_same_rng_id_reuses_one_hypothesis_regardless_of_action`で検出）。修正: Branch自身の結果によるRNG参照の自動解放をやめ、Cancel／Release／root Commit／instance Closeの**明示的な**タイミングでのみ解放するよう変更（`instance_whole_run.py`のコメントに設計意図を記載）。これにより、あるBranchが自分自身の理由でEvent境界を離れても、他のBranchが同じHypothesisを引き続き参照できる。

## 6. DTOとHidden Information

`masked_emulator_dto`の形式は変更していない。RNG Stream／counter／内部word／Hypothesis生成seed／digest／Snapshot差分／Worker情報／rootの実RNG Stateはいずれも公開DTOへ一切含まれない（`test_no_rng_internal_state_leaks_into_combat_response`相当の監査を新規テストでも実施）。Responseに含まれるのは、指定された論理`rng_id`（Trainingが送った値そのまま）とstatus／fault_kind／errorのみ。

## 7. 必須テスト結果

新規テスト（2ファイル、**27 tests、全PASS**、独立再実行で確認済み）:

| ファイル | 件数 | 内容 |
|---|---|---|
| `TrainingAPI/tests/test_event_rng_hypothesis.py` | 12 | `derive_event_rng_hypothesis()`の純粋関数性・決定性・型範囲・分離性・縮退状態ガード、`EventRngHypothesisRegistry`の全契約（単体、CLR不要） |
| `TrainingAPI/tests/test_event_rng_hypothesis_integration.py` | 15 | 再現性（同一Key／別セッション／Worker Respawn後）、公平性（同一rng_idの複数Action共有）、分離性（異なるrng_id）、深いBranchの継承、root保護、境界拒否（Map境界含む複数境界を実走査）、Lifecycle（Cancel/Release/root Commit/instance Close）、Mock Training Client E2E — 全て実Emulator・実OSプロセスに対して実行 |

既存テストの更新（Whole Run部分のみ、新しい境界制約に適合させた）:

* `TrainingAPI/tests/test_rng.py`: Whole Runの2テストを1テスト（`test_whole_run_positive_rng_id_rejected_at_map_boundary`）に統合し、新しい拒否契約を検証するよう変更。8 passed, 0 failed（Combat側4テストは無変更）。
* `TrainingAPI/tests/test_root_protection.py`: Whole Runの3テストをEvent境界まで到達してから`emulate_action`するよう変更（`_reach_event`ヘルパー追加）。10 passed, 0 failed。
* `TrainingAPI/tests/test_e2e.py`: Whole Run E2Eシナリオを、Map境界の先のEvent境界まで到達してから分岐するよう変更。2 passed, 0 failed。
* `TrainingAPI/tests/test_fault_lifecycle.py`／`test_dto_validation.py`／`test_mask_audit.py`: 無変更、全PASS（6／16／4）。

**TrainingAPI全体合計: 73 tests, 0 failed**（Codex実行→独立再実行の両方で一致確認）。

## 8. 回帰結果

### Combat（26ファイル）

全PASS。既知の事前既存失敗以外に新規regressionなし:
* `test_restore_snapshot_phase3c1.py`: 26 passed, 2 failed（既知baseline）
* `test_scenario_v2.py`: 31 passed, 1 failed（既知の環境依存flake）
* その他24ファイル全PASS（`test_branch_manager.py`／`test_branch_worker_pool.py`／`test_worker_respawn.py`含む、Combat側は本タスクで一切変更していない）

### Whole Run（Run/tests、6ファイル）

全PASS。既知baseline以外に新規regressionなし:
* `test_whole_run_connectivity.py`: 4 passed, 1 failed（既知の`test_choice_branch_shop_holder_sibling_reproduction`、旧式単一プロセスrunner起因、無関係）
* その他5ファイル全PASS（`test_worker_pool_process_separation.py`含む — `Run/worker_pool.py`への変更が既存Worker Respawn/Lease機構を壊していないことを確認）

## 9. 停止条件チェック

| 条件 | 判定 |
|---|---|
| `GetEventRngState`／`SetEventRngState`だけでは同じHypothesisを再現できない | 該当なし（`derive_event_rng_hypothesis`は純関数、Worker Respawn後の再現性をテストで確認済み） |
| 異なる`rng_id`の正規なStateを安全に生成できない | 該当なし（SHA-256ベースの決定論的導出、xoshiro256**の縮退状態のみ防御的にガード） |
| 現在の公開Event状態や過去履歴が変化する | 該当なし（root保護テストで確認済み - Hypothesis生成・SimulationはrootのSnapshotを一切変更しない） |
| 同じ`rng_id`で候補間の公平性を保証できない | 該当なし（同一Hypothesisを共有する設計、§5のバグ修正で実際に保証を確認） |
| Event終了範囲を判定できない | 該当なし（`boundary=="event_choice"`かどうかで一意に判定） |
| Emulator側の契約変更が必要 | 該当なし（既存の`GetEventRngState`/`SetEventRngState`公開APIのみ使用） |

## 10. 変更ファイル

* `Run/run_emulator_bridge.py`（**バグ修正**: `SerializableRng`の実フィールド名`state0`〜`state3`に対応。公開dict形状`s0`〜`s3`は不変）
* `Run/worker_pool.py`（`ChoiceWorkItem.event_rng_override`／`event_rng_override_at_index`追加、`ChoiceReachResult.event_rng_state`追加、Worker実行時の適用ロジック追加 - いずれも加算的、デフォルト`None`で完全後方互換）
* `TrainingAPI/whole_run_event_rng.py`（新規、Codex初稿→独立検証）
* `TrainingAPI/instance_whole_run.py`（Hypothesis統合、境界拒否、深いBranch継承、Lifecycle解放）
* `TrainingAPI/validation.py`（`RequestRejected`に`fault_kind`属性を追加）
* `TrainingAPI/server.py`（`_rejected()`が`fault_kind`を伝播するよう変更）
* `TrainingAPI/dto.py`（`FAULT_RNG_HYPOTHESIS_UNSUPPORTED_AT_BOUNDARY`、`RNG_HYPOTHESIS_CAPABILITIES`追加）
* `TrainingAPI/tests/test_event_rng_hypothesis.py`（新規、純粋関数＋Registry単体テスト、Codex初稿）
* `TrainingAPI/tests/test_event_rng_hypothesis_integration.py`（新規、実Emulator統合テスト）
* `TrainingAPI/tests/test_rng.py`／`test_root_protection.py`／`test_e2e.py`（Whole Run部分を新しい境界制約に合わせて更新）

`Training/`の既存差分、Map Boundary chaining、Combat側RNG Hypothesis機構には一切触れていない。

## 補足: Codexの活用

`whole_run_event_rng.py`（純関数＋Registry）の初稿と単体テストをCodex CLIへ委任、独立再実行で12/12 PASSを確認。実装統合後の全テストスイート実行・デバッグ（DTO validation／Mask監査／RNG／root保護／Fault Lifecycle／E2E／本タスクの統合テスト、計8ファイル）もCodexへ委任し、結果を独立検証した。
