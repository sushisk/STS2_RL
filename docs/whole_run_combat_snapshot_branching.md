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

**戦闘に入ったら handle の所有権を `CombatInstance` へ移す。** 戦闘中は Combat が実行者、
Whole Run が観測者になる。再生窓は「部屋全体」から
「stable アンカー + 1 マクロ行動」に縮む。

## 2. 設計

### 2.1 戦闘中は Combat が実行し、Whole Run が観測する

状態を持つ側が実行者であり、状態を持たない側が観測者である。逆にしてはならない。

| | 保持する状態 | 陳腐化するか |
|---|---|---|
| `WholeRunSession` | `_game` **のみ**（`Run/whole_run_session.py:62-69`） | **しない**。キャッシュを持たないので観測は常に真 |
| `LiveCombatSession` | `_current_frame` / `_session_faulted` / `step_count` / `resynchronize_count` / `last_fault_context` | **する**。他者が下の `GameInstance` を進めると全部ずれる |

逆に置くと、**ラン全体を破壊する経路が開く**。

```python
# Combat/live_combat_session.py:776-778  ← step() の中
if not self._is_still_current():
    self._resynchronize(battle_state)
# :686-696
    self._game.ResetFromScenario(scenario)   # 共有 GameInstance を戦闘 scenario で上書き
```

`_is_still_current()` は `(CombatSessionId, StepIndex)` の比較なので、Whole Run が root を
1 手進めれば必ず false になり、次の `commit_action()` がランの地図ごと消す。
`_resynchronize` は共有インスタンスを前提にした親切な機構だが、
**Whole Run 文脈ではその親切さが破壊になる。**

実行者を Combat 側にすると、戦闘中に `GameInstance` を進めるのが `LiveCombatSession` だけに
なるので `_is_still_current()` は常に true であり、この経路は**発火しない**。

情報の粒度も同じ向きを支持する。`step_live_action` は `game.Step()` に加えて
`choice_target` の継続まで解決する（`Combat/battle_emulator.py:858-875`）。
Whole Run 側の `step()` はこの解決を持たない。**細かい側から荒い側へ渡す。**

### 2.2 handle の所有権を移す（設計の中心）

2.1 を成立させるには、戦闘中に Whole Run が共有 game を動かさないことが条件になる。
これを**禁止リストで守ってはならない。**

コントローラプロセスで共有 game を動かしうる経路は次のとおりで、規律で守るには多すぎる。

| 呼び出し | 場所 |
|---|---|
| `Step` | `Run/whole_run_session.py:93` ← `API/instance_whole_run.py:423-427` |
| `ChooseRoom` | `:90` ← `API/instance_whole_run.py:418-422` |
| `LoadState` / `RestoreSnapshotJson` | `:105` / `:112` |
| `StartRun` | `:74` |
| `ResetFromScenario` | `Combat/live_combat_session.py:481, 583, 686` / `Combat/battle_emulator.py:800, 828` |
| `drain_trivial_reward_frontier` | `API/instance_whole_run.py:421-422, 426-427` |

最後の 1 つは特に見落としやすい。root の mutating 経路に同居しているので、
**戦闘中に走ってはならない**（`combat_completed` を受けて委譲を畳み、Whole Run が実行権を
取り戻した後にのみ許される）。

**決定**: handle は**同時に 1 人しか持てない所有物**として扱う。

```
戦闘外 : 所有者 = WholeRunSession
戦闘中 : 所有者 = CombatInstance（LiveCombatSession）
```

- 所有権を持たない側からの **mutating 呼び出しはオブジェクト自身が拒否する**
- 観測（`get_observation` / `get_legal_actions` / `get_room_context` / `save_state` /
  `CaptureSnapshotJson`）は所有権に関係なく許す。これらは game を進めない
- **持っていないものは誤用できない。** 禁止リストは呼び出し箇所が増えるたびに破れるが、
  所有権はコードが担保する

これは `close()` や解放の順序（2.9）とも一貫する。**所有者が畳む順序を決める。**

### 2.3 GameInstance を `shared_game_instance()` に一本化する

`RunManager.Instance` / `CombatManager.Instance` はプロセス全体の static であり、
2 つ目の `GameInstance` を作ると 1 つ目が supersede され `EnsureNotSuperseded()` が落とす。

```python
# Combat/emulator_bridge.py:112
def shared_game_instance(repo_root=None):
    """The one and only live GameInstance for this process."""
```

Whole Run 側だけが独自に作っており、その理由も明記されている。

```
# Run/run_emulator_bridge.py の docstring
Kept deliberately independent from `Combat/emulator_bridge.py` (no import of it):
... the same one-instance-per-process discipline, just owned by this module
instead of shared with Combat's.
```

**「同じ規律を、別々に所有しているだけ」**である。同居しない前提でのみ成立していた独立性なので、
`WholeRunSession` が `shared_game_instance()` から取るようにする。

**注入という仕掛けは要らない。** ただし `run_emulator_bridge` の変換層
（`observation_to_dict` などの Run 固有の DTO 変換）は**そのまま残す**。
置き換えるのは GameInstance の取得経路だけである。

### 2.4 コントローラは restore しない

プロセス内に live な `GameInstance` は 1 つしかない（2.3）。**コントローラプロセスで
snapshot を restore すると、ランを保持している唯一の GameInstance が
戦闘専用状態で上書きされる。**

実測: 復元後は `totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType` が
`None` になり、その戦闘に勝つと `boundary = run_terminal` / `outcome = victory` を返す。
**戦闘が終わってもランを続けられない。**

| | 何をするか |
|---|---|
| **コントローラ** | live な戦闘を **adopt** する。restore は**しない** |
| **branch worker（別プロセス）** | snapshot を restore する。元々ここが復元の場所である |

worker がコントローラの game に触れないことは確認済みである。worker は
`multiprocessing.get_context("spawn")` の別プロセスで、各自 `WholeRunSession` を
プロセス開始時に 1 つ作り、プロセスローカルなオブジェクトに対して
`LoadState` / `ChooseRoom` / `Step` を行う（`Run/worker_pool.py:513-523, 400-430`）。
**CLR の static は共有されない。** ここに guard は不要。

### 2.5 Whole Run モードは明示フラグで宣言する

`Combat/battle_emulator.py:890-902` は `combat_completed` のとき
`result.Observation.IsTerminal` が真であることを assert する。根拠は
`!HasMap => 戦闘終了 == ラン終了` という**地図なしモードの契約**である。

```python
assert bool(result.Observation.IsTerminal), (
    "... legacy no-map mode's run_terminal-on-combat-conclusion contract no longer holds."
)
```

フルランでは戦闘が終わっても次の settled 状態（報酬・地図）へ進むだけなので、
**adopt したフルランの戦闘が終わった最初の瞬間にこの assert が落ちる。**

**地図の有無は Whole Run モードの必要条件だが十分条件ではない。**
`LoadState` でラン snapshot を読んだ combat-only インスタンスなど、
推論が崩れる余地がある。**adopt 時に「Whole Run から使われている」という明示フラグを渡し、
推論をやめて宣言にする。**

フルランモードでの `combat_completed` は 2 つの事実を別々に保つ。

1. **戦闘としての最終状態** — 戦闘の意味論に使う
2. **step 後の live な run 観測と transition** — 引き渡しに使う

最終戦闘 snapshot を「共有 game の現在フレーム」と偽ってはならない。

### 2.6 戦闘終了を `BattleState` に載せる

実行者が Combat になると、Whole Run は `WholeRunSession.step()` の戻り値を受け取らないため、
run 級の `transition` を見られない。必要な情報は**既に手元にある**。

```python
# Combat/battle_emulator.py:858
result = game.Step(action["action_id"])     # result.Transition を持っているが捨てている
```

`BattleState` は dataclass で、「purely additive」な拡張の前例がある
（`_cached_legal_actions`、DecisionFrame）。同じ形で既定 `None` の追加フィールドとして載せる。

**フィールドを足すだけでは足りない。** `with_shuffle_seed()` と `clone_state()`
（`Combat/battle_emulator.py:787-798, 817-826`）は現在の全フィールドをコピーするので、
**伝播も面倒を見ないと静かに消える。** `_wrap()` も含めて経路を揃えること。

**継承は採らない。** `Combat → Run` と `Run → Combat` の import は現在**双方向に 0 件**で
完全に独立している。戦闘終了通知 1 点のために相互依存を作る代償に見合わない。
将来構造化が必要になったら、継承ではなくコールバック（合成）を採る。

### 2.7 root の「プレイ可否」と「分岐可否」を分ける

`CombatInstance._root_view()` は `_held_stable_snapshot is None` のとき
`_START_PENDING_UNSUPPORTED` を投げる（`API/instance_combat.py:208-223`）。
そして `commit_action()` / `start_instance_response()` / `get_decision()` は
**すべて `_root_view()` を通る**（`:245-290`）。

したがって `pending_choice` で adopt すると**観測もコミットもできず、最初の stable に
永久に到達しない。** 「アンカー未確立のまま立てて stable で張る」は、
現状のゲートのままでは成立しない。

**決定**: 2 つを分離する。

```
root がプレイ可能か  ← アンカー不要。観測とコミットはできなければならない
root が分岐可能か    ← アンカー必須。ここだけ明示的な理由を付けて拒否する
```

拒否するのは `emulate_action(s)` だけである。

### 2.8 戦闘のライフサイクル

| | signal | 出所 |
|---|---|---|
| **開始** | `choose_room()` の戻り値の `is_combat == True` | `Run/run_emulator_bridge.py:242` |
| **終了** | 戦闘 step の結果に載る `transition.kind == "combat_completed"` | 2.6 |

`step_result["done"]` は**ラン**の終了であって戦闘の終了ではない。混同しないこと。

```
choose_room() → is_combat == True
  → CombatInstance を adopt で立て、handle の所有権を渡す（2.2）
  → 戦闘中の root commit を CombatInstance が実行する
  → root が stable に達したら CombatInstance が自分でアンカーを張る
  → 戦闘境界の emulate_actions を CombatInstance へ委譲
  → transition.kind == "combat_completed"
  → 公開 branch を解放・tombstone 化（2.9）→ CombatInstance を畳む
  → handle の所有権を Whole Run へ戻す
```

**アンカーはコントローラが張るのではない。** `CombatInstance.commit_action` は
コミット後の boundary が `stable` なら自分で `capture_snapshot()` して再アンカーする。
実行者を Combat 側にしたことで、この既存の仕組みがそのまま使える。

### 2.9 branch ライフサイクルの所有者と tombstone

`emulate_actions` だけ委譲すると、公開エンドポイントが 2 つのインスタンスに分裂する。

**決定**: Whole Run インスタンスが**唯一の公開ライフサイクル所有者**であり続ける。

- 公開 `branch_id` ごとに「どちらが実体を持つか」を Whole Run 側の台帳に記録する
- `get_decision` / `get_branch_status` / `cancel_branches` / `release_branches` は
  Whole Run が受け、台帳を見て実体側へ回す

**台帳から単に消してはならない。** `BranchIdRegistry` は id を恒久的に既知として保つ
（`API/identifiers.py:80-102`）一方、ライフサイクル呼び出し側は bookkeeping の存在を
前提にする（`API/instance_whole_run.py:364-395, 503-541`）。消すと後続の
`get_decision` / status / cancel が `unknown` や `KeyError` で不整合になり、
branch_id の再利用も起こりうる。

したがって**終了時は tombstone にする**。

- 戦闘終了後、その戦闘の公開 branch は `released` を返す
- **id は再利用しない**
- **順序**: 公開 branch をすべて解放・tombstone 化してから `CombatInstance.close()` を呼ぶ。
  `close()` はワーカープールごと落とす（`API/instance_combat.py:556-563`）ので、
  先に閉じると進行中の要求に一貫した status を返せなくなる
- `WholeRunInstance._cancel_and_release_all_branches()`（`:445`）は自分の branch しか
  知らない。**台帳経由で Combat 側にも配ること。** さもないと次の root commit で
  Combat のワーカー作業が生き残り、容量が漏れる

`branch_id` は Training が発行する client 指定であり、`CombatInstance.emulate_actions()` も
client 指定を受け取るので、**そのまま素通しでよい**。対応表は不要。

### 2.10 decision id の発行は 1 回ずつ

`decision_point_id` は両者が別レジストリを持つと衝突する（`API/identifiers.py:111` の
カウンタはレジストリごとに 1 から始まり、どちらも `d-root-000001` を発行する）。
**レジストリを 1 つにする。**

**同じレジストリを渡すだけでは足りない。** 現在、両者とも初期化時
（`API/instance_whole_run.py:254`、`API/instance_combat.py:202`）と
コミット後（`:445-447`、`:335-338`）に root id を発行する。切り替え・畳み込みの順序を
決めないと、有効な client id が stale になったり余分な id が出たりする。

**決定**: **adopt 時に 1 回、委譲した commit ごとに 1 回**だけ発行する。

### 2.11 容量は予約してから配る

`max_branches` は Whole Run / Combat とも既定 64 だが、**値を揃えても合計 64 にはならない。**
Whole Run は `active_branch_count()` と自前の bookkeeping で数え
（`API/instance_whole_run.py:478-501`、Beam `:327-365`）、Combat は自分の `BranchManager` で
数える（`API/instance_combat.py:429-448`）。Training は `start_instance` 応答の
`max_emulate_actions_items` を**一度だけ**読み、それを単一の上限として使う。

**決定**: 上限は Whole Run インスタンス全体でグローバルとする。台帳側が
**Combat を呼ぶ前に atomically 予約し、Combat の失敗・close の全経路で解放する**。
バッチ内の競合も含めて、広告した 64 を超えないこと。

### 2.12 DTO は正規化してから publish する

Whole Run の decision 応答は boundary / legal actions / room_context / history を必ず含む
（`API/instance_whole_run.py:317-347`）。`CombatInstance` の応答はマスク済みエンジンデータと
legal actions だけである（`API/instance_combat.py:233-243`）。

**浅いマージをしてはならない。** Training から見えるスキーマと branch history の意味論が
変わりうる。委譲した結果は、公開 API を越える前に
**Whole Run の decision 応答と同じ形へ正規化する。**

### 2.13 終端の publish 形

`CombatInstance` は自分の終端を `terminal: true, outcome: victory/defeat` で表す
（`API/instance_combat.py:506-530`）。**これをそのまま publish してはならない。**
Training の `decision/value.py:233-247` は `outcome in ("victory","defeat")` を
ラン勝敗と解釈し `±100,000` を返すため、戦闘に勝っただけの leaf がすべてラン勝利になる。

戦闘の終端は `transition: {"kind": "combat_completed", ...}` として publish する。

### 2.14 戦闘が選択で始まる場合（既知の限界）

`GAMBLING_CHIPS` は `AfterPlayerTurnStart` でターン 1 に手札選択を要求するため
（`MegaCrit.Sts2.Core.Models.Relics/GamblingChip.cs:16-26`）、**戦闘が `pending_choice` で
始まる**。`pending_choice` の snapshot は restore できないので
（`unsupported_capture_boundary:published_target`）、開始時点で restore 可能なアンカーが無い。

2.7 の分離により、root のプレイは進む。分岐だけが最初の `stable` まで拒否される。
拒否理由は `emulate_actions_rejected:<detail>` として search trace に残るので、
**後から件数を数えられる**。Training は beam が actionable な結果を得られず、
その 1 決定だけ heuristic fallback に落ちる。

これは `CombatInstance` 側の既知の未対応事項と同じものである
（`API/instance_combat.py:85-88` の `_START_PENDING_UNSUPPORTED`、解法の在り処も併記）。

### 2.15 wrapper は統合しない

`WholeRunSession`（227 行・17 メソッド）は GameInstance への薄いファサードで、独自の状態を
持たない。`LiveCombatSession`（811 行・24 メソッド）は決定フレーム、再同期追跡、
フォールト規律、restore 検証といった**戦闘固有の意味論**を持つ。

**層が違うので統合しない。** 統一するのは GameInstance の取得経路だけである（2.3）。
そしてこの非対称性こそが 2.1 の根拠でもある。

### 2.16 実測値

| 項目 | 結果 |
|---|---|
| `CaptureSnapshotJson()` at `stable` | 可能（26,428 bytes）。restore まで通る |
| `CaptureSnapshotJson()` at `pending_choice` | capture は通るが **restore は拒否**: `unsupported_capture_boundary:published_target` |
| capture → restore の戦闘状態 | hp / energy / block / gold / relics / deck / 敵 / 手札 / legal actions **すべて一致** |
| capture → restore の run 位置 | `totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType` が `None` |
| **復元盤面からの End Turn** | **成功する**（下記） |
| `combat_session_id` | 復元で変わる。`Lease.is_valid_for()` は比較に使わず `masking.py` が publish も禁止しているため無害 |
| snapshot サイズ | 26〜29 KB |

**復元盤面から End Turn できないという記述は誤りだった。**
`SnapshotRestoreMissingMoveError` の docstring が「すべての restore で Intent が欠落する」と
断定していたが、実測では

```
復元前 intent: TACKLE_MOVE(4) / STICKY_SHOT / TACKLE_MOVE(3)
復元後 intent: TACKLE_MOVE(4) / STICKY_SHOT / TACKLE_MOVE(3)   ← 同一
UNSET_MOVE の生存敵: 0
End Turn → 成功（hp 80→73, turn 1→2, 敵は次の Move へ正常に遷移）
```

Emulator 側で既に解消されており docstring だけが残っていた（`0ff0b16` で修正済み）。
Training の `turn_boundary_scoring` は全 leaf を強制 End Turn で採点するため、
**この点を誤読すると設計全体が成立しないと判断してしまう。**

### 2.17 完成形

```
  RL サーバプロセス（コントローラ）
  ┌────────────────────────────────────────────────────────────────┐
  │ WholeRunInstance                                               │
  │   ├─ WholeRunSession        戦闘外の所有者 / 戦闘中は観測のみ  │
  │   ├─ 公開 branch 台帳       branch_id → 実体側 + tombstone     │
  │   ├─ グローバル容量         予約してから配る（2.11）           │
  │   ├─ DecisionPointRegistry ──┐ 共有・発行は 1 回ずつ（2.10）   │
  │   ├─ WholeRunWorkerPool      │ map/event/reward の分岐         │
  │   └─ CombatInstance ◄────────┘ 戦闘中だけ存在                  │
  │        ├─ LiveCombatSession    戦闘中の handle 所有者（2.2）   │
  │        ├─ _held_stable_snapshot  アンカー                      │
  │        └─ BranchWorkerPool ──────┐                             │
  │   shared_game_instance() が返す唯一の GameInstance             │
  └──────────────────────────────────┼─────────────────────────────┘
                                     │ snapshot + rng_id
  ┌──────────────────────────────────▼─────────────────────────────┐
  │ branch worker プロセス（複数）                                 │
  │   restore(anchor, rng_id) → replay → 分岐 action               │
  │   **ここだけが restore する**。コントローラの game には触れない │
  └────────────────────────────────────────────────────────────────┘
```

```
Training            WholeRunInstance          CombatInstance        branch worker
   │  commit_action(map)   │                        │                     │
   ├──────────────────────►│ choose_room()          │                     │
   │                       │ is_combat == True      │                     │
   │                       ├── adopt + handle 移譲 ►│ 非破壊に引き受ける   │
   │                       │  （以後 Whole Run の    │                     │
   │                       │    mutating は拒否）    │                     │
   │  emulate_actions      │                        │                     │
   ├──────────────────────►│───────────────────────►│ アンカー未確立なら   │
   │◄── RequestRejected ───┤◄───────────────────────┤ 分岐のみ拒否（2.7）  │
   │  commit_action(card)  │                        │                     │
   ├──────────────────────►├─── 委譲 ──────────────►│ LiveCombatSession   │
   │                       │◄── BattleState ────────┤  .step() ← 実行者    │
   │                       │   (+ transition)       │ stable なら再アンカー │
   │  emulate_actions      │                        │                     │
   ├──────────────────────►│ 容量を予約 ───────────►│ アンカー + rng_id   │
   │                       │                        ├────────────────────►│
   │◄─ 正規化した DTO ─────┤◄── branch_results ─────┤◄────────────────────┤
   │  commit_action(...)   │ transition.kind ==     │                     │
   ├──────────────────────►│  "combat_completed"    │                     │
   │                       ├─ 解放 → tombstone ────►│                     │
   │                       ├─ close() ─────────────►│                     │
   │                       │ handle を取り戻す       │                     │
```

## 3. 実装手順

### S1. `WholeRunSession` に capture / restore を公開する（完了・コミット済み）

**注記**: 実行者反転により委譲経路では使われない見込み。テストが記録している制約には
価値があるので残すが、完了時点で未使用なら削除を検討する。

### S2. GameInstance を `shared_game_instance()` に一本化する

`WholeRunSession` が `Combat/emulator_bridge.shared_game_instance()` から取る。
`run_emulator_bridge` の **DTO 変換層はそのまま残す**（置き換えるのは取得経路だけ）。
docstring の「意図的に独立」は理由ごと書き換える（なぜ変わったかを残す）。

**受け入れ**: 同一プロセスで両 wrapper を立てても supersede しない。既存経路が動く。

### S3. 戦闘終了を `BattleState` に載せ、フルランの assert を直す

- `step_live_action` が `Transition` を `BattleState` へ載せる
- `with_shuffle_seed()` / `clone_state()` / `_wrap()` の**伝播も揃える**（2.6）
- `combat_completed` の assert を**明示フラグ依存に直す**（2.5）。フルランでは
  `IsTerminal` が偽で正常。フルランでは戦闘最終状態と live 観測を別々に保つ

**受け入れ**: 戦闘終了 action で `combat_completed` が載る。コピーでも落ちない。
フルランモードで assert が落ちない。

### S4a. 非破壊の adopt 構築と、pending root のプレイ可能性

- `CombatInstance.adopt_live(...)` を名前付きファクトリとして足す。既存コンストラクタの
  start 経路を通らない。共有 `DecisionPointRegistry` を受け取る
  （nullable な scenario 引数にしない。破壊的初期化を事故で呼びやすい）
- `_game` を設定し、現在の観測と legal actions から `BattleState` を作り
  `_current_frame` を設定する。**reset も restore も呼ばない**
- **root のプレイ可否と分岐可否を分ける**（2.7）。アンカー未確立でも観測とコミットはできる

**受け入れ**: pending で adopt しても root が進み、最初の stable でアンカーが張られる。

### S4b. adopt モードの handle 所有権と fail closed

- handle を「同時に 1 人しか持てない所有物」にする（2.2）
- 非所有者からの mutating 呼び出しを拒否する。観測は許す
- adopt モードでは `start_combat` / `resume_from` / `restore_snapshot*` /
  自動 `_resynchronize` を到達不能にする

**受け入れ**: 戦闘中に Whole Run 側の mutating を呼ぶと拒否される。
コントローラ経路で `ResetFromScenario` が一度も呼ばれない。

### S5a. 戦闘ライフサイクルと委譲された root commit

- `is_combat` で adopt し所有権を渡す。`combat_completed` で畳み所有権を戻す
- 戦闘中の root commit を `CombatInstance` が実行し、Whole Run は結果を観測して記帳する
- `drain_trivial_reward_frontier` は所有権を取り戻した後にのみ走る（2.2）

**受け入れ**: 戦闘中に `WholeRunSession.step()` が呼ばれない。畳んだ後の非戦闘進行が動く。

### S5b. root 応答と decision id の publish

- 委譲した結果を Whole Run の decision 応答へ正規化する（2.12）
- decision id は adopt 時 1 回、委譲 commit ごと 1 回（2.10）

**受け入れ**: 戦闘中の root 応答が従来と同じ形。id が余分にも stale にもならない。

### S6a. 公開 owner 台帳と tombstone

- 公開 `branch_id` → 実体側の台帳。`get_decision` / status / cancel / release を回す
- 終了時は tombstone。id は再利用しない。**解放してから `close()`**（2.9）
- `_cancel_and_release_all_branches()` を台帳経由で Combat にも配る

**受け入れ**: 戦闘終了後も全エンドポイントが一貫した応答を返す。容量が漏れない。

### S6b. グローバル容量の予約

Combat を呼ぶ前に予約し、失敗・close の全経路で解放する（2.11）。

**受け入れ**: 広告した上限を超えない。バッチ内競合でも超えない。

### S6c. 戦闘境界の `emulate_actions` 委譲

- 戦闘境界の `emulate_actions` を `CombatInstance` へ回す。`branch_id` は素通し
- DTO を正規化（2.12）、終端は `combat_completed`（2.13）
- **旧 prefix 経路へのフォールバックを作らない**

**受け入**: 戦闘分岐が `load_state` / `choose_room` / 部屋全体の prefix 再生を行わない。

## 4. テスト

- S2: 両 wrapper を同一プロセスで立てても supersede しない
- S3: `combat_completed` が載り、コピーでも保たれる。フルランで assert が落ちない
- S4a: pending adopt から root が進み、最初の stable でアンカーが張られる
- S4b: 非所有者の mutating が拒否される。`ResetFromScenario` が呼ばれない（fake で観測）
- S5a: `is_combat` で立ち、`combat_completed` で畳まれる。戦闘中に `WholeRunSession.step()` が呼ばれない
- S5b: root 応答の形が従来と一致。id の発行回数
- S6a: 戦闘終了後の全エンドポイントの一貫性。tombstone
- S6b: 上限を超えない
- S6c: 分岐で prefix 再生が呼ばれない。DTO が Whole Run の形。
  戦闘勝利が `combat_completed` で publish され `outcome: "victory"` が出ない
- 全体: `python -m pytest -q` が既存 504 件を維持する
- Whole Run 実機評価: `AllBranchesFaultedError` が出ない

## 5. 今後の課題

- **戦闘がプレイヤーの選択で始まる場合、最初の `stable` まで分岐できない**（2.14）。
  件数は `emulate_actions_rejected` として観測できる
- **戦闘中は Whole Run のワーカープールが遊ぶ。** プールそのものは共有できない
  （`WorkItem` の型が別物で、`Run/worker_pool.py` の docstring が共通基底化を警告している）。
  プロセス予算の配分は調整できる
- S1 の `capture_combat_snapshot()` / `restore_combat_snapshot()` が未使用なら削除を検討

## 6. やらないこと

- 旧 prefix 経路へのフォールバック
- `rng_hypothesis` / `replay_draw_restore` を下位から呼び直すこと
- `WholeRunSession` と `LiveCombatSession` の統合（2.15）
- `Combat → Run` の依存を作ること（2.6）
- `run_emulator_bridge` の変換層の置き換え（2.3）
- worker pool そのものの共有
- 非戦闘（map / event / reward / rest / shop）分岐の変更
- Training 側の変更
