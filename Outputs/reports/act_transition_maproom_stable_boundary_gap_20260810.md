# Act遷移直後、`MapRoom`が`IsCurrentRoomResolved()`で永久に`false`扱いになりスタックする

**発見日**: 2026-08-10
**発見経緯**: `treasure_room_fix_implementation_plan_final_20260810.md`のTreasure修正後、STS2_Trainingの自己対戦で実際にAct 0→Act 1の境界を越えられるようになったことで到達できた、別の(Treasureとは無関係の)既存バグ。
**対象リポジトリ**: STS2_Emulator(C#側の根本原因)。STS2_RL側の追加対応は不要と見られる(未検証)。
**ステータス**: 未修正。本ドキュメントは問題の指摘のみ。

## 症状

Whole Runの`WholeRunSession`(STS2_RL)経由で、Act境界(Actの最終戦闘を終えて次のActへ移る場面)を越えた直後、以下の状態で永久にスタックする:

```json
{
  "boundary": "stable",
  "room_type": "MapRoom",
  "in_room": true,
  "room_resolved": false,
  "at_map_boundary": false,
  "act_index": 1,
  "act_floor": 0
}
```

`get_legal_actions()`は空リストを返し続け、`run_terminal`/`outcome`も設定されない。クライアント(STS2_RL `WholeRunSession`/`instance_whole_run.py`、あるいはSTS2_Training)から見て「非終端なのに取れる行動が無い」状態が解消されない。

## 再現手順

STS2_Trainingの自己対戦(`sts2_training.runner.start_new_run`、character_id=IRONCLAD, ascension=0)を実行し、Act 1のボスを撃破してAct 2へ遷移させる。

実際に確認した再現例: seed=1348178664(IRONCLAD, ascension=0)で、101手目(commit_action)の直後にこの状態に到達。

## 原因(推定、Treasureバグと全く同じ構造)

`Sts2Emulator/Api/GameInstance.cs`の`IsCurrentRoomResolved(AbstractRoom? currentRoom)`(5219行目付近):

```csharp
private bool IsCurrentRoomResolved(AbstractRoom? currentRoom)
{
    return currentRoom switch
    {
        CombatRoom => _combatState == null && _pendingCardReward == null,
        EventRoom => RunManager.Instance.EventSynchronizer.GetLocalEvent()?.IsFinished == true,
        MerchantRoom => _shopLeft,
        RestSiteRoom => _restSiteResolved || RunManager.Instance.RestSiteSynchronizer.GetLocalOptions().Count == 0,
        TreasureRoom => RunManager.Instance.TreasureRoomRelicSynchronizer.IsComplete,
        _ => false   // <-- MapRoomはここに落ちる
    };
}
```

`MapRoom`(`Imported/Source/MegaCrit.Sts2.Core.Rooms/MapRoom.cs`)がこのswitchのどのcaseにも該当せず、デフォルトの`_ => false`に落ちる。そのため`TryEagerExitResolvedRoom()`が`EagerExitCurrentRooms()`を一切呼ばず、`MapRoom`が`CurrentRoom`に居座り続ける。

これはTreasureRoomで見つかったバグ(`treasure_room_fix_implementation_plan_final_20260810.md`参照)と**全く同じ根本原因パターン**(switchに新しいRoom派生クラスのcaseを追加し忘れている)。今回Treasureを直したことで初めて、Act遷移まで到達できるRunが実際に生成されるようになり、この既存の欠落が表面化した。

`MapRoom`自体の実装(`MapRoom.cs`)を見ると:

```csharp
public class MapRoom : AbstractRoom
{
    public override RoomType RoomType => RoomType.Map;
    public override ModelId? ModelId => null;
    public override Task EnterInternal(...) { ... NRun.Instance.SetCurrentRoom(currentRoom); return Task.CompletedTask; }
    public override Task Exit(IRunState? runState) => Task.CompletedTask;
}
```

`Exit()`は無条件で即完了する no-op。つまり`MapRoom`はEnterした時点で「解決すべき状態」を何も持っておらず、常に即座に"resolved"扱いにして問題ないように見える(ただし、これはコード読解による推測であり、Act遷移フロー全体の設計意図の確認は取れていない — 実装者の判断を仰ぐこと)。

## 想定される対応(未検証、判断は実装担当に委ねる)

`IsCurrentRoomResolved`のswitchに`MapRoom => true`を追加する、というのがTreasureの修正パターンをそのまま踏襲した最小の対応候補と考えられる。ただし:

- `MapRoom`がAct遷移以外の文脈でも使われるか(他のRoomType遷移の中間状態としても登場するか)は未確認
- `atMapBoundary`(`currentRoom == null`の場合)と`MapRoom`(`currentRoom`がMapRoomインスタンス)の使い分けの設計意図が不明なため、単純に`true`を返すだけで良いのか、`EagerExitCurrentRooms()`相当の処理を明示的に呼ぶべきかは要検討

## 未実施のこと

- 実際のコード修正(C#側)
- STS2_RL側(`WholeRunSession`/`instance_whole_run.py`)に追加対応が必要かどうかの確認(Treasureのときはchoice_branch_runner.pyの検索ヘルパー側にも副次的な修正が必要だった。同様の副作用がRun/API側にあるかは未調査)
- 再現テストの追加(Treasureの`Run/tests/test_treasure_room_stable_gap.py`に相当するもの)
- 他のRoom派生クラス(将来追加されるもの、または既存で見落とされているものが無いか)の網羅的な確認
