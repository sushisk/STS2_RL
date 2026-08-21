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

**戦闘中は `CombatInstance` が実行者となり、Whole Run が観測者になる。**
再生窓は「部屋全体」から `CombatInstance` が管理する「stable アンカー + 1 マクロ行動」に縮む。

## 2. 設計

### 2.1 戦闘中は Combat が実行し、Whole Run が観測する（設計の中心）

状態を持つ側が実行者であり、状態を持たない側が観測者である。逆にしてはならない。

| | 保持する状態 | 陳腐化するか |
|---|---|---|
| `WholeRunSession` | `_game` **のみ**（`Run/whole_run_session.py:62-69`） | **しない**。キャッシュを持たないので観測は常に真 |
| `LiveCombatSession` | `_current_frame` / `_session_faulted` / `step_count` / `resynchronize_count` / `last_fault_context` | **する**。他者が下の `GameInstance` を進めると全部ずれる |

これを逆に置くと、**ラン全体を破壊する経路が開く**。

```python
# Combat/live_combat_session.py:776-778  ← step() の中
if not self._is_still_current():
    self._resynchronize(battle_state)

# :686-696
def _resynchronize(self, battle_state):
    scenario = build_scenario_from_state(battle_state.engine_state, ...)
    self._game.ResetFromScenario(scenario)   # ← 共有 GameInstance を戦闘 scenario で上書き
```

`_is_still_current()` は `(CombatSessionId, StepIndex)` を比較するだけなので、
**Whole Run が root を 1 手進めれば必ず false になる**。次の
`CombatInstance.commit_action()` が `ResetFromScenario` を呼び、ランの地図と部屋の状態が消える。
`_resynchronize` は共有インスタンスを前提にした親切な機構だが、
**Whole Run 文脈ではその親切さが破壊になる。**

**実行者を Combat 側にすると、この経路は発火しない。** 戦闘中に `GameInstance` を進めるのが
`LiveCombatSession` だけになるので `_is_still_current()` は常に true であり、
`ResetFromScenario` が呼ばれる契機自体が消える。

情報の粒度も同じ向きを支持する。`step_live_action` は `game.Step()` に加えて
`choice_target` の継続まで解決する（`Combat/battle_emulator.py:858-875`）。
Whole Run 側の `step()` はこの解決を持たない。**細かい側から荒い側へ渡す。**

```
戦闘中
  ├ 実行者 : CombatInstance（LiveCombatSession.step）
  │          フレーム・フォールト規律・アンカー・prefix を維持
  └ 観測者 : WholeRunInstance
             共有 GameInstance を観測して run 級の記帳を更新
戦闘外
  └ 実行者 : WholeRunSession（従来どおり）
```

### 2.2 GameInstance を `shared_game_instance()` に一本化する

`RunManager.Instance` / `CombatManager.Instance` はプロセス全体の static であり、
2 つ目の `GameInstance` を作ると 1 つ目が supersede され `EnsureNotSuperseded()` が落とす。

Combat 側は既に「プロセスに 1 つ」を正としている。

```python
# Combat/emulator_bridge.py:112
def shared_game_instance(repo_root=None):
    """The one and only live GameInstance for this process."""
```

Whole Run 側だけが独自に作っており、その理由も明記されている。

```
# Run/run_emulator_bridge.py の docstring
Kept deliberately independent from `Combat/emulator_bridge.py` (no import of it):
... The Whole Run layer instead constructs exactly one `GameInstance` per OS process
itself - the same one-instance-per-process discipline, just owned by this module
instead of shared with Combat's.
```

**「同じ規律を、別々に所有しているだけ」**である。両者が同居しない前提では足りていたが、
同居させる今はこの独立性が害になる。`WholeRunSession` が `shared_game_instance()` から
取るようにすれば、両者は自然に同じインスタンスを見る。

**注入という仕掛けは要らない。** 注入は分岐を温存したまま迂回する回り道であり、
`shared_game_instance()` の呼び出し箇所（start / restore / validate / resume）を
1 つでも漏らすと 2 つ目の `GameInstance` が生まれるという網羅リスクも背負う。

### 2.3 コントローラは restore しない

プロセス内に live な `GameInstance` は 1 つしかない（2.2）。**コントローラプロセスで
snapshot を restore すると、ランを保持している唯一の GameInstance が
戦闘専用状態で上書きされる。**

実測: 復元後は `totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType` が
`None` になり、その戦闘に勝つと `boundary = run_terminal` / `outcome = victory` を返す。
**戦闘が終わってもランを続けられない。** DTO に floor を補っても実行可能なラン状態は戻らない。

| | 何をするか |
|---|---|
| **コントローラ** | live な戦闘を **adopt** する。restore は**しない** |
| **branch worker（別プロセス）** | snapshot を restore する。元々ここが復元の場所である |

`CombatInstance` に必要なのは「snapshot から起動する入口」ではなく
**「既に live な戦闘を adopt する入口」**である。adopt は非破壊でなければならない。
`start_combat` / `resume_from` / `restore_snapshot*` / 自動 `_resynchronize` は
コントローラ経路では使わない。フレーム不一致は **fail closed**（落ちる）であり、
決して reset で「直す」ことをしない。

### 2.4 戦闘のライフサイクル

| | signal | 出所 |
|---|---|---|
| **開始** | `choose_room()` の戻り値の `is_combat == True` | `Run/run_emulator_bridge.py:242` |
| **終了** | 戦闘 step の結果に載る `transition.kind == "combat_completed"` | 2.5 |

`step_result["done"]` は**ラン**の終了であって戦闘の終了ではない。混同しないこと。

```
choose_room() → is_combat == True
  → CombatInstance を用意し、live な戦闘を adopt させる（restore はしない）
  → 戦闘中の root commit を CombatInstance へ委譲する（実行者は Combat）
  → root が stable に達したら CombatInstance が自分でアンカーを張る
  → 戦闘境界の emulate_actions を CombatInstance へ委譲
  → transition.kind == "combat_completed"
  → CombatInstance を畳み、以降の実行者を Whole Run に戻す
```

**アンカーはコントローラが張るのではない。** `CombatInstance.commit_action` は
コミット後の boundary が `stable` なら自分で `capture_snapshot()` して再アンカーする。
実行者を Combat 側にしたことで、この既存の仕組みがそのまま使える。

### 2.5 戦闘終了を `BattleState` に載せる

実行者が Combat になると、Whole Run は `WholeRunSession.step()` の戻り値を受け取らないため、
run 級の `transition` を見られない。

必要な情報は**既に手元にある**。`step_live_action` は `game.Step()` の結果を受け取りながら
`Transition` を捨てているだけである。

```python
# Combat/battle_emulator.py:858
result = game.Step(action["action_id"])     # result.Transition を持っている
```

`BattleState` は dataclass で、**「純粋に追加的」なフィールド拡張の前例がある**
（`_cached_legal_actions`、DecisionFrame）。同じ形で `transition` を既定 `None` の
追加フィールドとして載せる。

**継承は採らない。** `Combat → Run` と `Run → Combat` の import は現在**双方向に 0 件**で、
完全に独立している。戦闘終了通知 1 点のために相互依存を作る代償に見合わない。
dataclass のフィールド 1 つなら、produce するのは Combat、consume するのは Whole Run で、
定義場所を分ける必要がない。将来構造化が必要になったら、継承ではなくコールバック（合成）を採る。

### 2.6 branch ライフサイクルの所有者を 1 つに決める

`emulate_actions` だけ委譲すると、公開エンドポイントが 2 つのインスタンスに分裂する。
Whole Run は `_bookkeeping` で branch 参照・decision 参照・status・cancel・release・
branch_log・history を所有し、`CombatInstance` も同じライフサイクルを独立に所有している。

**決定**: Whole Run インスタンスが**唯一の公開ライフサイクル所有者**であり続ける。

- 公開 `branch_id` ごとに「どちらが実体を持つか」を Whole Run 側の台帳に記録する
- `get_decision` / `get_branch_status` / `cancel_branches` / `release_branches` は
  Whole Run が受け、台帳を見て実体側へ回す
- 戦闘が終わったら、その戦闘に属する branch の実体を解放し、台帳から落とす

`branch_id` は Training が発行する client 指定であり、`CombatInstance.emulate_actions()` も
client 指定を受け取るので、**そのまま素通しでよい**。対応表は不要。

`decision_point_id` は両者が別レジストリを持つと衝突する
（`API/identifiers.py:111` のカウンタはレジストリごとに 1 から始まり、
どちらも `d-root-000001` を発行する）。**レジストリを 1 つにする。**
戦闘中は Combat が実行者として発行し、Whole Run はその id をそのまま publish する。

### 2.7 容量

`max_branches` は Whole Run / Combat とも既定 64 だが、**値を揃えても合計 64 にはならない。**
両者が独立にカウントするため 64 + 64 になりうる。Training は `start_instance` 応答の
`max_emulate_actions_items` を**一度だけ**読み、それを単一の上限として使う。

**決定**: 上限は Whole Run インスタンス全体でグローバルとする。2.6 の台帳が
公開 branch の総数を持つので、そこで予約と解放を行い、広告する値と一致させる。

### 2.8 wrapper は統合しない

`WholeRunSession`（227 行・17 メソッド）は GameInstance への薄いファサードで、独自の状態を
持たない。`LiveCombatSession`（811 行・24 メソッド）は決定フレーム、再同期追跡、
フォールト規律、restore 検証といった**戦闘固有の意味論**を持つ。

**層が違うので統合しない。** 統一するのは GameInstance の取得経路だけである（2.2）。
そしてこの非対称性こそが 2.1 の根拠でもある。状態を持たない `WholeRunSession` は
観測者に向き、状態を持つ `LiveCombatSession` は実行者に向く。

### 2.9 戦闘が選択で始まる場合（既知の限界）

戦闘の最初の決定は `stable` とは限らない。`GAMBLING_CHIPS` は `AfterPlayerTurnStart` で
ターン 1 に手札選択を要求するため
（`MegaCrit.Sts2.Core.Models.Relics/GamblingChip.cs:16-26`）、**戦闘が `pending_choice` で
始まる**。`pending_choice` の snapshot は restore できないので
（`unsupported_capture_boundary:published_target`）、開始時点で restore 可能なアンカーが無い。

`CombatInstance` を adopt で立て、アンカーは root が最初の `stable` に達したときに
`CombatInstance` 自身が張る。それまでの戦闘分岐の要求は**明示的な理由を付けて拒否する**。

これは `CombatInstance` 側の既知の未対応事項と同じものである
（`API/instance_combat.py:85-88` の `_START_PENDING_UNSUPPORTED`:
"does not yet support a Start-of-Combat Pending root"、解法の在り処も併記されている）。
委譲によって新たに作り込む欠陥ではなく、既存の制約をそのまま引き継ぐ。

拒否は握りつぶさない。Training 側は beam が actionable な結果を得られず、その 1 決定だけ
heuristic fallback に落ちる。拒否理由は `emulate_actions_rejected:<detail>` として
search trace に残るので、**後から件数を数えられる**。

### 2.10 終端の publish 形

`CombatInstance` は自分の終端を `terminal: true, outcome: victory/defeat` で表す
（`API/instance_combat.py:516-530`）。**これをそのまま publish してはならない。**
Training の `decision/value.py:233-247` は `outcome in ("victory","defeat")` を
ラン勝敗と解釈し `±100,000` を返すため、戦闘に勝っただけの leaf がすべてラン勝利になる。

戦闘の終端は `transition: {"kind": "combat_completed", ...}` として publish する。
Training はこの形を既に解釈できる。

### 2.11 DTO の差は floor 4 項目では済まない

Whole Run の decision 応答は boundary / legal actions / room_context / history を必ず含む
（`API/instance_whole_run.py:317-347`）。`CombatInstance` の応答はマスク済みエンジンデータと
legal actions だけである（`API/instance_combat.py:233-243`）。

浅いマージは Training から見えるスキーマと branch history の意味論を変えうる。
委譲した branch の DTO は、**Whole Run の decision 応答と同じ形に整えてから publish する。**

### 2.12 実測値

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

Emulator 側で既に解消されており docstring だけが残っていた（コミット `0ff0b16` で修正済み）。
Training の `turn_boundary_scoring` は全 leaf を強制 End Turn で採点するため、
**この点を誤読すると設計全体が成立しないと判断してしまう。**

### 2.13 完成形

**プロセスと所有関係**

```
  RL サーバプロセス（コントローラ）
  ┌────────────────────────────────────────────────────────────────┐
  │ WholeRunInstance                                               │
  │   ├─ WholeRunSession        戦闘外の実行者 / 戦闘中は観測者    │
  │   ├─ 公開 branch 台帳       branch_id → 実体側（2.6）          │
  │   ├─ DecisionPointRegistry ──┐ 共有（2.6）                     │
  │   ├─ WholeRunWorkerPool      │ map/event/reward の分岐         │
  │   └─ CombatInstance ◄────────┘ 戦闘中だけ存在                  │
  │        ├─ LiveCombatSession    戦闘中の実行者（2.1）           │
  │        │    （adopt。restore しない）                          │
  │        ├─ _held_stable_snapshot  アンカー                      │
  │        ├─ _replay_prefix         アンカーからの行動列          │
  │        └─ BranchWorkerPool ──────┐                             │
  │                                  │                             │
  │   shared_game_instance() が返す唯一の GameInstance を全員が共有 │
  └──────────────────────────────────┼─────────────────────────────┘
                                     │ snapshot + rng_id
  ┌──────────────────────────────────▼─────────────────────────────┐
  │ branch worker プロセス（複数）                                 │
  │   restore(anchor, rng_id) → replay → 分岐 action               │
  │   **ここだけが restore する**                                  │
  └────────────────────────────────────────────────────────────────┘
```

**戦闘 1 回の流れ**

```
Training            WholeRunInstance          CombatInstance        branch worker
   │                       │                        │                     │
   │  commit_action(map)   │                        │                     │
   ├──────────────────────►│ choose_room()          │                     │
   │                       │ is_combat == True      │                     │
   │                       ├───── adopt ───────────►│ live をそのまま      │
   │                       │                        │ 引き受ける（非破壊） │
   │                       │                        │                     │
   │  ── 戦闘が pending_choice で始まった場合（2.9）──                    │
   │  emulate_actions      │                        │                     │
   ├──────────────────────►│───────────────────────►│ アンカー未確立      │
   │◄── RequestRejected ───┤◄───────────────────────┤ → 拒否・trace に残る │
   │                       │                        │                     │
   │  commit_action(card)  │                        │                     │
   ├──────────────────────►├─── 委譲 ──────────────►│ LiveCombatSession   │
   │                       │                        │  .step()  ← 実行者   │
   │                       │◄── BattleState ────────┤ stable なら          │
   │                       │    (+ transition)      │ capture して再アンカー│
   │                       │ 観測して run 級を記帳  │                     │
   │                       │                        │                     │
   │  emulate_actions      │                        │                     │
   ├──────────────────────►│───────────────────────►│ アンカー + rng_id   │
   │                       │                        ├────────────────────►│
   │                       │                        │                     │ restore
   │                       │                        │◄────────────────────┤ → replay
   │◄─ Whole Run 形の DTO ─┤◄── branch_results ─────┤                     │ → step
   │      （2.11 で整形）   │                        │                     │
   │                       │                        │                     │
   │  commit_action(...)   │ transition.kind ==     │                     │
   ├──────────────────────►│  "combat_completed"    │                     │
   │                       ├───── 畳む ────────────►│ branch を解放       │
   │                       │ 以降の実行者は Whole Run に戻る              │
```

**変更されるクラスと、加わるもの**

| クラス | 加わるもの | 手順 |
|---|---|---|
| `Run/run_emulator_bridge.py` | `shared_game_instance()` へ一本化 | S2 |
| `Combat/battle_emulator.py` | `BattleState.transition`（既定 `None` の追加フィールド） | S3 |
| `Combat/live_combat_session.py` | 非破壊の adopt 入口。コントローラ経路での破壊系 API 禁止 | S4 |
| `API/instance_combat.py` | live を adopt する入口。`DecisionPointRegistry` を外から受ける | S4 |
| `API/instance_whole_run*.py` | 戦闘ライフサイクル管理、実行の委譲、公開 branch 台帳、容量、DTO 整形 | S5・S6 |

**変更しないもの**: `Run/worker_pool.py` の分岐機構、`rng_hypothesis`、`replay_draw_restore`、
非戦闘の分岐経路、Training 側。

## 3. 実装手順

各手順は独立にレビュー・テスト可能であること。手順をまたいで先回りしないこと。

### S1. `WholeRunSession` に capture / restore を公開する（完了・コミット済み）

`capture_combat_snapshot()` / `restore_combat_snapshot()`。
ラウンドトリップのテストが `totalFloor` の欠落も pin している。

**注記**: 実行者反転により、この 2 つは委譲経路では使われない見込みである
（アンカーは `CombatInstance` が `LiveCombatSession.capture_snapshot()` で張る）。
テストが記録している制約には価値があるので残すが、S6 完了時点で未使用なら削除を検討する。

### S2. GameInstance を `shared_game_instance()` に一本化する

`WholeRunSession` が `Combat/emulator_bridge.shared_game_instance()` から取るようにする。

- `Run/run_emulator_bridge.py` の docstring にある「意図的に独立」の理由は、
  両者が同居しない前提のものである。同居させる以上、その前提は失効している。
  **理由ごと書き換えること。** 消すのではなく、なぜ変わったかを残す
- worker プロセスは各自 1 つの `WholeRunSession` を作るが、プロセスごとに 1 つなので影響しない

**受け入れ**: 同一プロセスで `WholeRunSession` と `LiveCombatSession` を両方立てても
supersede が起きないこと。既存の Whole Run 経路が従来どおり動くこと。

### S3. 戦闘終了を `BattleState` に載せる

`step_live_action` が `game.Step()` の `Transition` を捨てずに `BattleState` へ載せる。

- `BattleState` の既定 `None` の追加フィールドとして、既存の「purely additive」な
  拡張（`_cached_legal_actions`、DecisionFrame）と同じ形にする
- Combat から Run への依存を作らない（2.5）

**受け入れ**: 戦闘を終わらせる action の結果に `combat_completed` が載ること。
それ以外では `None` のままで、既存の呼び出しが壊れないこと。

### S4. `CombatInstance` に live を adopt する入口を作る

現在の入口は `start_combat(scenario_spec)` だけである。**既に live な戦闘をそのまま
引き受ける非破壊の入口**を足す。

- `_game` を設定し、現在の観測と legal actions から `BattleState` を作り、
  `_current_frame` を設定する。**reset も restore も呼ばない**
- adopt する盤面が `stable` なら `_held_stable_snapshot` / `_replay_prefix` が
  アンカーとして確立されること。`pending_choice` なら**アンカー未確立のまま立てられること**（2.9）。
  既存の `_held_stable_snapshot is None` チェック（`API/instance_combat.py:218`）が
  その状態を既に表現している
- adopt モードでは `start_combat` / `resume_from` / `restore_snapshot*` /
  自動 `_resynchronize` を禁止する。フレーム不一致は **fail closed**（2.3）
- `DecisionPointRegistry` を外から受け取れるようにする（2.6）
- 2 つの入口は**不変条件を確立する 1 箇所**に収束させる。同じ処理を 2 つ書かない

**受け入れ**: adopt した `CombatInstance` が親と同じ盤面を返し、`emulate_actions` が動く。
コントローラ経路で `ResetFromScenario` が一度も呼ばれない。

### S5. Whole Run が戦闘ライフサイクルを管理し、実行を委譲する

- `choose_room().is_combat == True` で `CombatInstance` を用意し adopt させる（2.4、S4）
- **戦闘中の root commit を `CombatInstance` へ委譲する。**Whole Run は自分で step せず、
  返ってきた結果を観測して run 級の記帳（floor / room / history）を更新する
- `transition.kind == "combat_completed"`（S3）で畳み、実行者を Whole Run に戻す

**やらないこと**: 非戦闘（map / event / reward / rest / shop）の経路に触ること。

**受け入れ**: 戦闘中に `WholeRunSession.step()` が呼ばれないこと。戦闘終了で確実に畳まれ、
以降の非戦闘の進行が従来どおり動くこと。

### S6. 戦闘境界の分岐を委譲する

- 戦闘境界の `emulate_actions` を `CombatInstance` へ回す。`branch_id` は素通し（2.6）
- **公開ライフサイクルは Whole Run が所有し続ける**（2.6）。`get_decision` /
  `get_branch_status` / `cancel_branches` / `release_branches` は Whole Run が受けて回す
- 容量は Whole Run 全体でグローバルに数える（2.7）
- publish する DTO を Whole Run の decision 応答と同じ形に整える（2.11）
- 終端は `combat_completed` として publish する（2.10）

**やらないこと**: 旧 prefix 経路へのフォールバック。静かに落ちると乖離がまた見えなくなる。

**受け入れ**: 戦闘分岐が `load_state` / `choose_room` / 部屋全体の prefix 再生を
一度も行わない。

## 4. テスト

- S2: 同一プロセスで両 wrapper を立てても supersede しないこと。既存経路が壊れないこと
- S3: 戦闘終了 action で `combat_completed` が載り、それ以外では `None` であること
- S4: adopt した `CombatInstance` が親と同じ盤面を返すこと。
  コントローラ経路で `ResetFromScenario` が呼ばれないこと（fake で呼び出しを観測）
- S5: `is_combat` で立ち上がり `combat_completed` で畳まれること。
  戦闘中に `WholeRunSession.step()` が呼ばれないこと
- S6: 戦闘分岐で `choose_room` / prefix 再生が呼ばれないこと。
  branch DTO が Whole Run の形であること。戦闘勝利が `combat_completed` として publish され
  `outcome: "victory"` が出ないこと
- 全体: `python -m pytest -q` が既存 504 件を維持すること
- Whole Run 実機評価: `AllBranchesFaultedError` が出ないこと

## 5. 今後の課題

- **戦闘がプレイヤーの選択で始まる場合、最初の `stable` まで分岐できない（既知の限界）。**
  `CombatInstance` 側の `_START_PENDING_UNSUPPORTED`（`API/instance_combat.py:85-88`）と
  同一の制約であり、そこに解法の在り処も書かれている
  （`main_loop.py` の `CombatStartReplayRoot` の扱い）。件数は
  `emulate_actions_rejected` として観測できるので、問題になるならその機構を通す
- **戦闘中は Whole Run のワーカープールが遊ぶ。** プールそのものは共有できない。
  Combat の `WorkItem` は `DecisionContext` / `PipelineCandidateRef` を持ち、Whole Run の
  `ChoiceWorkItem` は `map_snapshot` / `room_id` / `action_prefix` を持つ別物で、
  `Run/worker_pool.py` の docstring が共通基底化を明示的に警告している。
  ただし**プロセス予算の配分**は調整できる（`_make_branch_pool` は `worker_count` を受け取る）
- S1 で足した `capture_combat_snapshot()` / `restore_combat_snapshot()` が
  委譲経路で未使用なら、削除を検討する

## 6. やらないこと

- 旧 prefix 経路へのフォールバック
- `rng_hypothesis` / `replay_draw_restore` を下位から呼び直すこと（`CombatInstance` に委譲する）
- `WholeRunSession` と `LiveCombatSession` の統合（2.8）
- `Combat → Run` の依存を作ること（2.5）
- worker pool そのものの共有（§5）
- 非戦闘（map / event / reward / rest / shop）分岐の変更
- Training 側の変更
