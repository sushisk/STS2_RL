# 部屋入口アンカー 実装計画（S8）

## 0. 文章の目的

開始時 `pending_choice` の戦闘で分岐できるようにする実装の計画を示す。
設計判断の根拠は `whole_run_combat_snapshot_branching.md` の 2.8 / 2.8b / S8 にあり、
本文書はそれを**実装者が着手できる粒度**へ落とす。

## 1. 概要

**問題**: 戦闘が `pending_choice` で始まると、その盤面の snapshot は restore できない
（`unsupported_capture_boundary:published_target`）。S1–S7 の実装は最初の `stable` に
到達するまで分岐要求を拒否する。

**解法**: アンカーを戦闘の外へ 1 段動かす。Whole Run は地図境界の `save_state()` と
選んだ `room_id` を既に持っている。worker は `load_state` → `choose_room` で
**戦闘開始直後の盤面**を作れる。restore できない snapshot を必要としない。

**成立の根拠は実測済み**（2.8b の表）。敵の初手は RNG を引かず、部屋への再入は
別プロセスでも敵・HP・初手 Intent・初期手札・山札枚数まで一致する。
`GAMBLING_CHIP`（draw 関与）と `TOOLBOX`（draw 非関与）の両方で確認した。

**この設計は新しい機構ではない。** 既存の `CombatStartReplayRoot` と同じ形である。

## 2. 設計

### 2.1 継ぎ目はあるが、1 箇所ではない

`DecisionContext.root_snapshot` は既に直和型で、`CombatStartReplayRoot` は Combat instance が
「開始時 pending には Stable 境界が無い」問題を解いた形である。部屋入口アンカーは
その 3 つ目の変種にあたる。

**ただし「分岐を 1 本足すだけ」ではない。** root 型を `isinstance` で判定している箇所は
8 ファイル・10 箇所ある。

```
Combat/search/branch_worker_pool.py:118   _snapshot_identity_json   branch 同一性ハッシュ
Combat/search/branch_worker_pool.py:146   _snapshot_ipc_json        worker へ渡す形
Combat/search/decision_context.py:498, 539
Combat/search/rng_hypothesis.py:544
Combat/search/search_coordinator.py:168, 249
API/combat_rng_mapping.py:49
```

**新しい dataclass を足すだけでは静かに壊れる。** `_snapshot_ipc_json` は
`CombatStartReplayRoot` を特別扱いした後、`dataclasses.is_dataclass()` で捕まえて
文字列へ落とす（:153-154）。新型はそこに落ち、worker は `str` を受け取り、
`isinstance(..., CombatStartReplayRoot)` が偽になるため
**ラン全体の `SaveState()` を戦闘 snapshot として restore しようとする。**
`_snapshot_identity_json` も同様に地図 snapshot 全体をハッシュへ巻き込む（:124-125）。

### 2.2 決定: root 型を共通プロトコルへ集約する

10 箇所へ分岐を書き足す形は採らない。**1 箇所でも書き忘れれば黙って壊れ、
今回の IPC がまさにその形である。** root 型が自分で答えられるようにする。

```python
class SearchRoot(Protocol):
    def bootstrap(self, session) -> BattleState: ...   # 復元 / start_combat / 部屋入口
    def identity_payload(self) -> str: ...             # 同一性ハッシュ用
    def ipc_payload(self) -> Any: ...                  # worker へ渡す形（picklable）
```

既存の 3 形態（`CombatStateSnapshot` / `CombatStartReplayRoot` / 新設）をこの形へ寄せ、
`isinstance` 分岐を消す。**今回の作業量は増えるが、次に root 種別が増えたとき
10 箇所を探さずに済む。** rng 関連（`rng_hypothesis.py:544`、`combat_rng_mapping.py:49`、
`search_coordinator.py:249`）が root 種別で何を変えているかを先に読み、
プロトコルに載せるべき問いがもう 1 つあるかを判断する。

### 2.3 決定: worker 専用の bootstrap クラスを置く

worker の session は `LiveCombatSession` であり、`load_state` / `choose_room` は
`WholeRunSession` にしかない。**戦闘 session にラン級の操作を生やさない。**
部屋入口の復元だけを行う小さいクラスを置き、その結果を `LiveCombatSession` へ渡す。
境界が 1 箇所に集まり、S2 の lease 規約（worker は `IsolatedLiveCombatSession` で
プロセス lease を通らない）もそこだけで扱える。

### 2.4 アンカーを張る側

`CombatPhase` は現在 `_held_stable_snapshot` を持ち、`None` なら分岐不可とする。

- adopt 時に `stable` なら従来どおり snapshot を capture
- adopt 時に `pending_choice` なら部屋入口 root を保持
- **`root_decision()` を直す必要がある。** 現状は `_held_stable_snapshot is None` で
  無条件に `(legal, None, boundary)` を返すため、`adopt()` へ地図情報を渡すだけでは
  分岐可否は変わらない
- root が最初の `stable` に到達したら snapshot を capture し、張り替える

### 2.5 コントローラが渡すもの

`WholeRunInstance.enter_combat_phase` が adopt 時に `_map_snapshot` と `_room_id` を渡す。
両方が揃わない場合（地図境界を経ずに戦闘へ入った場合）だけ 2.8 の拒否が残る。
**拒否理由を「地図境界の情報が無い」と「開始 pending でアンカー未確立」に分ける。**

### 2.6 再生する範囲

再生するのは戦闘に入ってからの行動列だけであり、**部屋全体の行動列は再生しない。**

**長さを 1 手と仮定してはならない。** pending が別の pending を生む場合があり、
既存の `reach_choice_boundary()` が `max_steps` を取るのは境界までの距離が
1 と限らないためである。上限付きの任意長として実装し、超えたら落とす。

### 2.7 初期 draw の pin について

部屋入口が決定的なので初期手札は再現する、というのは
**`GAMBLING_CHIP` と `TOOLBOX` の 2 例で測った結果であって、コードから導かれる保証ではない。**
実装では「pin しない」を前提に置かず、root の観測と一致するかを検証し、
食い違ったら落とす。一致検証に使うフィールドは既存の `DecisionSignature` に合わせ、
独自の比較規則を作らない。

### 2.8 食い違ったときの扱い

**大声で落とす。** 黙って別経路へ落とさない。既存の `ReplayMismatch` の形に合わせる。

### 2.9 在庫中の branch とアンカーの張り替え

root が `stable` に達してアンカーを張り替えるとき、部屋入口 root で発行済みの branch が
走っている可能性がある。**張り替えは既存 branch の結果を無効にしない**方針とし、
既存の stale 判定（`branch_manager` の世代）で扱えるかを実装前に確認する。
扱えないなら、張り替えを root commit の境界に限る。

## 3. 実装手順

**S8a. root プロトコルへの集約（部屋入口 root を足す前）**

既存 3 形態を `SearchRoot` プロトコルへ寄せ、`isinstance` 分岐を消す。
**この時点で振る舞いは変えない。** 既存テストが無変更で通ることが検査になる。

**受け入**: `isinstance(..., CombatStartReplayRoot)` が本番コードから消えること。
既存テストがアサーション無変更で通ること。

**S8b. 部屋入口 root と worker bootstrap**

- 部屋入口 root を `SearchRoot` として定義する（bootstrap / identity / ipc を実装）
- worker 専用の bootstrap クラスを置き、`load_state` → `choose_room` を行う
- 戦闘に入ってからの行動列を上限付きで再生する

**受け入**: 実プロセスで作った盤面が root と一致すること。IPC を跨いで型が保たれること。

**S8c. `CombatPhase` のアンカー二種**

- `pending_choice` で adopt したとき部屋入口 root を保持する
- `root_decision()` と `root_branching_unavailable_reason` を直す
- 最初の `stable` で張り替える

**受け入**: `GAMBLING_CHIP` / `TOOLBOX` を注入した戦闘で root の最初の決定から分岐でき、
branch 結果が公開エンドポイントで使えること。以降は部屋入口 root が使われないこと。

**S8d. コントローラの受け渡しと拒否理由の分離**

**受け入**: 地図境界を経ない戦闘では従来どおり拒否され、理由が区別できること。

## 4. テスト方針

**stub で固めてはならない。** S5 は偽の session で 529 passed になった一方、
実配線ではランを完走できなかった（`Game access is TRANSFERRING`）。
偽の `step` は `_mutating_game` に到達しないため、検査すべき制約がそもそも存在しなかった。

本手順は**再現性そのものが主張**なので、次を実プロセス・実 Emulator で確かめる。

- `inject_relic(map_snapshot, "GAMBLING_CHIP")`（**単数形**）で戦闘が
  `pending_choice` で始まること
- 同じ snapshot から spawn した子プロセスが root と同一の盤面を作ること
- そこから分岐した branch 結果が使えること

`GAMBLING_CHIPS`（複数形）は認識されず `DEPRECATED_RELIC` に落ちる。
その綴りで書くと relic が装備されず、「発火しない」という誤った結論になる。

## 5. 変更しないもの

`Run/worker_pool.py` の分岐機構、`rng_hypothesis`、`replay_draw_restore`、
非戦闘の分岐経路、Training 側、2.9b の「分岐は戦闘境界で終端する」規則。
