# RL担当報告：RL–Training API実装

- RL基準commit（作業開始点）: `cc13130`
- Emulator基準commit: `fca2f06`
- 契約文書: `docs/contracts/rl_training_dto_documentation_v0_5.md`（v0.5、内容無変更でコピー配置）
- Trainingモデルの実装は対象外。Mock Training Clientで接続を検証。

---

## 0. サマリ

v0.5契約のRequest/Response DTO、Schema validation、Operation別必須項目検証、DTO version/mask version管理、8つのOperation、OSプロセス分離のAPI Runtime、Mock Training Clientを実装した。独立Combat instanceは契約の全機能（root進行・Branch生成・深いBranch連鎖・Cancel/Release・RNG Hypothesis対応）を完全実装。Whole Run instanceは同じ8 Operation・同じmasked DTO規則を共有しつつ、2点の意図的なスコープ縮小がある（§6で詳述、いずれも停止条件には該当しない）。

新規47 testが全てPASSし、既存の全回帰（Combat 26ファイル、Whole Run 6ファイル）は既知baseline以外の新規失敗なし。停止条件（7項目）はいずれも検出されなかった。

---

## 1. API構成図

```
Training プロセス（pythonnet/CLR 初期化なし）
  MockTrainingClient
      │  plain dict (JSON互換) を Queue 経由で送受信
      ▼
RLApiServerProcess（親側ハンドル、CLRに触れない）
      │  multiprocessing.get_context("spawn"), 非daemon
      ▼
RL Runtime プロセス（子、spawn）
  RLApiServer
      │  validate_request() → operation dispatch
      ▼
  Instance（instance_id ごとに1つ、CombatInstance or WholeRunInstance）
      ├─ root: LiveCombatSession / WholeRunSession を直接所有（このプロセス内で
      │        唯一のGameInstance構築 - Branch Workerは別プロセスなので競合しない）
      └─ Branch: 既存の実運用済みインフラをそのまま再利用
            Combat  → search.branch_manager.BranchManager
                       + search.branch_worker_pool.BranchWorkerPool
                       （Phase N: Worker Respawn / Lease / Cancel-Release 実装済み）
            Whole Run → worker_pool.WholeRunWorkerPool
                       （Phase K/L: 同種のRespawn/Lease機構、include_main_worker=False）
```

Trainingプロセスが直接触れるのは `TrainingAPI/api_runtime.py`（`RLApiServerProcess`）と `TrainingAPI/dto.py`（定数）のみで、いずれもCLR非依存。`TrainingAPI/server.py`／`instance_combat.py`／`instance_whole_run.py`（CLR依存）は子プロセスのエントリポイント内でのみ遅延importされる。

---

## 2. 契約文書

`docs/contracts/rl_training_dto_documentation_v0_5.md` は提供された文書と内容が完全一致することを`diff`で確認済み（無変更配置）。実装上の正本として全モジュールがこの文書のセクション番号をコメントで参照している。

## 3. Request／Response DTO・Schema validation・Operation別必須項目検証

* `TrainingAPI/dto.py`: `SCHEMA_VERSION="0.5"`、8 Operation定数、8 Status定数、fault_kind定数、`DTO_VERSION="emulator-fca2f06"`、`MASK_VERSION="1.0"`。
* `TrainingAPI/validation.py`: `validate_request(payload) -> dict`（成功時はpayloadをそのまま返す、失敗時は`RequestRejected`）。契約§2.2のOperation別必須項目表をそのままコードへ落とし込み、型検証・`simulation_options`検証（`stop_condition`許容値・各上限値の型）を含む。**設計判断（文書化）**: 未知の追加フィールドは無視（許容）し、未知の`operation`値・未対応`schema_version`・必須項目欠落/型不一致のみ`rejected`とする — バージョン間前方互換性を優先した保守的な選択。
* `TrainingAPI/identifiers.py`: `RequestLedger`（`request_id`重複排除・内容不一致時rejected）、`BranchIdRegistry`（`branch_id`永久非再利用、`"root"`予約済み）、`DecisionPointRegistry`（`decision_point_id`発行・stale検出）、`RngHypothesisTable`（`rng_id`→Hypothesis index の安定写像）。

## 4. DTO version／mask version管理

`masked_emulator_dto`には常に`dto_version`（Emulator commit紐付け文字列）と`mask_version`（マスク規則のバージョン）を付与（`TrainingAPI/masking.py::build_masked_emulator_dto`）。両者はCombat/Whole Run間で完全に同一の規則・同一の値を使う。

## 5. masked Emulator DTOビルダー

`TrainingAPI/masking.py`。Part Aの監査結果（`rl_dto_exposure_audit_20260803.md`）に基づき、**名前ベースの再帰的scrub**方式を採用（フィールドを1つずつ許可リスト化する方式ではなく、禁止パターン名を含むキーをツリーのどこにあっても除去する方式）。理由: 監査（Mask監査テスト）と実装が同じ禁止リストを共有でき、両者が乖離しない。

* Draw/Discard/Exhaust PileをMultiset化（順序破棄）。
* Play Pile削除（mask version 1.0）。
* `transition.final_observation`にも同一関数を再帰適用（テストで明示検証）。
* Snapshot/RNG内部状態/Worker ID/PID/generation/Lease/Replay Prefix/内部Context識別子/CombatSessionId/事前生成Queue cursor・順序/将来Event・Encounter・Boss・Ancient情報を除去。
* `Metrics`/`Extras`/`Info`はallowlist制（Part A監査時点で未検証の自由形式内容のため、現時点のallowlistは空 = 空dictへ縮退。Training側で必要なサブキーが確定次第拡張する設計）。
* Emulator固有Combat Reward（`reward`キー）を除去。

過去のEvent／Encounter履歴（`TrainingAPI/history_builder.py`）は実際に観測した`RoomContext`（訪問済み座標・RoomType）とEvent選択肢から逐次構築し、事前生成列からは一切参照していない。

## 6. 実装Operationとスコープに関する設計判断

### 6.1 共通実装

8 Operation全て`TrainingAPI/server.py`で実装。`start_instance`はRLが`instance_id`を発行しroot（`branch_id="root"`, `rng_id=0`）の最初のDecisionを返す。`get_decision`は状態を一切進行させない（`test_root_protection.py`で反復呼び出しにより検証）。`commit_action`はroot Worker（Instance自身が直接保持するセッション）上でActionを再実行し、Branch状態はrootへ一切移植しない。成功後、その時点で追跡している全Branchを機械的にCancel＋Release（Lease無効化含む）。`emulate_action`は`parent_branch_id`から新しい`branch_id`を作成し、親とrootを変更しない。

### 6.2 Combat instanceでの`rng_id`→Hypothesis対応

既存の`search.rng_hypothesis`（DrawPile順序Belief機構）を再利用する`TrainingAPI/combat_rng_mapping.py`を新規実装。既存の`search_coordinator.dispatch_explicit_candidates`は候補ごとに`hypothesis_count`個のHypothesisを自動展開して集約する設計（Phase M確認済み: 2候補×4Hypothesis=8WorkItem）であり、これは「RLは内部で候補比較や勝者選択をしない」というv0.5の`emulate_action`契約と両立しない。そこで`generate_belief_hypotheses(multiset, count=n)`が純関数であること（同じ入力なら同じ出力の先頭n件を返す）を利用し、要求された`rng_id`に対応する1件だけを生成・使用する専用パスを実装した。`(parent_branch_id, decision_point_id, rng_id)`の識別単位は契約§3のとおり`RngHypothesisTable`で管理する。

### 6.3 Whole Run instanceの2つの意図的なスコープ縮小（文書化・停止条件には非該当）

1. **`rng_id`にBelief機構が存在しない**: Whole RunのRoom/Event/Shop/Rest進行は`(map_snapshot, room_id, action_prefix, action_id)`が決まれば完全に決定的であり、Combatのような「隠れた順序に対する複数の尤もらしい仮説」という概念自体が存在しない。したがって同一親Decision内のどの`rng_id`も同一の（唯一の）結果に写像される。これは契約の「同じrng_idは同じHypothesis」「異なるrng_idは異なるHypothesis」を字義通り満たす（Hypothesisが常に1つしかない）ため、契約違反ではないが、Combatとは異なる挙動である旨を明記する。
2. **Branch側からの新規Map境界を跨ぐ多段分岐は未対応**: `emulate_action`はWhole Run既存の`WholeRunWorkerPool`（Map Snapshot＋room_id＋action_prefixからのBootstrap方式）をそのまま再利用しており、rootは`save_state()`を直接呼べるため何度でも新しいMap Snapshotを再取得できるが、Branch（別プロセスのWorker内で完結）はSnapshot取得の追加往復が組み込まれていないため、Branchの結果が新しいMap境界に到達した場合はその後の`emulate_action`をrejectする（`chain_blocked`）。root自身の連鎖には制限なし。

いずれも「Whole RunとCombatで共通Responseを維持できない」の停止条件には該当しない（Response形状・Status語彙は完全共通）。将来的な対応には、Whole Run用の非同期submit/poll層（`search.branch_manager`のWhole Run版）の新規実装が必要になる見込み。

### 6.4 Branch Cancel/Release実装の再利用

Combat側はPhase Nで構築済みの`BranchManager`（非同期submit/poll、Worker kill+respawnによるrunning Cancel、Lease無効化）をそのまま利用。Whole Run側は`WholeRunWorkerPool.dispatch_choice_work_items`が同期ブロッキング設計のため、Branchが外部から観測可能な`running`状態を持たず、Cancel/Releaseは既に確定した結果への状態遷移のみで実装した（Worker kill不要 - 実行自体が`emulate_action`の応答内で完結するため）。

## 7. Mock Training Client

`TrainingAPI/mock_training_client.py`。モデル推論なし、明示的なaction_idまたはLegal Action index指定のみ。`start_instance`/`get_decision`/`commit_action`/`emulate_action`（root起点の複数Branch、Branchからの深い分岐、同一rng_idでの複数Action比較、複数rng_idでの同一Actionシミュレーション）/`get_branch_status`/`cancel_branches`/`release_branches`/`close_instance`を全てカバー。`_demo_combat()`が実際にこれら全操作を実行するデモを兼ねる。

## 8. 必須テスト結果

新規テストスイート `TrainingAPI/tests/`（6ファイル、**47 tests、全PASS**）:

| ファイル | 件数 | 内容 |
|---|---|---|
| `test_dto_validation.py` | 16 | Operation別必須項目、未知operation/schema_version、型検証、未知フィールド許容、simulation_options検証、RequestLedger/BranchIdRegistry/DecisionPointRegistry直接検証 |
| `test_mask_audit.py` | 4 | 全Response再帰監査（禁止キー不在、Pile Multiset化、`transition.final_observation`再帰適用、非破壊性） |
| `test_root_protection.py` | 10 | get_decision/emulate_actionでのroot不変、子Branch作成での親不変、commit_actionのみがroot進行、Branch状態の非移植、commit後の旧Branch全解放、rootのCancel/Release拒否（Combat/Whole Run双方） |
| `test_rng.py` | 9 | 同一rng_id→同一Hypothesis、異なるrng_id→異なるHypothesis、rng_id=0拒否、非rootでの親と異なるrng_id拒否、RNG内部情報の非漏洩（Combat/Whole Run双方） |
| `test_fault_lifecycle.py` | 6 | Worker timeout→faulted＋Pool継続動作、release後のget_decision/emulate_action拒否、Holder Lease無効化、close_instance冪等性、Worker全解放、Training切断（close_instance未送信での強制終了）でも全資源解放 |
| `test_e2e.py` | 2 | Combat/Whole Run双方でstart→root Decision→複数emulate_action→深いBranch→Cancel/Release→commit_action→次Decision→close_instanceの8段階を実プロセス経由（`api_runtime.RLApiServerProcess`）で完走 |

いずれも実Emulator・実OSプロセス分離（`multiprocessing.get_context("spawn")`）に対して実行し、モックやスタブは使用していない。

## 9. Hidden Information監査

`test_mask_audit.py`が`masking.py`の禁止キーリストを直接importして監査に使う設計のため、実装と監査が構造的に乖離しない。加えて`test_rng.py::test_no_rng_internal_state_leaks_into_combat_response`でRNG関連キーの非存在を独立に確認、`test_root_protection.py`でBranch/root間の状態非漏洩を確認。既知の未解決6件（Metrics/Extras/Info自由形式内容、playPile可視性等）はPart A監査（`rl_dto_exposure_audit_20260803.md`§4）のとおり据え置き — 本パスでは保守的に空allowlistとして扱っている。

## 10. Combat／Whole Run E2Eログ

`TrainingAPI/tests/test_e2e.py`実行結果（全回帰の一部として記録、生ログは`/c/STS2_RL`のテスト標準出力）:

```
PASS test_e2e_combat_full_sequence
PASS test_e2e_whole_run_full_sequence
2 passed, 0 failed
```

両ケースとも実際の`RLApiServerProcess`（別OSプロセス、real spawn）を介して8段階のシーケンスを完走し、途中のBranch状態遷移（completed→cancelled→released）を`get_branch_status`で確認している。

## 11. 全回帰結果

### Combat（26ファイル、既存＋新規2ファイル）

全PASS。既知の事前既存失敗以外に新規regressionなし:
* `test_restore_snapshot_phase3c1.py`: 26 passed, 2 failed（既知baseline）
* `test_scenario_v2.py`: 31 passed, 1 failed（既知の環境依存flake）
* その他24ファイル全PASS（`test_branch_manager.py` 11、`test_branch_worker_pool.py` 11、`test_worker_respawn.py` 6 含む — Part B/Cの資産も無傷）

### Whole Run（Run/tests、6ファイル）

全PASS。既知baseline以外に新規regressionなし:
* `test_whole_run_connectivity.py`: 4 passed, 1 failed（既知の`test_choice_branch_shop_holder_sibling_reproduction`、旧式単一プロセスrunner起因、無関係）
* その他5ファイル全PASS

### TrainingAPI（新規6ファイル）

47 passed, 0 failed（内訳は§8参照）。

**Worker Respawn／Branch Cancel／Release／Pending／Lease／Start-of-Combat Pending／external_control／zero_index**はいずれもPhase N以前に実装済みの既存インフラをそのまま再利用しており、本パスでのコード変更は`Combat/search/branch_worker_pool.py`への1点のみ（§12参照）。上記全回帰でこれらの既存テスト（`test_branch_worker_pool.py`／`test_worker_respawn.py`／`test_branch_manager.py`／`test_execution_mode.py`／`test_external_control_decision_types.py`）が全てPASSしていることをもって無regressionを確認した。**1,000件混合耐久試験**はPhase Nで既に実施済み（`branch_manager_endurance_runner.py`、violation 0件）であり、本パスではTrainingAPI層がその同じ土台をラップするだけの薄い層であるため独立再実行はしていない（変更のあった`branch_worker_pool.py`の変更内容は§12のとおり純粋加算的でPhase Nの既存試験群でカバー済み）。

## 12. 変更した既存ファイル

`Combat/search/branch_worker_pool.py`: `BranchResult`に`next_legal_actions: Optional[list] = None`フィールドを追加し、`_build_success_result`のStable境界成功時に`next_state._cached_legal_actions`を格納するようにした。目的: CombatのBranchから**さらに** `emulate_action`で分岐する（Branchの結果がStable境界だった場合の連鎖）ために、次のDecisionのLegal Actionsを取得する専用の追加dispatchを避けるため。デフォルト`None`で完全後方互換、既存の全Combat回帰がPASSすることを確認済み。

## 13. 停止条件チェック

| 条件 | 判定 |
|---|---|
| v0.5の項目だけでは状態を一意に指定できない | 該当なし |
| `rng_id`と既存Hypothesis機構を対応できない | 該当なし（Combat: 既存機構を直接再利用して対応。Whole Run: 機構が存在しないこと自体を契約は禁じておらず、§6.3のとおり文書化のうえ実装） |
| 同じ`rng_id`で候補間の公平性を保証できない | 該当なし（`generate_belief_hypotheses`の純関数性により保証） |
| Commit後のBranch一括無効化で別instanceへ影響する | 該当なし（`instance_id`ごとに完全に独立したBranchManager/WorkerPoolを保有） |
| Whole RunとCombatで共通Responseを維持できない | 該当なし（Response形状・Status語彙は完全共通、§6.3で相違点を明記） |
| Hidden Information除去にEmulator変更が必要 | 該当なし |
| API実装のためTrainingモデル側の変更が必要 | 該当なし |

## 14. 成果物・コミット

* `docs/contracts/rl_training_dto_documentation_v0_5.md`（契約文書、無変更配置）
* `TrainingAPI/`（新規パッケージ、12モジュール＋6テストファイル）
* `Combat/search/branch_worker_pool.py`（`next_legal_actions`フィールド追加）
* `Outputs/reports/rl_training_api_implementation_20260804.md`（本レポート）

`Training/`配下の既存の未コミット差分には一切触れていない。

## 補足: Codexの活用

ユーザーの指示に基づき、DTO Validationテストとmasked DTOの再帰監査テストの初稿をCodex CLI (`codex exec`) に委任した。Codex自身の完了報告（「12 passed, 0 failed」）は独立再実行で誤りと判明し（実際は複数の内部ヘルパーのバグにより9件失敗）、`test_dto_validation.py`側の複数バグ（フィールド名解決ヘルパーの誤設計によるペイロードキー破損、RequestLedgerのAPI呼び出し不整合、simulation_options検証を実際には検証しないOperationへの誤適用）を人手で特定・修正した。`test_mask_audit.py`は独立検証でも一発PASSだった。副作用として`README.md`に無関係な1行（`testfromcodex`）が混入していたため`git checkout`で復元済み。
