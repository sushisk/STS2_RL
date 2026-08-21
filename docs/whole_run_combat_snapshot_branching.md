# Whole Run の戦闘分岐を Combat Instance へ委譲する

## 0. 文章の目的

Whole Run の Combat branch を、Map snapshot + action prefix の再生から
**`CombatInstance` への委譲**へ移すための実装仕様である。

実装するのは **2 つのインスタンス型をつなぐ境界処理**であり、分岐ロジックの再実装ではない。
分岐に必要なもの（stable アンカー、行動列、`rng_id` からのドロー順序仮説、観測ドローの固定）は
すべて `CombatInstance` に既にある。

## 1. 概要

現在の Whole Run は、戦闘中の 1 手を分岐するたびに**地図画面のスナップショットまで巻き戻し、
部屋に入り直し、戦闘の最初からその時点までの全行動を再生**している。

branch は仕様上 root と異なる RNG でシミュレートされるため、この長い再生窓では
**再生した戦闘が別のゲームになる**。実際に評価が 2 戦落ちた。

これを、戦闘に入った時点で `CombatInstance` を立て、そこへ委譲する形に変える。
再生窓は「部屋全体」から `CombatInstance` が管理する「stable アンカー + 1 マクロ行動」に縮む。

S1（`WholeRunSession` への capture / restore の追加）は実装・コミット済み。

## 2. 設計

### 2.1 なぜ変えるか

現在の戦闘分岐（`Run/worker_pool.py`）:

```python
session.load_state(map_snapshot)     # 地図画面のスナップショット
session.choose_room(room_id)
for action_id in action_prefix:
    session.step(action_id)          # 戦闘の頭から打ち直す
```

`CombatScenario` / `CombatStateSnapshot` / `battle_emulator` を Whole Run 側は 1 つも
import していない。完全に別実装であり、**Combat Instance 側の修正はここに効かない。**

### 2.2 観測されたバグと、その説明

Whole Run 評価で `AllBranchesFaultedError` が 2 件発生した。fault 診断:

```
expected_boundary = stable        root  : floor 15, turn 1, 敵 117 HP 睡眠中, HP 71, stepIndex 110
actual_boundary   = reward_select replay: 同じ floor・同じ部屋, 敵全滅, HP 39, stepIndex 118
```

**同じ部屋の同じ戦闘を、最後まで戦って勝った状態**である。

実測で消去できた仮説:

| 仮説 | 判定 |
|---|---|
| 敵の move 選択の乱数 | 否定。`rng_id` 1〜4 で手札まで完全一致し、intent 列も `BygoneEffigy` の宣言どおり |
| prefix の数値 action_id が別カードを指す | 否定。replay 先の id 2/3/4 が親と一致。欠けていたのは唯一コスト 3 のカードで、`CanPlay()` の絞り込み（＝エネルギー不足）で説明できる |
| prefix が長すぎる（蓄積バグ） | 否定。失敗した決定の prefix は **1 手** |
| `auto_action_ids` の二重適用 | 否定。契約とコードで root/replay が対称 |

残ったのは**設計の前提**だった。branch は root と異なる RNG でシミュレートする仕様である
（RNG はゲーム情報として与えられない。RNG を知ってプレイするのは盤面予測ではない）。
同じ action_id 列を新しい RNG で再生すれば、ドローが変わり、打つカードが変わり、
与ダメージが変わる。**部屋全体ぶんの再生窓に対してこれを行えば別のゲームになる。**

これで観測がすべて説明できる。

- 12 回のリトライが同一だったのは `rng_id` 354〜357 を使い回すため。**同じ rng_id なら同じ盤面**
- `BYGONE_EFFIGY` に集中していたのは、起床時 Strength +10 が乖離を増幅し boundary が
  変わる閾値に届きやすいため。原因ではなく**可視化装置**
- 同一プロセスで prefix を再生した検証が bit 一致したのは、**rng_id を振っていなかった**ため

**replay 機構の不具合ではなく、RNG 規律に対して再生窓が長すぎることが原因。**

### 2.3 実測値

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

### 2.4 委譲先には必要なものが既にある

`CombatInstance.__init__`（`API/instance_combat.py:159`）:

```python
self._session = LiveCombatSession()
self._root_state = self._session.start_combat(scenario_spec)
self._held_stable_snapshot: Optional[CombatStateSnapshot] = None   # stable アンカー
self._replay_prefix: list[ReplayPrefixEntry] = []                  # アンカーからの行動列
```

`commit_action` はコミット後の boundary で自らアンカーを更新する。

```python
if <stable>:
    self._held_stable_snapshot = self._session.capture_snapshot()      # 再アンカー
    self._replay_prefix = start_new_replay_prefix_from_stable()
else:
    self._replay_prefix = append_replay_prefix_entry(self._replay_prefix, entry)
```

**アンカーは常に snapshot 由来**であり、`pending_choice` は「stable アンカー + 行動列」として
表現される。`pending_choice` から開始できない制約も既に実装されている。

さらに `API/combat_rng_mapping.py` が `rng_id` → 単一のドロー順序仮説を与え
（同じ `rng_id` なら同じ仮説。root の真の順序は決して使わない）、
`Combat/search/replay_draw_restore.py` が `pending_choice` までに観測済みのドローを固定する。

**したがってこれらを下位から呼び直してはならない。** 3 つ目の分岐実装が生まれるだけである。

### 2.5 プロセス内の GameInstance は 1 つだけ（決定的制約）

```python
# Combat/emulator_bridge.py:112
def shared_game_instance(repo_root=None):
    """The one and only live GameInstance for this process."""
```

Emulator の `RunManager.Instance` / `CombatManager.Instance` はプロセス全体の static であり、
2 つ目の `GameInstance` を作ると 1 つ目が supersede されて `EnsureNotSuperseded()` が落とす。

Combat 側は既に「プロセスに 1 つ」を正としているが、
**Whole Run 側だけが `new_game_instance()` で毎回新規に作っている**。これが 2 系統に
分かれた原因である。

したがって Whole Run のコントローラプロセス内で `CombatInstance` を素直に構築することは
**できない**。Whole Run が用意したものを引き渡す形にする。

### 2.6 引き渡すもの

```
WholeRunInstance が用意 → CombatInstance へ注入
  ├─ GameInstance           プロセスに 1 つ。supersede を避けるため必須（2.5）
  └─ DecisionPointRegistry  id 衝突を避けるため（下記）
```

`API/identifiers.py:111` の id 生成は

```python
decision_point_id = f"d-{branch_id}-{next(self._counter):06d}"
```

でカウンタが**レジストリごと**に 1 から始まる。両者が別レジストリを持つと
**どちらも `d-root-000001` を発行して衝突する**。レジストリを 1 つにして、
発行元を 1 箇所に保つ。

`branch_id` は client 指定（Training が発行）であり、`CombatInstance.emulate_actions()` も
client 指定を受け取るので、**そのまま素通しでよい**。対応表は不要。

### 2.7 wrapper は統合しない

`WholeRunSession`（227 行・17 メソッド）は GameInstance への薄いファサードで、独自の状態を
持たない。`LiveCombatSession`（811 行・24 メソッド）は決定フレーム、再同期追跡、
フォールト規律、restore 検証といった**戦闘固有の意味論**を持つ。

**層が違うので統合しない。** 共有するのは GameInstance だけである。

### 2.8 容量と終端

- `max_branches` は Whole Run / Combat とも既定 64 で整合している。Training は
  `start_instance` 応答の `max_emulate_actions_items` を一度だけ読むため、
  **両者の値がずれないこと**が条件
- 分岐が戦闘を終わらせた場合、その branch はそこで終端でよい。Training の beam は
  `transition.kind == "combat_completed"` を terminal として扱う
- **`outcome: "victory"` をそのまま publish してはならない。** Training の
  `decision/value.py:233-247` はそれをラン勝利と解釈し `+100,000` を返すため、
  戦闘に勝っただけの leaf がすべてラン勝利として評価される

## 3. 実装手順

各手順は独立にレビュー・テスト可能であること。手順をまたいで先回りしないこと。

### S1. `WholeRunSession` に capture / restore を公開する（完了・コミット済み）

`capture_combat_snapshot()` / `restore_combat_snapshot()`。
ラウンドトリップのテストが `totalFloor` の欠落も pin している。

### S2. GameInstance と DecisionPointRegistry を注入できるようにする

**やること**

- `CombatInstance` が GameInstance と `DecisionPointRegistry` を外から受け取れるようにする。
  渡されなければ現状どおり自前で用意する（既存の使い方を壊さない）
- `LiveCombatSession` が `shared_game_instance()` を呼んでいる箇所を、注入された
  GameInstance を使う形にする

**やらないこと**: 分岐ロジック、worker pool、rng 仮説、ドロー固定に触ること。

**受け入れ**: 注入した GameInstance / レジストリが実際に使われ、
注入しない既存の呼び出しが従来どおり動くこと。

### S3. `CombatInstance` に snapshot からの起動口を作る

現在の入口は `start_combat(scenario_spec)` だけである。**snapshot を反映して同じ不変条件を
確立する入口**を足す。

- `LiveCombatSession.restore_snapshot_json()` は既にある
- 反映後は `start_combat` の後と同じ状態になること。すなわち `_held_stable_snapshot` と
  `_replay_prefix` が stable アンカーとして確立されること
- `stable` 以外の snapshot は拒否する（restore 自体が拒否するので素通しでよい）
- 2 つの入口は**不変条件を確立する 1 箇所**に収束させる。同じ処理を 2 つ書かない

**scenario から戦闘を開始してはならない。** 敵の初手が RNG 依存の場合、
scenario 開始では実際のゲーム状態と乖離しうる。snapshot 反映なら忠実である。

**受け入れ**: snapshot から起こした `CombatInstance` が `start_instance_response()` で
親と同じ盤面を返し、`emulate_actions` が動くこと。

### S4. Whole Run が戦闘境界で `CombatInstance` を保持する

- root が戦闘に入ったら `CombatInstance` を作り（S2 の注入、S3 の snapshot 反映）、
  Whole Run セッションから capture した snapshot を反映する
- root が戦闘内で進むたびに、同じ行動を `CombatInstance.commit_action()` へ流す。
  アンカー管理は `CombatInstance` が自分で行う（2.4）
- 戦闘が終わったら畳む

**やらないこと**: 非戦闘（map / event / reward / rest / shop）の経路に触ること。

**受け入れ**: 戦闘中は `CombatInstance` が root と同じ盤面を保つこと。

### S5. 戦闘境界の `emulate_actions` を委譲する

- 戦闘境界の分岐要求を `CombatInstance.emulate_actions()` へ回す。`branch_id` は素通し（2.6）
- 非戦闘の分岐は従来どおり Whole Run の worker pool
- publish する DTO に run 級の情報を補う。`totalFloor` / `actFloor` /
  `currentActIndex` / `currentRoomType` は `CombatInstance` の DTO には無いので、
  親 view の値で埋める。戦闘中に floor は変わらないので引き継いで問題ない
- 終端は `combat_completed` として publish する（2.8）

**やらないこと**: 旧 prefix 経路へのフォールバック。静かに落ちると乖離がまた見えなくなる。

**受け入れ**: 戦闘分岐が `load_state` / `choose_room` / 部屋全体の prefix 再生を
一度も行わないこと。

## 4. テスト

- S2: 注入した GameInstance / レジストリが使われること。注入しない既存経路が壊れないこと
- S3: snapshot から起こした `CombatInstance` が親と同じ盤面を返すこと
- S4: root が戦闘内で進むと `CombatInstance` が追随すること
- S5: 戦闘分岐で `choose_room` / prefix 再生が呼ばれないこと（fake で呼び出しを観測）。
  branch DTO の run 位置フィールドが親と一致すること。戦闘勝利が `combat_completed` として
  publish され `outcome: "victory"` が出ないこと
- 全体: `python -m pytest -q` が既存 504 件を維持すること
- Whole Run 実機評価: `AllBranchesFaultedError` が出ないこと

## 5. 今後の課題

- **戦闘中は Whole Run のワーカープールが遊ぶ。** 全戦闘分岐が `CombatInstance` へ移ると、
  ランの大半を占める戦闘中ずっと Whole Run 側のワーカープロセスが待機する。
  正しさを確認したあと、ワーカー数の見直し余地がある
- `Run/run_emulator_bridge.py` の `new_game_instance()` と
  `Combat/emulator_bridge.py` の `shared_game_instance()` が二系統のままである。
  本仕様では注入で回避するが、いずれ一本化を検討する余地がある

## 6. やらないこと

- 旧 prefix 経路へのフォールバック
- `rng_hypothesis` / `replay_draw_restore` を下位から呼び直すこと（`CombatInstance` に委譲する）
- `WholeRunSession` と `LiveCombatSession` の統合（2.7）
- 非戦闘（map / event / reward / rest / shop）分岐の変更
- Training 側の変更
- `combat_session_id` の維持（無害と確認済み）
