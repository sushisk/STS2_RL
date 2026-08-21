# Whole Run の戦闘分岐を Combat Phase へ委譲する

## 0. 文章の目的

Whole Run の Combat branch を、Map snapshot + action prefix の再生から
**戦闘の意味論を持つ内部コンポーネント（`CombatPhase`）への委譲**へ移すための実装仕様である。

再利用するのは分岐アルゴリズムであって、公開インスタンスではない。
`CombatInstance` は単体用の公開ファサードとして独立したまま残る。

## 1. 概要

現在の Whole Run は、戦闘中の 1 手を分岐するたびに地図画面のスナップショットまで巻き戻し、
部屋に入り直し、戦闘の最初からその時点までの全行動を再生している。

branch は仕様上 root と異なる RNG でシミュレートされる。RNG はゲーム情報として与えられない
（RNG を知ってプレイするのは盤面予測ではない）。**同じ action_id 列を新しい RNG で再生すると、
ドローが変わり、打つカードが変わり、与ダメージが変わる。部屋全体ぶんの再生窓に対して
これを行えば別のゲームになる。** 実際に評価が 2 戦 `AllBranchesFaultedError` で落ち、
診断は「同じ部屋の同じ戦闘を最後まで戦って勝った状態」を示していた。

変更後は、プロセスに 1 つの **`GameAccess`** が唯一の `GameInstance` と lease を持ち、
戦闘中は `CombatPhase` が lease を借りて実行者になる。再生窓は「部屋全体」から
「stable アンカー + 1 マクロ行動」に縮む。

## 2. 設計

### 2.1 プロセスモデル

**1 プロセス = 1 ラン。同時に入る戦闘は 1 つ。**

これは方針ではなく前提であり、**強制する**。

現在の `API/server.py` は**セッション単位の制約は持っている**。

```python
# :149-154  同一セッションが 2 つ目のインスタンスを持つことは拒否される
if ledger.active_instance_id is not None:
    raise RequestRejected(..., fault_kind=FAULT_SESSION_INSTANCE_CONFLICT)
# :65  セッション数の上限もある
self._max_sessions = max_sessions
```

**持っていないのはセッションをまたぐ制約である。** 危険なのは「無制限」ではなく、
別セッションが 2 つ目の `GameInstance` を作ったときに、CLR のプロセスグローバルな
`RunManager.Instance` / `CombatManager.Instance` が奪われることである。
そのとき 1 つ目は `EnsureNotSuperseded()` の不透明な `InvalidOperationException` で落ち、
**原因を示さない。** これは今回の設計が作る穴ではなく、現状すでに存在する。

`GameAccess` が game を所有する（2.2）ため、**2 つ目の game 所有インスタンスの生成は
意味のあるエラーで拒否する**。将来複数戦闘を扱う可能性はあるが、
本仕様では「同時に 1 戦闘」を不変条件とし、2 回目の adopt を拒否する（再入・入れ子は不可）。

### 2.2 `GameAccess` — プロセスに 1 つの lease

共有 `GameInstance` は**プロセスグローバルな資源**である。したがって所有権を
「`WholeRunSession` と `CombatPhase` の二者間の約束」として表現しても守れない。
`WholeRunSession` は直接のミューテータと raw game のエスケープハッチを公開しており、
第三者のインスタンスは合意の外にいる。

**決定**: 共有 game への唯一のアクセスファサードとして `GameAccess` を置く。**薄く保つ。**

```
GameAccess が持つもの
  ├ 唯一の GameInstance
  └ lease 状態機械（世代トークン付き）
       RUN          Whole Run が mutating できる
       COMBAT       CombatPhase が mutating できる
       TRANSFERRING 遷移中。誰も mutating できない
       POISONED     恒久的に不可。明示 close まで
```

- **mutating 操作はすべて `GameAccess` を通り、lease と世代を検査する。**
  非所有者・古い世代は拒否する
- **観測は所有権に関係なく許す。** `get_observation` / `get_legal_actions` /
  `get_room_context` / `save_state` / `CaptureSnapshotJson` は game を進めない
- **フェーズのライフサイクルは持たない。** `CombatPhase` を作る・畳むのは
  `WholeRunInstance` の仕事である。`GameAccess` が知るのは「今誰が触ってよいか」だけで、
  ランの状態機械まで抱え込まない
- ただし遷移の**開始・確定・巻き戻し**は `GameAccess` が知る（2.4）

**エスケープハッチを塞がなければ lease は飾りである。** `WholeRunSession` は
`raw_game_instance`（`Run/whole_run_session.py:65-69`）で生の `GameInstance` を公開し、
`start_run` / `choose_room` / `step` / `load_state` / `restore_combat_snapshot`
（`:74-114`）を直接のミューテータとして持つ。`GameAccess` を**隣に置くだけでは
何も強制されない。** これらを private にするか `GameAccess` 経由に付け替えることが、
lease の成立条件である。

**規律で守ってはならない。** 戦闘中に共有 game を動かしうる経路は次のとおりで、
禁止リストは呼び出し箇所が増えるたびに破れる。

| 呼び出し | 場所 |
|---|---|
| `Step` | `Run/whole_run_session.py:93` ← `API/instance_whole_run.py:423-427` |
| `ChooseRoom` | `:90` ← `API/instance_whole_run.py:418-422` |
| `LoadState` / `RestoreSnapshotJson` | `:105` / `:112` |
| `StartRun` | `:74` |
| `ResetFromScenario` | `Combat/live_combat_session.py:481, 583, 686` / `Combat/battle_emulator.py:800, 828` |
| `drain_trivial_reward_frontier` | `API/instance_whole_run.py:421-422, 426-427` |

最後の 1 つは特に見落としやすい。root の mutating 経路に同居しているので、
**戦闘中に走ってはならない**（フェーズを出る処理の一部として扱う。2.5）。

### 2.3 `CombatPhase` — 公開インスタンスを入れ子にしない

再利用したいのは**戦闘の意味論**である。stable アンカー、アンカーからの行動列、
`rng_id` からのドロー順序仮説、`pending_choice` までの観測ドローの固定。

`CombatInstance` はライブラリではない。**独自の root ライフサイクル、branch manager、
識別子レジストリ、終端 DTO 契約、scenario を開始するコンストラクタを持つ公開インスタンス**である。
これを入れ子にすると `WholeRunInstance` が**ルータ・識別子ブローカー・容量ブローカー・
DTO 翻訳者・後始末係**になる。複雑さは配管ではなく、入れ子にしたこと自体から来る。

**決定**: 意味論を `CombatPhase` として切り出す。

| | 持つもの |
|---|---|
| **`CombatPhase`**（内部） | `LiveCombatSession` / `_held_stable_snapshot` / 行動列 / rng 仮説 / ドロー固定 / branch worker pool と branch manager |
| **`CombatInstance`**（公開・据え置き） | 単体用ファサード。`CombatPhase` を standalone モードで使う。公開 API・識別子・DTO 整形は従来どおり |
| **`WholeRunInstance`** | `CombatPhase` を adopted モードで使う。**公開 API は 1 つだけ**なので、台帳・id ブローカー・DTO 翻訳は要らない |

**公開ライフサイクルが 1 つになることが、この切り出しの主な利得である。**

### 2.4 戦闘の出入りは 1 トランザクション

「adopt して」「所有権を渡して」「使い始める」を別々の手順にすると、**中間状態が不正になる**。
adopt 済みだが非所有者の `CombatPhase` は無効であり、フェーズ初期化前に mutating を手放した
`WholeRunInstance` も無効である。

**決定**: `enter_combat_phase` / `leave_combat_phase` をそれぞれ**単一のトランザクション**にする。

```
enter_combat_phase
  1. GameAccess: RUN → TRANSFERRING
  2. CombatPhase を adopt で構築（非破壊）。アンカーが張れるなら張る
  3. root decision をちょうど 1 回発行する
  4. GameAccess: TRANSFERRING → COMBAT（確定）
  ※ 4 の前に失敗したら巻き戻して RUN へ戻す。副作用を残さない

leave_combat_phase
  1. GameAccess: COMBAT → TRANSFERRING
  2. 戦闘終了レコード（2.6）を取り込む
  3. 公開 branch を解放・tombstone 化 → その後に phase を畳む（順序は逆にしない）
  4. Whole Run の root を正規化。drain_trivial_reward_frontier はここで走る
  5. GameAccess: TRANSFERRING → RUN（確定）
```

**物理的な `Step` は成功したが記帳・publish 前に失敗した場合**は、上のどちらでもない。
lease を返せばランが未分類の状態から再開し、保持すれば死んだランが漏れる。

**決定**: `POISONED` へ落とす。以後すべての mutating を拒否し、worker を解放し、
`WholeRunInstance` を terminal/faulted にして明示 close を待つ。
**戦闘 snapshot からの暗黙の回復はできない**（ラン位置が戻らない。2.7）。

### 2.5 戦闘中は Combat が実行し、Whole Run が観測する

状態を持つ側が実行者であり、状態を持たない側が観測者である。逆にしてはならない。

| | 保持する状態 | 陳腐化するか |
|---|---|---|
| `WholeRunSession` | `_game` **のみ**（`Run/whole_run_session.py:62-69`） | **しない**。観測は常に真 |
| `LiveCombatSession` | `_current_frame` / `_session_faulted` / `step_count` / … | **する** |

逆に置くと破壊経路が開く。

```python
# Combat/live_combat_session.py:776-778  ← step() の中
if not self._is_still_current():
    self._resynchronize(battle_state)
# :686-696
    self._game.ResetFromScenario(scenario)   # 共有 GameInstance を戦闘 scenario で上書き
```

`_is_still_current()` は `(CombatSessionId, StepIndex)` の比較なので、Whole Run が root を
1 手進めれば必ず false になり、次の commit がランの地図ごと消す。
実行者を Combat 側にすれば、戦闘中に game を進めるのが `LiveCombatSession` だけになるので
**この経路は発火しない。**

情報の粒度も同じ向きを支持する。`step_live_action` は `game.Step()` に加えて
`choice_target` の継続まで解決する（`Combat/battle_emulator.py:858-875`）。
**細かい側から荒い側へ渡す。**

### 2.6 戦闘終了は 1 つのレコードとして引き渡す

戦闘終了時には**真実が 2 つある**。

1. **戦闘としての最終状態** — 戦闘の意味論に使う
2. **step 後の live な run 観測 / room_context / transition** — 引き渡しに使う

`BattleState` に `transition` を足すだけでは原子的な引き渡しにならない。
**引き渡しレコードとして両方を明示的に保持する。** 最終戦闘 snapshot を
「共有 game の現在フレーム」と偽ってはならない。

契約として決めること。

- 何を、lease 解放の**どの時点で**読むか
- `drain_trivial_reward_frontier` は `leave_combat_phase` の一部である（2.4 の 4）
- 敗北と通常終了がそれぞれどう run-terminal になるか

さもないと**戦闘後の最初の決定で、古い戦闘ビューと live なランビューが混ざる。**

実装上の注意。`step_live_action` は `game.Step()` の `Transition` を既に受け取りながら
捨てている（`Combat/battle_emulator.py:858`）。`BattleState` への追加は
`with_shuffle_seed()` / `clone_state()` / `_wrap()`
（`:787-798, 817-826`）の**伝播も揃えないと静かに消える**。

**継承は採らない。** `Combat → Run` と `Run → Combat` の import は現在**双方向に 0 件**で
完全に独立している。戦闘終了通知 1 点のために相互依存を作る代償に見合わない。

### 2.7 Whole Run モードは明示フラグで宣言する

`Combat/battle_emulator.py:890-902` は `combat_completed` のとき
`result.Observation.IsTerminal` が真であることを assert する。根拠は
`!HasMap => 戦闘終了 == ラン終了` という**地図なしモードの契約**である。

フルランでは戦闘が終わっても次の settled 状態（報酬・地図）へ進むだけなので、
**adopt したフルランの戦闘が終わった最初の瞬間にこの assert が落ちる。**

**地図の有無は必要条件だが十分条件ではない。** `LoadState` でラン snapshot を読んだ
combat-only インスタンスなど、推論が崩れる余地がある。**adopt 時に
「Whole Run から使われている」という明示フラグを渡し、推論をやめて宣言にする。**

### 2.8 コントローラは restore しない

`GameAccess` が持つ game は 1 つだけである。**コントローラで snapshot を restore すると、
ランを保持している唯一の game が戦闘専用状態で上書きされる。**

実測: 復元後は `totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType` が
`None` になり、その戦闘に勝つと `boundary = run_terminal` / `outcome = victory` を返す。
**戦闘が終わってもランを続けられない。**

| | 何をするか |
|---|---|
| **コントローラ** | live な戦闘を **adopt** する。restore は**しない** |
| **branch worker（別プロセス）** | snapshot を restore する。元々ここが復元の場所である |

worker がコントローラの game に触れないことは確認済みである。worker は
`multiprocessing.get_context("spawn")` の別プロセスで、各自 `WholeRunSession` を 1 つ作り、
プロセスローカルなオブジェクトを操作する（`Run/worker_pool.py:513-523, 400-430`）。
**CLR の static は共有されない。** ここに guard は不要。

### 2.9 root の「プレイ可否」と「分岐可否」を分け、状態として公開する

`CombatInstance._root_view()` は `_held_stable_snapshot is None` のとき
`_START_PENDING_UNSUPPORTED` を投げ（`API/instance_combat.py:208-223`）、
`commit_action()` / `start_instance_response()` / `get_decision()` は
**すべてそこを通る**（`:245-290`）。したがって `pending_choice` で adopt すると
**観測もコミットもできず、最初の stable に永久に到達しない。**

**決定**: 分離する。

```
root がプレイ可能か  ← アンカー不要。観測とコミットはできなければならない
root が分岐可能か    ← アンカー必須
```

**そして「まだ分岐できない」を root 応答の状態として公開する。**
拒否トレースだけにしてはならない。サーバが評価できないと分かっているフロンティアを
クライアントが投げ続けることになり、容量・branch ライフサイクルに
「まだ分岐できない」をエラーとして表現させることになる。

該当するのは、戦闘がプレイヤーの選択で始まる場合である。`GAMBLING_CHIPS` は
`AfterPlayerTurnStart` でターン 1 に手札選択を要求する
（`MegaCrit.Sts2.Core.Models.Relics/GamblingChip.cs:16-26`）。`pending_choice` の snapshot は
restore できない（`unsupported_capture_boundary:published_target`）ため、
開始時点で restore 可能なアンカーが無い。

これは `CombatInstance` 側の既知の未対応事項と同じである
（`API/instance_combat.py:85-88`、解法の在り処も併記）。

### 2.9b 分岐は戦闘境界で終端する（制約）

戦闘スナップショットにはラン位置が入っていない（`totalFloor` などが `None`。2.14）。
したがって **worker で復元した分岐は、その分岐が戦闘を終わらせた場合、
その先（報酬・地図・ラン終了）を生成できない。**

これは現行の prefix 方式にはできていたことであり、**到達範囲の縮小である。**
明記せずに進めてはならない。

受け入れられる理由は、Training が既にその先を使っていないからである。beam は
`transition.kind == "combat_completed"` を terminal として扱い、戦闘外へ出た branch は
`branches_out_of_scope` として捨てている（実測 160 件・112 件）。
**作れなくなるのは、もともと捨てられているものだけである。**

**したがって分岐は戦闘境界で終端する。** 戦闘を終わらせた分岐は leaf であり、
そこから先へ分岐する要求は受け付けない。

### 2.9c 意思決定の情報源を混線させない（将来の要件）

上の制約は「今は要らないから返さない」であって、
**「Whole Run が別経路で補えばよい」ではない。**

将来、地図上のどこにいるかを戦闘の意思決定に混ぜるようになったとき、
**戦闘の意思決定に使う情報はすべて Combat Instance から渡されなければならない。**
一部を Combat から、一部を Whole Run から取る形にすると、
**同じ決定のための情報に 2 つの出所ができる。** そうなった時点で、
どちらが正かを決める規則が要り、ずれたときに気づけなくなる。

したがって将来の作業は「Whole Run 側で floor を継ぎ足す」ではなく、
**ラン状態を保ったまま戦闘を復元できる checkpoint を用意し、Combat Instance が
run 級の情報も一次情報として持つ**方向に取る。§5 に記載する。

### 2.10 branch は 1 つのトランザクションとして扱う

公開 branch は**所有者と容量予約の両方が揃って初めて coherent** であり、
**両方を解放しないと正しく退役できない**。admission / registration / dispatch / retirement を
別々の関心事にしない。

- **容量**: 上限は Whole Run インスタンス全体でグローバル。Training は `start_instance` 応答の
  `max_emulate_actions_items` を**一度だけ**読む。`CombatPhase` を呼ぶ**前に**予約し、
  失敗・close の全経路で解放する。バッチ内の競合でも広告値を超えない
- **退役**: **tombstone は新設しない。既にある。** `release_branches` は bookkeeping を
  消さず、`status` を `released` にして `view` を落とす（`API/instance_whole_run.py:524-527`）。
  `get_branch_status` はその後も `unknown` ではなく `released` を返す。
  `BranchIdRegistry` も id を恒久的に既知として保つ（`API/identifiers.py:80-102`）。
  **この既存の形をそのまま使い、phase の branch にも同じ扱いを適用する。**
  id は再利用しない
- **順序**: 公開 branch をすべて解放・tombstone 化してから phase を畳む。
  逆にすると進行中の要求に一貫した status を返せない
- `WholeRunInstance._cancel_and_release_all_branches()`（`:445`）は自分の branch しか
  知らない。**phase の branch にも届かせること。** さもないと次の root commit で
  worker 作業が生き残り、容量が漏れる

`branch_id` は Training が発行する client 指定なので**そのまま素通しでよい**。

`decision_point_id` は発行元を 1 つにする（`API/identifiers.py:111` のカウンタは
レジストリごとに 1 から始まり、両者が持つと衝突する）。**同じレジストリを渡すだけでは
足りない**。両者とも初期化時とコミット後に root id を発行するので
（`API/instance_whole_run.py:254, 445-447`、`API/instance_combat.py:202, 335-338`）、
**adopt 時に 1 回、委譲 commit ごとに 1 回**と決める。

### 2.11 DTO は正規化してから publish する

Whole Run の decision 応答のトップレベルは `branch_id` / `decision_point_id` /
`branch_log` / `masked_emulator_dto` である（`API/instance_whole_run.py:317-347`）。
boundary / legal actions / room_context / history は**その中に入れ子で入る**。
`CombatPhase` が返すのは戦闘の状態であり、この形ではない。

**浅いマージをしてはならない。** Training から見えるスキーマと branch history の意味論が
変わりうる。公開 API を越える前に、**上記のトップレベル 4 項目と、その中の
masked DTO の内容の両方**を Whole Run の形へ正規化する。
「同じ形」が何を指すかを実装前に確定させること。

終端は `CombatInstance` の `terminal: true, outcome: victory/defeat`
（`API/instance_combat.py:506-530`）を**そのまま publish してはならない。**
Training の `decision/value.py:233-247` は `outcome in ("victory","defeat")` を
ラン勝敗と解釈し `±100,000` を返すため、戦闘に勝っただけの leaf がすべてラン勝利になる。
戦闘の終端は `transition: {"kind": "combat_completed", ...}` として publish する。

### 2.12 save / load の立場を決める

戦闘中の `save_state` を観測として許す一方、コントローラでの復元は禁じている（2.8）。
**この 2 つは、そのままでは概念的に不整合である。**

**決定**: 戦闘中の run save は**診断専用**とし、耐久チェックポイントとして広告しない。
戦闘中に取った save から再開する経路は本仕様では提供しない
（フェーズ、アンカー、行動列、decision の世代を作り直す必要があり、
戦闘 snapshot はラン位置を持たないのでこの穴を埋められない）。

サーバ再起動をまたぐ復元も同様に対象外とする（session 台帳は元々再起動を越えない）。

### 2.13 wrapper は統合しない

`WholeRunSession`（227 行・17 メソッド）は薄いファサードで独自の状態を持たない。
`LiveCombatSession`（811 行・24 メソッド）は決定フレーム、再同期追跡、フォールト規律、
restore 検証という**戦闘固有の意味論**を持つ。**層が違うので統合しない。**
この非対称性こそが 2.5 の根拠でもある。

### 2.14 実測値

| 項目 | 結果 |
|---|---|
| `CaptureSnapshotJson()` at `stable` | 可能（26,428 bytes）。restore まで通る |
| `CaptureSnapshotJson()` at `pending_choice` | capture は通るが **restore は拒否** |
| capture → restore の戦闘状態 | hp / energy / block / gold / relics / deck / 敵 / 手札 / legal actions **すべて一致** |
| capture → restore の run 位置 | `totalFloor` / `actFloor` / `currentActIndex` / `currentRoomType` が `None` |
| **復元盤面からの End Turn** | **成功する**（下記） |
| snapshot サイズ | 26〜29 KB |

**復元盤面から End Turn できないという記述は誤りだった。**

```
復元前 intent: TACKLE_MOVE(4) / STICKY_SHOT / TACKLE_MOVE(3)
復元後 intent: TACKLE_MOVE(4) / STICKY_SHOT / TACKLE_MOVE(3)   ← 同一
UNSET_MOVE の生存敵: 0
End Turn → 成功（hp 80→73, turn 1→2, 敵は次の Move へ正常に遷移）
```

Emulator 側で既に解消されており docstring だけが残っていた（`0ff0b16` で修正済み）。
Training の `turn_boundary_scoring` は全 leaf を強制 End Turn で採点するため、
**この点を誤読すると設計全体が成立しないと判断してしまう。**

### 2.15 完成形

```
  RL サーバプロセス（1 プロセス = 1 ラン）
  ┌────────────────────────────────────────────────────────────┐
  │ GameAccess（プロセスに 1 つ・薄い）                        │
  │   唯一の GameInstance + lease 状態機械（世代トークン付き）  │
  │   RUN / COMBAT / TRANSFERRING / POISONED                   │
  │   mutating は全部ここを通る。観測は素通し                  │
  └───────┬────────────────────────────┬───────────────────────┘
          │ 借りる                     │ 借りる
  ┌───────▼──────────────┐   ┌─────────▼───────────────────────┐
  │ WholeRunInstance     │   │ CombatPhase（内部）             │
  │  ラン進行・公開 API  │──►│  live 戦闘 / アンカー / 行動列  │
  │  branch トランザクション  │  rng 仮説 / ドロー固定          │
  │  （所有者 + 容量）    │   │  BranchWorkerPool ───┐          │
  └──────────────────────┘   └──────────────────────┼──────────┘
                                                    │
    CombatInstance（公開・独立のまま）              │ snapshot + rng_id
      └ 同じ CombatPhase を standalone で使う       │
  ┌─────────────────────────────────────────────────▼──────────┐
  │ branch worker プロセス（複数）                             │
  │   restore(anchor, rng_id) → replay → 分岐 action           │
  │   **ここだけが restore する**。コントローラの game に触れない│
  └────────────────────────────────────────────────────────────┘
```

```
Training          WholeRunInstance        GameAccess         CombatPhase
   │ commit(map)      │                       │                   │
   ├─────────────────►│ choose_room()         │                   │
   │                  │ is_combat == True     │                   │
   │                  ├── enter_combat_phase ─┤                   │
   │                  │   RUN → TRANSFERRING ►│                   │
   │                  │   adopt（非破壊）─────┼──────────────────►│
   │                  │   root decision を 1 回発行               │
   │                  │   TRANSFERRING → COMBAT（確定）           │
   │                  │   ※確定前の失敗は巻き戻して RUN へ        │
   │ emulate_actions  │                       │                   │
   ├─────────────────►│ アンカー未確立なら分岐のみ拒否            │
   │◄─ 状態として公開 ┤ （root 応答に「まだ分岐できない」を出す） │
   │ commit(card)     │                       │                   │
   ├─────────────────►├── 委譲 ───────────────┼──────────────────►│ step（実行者）
   │                  │◄─ 引き渡しレコード ───┼───────────────────┤ stable なら再アンカー
   │ emulate_actions  │ 容量を予約してから配る│                   │
   ├─────────────────►├───────────────────────┼──────────────────►│ ──► worker
   │◄─ 正規化した DTO ┤                       │                   │
   │ commit(...)      │ transition == combat_completed            │
   ├─────────────────►├── leave_combat_phase ─┤                   │
   │                  │   COMBAT → TRANSFERRING                   │
   │                  │   branch を解放 → tombstone → phase を畳む│
   │                  │   root を正規化・drain を実行             │
   │                  │   TRANSFERRING → RUN（確定）              │
```

## 3. 実装手順

継ぎ目は「大きさ」ではなく「その時点でシステムが一貫しているか」で置く。

### S1. `WholeRunSession` に capture / restore を公開する（完了・コミット済み）

**注記**: 委譲経路では使われない見込み。テストが記録している制約には価値があるので残すが、
完了時点で未使用なら削除を検討する。

### S2. `GameAccess` と lease 状態機械を置く

- プロセスに 1 つの `GameAccess`。唯一の `GameInstance` を保持し、
  `shared_game_instance()` に一本化する（`run_emulator_bridge` の **DTO 変換層は残す**）
- lease 状態機械（`RUN` / `COMBAT` / `TRANSFERRING` / `POISONED`）と世代トークン
- mutating は全部ここを通し、非所有者・古い世代を拒否する。観測は素通し
- `API/server.py` で **2 つ目の game 所有インスタンスを意味のあるエラーで拒否する**（2.1）

**受け入れ**: 非所有者の mutating が拒否される。2 つ目の `start_instance` が
`EnsureNotSuperseded` ではなく意味のあるエラーで落ちる。既存の単体経路が動く。

### S3. 戦闘終了の引き渡しレコードと、明示的な Whole Run モード

- `step_live_action` が `Transition` を捨てずに引き渡しレコードへ載せる。
  `with_shuffle_seed()` / `clone_state()` / `_wrap()` の**伝播も揃える**（2.6）
- 戦闘の最終状態と live な run 観測を**別々に保つ**
- `combat_completed` の assert を**明示フラグ依存に直す**（2.7）

**受け入**: フルランモードで assert が落ちない。コピーで `transition` が消えない。

### S4. `CombatPhase` を切り出す

- 戦闘の意味論（live session / アンカー / 行動列 / rng 仮説 / ドロー固定 /
  branch pool と manager）を `CombatPhase` へ移す
- `CombatInstance` は `CombatPhase` を standalone モードで使う公開ファサードとして残す。
  **公開 API・識別子・DTO 整形は従来どおり**
- adopted モードを持つ。**非破壊**（reset も restore もしない）。
  root のプレイ可否と分岐可否を分ける（2.9）

**受け入**: `CombatInstance` の既存テストが無変更で通る。
adopted モードで `ResetFromScenario` が一度も呼ばれない。

### S5. `enter_combat_phase` / `leave_combat_phase` を 1 トランザクションにする

- 2.4 のとおり実装する。**確定前のロールバック地点を明示する**
- `Step` 成功後・記帳前の失敗は `POISONED`
- `drain_trivial_reward_frontier` は `leave` の一部
- 「まだ分岐できない」を root 応答の状態として公開する（2.9）

**受け入**: enter/leave の途中で失敗しても、`RUN` に戻るか `POISONED` になるかのどちらかで、
中間状態が観測されない。

### S6. root commit を phase へ通し、root 応答を正規化する

- 戦闘中の root commit を `CombatPhase` が実行し、Whole Run は結果を観測して記帳する
- 応答を Whole Run の形へ正規化（2.11）。decision id は adopt 時 1 回、commit ごと 1 回（2.10）

**受け入**: 戦闘中に `WholeRunSession` の mutating が呼ばれない。root 応答の形が従来と一致。

### S7. branch トランザクションと分岐の委譲

- admission（容量予約）/ registration（所有者）/ dispatch / retirement（tombstone）を
  **1 つのトランザクション**として実装する（2.10）
- 戦闘境界の `emulate_actions` を `CombatPhase` へ回す。`branch_id` は素通し
- DTO を正規化、終端は `combat_completed`（2.11）
- **旧 prefix 経路へのフォールバックを作らない**

**受け入**: 戦闘分岐が `load_state` / `choose_room` / 部屋全体の prefix 再生を行わない。
戦闘終了後も全エンドポイントが一貫した応答を返す。広告した上限を超えない。

## 4. テスト

- S2: 非所有者 mutating の拒否。2 つ目のインスタンスの拒否。世代の古い呼び出しの拒否
- S3: フルランで assert が落ちない。コピーで `transition` が保たれる
- S4: `CombatInstance` の既存テストが無変更で通る。adopted で `ResetFromScenario` が
  呼ばれない（fake で観測）。pending adopt でも root が進む
- S5: enter/leave の各段階での失敗注入。中間状態が観測されないこと。`POISONED` の到達
- S6: 戦闘中に `WholeRunSession` の mutating が呼ばれない。root 応答の形。id の発行回数
- S7: 上限を超えない。tombstone 後の全エンドポイントの一貫性。
  戦闘勝利が `combat_completed` で publish され `outcome: "victory"` が出ない
- 全体: `python -m pytest -q` が既存 504 件を維持する
- Whole Run 実機評価: `AllBranchesFaultedError` が出ない

## 5. 今後の課題

- **同時に複数の戦闘**。本仕様は「同時に 1 戦闘」を不変条件とし、2 回目の adopt を拒否する。
  将来必要になったら lease を複数フェーズへ拡張する
- **ラン状態を保ったまま戦闘を復元できる checkpoint。** 本仕様は分岐を戦闘境界で
  終端させることで回避しているが（2.9b）、地図上の位置を戦闘の意思決定に使うようになったら、
  **その情報も Combat Instance が一次情報として持たなければならない**（2.9c）。
  Whole Run 側で継ぎ足す形にすると、同じ決定のための情報に出所が 2 つできる。
  Emulator 側の対応が要る見込み
- **戦闘がプレイヤーの選択で始まる場合、最初の `stable` まで分岐できない**（2.9）。
  `CombatInstance` 側の `_START_PENDING_UNSUPPORTED` と同一の制約で、解法の在り処も
  そこに書かれている
- **戦闘中は Whole Run のワーカープールが遊ぶ。** プールそのものは共有できない
  （`WorkItem` の型が別物で、`Run/worker_pool.py` の docstring が共通基底化を警告している）。
  プロセス予算の配分は調整できる
- **戦闘中の run save からの再開**（2.12）。現状は診断専用と決めた
- S1 の `capture_combat_snapshot()` / `restore_combat_snapshot()` が未使用なら削除を検討

## 6. やらないこと

- 旧 prefix 経路へのフォールバック
- 公開 `CombatInstance` を `WholeRunInstance` に入れ子にすること（2.3）
- `rng_hypothesis` / `replay_draw_restore` を下位から呼び直すこと
- `WholeRunSession` と `LiveCombatSession` の統合（2.13）
- `Combat → Run` の依存を作ること（2.6）
- `run_emulator_bridge` の変換層の置き換え（S2）
- 戦闘の再入・入れ子（2.1）
- 非戦闘（map / event / reward / rest / shop）分岐の変更
- Training 側の変更
