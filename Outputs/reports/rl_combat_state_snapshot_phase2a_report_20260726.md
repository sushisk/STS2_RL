# RL担当 Phase 2A実装報告 — CombatStateSnapshot Capture基盤(2026-07-26)

対象: 「RL・Emulator共同作業指示 — Phase 2A CombatStateSnapshot Capture基盤」
「RL担当 Phase 2A実装指示」。実装事項1〜5を完了し、ここで報告のため停止する。
`RestoreSnapshot`・`SnapshotBranchEvaluator`・既存経路(Policy/Value入力・
Choice Policy・LiveCombatSessionのStep処理・Heuristic・beam-search・
lookahead・legacy_approximate_restore・trajectory生成)のいずれにも
着手・変更していない。

---

## 0. 結論

Emulator担当のPhase 2A実装(commit `5766528`、DLL `b656b0f2...`)を確認した
上で、RL側の受け皿(Python型・Capture呼出ラッパー・保存形式・突合試験)を
実装した。**C# Snapshot JSONはPythonで損失なく読み書き可能、Capture前後で
live Observation/LegalActionsは不変、Scenario 6546-21は毎decision Capture
してもPhase 1と同一の49 decision・victoryを維持、completeness/unsupported
情報はEmulator側の判定をそのまま伝播(格上げなし)することを確認した。
Phase 1回帰(52/52)も合格。Phase 2B/3へは進まず、ここで停止する。**

---

## 1. 参照情報の確認

| 項目 | 基準情報(指示書) | 実際に確認した値 |
|---|---|---|
| Emulatorコード(Phase 1) | `ce7ecc2` | 一致(祖父commit) |
| Emulator契約参照commit | `f7a0ac8` | 一致(親commit) |
| 契約正本SHA256 | `6c0a9ecc228099f878b99d8eb43b1e095e6b2184b48e5aed2ab14920008757c9` | 一致(現在の`combat_state_contract.v0.3.md`と再計算で確認済み) |
| **Emulator Phase 2A実装commit** | (指示書の基準情報はPhase 1時点のもの) | `5766528a3311c7fd3e65918662d38bd8888f7707` |
| **Emulator Phase 2A報告commit** | — | `eeef0a3cbfa841a44150b546914a17df6d22ed47`(現HEAD) |
| **Phase 2A DLL SHA256** | — | `b656b0f214fdf01477d2829a2eed0e74698ad1c4d9fb7fa37ff403525a9fee34`(Phase 1の`041a44cc...`とは異なる — 新DLL) |

`git -C C:\STS2_Emulator log --oneline`で
`eeef0a3`(Phase 2A報告)→`5766528`(Phase 2A実装)→`f7a0ac8`(契約固定)→
`a4c3c02`→`ce7ecc2`→`722b019`の順を確認し、Emulator担当のPhase 2A報告書
(`combat_state_snapshot_phase2a_emulator_report_20260726.md`)を読了した
上で統合を開始した(指示書「Emulator担当のDTOとJSON形式が確定してから
統合を開始すること」を遵守)。

---

## 2. RL側実装内容

### 2-1. Python型(`Combat/combat_state_snapshot.py`、新規)

C# `Sts2Emulator.Dto.Snapshot.*`(全12型)と1対1対応するPython
dataclassesを新設: `CombatStateSnapshot`・`SnapshotMetadata`・
`UnsupportedSnapshotField`・`PlayerSnapshot`・`EnemySnapshot`・
`CardInstanceSnapshot`・`RelicSnapshot`・`PowerSnapshot`・`PotionSnapshot`・
`OrbSnapshot`・`SerializableRngSnapshot`・`RngSnapshotSet`・
`CombatHistoryEntrySnapshot`・`CombatHistorySnapshot`。

**フィールド名はPascalCaseのまま**(Emulator担当報告書5節の指摘通り
`JsonOptions`にnaming policyがないため、JSON側もPascalCase — snake_caseへの
変換は行わず、C#プロパティ名をそのまま踏襲。損失・誤変換リスクを避けるため)。
`BattleState`/Observation(Phase 1)とは完全に別型 — 既存コードのどこからも
importされない。

### 2-2. JSON検証

`SnapshotMetadata.from_dict()`/`CombatStateSnapshot.from_dict()`が:

* `SchemaVersion`必須、既知集合`{"phase2a.1"}`外は`SnapshotValidationError`
  で拒否。
* `Completeness`/`CaptureBoundary`も既知の値集合外は拒否。
* 必須フィールド欠落は`SnapshotValidationError`で拒否
  (`_require()`ヘルパー)。
* 未知フィールドは`unknown_fields`辞書に記録するのみ(拒否しない)。
* `completeness`は**Python側で一切格上げしない**——
  `completeness_is_complete()`はEmulatorの値をそのまま読むだけ。
* `null`と欠落キーの区別: `.get()`使用箇所は全て「キー自体が存在しない
  場合のみNone」という既存Pythonの意味論に従い、Emulator側が明示的に
  `null`を書いた値との混同はない(C#→JSON→Python変換で両者は区別可能な
  形のまま渡ってくる)。

### 2-3. Capture呼出ラッパー(`LiveCombatSession.capture_snapshot()`)

`Combat/live_combat_session.py`へ**1メソッドのみ追加**
(既存`start_combat`/`resume_from`/`step`/`_is_still_current`/
`_resynchronize`は無変更)。

* `GameInstance.CaptureSnapshotJson()`を呼び、`CombatStateSnapshot.
  from_json()`でパースして返す。
* Emulator側の`AssertQuiescentDecisionBoundary`を再利用しているため、
  境界外での呼出は`QuiescentBoundaryViolation`(Phase 1で確立済みの
  Python例外型)を送出——新しい例外型は増やしていない。
* **通常decisionループでは呼ばれない**——`step()`/`start_combat()`/
  `resume_from()`のいずれからも`capture_snapshot()`への内部呼出は追加
  していない。呼出は本報告の検証スクリプト等、明示的な呼出元のみ。
* Snapshotは`BattleEmulator.apply_action()`へは一切渡していない
  (指示通り)。

### 2-4. 保存形式(`Combat/evaluation/online_eval/capture_snapshot_diagnostic.py`、新規)

`save_snapshot_with_envelope()`が、Capture結果を以下を併記した
JSONファイルへ保存する:

```text
schemaVersion / contractPath・contractSha256 / emulatorCommit・
emulatorDllSha256 / rlSourceManifestPath・rlSourceManifestSha256 /
decisionFrameAtCapture(combat_session_id/step_index/continuation_step_index) /
snapshot(Snapshot本体)
```

サンプル出力: `Combat/evaluation/online_eval/snapshot_diagnostic_sample.json`
(Scenario `302-13`、decision 0時点、`completeness=complete`・
`captureBoundary=published_choice`を確認)。

### 2-5. 突合試験・受け入れテスト(`Combat/evaluation/online_eval/
verify_snapshot_phase2a.py`、新規)

---

## 3. テスト結果

### 3-A. 突合試験(生Emulator Observation／Snapshot public projection／RL既存Observation)

`session._game.GetObservation()`(RLラッパーを経由しない生の呼出)・
`session.capture_snapshot()`・`env.battle_state.engine_state`の3者を
同一時点で比較(HP/Block/Energy/Stars/Gold/各pile/relics/powers/potions/
orbs/enemies/turnNumber/combatRoundNumber/pendingChoice.choiceType/terminal)。

**初回実行で1件の不一致を検出**(`pendingChoice.choiceType`: raw/RL側は
`"GamblingChipDiscard"`、Snapshot側は`None`)——調査の結果、**本検証
スクリプト自身のバグ**と判明(`PendingChoice`は`CombatStateSnapshot`の
既存`BuildPendingChoiceDict()`をそのまま流用する`Dictionary<string,
object?>`のパススルーであり、Snapshotの他フィールドと異なりcamelCaseの
キー(`"choiceType"`)のまま——検証スクリプトが誤って`"ChoiceType"`
(PascalCase)を参照していた)。実際の生JSONを確認しキーを修正した結果、
**全項目で3者完全一致**を確認した(Snapshot Capture自体に不良はなかった)。

### 3-B. Capture副作用ゼロ

`capture_snapshot()`呼出前後で`LegalActions`・`engine_state`が完全一致
することを確認(PASS)。

### 3-C. 再シリアライズ一致

同一境界で2回連続Capture → 正規化後(`SnapshotId`/`CapturedAtUtc`除外)
JSON辞書が完全一致することを確認(PASS)。

### 3-D. Scenario `6546-21`(毎decision Capture)

Phase 1で確定した49 decision・victoryの経路に対し、**毎decisionで
`capture_snapshot()`を追加実行**(結果は破棄、決定には一切使わない)。

* decision数: **49**(Phase 1と同一)
* 最終結果: **victory**(Phase 1と同一)
* Capture実行回数: **49**(全decisionで実行、例外なし)
* Completenessは`complete`と`partial_known_gaps`の両方が動的に出現
  (`CardPlay.Resources`未対応によるもの、Emulator報告書2-G節の通り)

**Captureの追加によるライブ挙動の変化なし**(PASS)。

### 3-E. completeness/unsupportedの伝播確認(追加検証)

10 Scenarioの毎decision Captureで`completeness`値を収集した結果、
`{"complete", "partial_known_gaps"}`の両方が実際に動的に出現し
(固定値ではないことを実証)、`partial_known_gaps`時の`UnsupportedFields`
(`fieldPath="combatHistory.entries[].fields.cardPlay.resources"`、
`status="partial_known_gaps"`、`reason`にEmulator側の説明文)が正しく
Python側`UnsupportedSnapshotField`へパースされることを確認した——
Python側が`completeness`を`complete`へ格上げする箇所は存在しない
(`completeness_is_complete()`はEmulatorの値をそのまま返すのみ)。

### 3-F. Phase 1回帰

`test_scenario_v2.py` + `test_choice_semantics.py`: **52/52 passed**
(Phase 1報告時と同数、regressionなし)。

---

## 4. 既存経路の無変更確認

* `Combat/policy_agent.py`・`Combat/choice_policy_agent.py`・
  `Combat/heuristic_agent.py`・`Training/sts2_training/encoding.py`
  (Policy/Value/Choice Policy入力): 一切編集していない。
* `Combat/live_combat_session.py`の`start_combat`/`resume_from`/`step`/
  `_is_still_current`/`_resynchronize`: 一切編集していない(新規メソッド
  `capture_snapshot()`の追加のみ、既存メソッドの内部ロジック変更なし)。
* `Combat/env/combat_env.py`: 無変更。
* beam-search/lookahead: 該当ファイルを未読・未編集。
* `legacy_approximate_restore`経路(HeuristicAgentの候補評価): 無変更。
* 既存trajectory生成(`generate_heuristic_trajectories.py`・
  `generate_choice_teacher_data.py`): 無変更。

---

## 5. 変更・新規ファイル一覧

| ファイル | 状態 | SHA256 |
|---|---|---|
| `Combat/combat_state_snapshot.py` | 新規 | `7e58240712c5d8094c563932bf4219cc04ba7747b457029dbd01d045b1a50cc7` |
| `Combat/live_combat_session.py` | 変更(`capture_snapshot()`追加のみ) | `056e1e9e976dfcfa7c93d3734d020a088078f7f835dd152b70b87e57f5daecb8` |
| `Combat/evaluation/online_eval/capture_snapshot_diagnostic.py` | 新規 | `9ddcaef4bf542f467fc8e929f0173461f9e001efd94302a16019a952d71e9d95` |
| `Combat/evaluation/online_eval/verify_snapshot_phase2a.py` | 新規 | `6e5e724309f153d0f76ac6ad288283e32cecfb878d07d5abf0171240fc7f59b1` |

---

## 6. 受け入れ条件との照合

| 条件 | 結果 |
|---|---|
| C# Snapshot JSONをPythonで損失なく読み書き可能 | ✅(3-A/3-Eで確認、未知フィールドも保持) |
| Python再シリアライズ後の正規化JSONが一致 | ✅(3-C) |
| Capture前後でlive Observation／LegalActionsが不変 | ✅(3-B) |
| Scenario 6546-21でCaptureによる挙動変化なし | ✅(3-D、49 decision・victory維持) |
| completeness／unsupportedが正しく伝播 | ✅(3-E、格上げなし) |
| Phase 1回帰が全件合格 | ✅(3-F、52/52) |

---

Phase 2B(`RestoreSnapshot`)・Phase 3へは進まず、ここで停止する。
Emulator担当・監督者の確認を待つ。
