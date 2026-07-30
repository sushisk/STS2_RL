# RL担当 Phase 1実装状況報告 — LiveCombatSession — 2026-07-26

## 0. 結論

**RL側`LiveCombatSession`の実装には着手していない。** 統合順序(共同
Phase 1実装指示)の1-2番(Emulator側の境界判定/DecisionFrame実装、および
Emulator既存回帰テスト)が完了しておらず、3番(「RLが新DLLを参照して
LiveCombatSessionを実装」)の前提を満たしていないことを確認したため。
コード変更は一切行っていない。

---

## 1. 確認した現在のEmulator側状態

```text
git -C C:\STS2_Emulator rev-parse HEAD
  -> 722b019051e6f7ea368fef488abcc6451d6c9d47 (変更なし、旧baselineのまま)

Sts2Emulator.dll SHA256
  -> e3c3d26d7499e93e89f2718ccb51e18a2d66559021bbb5cdca33980bb644c036
     (choice_policy_adoption_v1_20260725.jsonに記録済みの値と完全一致 -
     722b019時点のDLLから変更なし)

git status --short
  -> ?? docs/reports/ のみ(未追跡の報告書ファイル群 - v0.1/v0.2回答
     ドキュメント。追跡対象のソースコードに変更なし)

git log --oneline -5
  -> 722b019が最新commit。AssertQuiescent()相当・combatSessionId・
     DecisionFrame関連のコード変更を示すcommitは存在しない。
```

**結論**: Emulator担当はv0.2回答(`combat_state_snapshot_snapshot_
contract_v0.2_emulator_response_20260726.md`)を提出した段階に留まって
おり、統合順序1番(Quiescent Decision Boundary判定・DecisionFrame基盤の
実コード実装)・2番(Emulator既存回帰テスト)はまだ着手されていない。
新しいDLLも存在しない。

## 2. v0.2回答が明らかにした未解決の設計上の矛盾(RLの実装判断に直結)

Emulator担当のv0.2回答2-A節は、v0.2契約書の`Quiescent Decision Boundary`
定義(`CurrentlyRunningAction == null`)が**`CardSelectCmd`経由の
pending choice(現行`choice_card`/`choice_confirm`/`choice_skip`の主要な
発生源)中は原理的に成立しない**という重大な自己矛盾を指摘している
(`InteractiveCardSelector.GetSelectedCards`が`ActionQueueSet.
PauseActionForPlayerChoice`を経由せず、`CurrentlyRunningAction`が
`Execute()`完了まで保持され続けるため)。

今回配布された「Canonical Combat State Contract v0.3」のQuiescent
Decision Boundary定義(1節)は、この指摘とほぼ一致する修正
(「`CurrentlyRunningAction`は`null`、または現在公開中のPendingChoice／
PendingTargetの完了だけを待つaction」)を既に反映しているように見える
——**ただしこれは契約書上の定義更新であり、Emulator側の実コード
(`AssertQuiescent()`相当の新設、`Step()`への組み込み)にはまだ反映
されていない**(1節の通り、ソースコードに変更なし)。

## 3. 統合順序との照合

共同Phase 1実装指示の統合順序:

```text
1. Emulator単体で境界判定とDecisionFrameを実装      <- 未完了(2節参照)
2. Emulator既存回帰テスト                              <- 未実施(1が未完了のため)
3. RLが新DLLを参照してLiveCombatSessionを実装          <- 本報告の対象、着手せず
4. Scenario 6546-21単体試験
5. Choice Context 21件
6. Choice Policy固定30 Scenario
7. teacher2000由来の固定subsetでライブ経路試験
8. 性能・Reset呼出回数比較
```

3番は明示的に「新DLLを参照して」実装するとされている——現在参照可能な
DLLは1節で確認した通り722b019時点のものであり、v0.3が要求する
`combatSessionId`/`AssertQuiescent()`/DecisionFrame基盤のいずれも
含んでいない。この状態で`LiveCombatSession`を実装した場合、

* `StepResult`のObservation/LegalActionsをそのまま信頼する設計の
  前提となる「quiescent境界の保証」が、Emulator側で未実装かつ
  設計段階で矛盾が見つかったばかりの定義に基づくものになる。
* 境界違反時に「診断可能な例外または明示的エラーを返す」という
  Emulator担当分の要件(共同指示「Emulator担当」節)がまだ存在しないため、
  RL側が誤った境界で制御を受け取っても検出する手段がない。

これは、この一連の共同作業全体が一貫して重視してきた「未検証の前提の上に
実装を積み上げない」という方針(監査input報告書・v0.1/v0.2回答の随所で
明示)に反するため、**現時点でのLiveCombatSession実装は見送る**。

## 4. RL側で今回準備した内容(コード変更を伴わない範囲)

* 1節の状態確認(commit/DLL hash/git status)を実施し、記録した。
* 2-3節の突合により、着手不可の理由を明確化した。
* 4節以降の受け入れテスト対象(Scenario `6546-21`、Choice Context、
  Choice Policy固定30 Scenario、teacher2000 subset)は、いずれも
  既存の成果物(`investigate_no_legal_actions_6546_21.py`、
  `choice_policy_online_eval_manifest.jsonl`、`choice_teacher_data_
  manifest.jsonl`)がそのまま再利用可能な状態にあることを確認した
  ——新DLL到着後、追加の準備作業なしに4-7番の試験を開始できる。

## 5. 次のアクション

新しいEmulator DLL(`AssertQuiescent()`相当の実装・`combatSessionId`・
DecisionFrame基盤を含み、Emulator自身の既存回帰テストに合格したもの)が
提供され次第、以下を実施する(現時点では未着手)。

1. 新DLLのcommit/hashを確認・記録。
2. `Combat/live_combat_session.py`(新規)に`LiveCombatSession`を実装
   (`rl_combat_state_contract_v0.2_response_20260726.md`5節で提示した
   変更対象ファイル一覧に沿う)。
3. `CombatEnv`を`LiveCombatSession`委譲へ変更(Heuristic探索経路は不変)。
4. 統合順序4-8番の試験を順次実施。

---

## 6. 報告事項(共同指示の要求フォーマットに沿って)

* **RL側commit**: `C:\STS2_RL`はgitリポジトリではないため、コミット
  ハッシュはなし(既存の全報告書と同じ制約)。
* **参照したEmulator DLL hash**: `e3c3d26d7499e93e89f2718ccb51e18a2d66559021bbb5cdca33980bb644c036`
  (722b019時点のまま、Phase 1向けの変更は未反映)。
* **変更ファイル**: なし(本報告書のみ新規追加)。
* **テスト結果**: 実施せず(実装着手前のため)。
* **相手(Emulator担当)が参照すべき報告書**:
  `C:\STS2_RL\Outputs\reports\rl_combat_state_contract_v0.2_response_
  20260726.md`(前回提出済み、Phase 1変更対象ファイル・受け入れテスト案を
  含む)、および本報告書。

Phase 2へは進まず、ここで停止する。Emulator側の実コード実装・DLL提供・
既存回帰テスト合格を待つ。
