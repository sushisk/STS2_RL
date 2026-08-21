# Whole Run の戦闘分岐を Combat Instance へ委譲する

## 0. 文章の目的

Whole Run の Combat branch を、Map snapshot + action prefix の再生から
**`CombatInstance` への委譲**へ移すための実装仕様である。

実装するのは **2 つのインスタンス型をつなぐ境界処理**であり、分岐ロジックの再実装ではない。
分岐に必要なもの（stable アンカー、行動列、`rng_id` からのドロー順序仮説、観測ドローの固定）は
すべて `CombatInstance` に既にある。

## 1. 概要

現在の Whole Run は、戦闘中の 1 手を分岐するたびに地図画面のスナップショットまで巻き戻し、
部屋に入り直し、戦闘の最初からその時点までの全行動を再生している。

branch は仕様上 root と異なる RNG でシミュレートされる。RNG はゲーム情報として与えられない
（RNG を知ってプレイするのは盤面予測ではない）。**同じ action_id 列を新しい RNG で再生すると、
ドローが変わり、打つカードが変わり、与ダメージが変わる。部屋全体ぶんの再生窓に対して
これを行えば別のゲームになる。** 実際に評価が 2 戦 `AllBranchesFaultedError` で落ち、
診断は「同じ部屋の同じ戦闘を最後まで戦って勝った状態」を示していた。

これを、戦闘に入った時点で `CombatInstance` を立てて委譲する形に変える。再生窓は
「部屋全体」から `CombatInstance` が管理する「stable アンカー + 1 マクロ行動」に縮む。

S1（`WholeRunSession` への capture / restore の追加）は実装・コミット済み。

## 2. 設計

### 2.1 戦闘のライフサイクル

Emulator は開始・終了それぞれに単一の明示フラグを出す。推測は不要。

| | signal | 出所 |
|---|---|---|
| **開始** | `choose_room()` の戻り値の `is_combat == True` | `Run/run_emulator_bridge.py:242` |
| **終了** | `step()` の戻り値の `transition.kind == "combat_completed"` | `Run/run_emulator_bridge.py:152, 171-177` |

`step_result["done"]` は**ラン**の終了であって戦闘の終了ではない。混同しないこと。

```
choose_room() → is_combat == True
  → Whole Run セッションへ CaptureSnapshot を要求
  → CombatInstance を用意し、その snapshot を「今の戦闘」として渡す
  → 戦闘境界の emulate_actions を CombatInstance へ委譲
  → step() の transition.kind == "combat_completed"
  → CombatInstance を畳む
```

### 2.2 コントローラは restore しない（最重要）

プロセス内に live な `GameInstance` は 1 つしか存在できない（2.3）。したがって
**コントローラプロセスで snapshot を restore すると、ランを保持している唯一の
GameInstance が戦闘専用状態で上書きされる。**

実測: 復元後は `totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType` が
`None` になり、その戦闘に勝つと `boundary = run_terminal` / `outcome = victory` を返す。
**戦闘が終わってもランを続けられない。** DTO に floor を補っても実行可能なラン状態は戻らない。

したがって役割を分ける。

| | 何をするか |
|---|---|
| **コントローラ** | live な戦闘を **adopt** する。restore は**しない**。capture して配り、分岐を振り分ける |
| **branch worker（別プロセス）** | snapshot を restore する。元々ここが復元の場所である |

`CombatInstance` に必要なのは「snapshot から起動する入口」ではなく
**「既に live な戦闘を adopt する入口」**である。

### 2.3 プロセス内の GameInstance は 1 つだけ

```python
# Combat/emulator_bridge.py:112
def shared_game_instance(repo_root=None):
    """The one and only live GameInstance for this process."""
```

`RunManager.Instance` / `CombatManager.Instance` はプロセス全体の static であり、
2 つ目の `GameInstance` を作ると 1 つ目が supersede され `EnsureNotSuperseded()` が落とす。

Combat 側は既に「プロセスに 1 つ」を正としているが、**Whole Run 側だけが
`new_game_instance()` で毎回新規に作っている**。これが 2 系統に分かれた原因である。

`LiveCombatSession` は `shared_game_instance()` を start / restore / validate / resume の
**複数箇所**で呼ぶ（`Combat/live_combat_session.py:481, 497-509, 554, 568, 598`）。
注入はそのすべてを網羅すること。1 箇所でも漏らすと 2 つ目の GameInstance が生まれる。

### 2.4 引き渡すもの

```
WholeRunInstance が用意 → CombatInstance へ注入
  ├─ GameInstance           プロセスに 1 つ（2.3）
  └─ DecisionPointRegistry  id 衝突を避けるため
```

`API/identifiers.py:111` の id 生成はカウンタが**レジストリごと**に 1 から始まる。

```python
decision_point_id = f"d-{branch_id}-{next(self._counter):06d}"
```

両者が別レジストリを持つと**どちらも `d-root-000001` を発行して衝突する**。
レジストリを 1 つにして発行元を 1 箇所に保つ。

`branch_id` は Training が発行する client 指定であり、`CombatInstance.emulate_actions()` も
client 指定を受け取るので、**そのまま素通しでよい**。対応表は不要。

### 2.5 branch ライフサイクルの所有者を 1 つに決める

`emulate_actions` だけ委譲すると、公開エンドポイントが 2 つのインスタンスに分裂する。
Whole Run は `_bookkeeping` で branch 参照・decision 参照・status・cancel・release・
branch_log・history を所有し、`CombatInstance` も同じライフサイクルを独立に所有している。
さらに Combat の branch 自身が親になりうる。

**決定**: Whole Run インスタンスが**唯一の公開ライフサイクル所有者**であり続ける。

- 公開 `branch_id` ごとに「どちらが実体を持つか」を Whole Run 側の台帳に記録する
- `get_decision` / `get_branch_status` / `cancel_branches` / `release_branches` は
  Whole Run が受け、台帳を見て実体側へ回す
- 戦闘が終わったら、その戦闘に属する branch の実体を解放し、台帳から落とす

### 2.6 容量

`max_branches` は Whole Run / Combat とも既定 64 だが、**値を揃えても合計 64 にはならない。**
両者が独立にカウントするため 64 + 64 になりうる。Training は `start_instance` 応答の
`max_emulate_actions_items` を**一度だけ**読み、それを単一の上限として使う。

**決定**: 上限は Whole Run インスタンス全体でグローバルとする。2.5 の台帳が
公開 branch の総数を持つので、そこで予約と解放を行い、広告する値と一致させる。

### 2.7 wrapper は統合しない

`WholeRunSession`（227 行・17 メソッド）は GameInstance への薄いファサードで、独自の状態を
持たない。`LiveCombatSession`（811 行・24 メソッド）は決定フレーム、再同期追跡、
フォールト規律、restore 検証といった**戦闘固有の意味論**を持つ。

**層が違うので統合しない。** 共有するのは GameInstance だけである。

### 2.8 開始条件と終端の publish 形

**開始**: `choose_room().is_combat == True` を検出した時点で、Whole Run セッションが
`stable` にいることを確認して capture する。`pending_choice` の snapshot は restore できない
（`unsupported_capture_boundary:published_target`）。戦闘の初手は必ず `stable` なので、
入場直後の capture は常に有効である。

**終端**: `CombatInstance` は自分の終端を `terminal: true, outcome: victory/defeat` で表す
（`API/instance_combat.py:516-530`）。**これをそのまま publish してはならない。**
Training の `decision/value.py:233-247` は `outcome in ("victory","defeat")` を
ラン勝敗と解釈し `±100,000` を返すため、戦闘に勝っただけの leaf がすべてラン勝利になる。

戦闘の終端は `transition: {"kind": "combat_completed", ...}` として publish する。
Training はこの形を既に解釈できる。

### 2.9 DTO の差は floor 4 項目では済まない

Whole Run の decision 応答は boundary / legal actions / room_context / history を必ず含む
（`API/instance_whole_run.py:317-347`）。`CombatInstance` の応答はマスク済みエンジンデータと
legal actions だけである（`API/instance_combat.py:233-243`）。

浅いマージは Training から見えるスキーマと branch history の意味論を変えうる。
委譲した branch の DTO は、**Whole Run の decision 応答と同じ形に整えてから publish する。**

### 2.10 実測値

| 項目 | 結果 |
|---|---|
| `CaptureSnapshotJson()` at `stable` | 可能（26,428 bytes）。restore まで通る |
| `CaptureSnapshotJson()` at `pending_choice` | capture は通るが **restore は拒否**: `unsupported_capture_boundary:published_target` |
| capture → restore の戦闘状態 | hp / energy / block / gold / relics / deck / 敵 / 手札 / legal actions **すべて一致** |
| capture → restore の run 位置 | `totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType` が `None` |
| **復元盤面からの End Turn** | **成功する**（下記） |
| `RestoreSnapshotJson` の拒否 | `SnapshotRestoreRejectedException` + 構造化コード。復元前に検証 |
| `combat_session_id` | 復元で変わる。`Lease.is_valid_for()` は比較に使わず `masking.py` が publish も禁止しているため無害 |
| snapshot サイズ | 26〜29 KB |

**復元盤面から End Turn できないという記述は誤りだった。** `SnapshotRestoreMissingMoveError`
の docstring が「すべての restore で Intent が欠落する」と断定していたが、実測では

```
復元前 intent: TACKLE_MOVE(4) / STICKY_SHOT / TACKLE_MOVE(3)
復元後 intent: TACKLE_MOVE(4) / STICKY_SHOT / TACKLE_MOVE(3)   ← 同一
UNSET_MOVE の生存敵: 0
End Turn → 成功（hp 80→73, turn 1→2, 敵は次の Move へ正常に遷移）
```

Emulator 側で既に解消されており、docstring だけが残っていた（コミット `0ff0b16` で修正済み）。
Training の `turn_boundary_scoring` は全 leaf を強制 End Turn で採点するため、
**この点を誤読すると設計全体が成立しないと判断してしまう。**

## 3. 実装手順

各手順は独立にレビュー・テスト可能であること。手順をまたいで先回りしないこと。

### S1. `WholeRunSession` に capture / restore を公開する（完了・コミット済み）

`capture_combat_snapshot()` / `restore_combat_snapshot()`。
ラウンドトリップのテストが `totalFloor` の欠落も pin している。

### S2a. `LiveCombatSession` に GameInstance を注入できるようにする

`shared_game_instance()` の呼び出しを、注入された GameInstance を使う形にする。
**start / restore / validate / resume のすべての取得箇所**を網羅すること（2.3）。
注入されなければ従来どおり `shared_game_instance()` を使う。

**受け入れ**: 注入した GameInstance が全経路で使われ、注入しない既存の呼び出しが従来どおり動く。

### S2b. `CombatInstance` に GameInstance と DecisionPointRegistry を注入できるようにする

渡されなければ現状どおり自前で用意する。公開 id の発行元が 1 箇所になること。

**受け入れ**: 注入したレジストリが実際に使われ、既存の使い方が壊れない。

### S3. `CombatInstance` に live な戦闘を adopt する入口を作る

現在の入口は `start_combat(scenario_spec)` だけである。**既に live な戦闘をそのまま
引き受ける入口**を足す。restore はしない（2.2）。

- adopt 後は `start_combat` の後と同じ不変条件が立つこと。すなわち
  `_held_stable_snapshot` と `_replay_prefix` が stable アンカーとして確立されること
- アンカー用の snapshot はコントローラが capture したものを使う
- 2 つの入口は**不変条件を確立する 1 箇所**に収束させる。同じ処理を 2 つ書かない

**scenario から戦闘を開始してはならない。** 敵の初手が RNG 依存の場合、scenario 開始では
実際のゲーム状態と乖離しうる。

**受け入れ**: adopt した `CombatInstance` が親と同じ盤面を返し、`emulate_actions` が動く。

### S4. Whole Run が戦闘のライフサイクルを管理する

- `choose_room().is_combat == True` で `CombatInstance` を用意し adopt させる（2.1、S2/S3）
- root の進行は **Whole Run セッションが唯一の実行者**である（2.2）。`CombatInstance` は
  root の現在位置を映すだけで、自分で step しない
- `step().transition.kind == "combat_completed"` で畳み、その戦闘の branch を解放する

**やらないこと**: 非戦闘（map / event / reward / rest / shop）の経路に触ること。

**受け入れ**: 戦闘中は `CombatInstance` が root と同じ盤面を保ち、戦闘終了で確実に畳まれる。

### S5. 戦闘境界の分岐を委譲する

- 戦闘境界の `emulate_actions` を `CombatInstance` へ回す。`branch_id` は素通し（2.4）
- **公開ライフサイクルは Whole Run が所有し続ける**（2.5）。`get_decision` /
  `get_branch_status` / `cancel_branches` / `release_branches` は Whole Run が受けて回す
- 容量は Whole Run 全体でグローバルに数える（2.6）
- publish する DTO を Whole Run の decision 応答と同じ形に整える（2.9）
- 終端は `combat_completed` として publish する（2.8）

**やらないこと**: 旧 prefix 経路へのフォールバック。静かに落ちると乖離がまた見えなくなる。

**受け入れ**: 戦闘分岐が `load_state` / `choose_room` / 部屋全体の prefix 再生を
一度も行わない。

## 4. テスト

- S2a: 注入した GameInstance が全取得経路で使われること。注入しない経路が壊れないこと
- S2b: 注入したレジストリが使われ、id の発行元が 1 つであること
- S3: adopt した `CombatInstance` が親と同じ盤面を返すこと
- S4: `is_combat` で立ち上がり、`combat_completed` で畳まれること。戦闘中 root に追随すること
- S5: 戦闘分岐で `choose_room` / prefix 再生が呼ばれないこと（fake で呼び出しを観測）。
  branch DTO が Whole Run の形であること。戦闘勝利が `combat_completed` として publish され
  `outcome: "victory"` が出ないこと
- 全体: `python -m pytest -q` が既存 504 件を維持すること
- Whole Run 実機評価: `AllBranchesFaultedError` が出ないこと

## 5. 今後の課題

- **戦闘中は Whole Run のワーカープールが遊ぶ。** プールそのものは共有できない。
  Combat の `WorkItem` は `DecisionContext` / `PipelineCandidateRef` を持ち、Whole Run の
  `ChoiceWorkItem` は `map_snapshot` / `room_id` / `action_prefix` を持つ別物で、
  `Run/worker_pool.py` の docstring が共通基底化を明示的に警告している。
  ただし**プロセス予算の配分**は調整できる（`_make_branch_pool` は `worker_count` を受け取る）。
  正しさを確認したあとに検討する
- `Run/run_emulator_bridge.py` の `new_game_instance()` と
  `Combat/emulator_bridge.py` の `shared_game_instance()` が二系統のままである。
  本仕様では注入で回避するが、いずれ一本化を検討する余地がある

## 6. やらないこと

- 旧 prefix 経路へのフォールバック
- `rng_hypothesis` / `replay_draw_restore` を下位から呼び直すこと（`CombatInstance` に委譲する）
- `WholeRunSession` と `LiveCombatSession` の統合（2.7）
- worker pool そのものの共有（§5）
- 非戦闘（map / event / reward / rest / shop）分岐の変更
- Training 側の変更
- `combat_session_id` の維持（無害と確認済み）
