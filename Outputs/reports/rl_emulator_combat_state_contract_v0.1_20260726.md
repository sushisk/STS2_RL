# RL・Emulator共通契約案 — Canonical CombatStateSnapshot v0.1

本書は契約案であり、まだ実装へ進まない。RL担当・Emulator担当は、各項目に
ついて「承認／修正要求／実現不能」を回答する。

（本文1〜9節は共同で提示された契約案原文をそのまま保持。10節にRL担当の
回答を記載する。RLコード・schema・データはいずれも変更していない。）

---

## 1. 状態契約

戦闘状態の正本を次の1種類に統一する。

```text
Canonical CombatStateSnapshot
```

Python版とC#版は同じ意味・同じschema versionを持ち、相互変換可能とする。

```text
C# live combat state
↕ Capture / Restore
C# CombatStateSnapshot DTO
↕ Serialize / Deserialize
Python CombatStateSnapshot
```

`CombatScenario`は新規戦闘開始用として残し、完全Snapshotとは分離する。

## 2. APIの役割

```text
StartCombat(CombatScenario)
```

* 新規戦闘を開始する
* EnterRoom、Relic取得、戦闘開始・ターン開始hookを実行する

```text
CaptureSnapshot()
RestoreSnapshot(CombatStateSnapshot)
```

* decision boundary上の戦闘を保存・再開する
* EnterRoom、AfterObtained、StartTurn、初期ドロー等を再実行しない

`ResetFromScenario`を中間状態再開には使用しない。

## 3. ライブ実行

通常Policy／Choice Policyでは、戦闘開始時のみ`StartCombat`を呼び、
同一GameInstanceを戦闘終了まで維持する。

```text
StartCombat
→ Observation + LegalActions
→ Step
→ Observation + LegalActions
→ Step
```

毎decisionのrestoreと、別途の`GetLegalActions()`再取得を廃止する。

探索・ビームサーチのみ、完全Snapshotから分岐する。

## 4. Decision boundary

Snapshot取得とPythonへの返却を許可する状態を次とする。

* ActionQueueが空
* CurrentlyRunningActionがない
* 未解決Continuationがない
* 通常のPlayer decision、公開Choice、またはterminal
* Observation・LegalActions・terminalが同一時点
* Pythonへ返却後、次の`Step`までC#状態が自律的に変化しない

この条件を満たさない場合はSnapshotを生成せず、settlingを継続するか
明示的なエラーを返す。

## 5. Snapshot必須情報

最低限、将来挙動を決定する以下を含める。

* HP／Block／Energy／Stars
* 全カードpile、順序、カードinstance状態
* PlayPile
* TurnNumber／RoundNumber／CurrentSide／Phase
* 敵の状態、intent、内部カウンタ
* Relic ID、基底状態、個別SavedProperty
* Power ID、Amount、ターン開始値、duration制御、個別状態
* Potions／Orbs
* 全RNGの内部stateとcounter
* CombatHistory
* pending Choice
* terminal／victory／loss進行状態

`action_id`はSnapshot全体ではなく、1つのDecisionFrame内だけで有効とする。

## 6. 未対応情報

値が現在存在しない場合だけ`null`を使用する。

未実装、取得失敗、復元不能を`null`で表さない。

Snapshot生成時に必要情報を取得できない場合は、

```text
SnapshotUnsupportedStateException
```

等で取得を拒否する。

「部分的なSnapshotを完全Snapshotとしてrestoreする」ことは禁止する。

Powerの非公開内部データなどについては、対象クラスごとの明示的な
Snapshot serializerを追加する。serializer未実装のPower／Relicが
存在する場合、その状態の完全Snapshot取得を拒否する。

## 7. Schema管理

Snapshot自身に以下を必須で持たせる。

```text
schemaVersion
emulatorCommit
snapshotId
captureBoundary
```

RL・Emulator双方が同一schema hashを記録する。

既存schemaに未記載の`pendingChoice`関連フィールドも正式に組み込む。

## 8. Observationの扱い

Policy／Value／ログ用の状態は、Canonical Snapshotから生成する公開
projectionとする。

```text
Canonical Snapshot
→ Public Observation
```

Observationだけから完全restoreできるとは保証しない。ただし、同一
decision boundaryのSnapshotと意味が矛盾してはならない。

## 9. 受け入れテスト

### Round-trip

```text
C# State A
→ Capture
→ Python
→ C#
→ Restore State B
```

AとBで以下が一致すること。

* Observation
* LegalActions
* actionの意味
* terminal
* pending Choice
* RNG
* 同一action後のStepResult

### Live execution

* `StepResult.Observation`と`StepResult.LegalActions`が常に一致
* 次decisionでrestore不要
* Python返却後に状態が変化しない

### Branch execution

同一Snapshotを複数回restoreし、同一actionを実行した結果が一致すること。

---

## 10. 今回の担当別回答事項 — RL担当 回答

（Emulator担当の回答は別担当が行う。以下はRL担当5項目のみへの回答。
現時点ではRL側の判断・提案であり、Emulator担当の確認・監督者承認を
経るまで実装には進まない。）

### 10-A. ライブ実行と探索実行を分離する具体的なクラス案

現行の`BattleEmulator`は`apply_action()`/`_restore()`という同一経路を、
実際にcommitする進行(`CombatEnv.step()`)と、破棄前提の仮説評価
(`HeuristicAgent`の候補スコアリング、`beam_search.py`/`lookahead.py`、
shadow評価)の両方に使っており、コードを読むだけでは両者を区別しづらい
(`rl_combat_state_snapshot_contract_proposal_20260726.md`4節で既に
指摘済み)。本契約案の`StartCombat`/`CaptureSnapshot`/`RestoreSnapshot`
語彙に合わせ、以下の2クラスへ分離することを提案する。

```text
class LiveCombatSession:
    始動: start_combat(scenario_spec) -> Observation + LegalActions
          (StartCombatを1エピソードにつき1回だけ呼ぶ)
    継続: step(action, target...) -> Observation + LegalActions + terminal
          (同一GameInstanceのStep()を直接呼ぶのみ、
           decisionごとのrestoreを行わない)
    分岐用出口: capture_snapshot() -> CombatStateSnapshot
          (decision boundary上でのみ許可 - 4節の条件を満たす時点だけ)

class SnapshotBranchEvaluator:
    evaluate_candidate(snapshot: CombatStateSnapshot, action, target)
        -> CombatStateSnapshot
          (RestoreSnapshot + Step、常に破棄前提、既存のapply_action()の
           ステートレス設計をそのまま踏襲)
```

* `CombatEnv`は`LiveCombatSession`の薄いラッパーへ縮小する
  (現在の`get_legal_actions()`+`step()`という2回のrestoreを、
  `LiveCombatSession.step()`1回のObservation+LegalActions取得へ統合)。
* `HeuristicAgent.choose_action_with_detail()`は、decision開始時に
  `LiveCombatSession.capture_snapshot()`で1つのSnapshotを取得し、
  候補ごとに`SnapshotBranchEvaluator.evaluate_candidate()`を呼ぶ形へ
  変更する — 現状の「同じ`battle_state`を候補ごとに`apply_action()`へ
  渡す」という暗黙の仮説評価パターンを、型レベルで
  「これはCombatStateSnapshotからの分岐である」と明示する。
* 呼出側が誤ってどちらの経路を使っているか取り違えるリスクを、
  クラス境界そのもので防ぐ設計を意図している。

### 10-B. `StepResult.LegalActions`継続利用への移行範囲

Emulator担当が「`Step()`後の`LegalActions`は常に正確」という不変条件を
保証できることを前提に、以下の範囲で移行する。

* **移行対象**: `LiveCombatSession.step()`(=実際にcommitする進行、
  Policy/Choice Policy/Heuristic各armの本番決定経路)のみ。
  ここでは`StepResult`のObservation/LegalActionsをそのまま次decisionへ
  引き継ぎ、追加のrestore/`GetLegalActions()`呼出を一切行わない。
* **移行対象外**: `SnapshotBranchEvaluator`(探索/仮説評価経路)。
  各候補は独立にSnapshotから分岐するため、「StepResultを信頼する」という
  概念自体が当てはまらない — 現状通りrestoreベースのまま維持する。
  ただし1候補の内部でActionContinuationが多段発生する場合、その
  continuationループ内では既存通りキャッシュ済みlegal_actionsを使う
  (現行の`battle_emulator.py::enumerate_legal_actions()`のキャッシュ
  条件と同じロジックを維持)。
* **段階的ロールアウト**: 保証確認後もいきなり全面切替はせず、
  (a) 既存の「毎decision fresh restore」経路を一時的にfallbackとして
  残し、両者を同一manifest(既存の`choice_policy_online_eval_manifest.
  jsonl`等)に対して並行実行して`Observation`/`LegalActions`/最終結果を
  diffする検証を1サイクル行う(`rl_combat_state_snapshot_contract_
  proposal_20260726.md`5節の「保守案」で提案した比較手法を転用)、
  (b) 差分0を確認した後にStepResult信頼をdefaultとし、fresh restore
  経路を削除する。
* 診断用の比較コードは恒久コードへcommitせず、検証完了後に削除する
  (共同作業指示の「診断用ログの追加が必要な場合も、恒久コードへ
  commitせず、実施内容とrevert確認を報告する」という制約を踏襲)。

### 10-C. 既存`BattleState`をObservation用途へ限定する移行案

現行の`BattleState`(`battle_emulator.py`)は、(a) `engine_state`
(Observationへ格上げすべき部分)と、(b) restoreで失われる情報を
Python側で個別に補うworkaround(`turn`独自カウンタ、`enemy_max_hps`
patch辞書、`_cached_legal_actions`)を1つのdataclassに混在させている。
本契約のSnapshotが§5の必須情報(TurnNumber/敵の内部カウンタ等)を
正しく保持できるようになれば、これらのworkaroundは不要になる。

* **移行後の型分離案**:
  * `CombatStateSnapshot`(新規、Emulator側DTOに対応するPython型) —
    `SnapshotBranchEvaluator`と、プロセス跨ぎ/セッション再開用にのみ
    使用。完全復元可能であることが前提。
  * `Observation`(新規、または既存`BattleState`をリネーム) —
    Policy/Value/ログが読む読み取り専用の公開projection(本契約8節)。
    **restore不可**(`_restore()`相当の関数はSnapshotのみを受け付け、
    Observationは受け付けない設計とする)。
* **移行手順(提案、いずれも今回は実施しない)**:
  1. `CombatStateSnapshot`をEmulator側DTOが固まった段階でPython側に
     追加実装。
  2. 既存`BattleState`は当面**互換ラッパーとして残す**
     (`battle_state.engine_state`等、既存呼出元を壊さないため) —
     内部的に`Observation`+(あれば)`CombatStateSnapshot`ハンドルを
     保持する形に変更。
  3. `policy_agent.py`/`choice_policy_agent.py`/`choice_semantics.py`/
     各評価harnessは、新型が安定してから**個別に**段階移行する
     (一括書き換えはしない — 影響範囲が全Combatコードに及ぶため)。
* 本移行は**Emulator側のSnapshot DTOが確定してから着手する**べきで、
  並行実施は提案しない。

### 10-D. 既存trajectoryの能力区分と互換方針

既存のteacher2000/Choice教師データは旧契約(`BattleState`/`engine_state`、
`state_restore_coverage.csv`記載の欠落を含む)で採取されている。
以下の区分と方針を提案する。

* **区分1: オフライン学習専用データ(再生成不要)** — teacher2000/
  Choice教師データの`state`/`next_state`/`legal_actions`/
  `selected_action`は、Policy/Value/Choice Policyの実際の入力フィールド
  (`rl_combat_state_snapshot_contract_proposal_20260726.md`2節で
  確認済みの通り、RNGカーソル・relic内部カウンタ・turnNumber連続性・
  PlayPileはいずれのモデルも消費していない)を全て満たしている。
  **これらは「Observation-only、恒久的に互換」と分類し、再生成しない。**
* **区分2: 中間状態を実際にrestoreする診断/監査ツール**
  (`investigate_no_legal_actions_6546_21.py`、`probe_gambling_chip_and_
  potions.py`、offline再解決ツール等) — これらは`build_scenario_from_
  state()`相当のrestoreに依存しており、`state_restore_coverage.csv`と
  同じ欠落の影響を受ける。**「restore近似、完全一致ではない」と各ツールの
  docstring/報告書に明記する運用を継続する**(既に一部のツールでは
  同種の注記が行われている — 今後は契約書としてこれを形式化する)。
* **一括再生成は提案しない** — 区分1に該当するデータの再取得コストに
  見合う効果がないため。
* **今後の新規データ生成の互換ポリシー**: `LiveCombatSession`が実装され
  次第、新規生成データの各行に`schemaVersion`/`captureBoundary`
  (本契約7節)を付与し、旧(`v1`、`BattleState`ベース)データと
  新(`v2`、Snapshotベース)データが同一データセット内で暗黙に混在
  しないようにする。

### 10-E. action_id／DecisionFrameの管理案

現行、`action_id`は「stateが変わるたびに再取得が必要」という制約が
`legal_action_schema.json`にドキュメントとして明記されているのみで、
違反しても実行時には検出されない(呼出側の規律に依存)。

* **提案**: `DecisionFrame`を明示的なトークンとして導入する。既に
  Choice教師データ生成・Choice Policyオンライン評価で使っている
  `{trajectory_id, decision_index, continuation_step_index}`という
  組み合わせ(`generate_choice_teacher_data.py`/`choice_policy_online_
  eval.py`で採用済みの命名規則)を、そのまま`DecisionFrame`の実体として
  昇格させる。
* `LiveCombatSession.step()`/`capture_snapshot()`はObservation/
  LegalActionsと同時に現在の`DecisionFrame`を返す。
* 次の`step()`呼出に渡す`action_id`は、**直前に取得した
  `DecisionFrame`とペアでなければ受理しない**(古いframeのaction_idを
  新しいframeに対して使おうとした場合は即座にエラーとする) — 現状
  「ドキュメント上の注意」でしかない制約を、実行時に検出可能な形へ
  格上げする提案。
* `SnapshotBranchEvaluator`側の各仮説分岐は、**commitされたtrajectoryの
  frame列とは別の、使い捨てのephemeral DecisionFrame**を持つものとして
  区別する(10-A節のクラス分離と対応)。
* 実装コストは小さいと見積もる(既存のログ用フィールドを正式化する
  だけであり、新しい概念の追加ではない)。

---

以上がRL担当5項目への回答。RLコード・schema・データの変更、Snapshot実装
はいずれも行っていない。Emulator担当の回答・監督者確認を待って停止する。
