# Whole Run の戦闘分岐を Combat Instance へ委譲する

## 0. 文章の目的

Whole Run の Combat branch を、Map snapshot + action prefix の再生から
**`CombatInstance` への委譲**へ移すための実装仕様。実装するのは境界処理であり、
分岐そのものの再実装ではない。

## 1. なぜ変えるか

現在の Whole Run の戦闘分岐は次の形になっている（`Run/worker_pool.py`）。

```python
session.load_state(map_snapshot)     # 地図画面のスナップショット
session.choose_room(room_id)
for action_id in action_prefix:
    session.step(action_id)          # 戦闘の頭から打ち直す
```

`CombatScenario` / `CombatStateSnapshot` / `battle_emulator` を Whole Run 側は 1 つも
import していない。完全に別実装であり、**Combat Instance 側の修正はここに効かない。**

### 1.1 観測されたバグと、その本当の説明

Whole Run 評価で `AllBranchesFaultedError` が 2 件発生した。fault 診断は

```
expected_boundary = stable        root  : floor 15, turn 1, 敵 117 HP 睡眠中, HP 71, stepIndex 110
actual_boundary   = reward_select replay: 同じ floor・同じ部屋, 敵全滅, HP 39, stepIndex 118
```

を報告していた。**同じ部屋の同じ戦闘を、最後まで戦って勝った状態**である。

消去できた仮説（すべて実測）:

| 仮説 | 判定 |
|---|---|
| 敵の move 選択の乱数 | 否定。`rng_id` 1〜4 で手札まで完全一致し、intent 列も `BygoneEffigy` の宣言どおり |
| prefix の数値 action_id が別カードを指す | 否定。replay 先の id 2/3/4 が親と一致。欠けていたのは唯一コスト 3 のカードで、`CanPlay()` の絞り込み（＝エネルギー不足）で説明できる |
| prefix が長すぎる（蓄積バグ） | 否定。失敗した決定の prefix は **1 手** |
| `auto_action_ids` の二重適用 | 否定。契約とコードで root/replay が対称 |

残ったのは、**設計の前提そのもの**だった。

branch は **root と異なる RNG でシミュレートする**のが仕様である。RNG はゲーム情報として
与えられない（RNG を知ってプレイするのは盤面予測ではない）。同じ action_id 列を新しい RNG で
再生すれば、ドローが変わり、打つカードが変わり、与ダメージが変わる。
**部屋全体ぶんの再生窓に対してこれを行えば、別のゲームになる。**

これで観測がすべて説明できる。

- 12 回のリトライが同一だったのは `rng_id` 354〜357 を使い回すため。**同じ rng_id なら同じ盤面**
- `BYGONE_EFFIGY` に集中していたのは、起床時 Strength +10 が乖離を増幅し boundary が
  変わる閾値に届きやすいため。原因ではなく**可視化装置**
- 同一プロセスで prefix を再生した私の検証が bit 一致したのは、**rng_id を振っていなかった**ため。
  検証方法が誤っていた

**replay 機構の不具合ではなく、RNG 規律に対して再生窓が長すぎることが原因。**

## 2. 事前調査の結果（実測済み）

| 項目 | 結果 |
|---|---|
| `CaptureSnapshotJson()` at `stable` | 可能（26,428 bytes）。restore まで通る |
| `CaptureSnapshotJson()` at `pending_choice` | capture は**通る**が、その snapshot の **restore は拒否**: `unsupported_capture_boundary:published_target` |
| capture → restore の戦闘状態 | hp / energy / block / gold / relics / deck / 敵 / 手札 / legal actions **すべて一致** |
| capture → restore の run 位置 | `totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType` が `None` になる |
| `RestoreSnapshotJson` の拒否 | `SnapshotRestoreRejectedException` + 構造化コード。復元前に検証 |
| `combat_session_id` | 復元で変わる。`Lease.is_valid_for()` は比較に使わず `masking.py` が publish も禁止しているため無害 |
| 復元した戦闘に勝ったとき | `boundary = run_terminal` / `outcome = victory`（地図の文脈が無いため） |
| snapshot サイズ | 26〜29 KB |

`GameInstance.DetermineCaptureBoundary()` は capture 時の boundary を
`normal_player_decision` / `published_target` / `published_choice` / `terminal` に分類し、
**restore できるのは `normal_player_decision`（= `stable`）だけ**である。

## 3. 委譲先には必要なものが既にある

`CombatInstance.__init__`（`API/instance_combat.py:159`）:

```python
self._session = LiveCombatSession()
self._root_state = self._session.start_combat(scenario_spec)
self._held_stable_snapshot: Optional[CombatStateSnapshot] = None   # stable アンカー
self._replay_prefix: list[ReplayPrefixEntry] = []                  # アンカーからの行動列
root_boundary = boundary_of_battle_state(self._root_state)
if root_boundary == BOUNDARY_STABLE:
    self._held_stable_snapshot = self._session.capture_snapshot()
    self._replay_prefix = start_new_replay_prefix_from_stable()
elif root_boundary == BOUNDARY_PENDING:
    raise RuntimeError(_START_PENDING_UNSUPPORTED)
```

**アンカー + 行動列も、`pending_choice` から開始できない制約も、既に実装されている。**
さらに `API/combat_rng_mapping.py` が `rng_id` → 単一のドロー順序仮説を与え、
`Combat/search/replay_draw_restore.py` が `pending_choice` までに観測済みのドローを固定する。

したがって **これらを下位から呼び直してはならない。** 3 つ目の分岐実装が生まれるだけである。

## 4. 実装する経路

```
戦闘開始を検出
  → CombatInstance を作成
  → Whole Run セッションへ CaptureSnapshot を要求
  → その snapshot を CombatInstance へ反映
  → 戦闘境界の emulate_actions を CombatInstance へ委譲
```

**scenario から戦闘を開始してはならない。** 敵の初手が RNG 依存の場合、
scenario 開始では実際のゲーム状態と乖離しうる。snapshot 反映なら忠実である。

root の進行（`commit_action`）は従来どおり Whole Run セッションが行う。
`CombatInstance` は分岐のためだけに存在し、root の現在位置を映す。

## 5. 実装手順

各手順は独立にレビュー・テスト可能であること。手順をまたいで先回りしないこと。

### S1. `WholeRunSession` に capture / restore を公開する（完了）

`capture_combat_snapshot()` / `restore_combat_snapshot()`。
ラウンドトリップのテストが `totalFloor` の欠落も pin している。

### S2. `CombatInstance` に snapshot からの起動口を作る

現在の入口は `start_combat(scenario_spec)` だけで、`BOUNDARY_PENDING` を拒否する。
**snapshot を反映して同じ不変条件を確立する入口**を足す。

- `LiveCombatSession.restore_snapshot_json()` は既にある
- 反映後は `start_combat` の後と同じ状態になること。すなわち `_held_stable_snapshot` と
  `_replay_prefix` が `stable` アンカーとして確立されること
- `stable` 以外の snapshot は拒否する（restore 自体が拒否するので素通しでよい）

**やらないこと**: 既存の scenario 入口を壊すこと。分岐ロジックに触ること。

**受け入れ**: snapshot から起こした `CombatInstance` が `start_instance_response()` で
親と同じ盤面を返し、`emulate_actions` が動くこと。

### S3. Whole Run が戦闘境界で `CombatInstance` を保持する

- root が戦闘境界に入ったら `CombatInstance` を作り、Whole Run セッションから capture した
  snapshot を反映する
- root が戦闘内で進むたびに、その位置を `CombatInstance` へ反映し直す
- 戦闘が終わったら畳む

**やらないこと**: 非戦闘（map / event / reward / rest / shop）の経路に触ること。

**受け入れ**: 戦闘中は `CombatInstance` が root と同じ盤面を保つこと。

### S4. 戦闘境界の `emulate_actions` を委譲する

- 戦闘境界の分岐要求を `CombatInstance.emulate_actions()` へ回す
- **`branch_id` / `decision_point_id` の名前空間を対応付ける。** Training は Whole Run が
  発行した id で分岐を指すので、両者の対応表が要る
- 非戦闘の分岐は従来どおり Whole Run の worker pool

**やらないこと**: 旧 prefix 経路へのフォールバック。静かに落ちると乖離がまた見えなくなる。

**受け入れ**: 戦闘分岐が `load_state` / `choose_room` / 部屋全体の prefix 再生を
一度も行わないこと。

### S5. publish する DTO を Whole Run の形に揃える

`CombatInstance` の DTO には run 級の情報が無い
（`totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType`）。親 view の値で補う。
戦闘中に floor は変わらないので引き継いで問題ない。

終端の形も確認する。Training は `transition.kind == "combat_completed"` を解釈できる
（`decision/value.py:233-247`）。`outcome: "victory"` をそのまま publish すると
**ラン勝利（+100,000）として評価される**ので、戦闘勝利は `combat_completed` で publish する。

## 6. テスト

- S2: snapshot から起こした `CombatInstance` が親と同じ盤面を返すこと
- S3: root が戦闘内で進むと `CombatInstance` が追随すること
- S4: 戦闘分岐で `choose_room` / prefix 再生が呼ばれないこと（fake で呼び出しを観測）
- S5: branch DTO の run 位置フィールドが親と一致し、戦闘勝利が `combat_completed` として
  publish され `outcome: "victory"` が出ないこと
- 全体: `python -m pytest -q` が既存 504 件を維持すること

## 7. やらないこと

- 旧 prefix 経路へのフォールバック
- `rng_hypothesis` / `replay_draw_restore` を下位から呼び直すこと（`CombatInstance` に委譲する）
- 非戦闘（map / event / reward / rest / shop）分岐の変更
- Training 側の変更
- `combat_session_id` の維持（無害と確認済み）
