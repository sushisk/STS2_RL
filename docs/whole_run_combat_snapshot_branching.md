# Whole Run の戦闘分岐を Combat snapshot 復元へ移す

## 0. 文章の目的

Whole Run の Combat branch を、Map snapshot + action prefix の再生から
`CombatStateSnapshot` の復元へ移すための実装仕様。手順ごとに分けて実装する前提で書く。

## 1. なぜ変えるか

現在の Whole Run の戦闘分岐は次の形になっている（`Run/worker_pool.py`）。

```python
session.load_state(map_snapshot)     # 地図画面のスナップショット
session.choose_room(room_id)
for action_id in action_prefix:
    session.step(action_id)          # 戦闘の頭から打ち直す
```

Combat Instance 側（`CombatScenario` / `CombatStateSnapshot` / `battle_emulator`）は
Whole Run から一切 import されていない。完全に別実装であり、Combat 側の修正はここに効かない。

この方式には 2 つの問題がある。

- **再現性**: 戦闘の再現が prefix 再生の忠実性に完全依存する。途中 1 箇所のズレで以降すべてが
  狂う。実際に `AllBranchesFaultedError` が発生し、調査の結果「同じ部屋・同じ戦闘を最後まで
  進めた状態に着地」していた（stepIndex +8、HP 71→39、敵全滅）。しかも 12 回のリトライ
  すべてで同一。敵 RNG・action_id の解釈違い・prefix の長さは実測で否定済み
- **速度**: 深いターンほど再生量が線形に増える

## 2. 事前調査の結果（実測済み）

| 項目 | 結果 |
|---|---|
| `pending_choice` からの `CaptureSnapshotJson()` | **可能**（26,422 bytes）。`stable` も可能（26,428 bytes） |
| capture → restore の戦闘状態 | hp / energy / block / gold / relics / deck / 敵 / 手札 / legal actions **すべて一致** |
| capture → restore の run 位置 | `totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType` が **`None` になる**。capture 位置で即 restore しても同じ |
| `RestoreSnapshotJson` の拒否 | `SnapshotRestoreRejectedException`。`invalid_json_required_field:$.Metadata` 等の構造化コード。復元前に検証 |
| `combat_session_id` | 復元で変わる。ただし `Lease.is_valid_for()` は比較に使わず、`masking.py` が publish も禁止しているため**無害** |
| **復元した戦闘に勝ったとき** | **`boundary = run_terminal` / `outcome = victory`**（HP 生存、敵全滅）。地図の文脈が無いため戦闘終了がラン終了として報告される |
| snapshot サイズ | 26〜29 KB |

## 3. 実装手順

各手順は独立にレビュー・テスト可能であること。手順をまたいで先回りしないこと。

### S1. `WholeRunSession` に capture / restore を公開する

`Run/whole_run_session.py` は `save_state` / `load_state`（run 級）しか持たない。
`Run/run_emulator_bridge.py` 経由で次を追加する。

```python
def capture_combat_snapshot(self) -> str      # GameInstance.CaptureSnapshotJson()
def restore_combat_snapshot(self, json: str)  # GameInstance.RestoreSnapshotJson(json)
```

- 命名は run 級の `save_state` / `load_state` と紛れないこと
- `Combat/emulator_bridge.py` の `restore_snapshot_json` と同じ形にする
- CLR 例外はそのまま伝播させる。ここで握りつぶさない

**受け入れ**: 戦闘中に capture → restore して観測が §2 の表どおりに戻ること。

### S2. snapshot を「作って運ぶ」

**分岐の 85% は親がルートではない。** 実トラフィックの `emulate_actions` items 7,130 件の
うち、`parent_branch_id == "root"` は 1,051 件で、残り 6,079 件は別の branch を親に持つ。

これが設計を決める。API プロセスのライブセッションは**常にルートの位置**にあり、
branch の盤面は worker プロセスにしか存在しない。したがってライブセッションから capture
できるのはルート親の場合だけで、それ以外で同じことをすると**全く別の盤面**を渡すことになる。
現行の prefix 方式が深さ 2 以上でも動いていたのは、`_build_child_view` が経路全体を
prefix にエンコードしているからであり、snapshot にはその性質が無い。

**snapshot は連鎖させる。**

```
ルート親の branch : API のライブセッションから capture
branch を親とする : その親 branch を実行した worker が、実行直後に capture したものを使う
```

構造としては次の 4 点。

| 置き場所 | 追加するもの | 役割 |
|---|---|---|
| `ChoiceWorkItem`（`Run/worker_pool.py`） | `combat_snapshot: "str \| None" = None` | **どこから組み立てるか**。設定時は `map_snapshot` / `room_id` / `action_prefix` を使わない |
| `ChoiceStepResult`（同上） | `settled_combat_snapshot: "str \| None" = None` | **その branch の実行後の盤面**。worker が `drain_trivial_reward_frontier` の直後、`settled_observation` を取るのと同じ場所で capture する |
| `_BranchBookkeeping`（`API/instance_whole_run.py`） | `combat_snapshot` | 上を branch ごとに保持し、子 branch が引ける状態にする |
| `_prepare_combat_branch`（`API/instance_whole_run_beam.py`） | 取得元の分岐 | 親がルートならライブセッションから capture、そうでなければ親 branch の bookkeeping から引く |

**capture できない場合は fault にする。フォールバックを作らない。**
戦闘が終わった branch は `EnsureCombatActive()` を満たさず capture できないので
`settled_combat_snapshot` は `None` になる。その branch を親にした子 branch の要求は
拒否してよい。Training は戦闘終了を terminal として扱うので、そこから先へ分岐する必要が無い。

**同一決定の兄弟では capture を 1 回だけにする。** `emulate_actions` は同じ親決定から
複数の兄弟をまとめてディスパッチする。branch ごとに capture すると同一内容の 26 KB が
兄弟の数だけ `multiprocessing.Queue` を通る。バッチ内で親決定ごとに 1 回に抑える。
キャッシュ用のクラスや抽象は作らない。

この手順では **worker はまだ `combat_snapshot` を消費しない**。運ぶだけで、
組み立て方を変えるのは S3。

**受け入れ**:
- ルート親の戦闘 work item に非空の `combat_snapshot` が載る
- 1 つの親決定の兄弟が同じ capture を共有する（兄弟ごとに capture しない）
- branch を親とする戦闘 work item は、その親 branch の実行結果由来の snapshot を載せる
- 非戦闘（map / event / reward）の work item は無変更
- worker の挙動は無変更

### S3. worker を snapshot 復元にする

`Run/worker_pool.py` の `_bootstrap_reach`:

- `work_item.combat_snapshot` があれば `restore_combat_snapshot()` **1 回だけ**行い、
  `load_state` / `choose_room` / prefix 再生は**行わない**
- 復元に失敗したら fault。**旧経路へのフォールバックは作らない**。静かに落ちると
  今回のような乖離がまた見えなくなる
- 非戦闘（map / event / reward）の work item は現状の経路のまま

`resolve_action_semantic_key` による最終行動の再解決は**そのまま残す**が、
役割は「解決」ではなく「検証」に近い。復元が正しければ親と同じ ID になるはず。

**受け入れ**: 戦闘分岐が prefix を 1 手も再生せず、復元 1 回で親と同じ盤面に立つこと。

### S4. 戦闘終了の publish 形を現状に合わせる（最重要）

**これを落とすと探索が静かに壊れる。**

復元した戦闘に勝つと、セッションは `boundary = run_terminal` / `outcome = victory` を返す。
Training 側 `decision/value.py:233-237` は `outcome in ("victory","defeat")` を
そのまま terminal 扱いし、`DamageRaceValueFunction` が `+100,000`（＝ラン勝利）を返す。
**戦闘に勝っただけの leaf がすべてラン勝利として評価される。**

したがって RL のアダプタ層で変換する。

- 復元由来の branch が戦闘終了に達したら、publish する DTO は
  **現在の経路と同じ `transition` 形**にする（`kind = "combat_completed"` と勝敗）
- `run_terminal` / `outcome: "victory"` を**そのまま publish しない**
- 敗北（プレイヤー死亡）は現状どおりで良い

**Training 側は無変更で済むこと**が受け入れ条件。`_terminal_outcome()` は
`transition.kind == "combat_completed"` を既に解釈できる。

### S5. run 位置フィールドの欠落を埋める

復元後は `totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType` が `None` になる。
branch の DTO と root の DTO で食い違うため、**親 view の値で補う**。

- 戦闘中に floor は変わらないので、親から引き継いで問題ない
- Training の combat beam はこれらを読まないが、DTO の一貫性のために埋める

## 4. テスト

- S1: capture / restore のラウンドトリップ（`API/tests/`。pytest 収集対象）
- S3: 戦闘分岐が `choose_room` / prefix 再生を行わないこと（fake session で呼び出しを観測）
- S4: **復元 branch の戦闘勝利が `combat_completed` として publish され、
  `outcome: "victory"` が publish されないこと**。回帰したら探索が壊れるので必須
- S5: branch DTO の run 位置フィールドが親と一致すること
- 全体: `python -m pytest -q` が既存 503 件を維持すること

## 5. やらないこと

- 旧 prefix 経路へのフォールバック
- 非戦闘（map / event / reward）分岐の変更
- Training 側の変更
- `combat_session_id` の維持（無害と確認済み）
