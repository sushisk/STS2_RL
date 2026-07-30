# RL担当 回答 — Canonical CombatStateSnapshot v0.2(2026-07-26)

対象: 「RL・Emulator共通契約案 — Canonical CombatStateSnapshot v0.2」8節
「今回の回答事項」5項目。本書は**回答のみ**であり、コード・schema・
データの変更は一切行っていない。

必須参照として指定された`rl_emulator_combat_state_contract_v0.1_
20260726.md`(前回のRL回答)、および
`C:\STS2_Emulator\docs\reports\combat_state_snapshot_contract_v0.1_
emulator_response_20260726.md`(Emulator担当のv0.1回答)を確認した上で
回答する。

---

## 1. v0.2を承認できるか

**承認する。** Emulator担当のv0.1回答内容とv0.2の記述に矛盾はなく、
特に以下の点でv0.2はv0.1回答を正しく反映していると判断する。

* Emulator担当が「ActionQueue空」は現状「空、または次のready actionが
  non-player-driven」という弱い条件しか保証できないと回答した点
  (v0.1回答1節)を受け、v0.2のQuiescent Decision Boundary定義
  (2節)は「queue内に要素が残る場合、それらは公開中のChoiceだけを
  待っている」という、より正確な条件へ更新されている——単純な
  「ActionQueueが空」という以前の表現より厳密で、Emulator側の実装実態
  (`WaitUntilQueueIsEmptyOrWaitingOnNonPlayerDrivenAction`)に即している。
* Emulator担当が「StepResult.LegalActions正式保証」を
  ライブ実行限定で承認した点(v0.1回答5節)は、v0.2の1節「採用事項」
  および3節Phase 1の記述と整合している。
* Emulator担当が「RestoreSnapshotはStartTurn経路なしでは実現不能、
  独立設計フェーズが必要」と回答した点(v0.1回答4節)は、v0.2が
  RestoreSnapshotをPhase 3として明確に分離し、「このPhaseが合格するまで
  `SnapshotBranchEvaluator`を正式利用しない」としている点と整合している。

---

## 2. 技術的に矛盾する項目

**ハードな矛盾は見当たらない。** ただし以下3点は、今後の実装フェーズで
誤解を招かないよう明確化を提案したい(いずれも「矛盾」ではなく
「精度を上げるべき記述」)。

### 2-A. 「毎decisionのrestore廃止」の適用範囲

v0.2の3節「Phase 1」は「毎decisionのrestore廃止」「このPhaseでは
ビームサーチやHeuristic分岐経路を変更しない」の両方を記載している。
この2つは矛盾しないが、**「毎decisionのrestore廃止」はライブ実行
(実際にcommitされる進行)に限定される**ことを明記すべきだと考える
——`HeuristicAgent.choose_action_with_detail()`の候補スコアリングは
Phase 1でも従来通りrestoreベースのまま残るため(v0.2自身がそう明記して
いる)、「毎decision」という表現だけを読むと全経路でrestoreが消える
ように誤解されうる。既存の`state_restore_coverage.csv`/`known_risks.md`
を参照する読者向けに、v0.2本文へ「ライブ経路(LiveCombatSession)のみ」
という限定を追記することを提案する。

### 2-B. Phase 1とCombatHistory/Snapshotの依存関係

v0.2の5節はCombatHistoryを「完全Snapshotの必須情報」としているが、
Phase 1(3節)はSnapshot自体を一切生成しない設計であるため、**Phase 1は
CombatHistoryのCapture/Restore実装(Emulator回答2節、Restoreに新規
識別子変換層が必要という最も重い項目)に一切依存しない**——これは
矛盾ではなく、3番目の質問(Phase 1の先行可否)を支持する重要な確認事項
として明記しておきたい。

### 2-C. `CombatState.Clone()`が存在しないこと(性能面の申し送り)

Emulator回答6節が指摘した通り、`CombatState`自体の軽量ディープクローンは
現状存在せず、分岐評価(`SnapshotBranchEvaluator`、Phase 2/3後も含む)は
引き続き**フルrestoreのコスト**を伴う。契約自体とは矛盾しないが、
`call_count_summary.csv`で確認済みのHeuristic評価コスト(1decisionあたり
L×T+2回のrestore)は、本契約が完全実装された後も**改善されない**
という点を、性能期待値のすり合わせとして明記しておきたい。

---

## 3. Phase 1をSnapshot実装より先行できるか

**先行できる、と判断する。** 根拠:

1. Phase 1が依存する唯一のEmulator側確認事項(「`StepResult.
   LegalActions`は正式に保証できるか」)は、Emulator担当のv0.1回答
   5節で**既に承認済み**(ライブ実行限定、既知の限界2点付き)。
2. Phase 1はCombatStateSnapshot DTO・CombatHistory Restore・Power/Relic
   serializer(Phase 2/3の中核、Emulator回答2-4節で最も重い課題として
   指摘されている項目)のいずれにも依存しない(2-B節参照)。
3. Phase 1で導入する`DecisionFrame`は、**新しいEmulator側APIを必要と
   しない**——`combat_state_schema.json`が既に文書化している
   `stepIndex`(「Monotonically increasing count of Step() calls
   processed by this GameInstance since Reset/ResetFromScenario」)を
   そのままDecisionFrameの実体として転用できる。前回のRL回答(v0.1
   10-E節)では新しいトークン概念の導入を提案したが、**既存の
   `stepIndex`フィールドで代替可能**であることを今回追加で確認した
   ——実装コストがさらに小さくなる方向の訂正として記載する。

**条件**: Emulator担当が回答5節で明記した「既知の2つのギャップにのみ
対応、独立検出の仕組みはない」という限界(=queueの完全空状態は
未検証)は、Phase 1のacceptance test(5節)で実際に検証する必要がある
——「先行できる」は「無条件に安全」という意味ではなく、「Snapshot実装の
完了を待つ必要がない」という意味に限定する。

---

## 4. Quiescent Decision Boundaryを検証する具体的方法

Emulator内部への计装(instrumentation)を伴わない、**RL側から
ブラックボックスで検証できる方法**を提案する(Emulator側の追加実装が
承認されるまでの暫定的検証手段としても使える)。

1. **二重取得による不動性チェック**: `Step()`が返した`Observation`/
   `LegalActions`を受け取った直後、**追加の`GetObservation()`/
   `GetLegalActions()`呼出をもう一度行い**、結果が完全一致することを
   確認する(v0.2 4節の「Pythonへ返却後、次のStepまで状態が自律変化
   しない」の直接検証)。恒久コードにはcommitしない診断モードとして
   実装し、既存の200/30 Scenario regression manifestに対して実行する
   (共同作業指示の「診断用ログ...revert確認を報告する」制約を遵守)。
2. **`stepIndex`の安定性チェック**: 上記の二重取得で、2回とも
   `stepIndex`が同一値であることを確認する——`stepIndex`は
   「このGameInstanceが処理したStep()呼出数」というドキュメント済み
   の意味を持つため、2回の問い合わせの間に値が変化していれば、
   Python側が制御を受け取った後もエンジン内部で何らかのStepが
   自律的に進行したことの動かぬ証拠になる(安価な一次スクリーニング、
   1のフル比較より軽量)。
3. **Scenario 6546-21を専用回帰ケースとして使用**: 監査input報告書で
   確認済みの、実際に異常終了した既知のScenario
   (`Combat/evaluation/online_eval/investigate_no_legal_actions_
   6546_21.py`が既に最小再現手順として存在する)を新しいboundary
   チェックに通し、(a) 新チェックが「返却しようとしていた時点は実は
   quiescentでなかった」と検出できるか、(b) 検出できない場合は
   「新チェックの網羅性が不十分」という重要な知見としてEmulator担当へ
   即座にフィードバックする、という位置づけで使う。
4. **`legal_actions`空チェックの意味論的裏付け**: boundary検証に合格した
   状態で`legal_actions`が空になるケースが(6546-21以外にも)発生するか
   どうかを、既存の`choice_policy_online_eval_manifest.jsonl`
   (30 Scenario)+`choice_teacher_data_manifest.jsonl`(200 Scenario)の
   再実行で確認する——サンプル数を活かした経験的検証。

---

## 5. Phase 1の変更対象ファイルと受け入れテスト案

### 変更対象ファイル(RL側、提案のみ・今回は変更しない)

| ファイル | 変更内容 |
|---|---|
| `Combat/live_combat_session.py`(新規) | `LiveCombatSession`クラス — `start_combat()`/`step()`/`capture_snapshot()`。`Step()`のObservation/LegalActionsをそのまま次decisionへ引き継ぎ、`ResetFromScenario`を戦闘開始時の1回のみに限定する |
| `Combat/env/combat_env.py`(変更) | `CombatEnv.get_legal_actions()`/`step()`の内部実装を`LiveCombatSession`委譲へ置き換え。`battle_state`プロパティ(Heuristic探索用のescape hatch)は現状のまま維持——Phase 1ではHeuristic分岐経路を変更しないため |
| `Combat/battle_emulator.py`(変更なし、または軽微な共有コード抽出のみ) | `apply_action()`/`_restore()`自体は`HeuristicAgent`の候補スコアリング用に現状維持。`_wrap()`のto_plain変換ロジックのみ`LiveCombatSession`と共有できるよう抽出する可能性はある |
| `Combat/policy_agent.py`/`Combat/choice_policy_agent.py`(変更) | `LiveCombatSession`が返すObservationの形状を、既存`engine_state`辞書と同一に保つことで変更を最小化する方針(4節の通り、大規模な型分離はPhase 1では行わない) |
| `Combat/evaluation/online_eval/*.py`(変更) | `CombatEnv`の内部実装差し替えに追従するのみ、harness自体のロジック変更は不要な設計を目指す |
| `Common/schemas/combat_state_schema.json`/`legal_action_schema.json`(ドキュメント更新のみ) | 未文書化の`pendingChoice`関連フィールド(`originEntityType`等)を正式化(v0.1提案0節/v0.2の「採用事項」に対応) — コード変更ではなくschemaドキュメントの追記 |

### 受け入れテスト案

1. **並行diff検証**: 既存(fresh-restore)経路と新`LiveCombatSession`経路を
   同一manifest(既存の`unused_200_manifest.jsonl`/
   `choice_policy_online_eval_manifest.jsonl`等)に対して並行実行し、
   `Observation`/`LegalActions`/最終`outcome`/`HP`/decision数が
   完全一致することを確認する(差分0が合格基準)。
2. **Scenario 6546-21回帰テスト**: 新経路で同一Scenarioを実行し、
   4節のboundary検証で異常が事前検知されるか、またはEmulator側修正後に
   正常完走することを確認する。
3. **DecisionFrame(`stepIndex`)検証**: 古い`stepIndex`に紐づく
   `action_id`を新しい`stepIndex`に対して送信した場合、即座にエラーに
   なることを確認する単体テストを追加する。
4. **呼出回数の実測確認**: `call_count_summary.csv`で概算した
   「通常armは1decisionあたり2回のResetFromScenario」が、新経路では
   「エピソードあたり1回のみ」に減ることを、実測(ログカウント)で
   確認する——Heuristic探索経路(変更なし)の呼出回数は不変であることも
   併せて確認する。
5. **既存回帰テストの継続合格**: `Combat/tests/test_scenario_v2.py`・
   `Combat/tests/test_choice_semantics.py`が全件passすることを維持する。

---

以上、v0.2の5項目への回答。RLコード・schema・データの変更、Snapshot実装
はいずれも行っていない。Emulator担当の回答・監督者確認を経るまで、
これ以上の実装作業には進まない。
