STS2_Emulator/Sts2Emulator/Api/GameInstance.csのIsCurrentRoomResolved(AbstractRoom? currentRoom)はswitch式でroom typeごとに解決済み判定を行うが、MapRoomに対応するcaseが無くdefault => falseに落ちる。# Act遷移MapRoomスタックバグ — 修正計画（再レビュー反映版）

作成日: 2026-08-10（改訂: 同日、`act_transition_maproom_fix_plan_rereview_20260810.md`の再レビュー指摘を反映）
対象PR: [#15](https://github.com/sushisk/STS2_RL/pull/15)（`act_transition_maproom_stable_boundary_gap_20260810.md`の再追加）
関連: [`treasure_room_fix_implementation_plan_final_20260810.md`](treasure_room_fix_implementation_plan_final_20260810.md)（同一パターンの既修正バグ）

**改訂の要点（v2 → v3）:** 再レビューの総合判定は **「Approve with minor changes」**。
`MapRoom => true`という根幹の修正方針、`WholeRunSession`/`WholeRunInstance`の契約分離、
`commit_action()`実経路を使った回帰検証という主要方針は妥当と評価された。その上で以下を反映した:

- **文言の修正（必須）**: 2.2の「`EagerExitCurrentRooms()`が`MapRoom.Exit()`以外に副作用を持たないこと」
  という確認条件は要求が強すぎた（room exitに伴う正当な副作用まで一律「問題」として扱ってしまう）。
  「副作用がAct遷移直後のMapRoomに対して安全かつ意図通りであること」を確認する表現に修正した（2.2）。
- **具体化（推奨）**: `commit_action()`回帰テストの構築方法を「完全E2E」と「境界直前setup＋最終操作のみ
  production path」の2案として明記し、採用方針を決めた（3.3）。
- **具体化（推奨）**: 被害再現seed（1348178664、Training自己対戦の非combat選択に乱数性がありprovenance
  用途）と、RL側の決定的回帰テスト用seedの役割を分離した（4.4）。

（v1 → v2の改訂点: `get_legal_actions()`の非空性/`map_room`含有を求める切り分け条件が`WholeRunSession`と
`WholeRunInstance`の契約を混同していた点、回帰テストは`commit_action()`の戻り値そのものまで検証すべき点、
`test_whole_run_connectivity.py`の更新・Emulator側事前確認の拡充。詳細は本書のgit履歴を参照。）

---

## 1. 問題の再確認

`STS2_Emulator/Sts2Emulator/Api/GameInstance.cs`の`IsCurrentRoomResolved(AbstractRoom? currentRoom)`は
`switch`式でroom typeごとに解決済み判定を行うが、`MapRoom`に対応する`case`が無く`default => false`に
落ちる。結果として`TryEagerExitResolvedRoom()`が`EagerExitCurrentRooms()`を呼ばず、Act境界越え直後の
`MapRoom`が`CurrentRoom`に居座り続け、`boundary == "stable"`・`get_legal_actions() == []`のまま
`WholeRunSession`が永久にスタックする（`run_terminal`/`outcome`も設定されない）。

再現: seed=1348178664（IRONCLAD, ascension=0）、STS2_Trainingの自己対戦でAct 1ボス撃破後の101手目
（`commit_action`）直後。**実被害はSTS2_Trainingの`EpisodeRunner`が`commit_action()`のレスポンスを
そのまま次Decisionとして使う経路で観測されている**（3章参照）ため、修正の検証もこの経路を通す必要がある。

`MapRoom.cs`の実装（`EnterInternal`/`Exit`とも実質的な状態を持たないno-op）を見る限り、
Combat/Event/Shop/Rest/Treasureと違い「解決を待つ何か」が元々存在しない。したがって
TreasureRoomのような非同期の報酬付与処理は不要で、**switchに1caseを追加するだけ**で足りると見られる
（未検証の前提であり、2章で検証手順を定める）。

---

## 2. Emulator側の修正（`STS2_Emulator`、mainへ直接コミット）

### 2.1 `IsCurrentRoomResolved()`にcaseを追加

```csharp
// GameInstance.cs:5219付近
private bool IsCurrentRoomResolved(AbstractRoom? currentRoom)
{
    return currentRoom switch
    {
        CombatRoom => _combatState == null && _pendingCardReward == null,
        EventRoom => RunManager.Instance.EventSynchronizer.GetLocalEvent()?.IsFinished == true,
        MerchantRoom => _shopLeft,
        RestSiteRoom => _restSiteResolved || RunManager.Instance.RestSiteSynchronizer.GetLocalOptions().Count == 0,
        TreasureRoom => RunManager.Instance.TreasureRoomRelicSynchronizer.IsComplete,
        MapRoom => true,   // 追加: MapRoomは解決すべき内部状態を持たないため常にresolved
        _ => false
    };
}
```

### 2.2 実装前に必須のコードリーディング（レビュー指摘により拡充）

Emulatorはlocal限定のリポジトリであり、本計画のレビューでも`GameInstance.cs`/`MapRoom.cs`の実体および
全call siteを外部から独立確認できていない。**したがって以下のコードリーディング工程は省略しないこと**
（`MapRoom.Exit()`がno-opであること単体を`MapRoom => true`の安全性根拠にせず、`EagerExitCurrentRooms()`
全体の副作用まで確認する）:

- `MapRoom`の生成箇所（`NMapRoom.Create`/`SetCurrentRoom`呼び出し元）を全て洗い出す
- `SetCurrentRoom(...)`の全call siteを確認し、`MapRoom`がAct境界越え以外の文脈でも一時的に
  `CurrentRoom`として現れないかを確認する（現れる場合、`=> true`即時解決が早すぎて別の状態を
  踏み潰さないかを検討する）
- `RoomType.Map`を条件にしているコード（`atMapBoundary`との使い分けを含む）を確認する
- `TryEagerExitResolvedRoom()`の全call siteを確認する
- Act生成直後、`TryEagerExitResolvedRoom()`（またはそれに相当する判定）が確実に呼ばれる経路になっている
  ことを確認する
- `EagerExitCurrentRooms()`が`MapRoom.Exit()`の呼び出し以外に行う付随処理・状態更新を洗い出し、
  それらがAct遷移直後の`MapRoom`に対して実行されることを意図した処理であり、複数回評価された場合も
  状態不整合や副作用の重複を起こさないことを確認する（「副作用が一切無いこと」を求めるのではない —
  room exitに伴う正当な状態更新自体は問題ではない。再レビュー指摘により表現を修正）。確認対象には
  少なくとも以下を含める:
  - `CurrentRoom`のクリアまたは更新
  - map boundaryへの遷移に必要な状態更新
  - room exitに伴う付随処理hook
  - synchronizer/lifecycleのリセット
  - Act/floor/map位置に関する更新
  - 二重評価時のidempotency
- `LoadState()`後に`CurrentRoom == MapRoom`が復元される経路がないか（あれば`SaveState()`直後の
  eager exit前提が崩れる可能性がある）
- eager exit判定が複数回実行されても副作用が重複しないこと
- `_pendingTarget`/`_pendingChoice`/`_pendingCardReward`等の既存guardが、Act遷移直後のタイミングで
  意図せず`false`を返し続けて`TryEagerExitResolvedRoom()`をブロックしていないこと

上記が全て問題なければ2.1の1行追加のみで修正完了となる。他文脈で`MapRoom`が現れる場合は、
Treasureと同様に専用フラグによる条件分岐が必要になる可能性があるが、現時点のコードリーディングでは
その根拠は見つかっていない。

---

## 3. テスト設計: `WholeRunSession`と`WholeRunInstance`の契約の切り分け（レビュー必須修正）

**レビュー指摘（必須修正1）:** 旧版の計画は「Act境界越え後、`get_legal_actions()`が空でなく`map_room`
actionを含む」ことを一律の受け入れ条件としていたが、これは2つの異なるAPI層の契約を混同していた。

- `WholeRunSession`（低レベルAPI）: `map_select`は`get_map_rooms()`/`choose_room()`で扱う専用の境界で
  あり、通常の`step()`用`LegalAction`とは別系統。したがって`WholeRunSession.get_legal_actions()`に
  `map_room` actionが存在することを切り分け条件にしてはならない。
- `WholeRunInstance`（`API/instance_whole_run.py`の公開API）: `boundary == "map_select"`のとき
  `get_map_rooms()`の結果をAPI側の`action_type == "map_room"`という`LegalAction`へ変換して公開する。
  `map_room`の非空性を検証すべきはこの層である。

### 3.1 `WholeRunSession`レベルの受け入れ条件（修正後）

- Act境界越え後に`boundary == "map_select"`へ到達すること
- `get_map_rooms()`が空でないこと
- `save_state()`が成功すること
- `room_context`が未解決`MapRoom`のまま出続けていないこと
- `get_legal_actions()`の非空性は`map_select`では要求しない（別系統の境界のため）

### 3.2 `WholeRunInstance`（APIレベル）の受け入れ条件（修正後）

- `masked_emulator_dto["boundary"] == "map_select"`
- `masked_emulator_dto["legal_actions"]`に`action_type == "map_room"`が1件以上存在すること

### 3.3 `commit_action()`実経路を通す回帰テスト（レビュー必須修正2）

実被害はSTS2_Trainingの自己対戦においてAct 1ボス撃破後の`commit_action`直後に観測された。
`WholeRunInstance`のテストで内部`_session`を直接Act境界後まで進めてから`get_decision()`のみ確認する
方法では、`commit_action()`自身が通る以下の経路を検証できず不十分:

1. 現在のroot viewを取得
2. `WholeRunSession.step()`または`choose_room()`を実行
3. `_maybe_capture_map_snapshot()`を実行
4. 新しいroot viewを再構築
5. 次Decisionを`commit_action()`のレスポンスとして返す

STS2_Trainingの`EpisodeRunner`はこの`commit_action()`レスポンスをそのまま次Decisionとして利用するため、
非終端なのに`legal_actions`が空だと`NoAvailableActionError`となる。したがって新規
`test_maproom_act_transition_gap.py`には、**Act境界を越える最後の操作自体を`WholeRunInstance.commit_action()`
で実行し、その戻り値を直接検証する**テストを含める:

```python
response = inst.commit_action(decision_point_id, action_id)
dto = response["masked_emulator_dto"]

assert dto["boundary"] == "map_select"
assert any(
    action.get("action_type") == "map_room"
    for action in dto["legal_actions"]
)
```

これにより以下を一度に回帰検証できる:

- Emulator側の`MapRoom` eager exit
- `WholeRunSession.step()`後のObservation
- `_maybe_capture_map_snapshot()`の新Act Map対応
- `_root_view()`による`map_room` LegalAction生成
- STS2_Trainingが実際に受け取る次Decisionの形

### 3.4 Act境界直前までの状態構築方法（再レビュー指摘により具体化）

Act境界を越える**最後の操作**は3.3の通り必ず`WholeRunInstance.commit_action()`で行うが、
そこに至るまでの状態をどう作るかは以下2案のいずれかを採用する（実装時にどちらか一方を選び、
テストコメントに明記すること）。

**採用方針: 案B（境界直前setup＋最終操作のみproduction path）を基本とする。**
本パッケージの既存テスト（`test_treasure_room_stable_gap.py`）が一貫して`session.load_state(snapshot)`で
直前状態を作り、対象roomの解決だけを検証する構成を採っており、規約として一貫性がある。加えて
案Aのフルraw E2Eは実行時間・途中経路の別バグ混入リスクの両面でコストが高い。

- **案A（完全E2E）**: `WholeRunInstance`の公開Decisionと`commit_action()`のみを使ってAct境界まで進める。
  - 利点: RL公開APIのproduction path全体を検証できる。STS2_Trainingが実際に利用する操作契約に近い。
    action ID/decision point lifecycleを同時に確認できる。
  - 欠点: テスト時間が長くなりやすい。途中の別のroom/eventの未修正バグに影響されうる。
- **案B（境界直前setup + 最終操作のみproduction path、採用）**: Act境界直前まで`_session`に
  保存済みsnapshot等を使ってsetupし、**Act境界を越える最後の1操作だけは必ず`WholeRunInstance.commit_action()`
  を使用する**。

案Bを採用する場合、テストコメントに以下を明記する:

> 内部session操作はAct境界直前状態を作るためのsetupに限定する。今回の被害発生点である最後のAction適用・
> 新Act Map snapshot取得・root view再構築・公開DTO生成は必ず`WholeRunInstance.commit_action()`の
> production pathを通して検証する。

最終レスポンスでは少なくとも以下を確認する（3.3のコード例と同一）:

```python
response = inst.commit_action(decision_point_id, action_id)
dto = response["masked_emulator_dto"]

assert dto["boundary"] == "map_select"
assert any(
    action.get("action_type") == "map_room"
    for action in dto["legal_actions"]
)
```

---

## 4. STS2_RL / STS2_Training側の追加確認とテスト計画

### 4.1 `test_whole_run_connectivity.py`の更新（レビュー強い推奨）

現行`test_whole_run_connectivity.py`には、Treasure修正後に到達可能になった別の既知問題として
「Act遷移後に`CurrentRoom == "MapRoom"`・`boundary == "stable"`・legal actionsなし」という状態を
既知問題として許容するコメント・検証が残っている。MapRoom修正後はこの許容記述を削除し、
少なくとも以下の回帰条件を追加する:

```python
assert not any(
    room["room_type"] == "MapRoom"
    for room in summary["unsupported_rooms"]
), summary["unsupported_rooms"]
```

新規専用テスト（`test_maproom_act_transition_gap.py`）とは別に、汎用Whole Run traversalがAct境界を
自然に越えられることの回帰ガードとして価値がある。

### 4.2 `room_progression_driver.py`

現行実装では通常の`step()`成功後に`last_map_snapshot = None`/`tried_room_ids_at_current_map = set()`へ
リセットし、次に`MAP_SELECT`が観測された時点で新しいMap snapshotを取得する。したがって
「Boss combat終了 → Emulator内でMapRoom eager exit → StepResultが新Actのmap_select」という流れになれば、
古いActのmap snapshotを新Actへ誤って持ち越す可能性は低い。**現時点ではproduction codeの追加修正は不要と
見込まれるが、実機テストで確認すること**（Treasureのときは同種のlifecycle前提のズレが実際にバグとして
見つかっているため、楽観視せず検証する）。

### 4.3 STS2_Training側のsmoke test

`start_new_run()`/`EpisodeRunner`はAPIから正しい`map_room` LegalActionが返れば通常のDecisionとして
処理できる構造であるため、STS2_Training側のproduction code変更は現時点では不要と見込まれる。
ただし最終smoke testとして以下を確認することを推奨する:

- Whole Runが少なくとも1回Act境界を越えること
- Act遷移直後に`NoAvailableActionError`が発生しないこと

なお、Training側の非combat選択にはランダム性があるため、known-affected seedを指定しただけで常に
同一手数・同一経路を通ることを前提とした決定的な自動テストにはならない点に注意する。

### 4.4 repro seedの役割の分離（再レビュー指摘により具体化）

当初の被害は`seed=1348178664`のSTS2_Training自己対戦で観測されており、この値自体は重要な被害
provenanceである。一方でSTS2_Training側の非combat選択には乱数性があるため、同じseedを指定しただけでは
常に同一手数・同一経路になるとは限らない。そのため、seedの役割を次のように分離する:

**被害provenance / Trainingスモークテスト用: `seed=1348178664`**

用途:

- 元の被害との対応関係を維持する
- STS2_Trainingの実運用経路でAct境界を実際に越えられることを確認する（4.3）
- Act遷移直後に`NoAvailableActionError`が発生しないことを確認する

ただし「101手目で必ず同じ状態になること」を自動テストの前提にはしない。

**RL側決定的リグレッション用: 既存Whole Run traversalでAct遷移gapへの到達実績があるseed**

`test_maproom_act_transition_gap.py`（3.3/3.4）および`test_whole_run_connectivity.py`（4.1）は、
既存traversalで決定的に到達できることを確認済みのseedを使う。現行`test_whole_run_connectivity.py`では
`seed=18`のtraversalにおいて、Treasure修正後にMapRoom Act遷移gapが露出することが既知問題として
記録されており、これを候補として利用できる。

テスト実装時には以下を固定し、policyの偶然の成否に依存しない決定的なリグレッションテストとする:

- seed
- character
- ascension
- room/action選択規則
- 最大step数
- Act境界到達条件

### 4.5 「RL側変更不要」の表現の明確化（レビュー指摘）

「RL側変更不要」という表現は「RL production code変更が不要」という意味に限定し、テスト・ドキュメント
変更（4.1の`test_whole_run_connectivity.py`更新、3章の新規/既存テスト変更）を「変更不要」の範囲に
含めないよう、以降のドキュメント・PR説明で明記する。

---

## 5. 作業手順と受け入れ条件

| # | 作業 | リポジトリ | 運用 |
|---|------|-----------|------|
| 1 | 2.2の事前コードリーディング（`MapRoom`の生成箇所・全call site・他文脈での出現有無・`EagerExitCurrentRooms()`副作用） | STS2_Emulator | 調査 |
| 2 | 問題なければ`IsCurrentRoomResolved()`に`MapRoom => true`を追加（2.1） | STS2_Emulator | mainへ直接コミット |
| 3 | 低レベル`WholeRunSession`のsmoke test（`boundary == "map_select"`、`get_map_rooms()`非空、`save_state()`成功）（3.1） | STS2_Emulator/STS2_RL | 手動確認 |
| 4 | `test_maproom_act_transition_gap.py`追加。案B（3.4）に基づき境界直前setup＋`WholeRunInstance.commit_action()`の戻り値を直接検証するテストを含める（3.2/3.3/3.4）。決定的なseed（4.4）を使う | STS2_RL | pull request |
| 5 | `test_whole_run_connectivity.py`の既存MapRoom-gap許容記述を削除し、回帰条件を追加（4.1） | STS2_RL | 同一PR |
| 6 | `room_progression_driver.py`のmap snapshot lifecycleが新Actでも正しく動作することを実機確認（4.2、production code変更は現時点では不要見込み） | STS2_RL | 同一PR内で確認 |
| 7 | STS2_Training側smoke test（`seed=1348178664`でAct境界を1回以上越える、`NoAvailableActionError`が出ない）（4.3/4.4） | STS2_Training | 手動/CI確認 |
| 8 | 既存テスト（Treasure回帰テスト含む）の回帰確認 | STS2_RL | 同一PR |

**受け入れ条件:**

- known-affected seedで、Act境界越え直後に`boundary`が`"stable"`のまま固着しない
- `WholeRunSession`レベルでは`get_map_rooms()`が空でなく`save_state()`が成功する（`get_legal_actions()`の
  非空性は`map_select`では求めない — 3.1）
- `WholeRunInstance`レベルでは`masked_emulator_dto["legal_actions"]`に`action_type == "map_room"`が
  含まれる（3.2）
- `WholeRunInstance.commit_action()`の戻り値そのものを検証する回帰テストが存在し、Act境界を越える最後の
  操作は必ずこの production path を通す（3.3/3.4、実被害の再発を直接検知できること）
- `EagerExitCurrentRooms()`の副作用確認は「一切の副作用が無いこと」ではなく「Act遷移直後のMapRoomに対して
  安全かつ意図通りであること」として実施済み（2.2）
- `test_whole_run_connectivity.py`がMapRoom Act遷移ギャップを既知問題として許容しなくなっている（4.1）
- `MapRoom`がAct境界越え以外の文脈で現れる場合、その文脈でも`=> true`が安全であることを検証済み
  （安全でない場合は専用状態管理に設計変更する）
- RL側の決定的回帰テストは、既存traversalで到達実績のあるseed・固定条件（4.4）を用いており、policyの
  偶然の成否に依存しない
- STS2_Training側のsmoke testでは`seed=1348178664`を被害provenanceとして使い、Act境界越えおよび
  `NoAvailableActionError`非発生を確認済み。ただし「常に同一手数・同一経路」は前提にしない（4.3/4.4）
- Emulator側変更はmainへ直接反映、RL側変更（production codeは基本的に不要見込み、テスト・
  ドキュメント変更は必須）は通常のpull requestフローで反映
