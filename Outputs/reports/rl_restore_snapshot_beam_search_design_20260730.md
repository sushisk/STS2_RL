# RestoreSnapshot 設計方針

## 1. 目的

`RestoreSnapshot`は、Beam Searchの候補状態を正確に複製するための戦闘状態復元APIである。

既存Beam Searchの外部インターフェースは維持し、内部の状態生成経路を段階的に次へ切り替える。

```text
旧：ResetFromScenario
新：RestoreSnapshot
```

通常のゲーム進行には`LiveCombatSession`を使用し、Restore経路とは分離する。

## 2. 直接復元する状態

直接復元の対象は、Pythonが通常の戦闘Actionを選べる**Stable Decision Boundary**に限定する。

主な対象：

* Player／Enemy／Pet
* HP／Block／Energy
* 手札・山札・捨て札・Exhaust等
* Relic／Potion／Orb
* Powerと将来挙動に必要な内部状態
* RNG
* Turn／Round／CurrentSide
* stable instance IDと参照関係
* 敵の行動状態
* ActionQueueの実行可能状態

## 3. 直接復元しない状態

以下はSnapshotから直接復元しない。

* Pending Choice
* Pending Target
* Action continuation
* 実行途中のAction
* 戦闘外のカード削除・報酬・イベント等のpending状態

これらは、直前のStable Snapshotから操作列を再実行して再現する。

```text
Stable Snapshot
→ PrimaryAction
→ SelectionStep
→ 必要なら追加SelectionStep
→ 次のStable Decision Boundary
```

探索途中では、Stable Snapshotと確定済み操作prefixを保持する。

## 4. StartCombatを使用しない

Restore経路では以下を呼ばない。

* EnterRoom
* StartCombat
* StartTurn
* BeforeCombatStart
* BeforeHandDraw
* 初期ドロー
* 開始時Relic hook
* 通常Pet召喚hook

これらを呼ぶと、元状態に存在しない副作用、履歴、RNG消費、カード生成などが発生するためである。

runtime成立に必要な低レベル処理のみを使用し、その後Snapshot値を適用する。

## 5. Restoreの基本手順

```text
入力検証
→ runtime／object graph構築
→ 必要な低レベルbinding
→ RNG復元
→ primitive state適用
→ piles／Relic／Power／Pet適用
→ stable IDと参照再結合
→ Turn／Round等の適用
→ ActionQueue状態同期
→ 最終検証
→ 新しいCombatSession／DecisionFrame発行
```

不正または未対応のSnapshotを自動修復しない。

## 6. CombatHistory

CombatHistoryは、すべてを機械的に復元すること自体を目的としない。

各履歴情報を次に分類する。

* `future-causal`：将来のカード、Power、Relic、敵行動等に影響する
* `structural-only`：履歴構造の再構築に必要
* `diagnostic-only`：表示、ログ、デバッグ専用

Beam Searchの正確性に必要な`future-causal`情報はCapture／Restoreする。`diagnostic-only`情報は省略可能とする。

履歴上だけに残る消滅済みobjectをlive objectとして自動生成しない。

## 7. Beam Searchノード

### Stable Node

```text
CombatStateSnapshot
評価値
探索深度
```

直接`RestoreSnapshot`可能。

### Continuation Node

```text
Root Stable Snapshot
確定済み操作prefix
次のSelection候補
```

直接Restoreせず、Root Snapshotからprefixを再実行する。

次のStable Decision Boundaryへ到達した時点で、新しいStable NodeをCaptureする。

## 8. 失敗時の契約

入力検証で拒否した場合：

* 現在のlive sessionを維持
* faultedにしない
* 構造化された拒否理由を返す

runtime破棄後の構築中に失敗した場合：

* 部分状態を公開しない
* sessionをfaultedにする
* 元例外情報を保持したRestore失敗として報告する

成功したResetまたはRestoreだけがfaultを解除できる。

## 9. 完了条件

Exact Restoreは、少なくとも次で確認する。

```text
Capture A
→ Restore A
→ 論理Action X
→ Result R1

Capture A
→ Restore A
→ 同じ論理Action X
→ Result R2
```

session IDなどを除き、以下が一致すること。

* StepResult
* Observation
* LegalActions
* RNG
* HP／Block／piles
* Power／Relic／Pet
* 敵状態
* 次のDecision Boundary

## 10. 最終方針

`RestoreSnapshot`は任意の実行途中状態を保存する汎用セーブ機能ではない。

**通常の戦闘Decision Boundaryを正確に複製し、継続入力は直前のStable Snapshotから決定論的に再実行する、Beam Search専用の状態複製基盤**として設計する。
