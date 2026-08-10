# TreasureRoom stable-boundary gap — 最終実装計画（上位判断反映版・改訂）

作成日: 2026-08-10（改訂: 同日）
系譜: `treasure_room_stable_boundary_gap_fix_plan_20260809.md`（原案）→
`treasure_room_fix_plan_review_report_20260810.md`（添削）→
`treasure_room_fix_plan_critique_assessment_20260810.md`（添削の検証評価）→
`treasure_room_fix_implementation_plan_final_20260810.md` 初版（上位判断反映）→
**本書（設計をさらに簡略化した改訂版）**

本書は上位監督者による4件の判断を反映し、実装スコープを確定させたもの。旧文書の技術的指摘のうち
有効なものは全て取り込み済み。以降の実装はこの文書を正とする。

**改訂の要点:** 初版では`treasure_choice`という新boundaryと`choice_treasure_pick_relic`という新action
typeをRL/Emulator双方に追加する設計だった。しかし「skipを実装しないなら、そもそもTreasureに
選択判断を渡す必要があるか」という指摘を受けて`TreasureRoomRelicSynchronizer.BeginRelicPicking()`の
実装を精査した結果、**single playerのTreasureRoomには元々「複数relicから選ぶ」メニューが存在しない**
ことが判明した（2節参照）。Skip非実装の下では意思決定ポイントが存在しないため、**新boundary/action
は一切追加せず、post-combat報酬と同じauto-claimパターンで完全に自動解決する**設計に変更した。
これにより3.1のDTO論点も「一部承認（additive）」から「変更不要」へさらに縮小する。

---

## 1. 上位判断ログ

| # | 論点 | 判断 | 備考 |
|---|------|------|------|
| 3.1 | DTO/schemaバージョニング | **一部承認** — 既存schema(v0.7)内で追加対応。version bumpなし | 当初の判断理由（3.3でSkip非実装なら追加は最小限で済む）は妥当。ただし2節の再検討により、そもそもDTOへの追加自体が不要という、承認の前提より一段強い結論に至った（後方互換の優先という判断の方向性とは矛盾しない） |
| 3.2 | Multiplayer投票対応 | **未対応のまま実装してよい** | single-player専用ロジックのみ実装。multi-vote/relic fight分岐は対象外 |
| 3.3 | `choice_treasure_skip` | **実装しない** | RLエージェントの行動空間に`choice_treasure_skip`を含めない。当初は「relicを1つ選ぶ」actionのみ公開する想定だったが、2節の再検討によりそのaction自体も不要と判明 |
| 3.4 | リポジトリ運用 | `STS2_Emulator`は権利関係により`gh`管理外 → **mainへの直接コミット**で実装。`STS2_RL`側は通常通り**pull request**で実装 | Emulator側はレビューフローがRLと異なる点に注意 |

**3.1の根拠（上位判断時点の理由付け、2節の再検討前）:** 3.3でSkipを実装しないため、公開する新規要素は
boundary 1件・action type 1件のみに縮小する、という前提での「additive・version bumpなし」承認だった。
2節の再検討により、この前提自体（「relic選択action 1件は残る」）が誤りで、実際には0件であることが
判明した。上位判断の「後方互換を保ちつつ最小限で対応する」という方向性そのものは維持されており、
今回の再検討はその方向性をさらに徹底した結果と位置づけられる。

---

## 2. なぜ`treasure_choice`もaction typeも不要なのか

`TreasureRoomRelicSynchronizer.BeginRelicPicking()`（`TreasureRoomRelicSynchronizer.cs:89-132`）を実装レベルで
確認した:

```csharp
foreach (Player player in _playerCollection.Players)
{
    _votes.Add(new PlayerVote { voteReceived = false });
    IRunState runState = player.RunState;
    if (Hook.ShouldGenerateTreasure(runState, player))
    {
        RelicRarity rarity = RelicFactory.RollRarity(_rng);
        RelicModel item = TryGetRelicForTutorial(player) ?? _sharedGrabBag.PullFromFront(rarity, runState) ?? RelicFactory.FallbackRelic;
        _currentRelics.Add(item);
    }
}
```

ループは`foreach player`であり、1プレイヤーにつき`_currentRelics`へ**最大1個**しかrelicを積まない
（rarityも`_rng`で1回だけロール — プレイヤーに複数候補を提示する仕組みではない）。single player
（`_playerCollection.Players.Count == 1`）では`_currentRelics.Count`は**常に0か1**であり、
「複数relicから1つを選ぶ」UIは元のゲームロジックにも存在しない。

`PickRelicLocally(index)`の`index`が指すのは「どのプレイヤー枠(=誰の事前ロール分)のrelicを対象にするか」
というmultiplayer調停（同じrelicを複数人が欲しがった場合に`AwardRelics()`でRPS解決する）のための引数であり、
single playerの選択メニューではない。裏付けとして`OnPicked()`のskip分岐:

```csharp
if (!index.HasValue && _playerCollection.Players.Count == 1)
{
    _singleplayerSkipped = true;
    return;
}
```

は「index有無」の二択(=受け取るか受け取らないか)しか想定しておらず、「どのrelicにするか」という
分岐は最初から存在しない。

**結論:** 3.3でSkipを実装しない場合、single playerのTreasureRoomは
「relicが1個ロールされていれば必ず受け取る」「0個ならそもそも何もない」のいずれかで**常に決定的**になり、
RLエージェントに判断させる分岐が存在しない。したがって、post-combat報酬のgold/relic/potionが
`TryBeginRewardPhase()`（`GameInstance.cs:4994`）で自動的に処理されているのと**全く同じパターン**で、
Treasureも新boundary・新action typeを一切設けずに完全自動解決すべきである。

これにより、初版計画にあった以下は**すべて不要**になる:

- boundary `treasure_choice`
- action type `choice_treasure_pick_relic`
- `BuildTreasureLegalActions()` / `AnswerTreasureChoice()`
- RL側`whole_run_session.py`への`TREASURE_CHOICE`/`BOUNDARY_VALUES`追加
- DTO/schemaへの一切の変更（3.1の論点そのものが消滅する）

---

## 3. 確定スコープでの実装設計（auto-claim方式）

### 3.0 【最重要・全面差し替え】`TreasureRoomRelicSynchronizer`の「本物の実装」はビルドから除外されている

**（2026-08-10再々改訂: `treasure_room_fix_implementation_plan_review_latest_assessment_20260810.md`で
判明した、これまでの設計前提を丸ごと覆す事実を反映。3.a以下の`PickRelicAction`/`CurrentRelics`ベースの
設計は実行不可能だったため、3.0〜3.aを全面差し替えた。）**

`Sts2Emulator.csproj`（`GameInstance.cs`を含む）は`Imported/**`を自身ではコンパイルせず
`Sts2Imported.Stage1.csproj`を`ProjectReference`する。その`Sts2Imported.Stage1.csproj`は

```xml
<Compile Remove="Source\MegaCrit.Sts2.Core.Multiplayer.Game\TreasureRoomRelicSynchronizer.cs" />
<Compile Remove="Source\MegaCrit.Sts2.Core.Multiplayer.Game\OneOffSynchronizer.cs" />
```

でこれまで参照してきた「本物」の実装（投票・`CurrentRelics`・`AwardRelics`等を持つ
`Imported/Source/.../TreasureRoomRelicSynchronizer.cs`）を明示的にビルドから除外し、代わりに
同名・同名前空間の**空のno-opスタブ**（`Imported/Stubs/RunMetaSystemsNoOpStubs.cs:676-692`）を
コンパイルする:

```csharp
public sealed class TreasureRoomRelicSynchronizer
{
    public TreasureRoomRelicSynchronizer(object state, ulong localPlayerId, object actionQueueSynchronizer, object sharedRelicGrabBag, object treasureRoomRelics) { }
    public void BeginRelicPicking() { }
    public void OnRoomExited() { }
    public void OnPicked(object? arg0, object? arg1) { }
}
```

`CurrentRelics`プロパティも`PickRelicLocally`/`AwardRelics`/`RelicsAwarded`も存在しない。
`OneOffSynchronizer.DoLocalTreasureRoomRewards()`も`return Task.FromResult(0);`という固定no-op。
**つまり`GameInstance`をどう配線しても、この空のスタブに向けて`PickRelicAction`を投げたところで
何も起きない。**

一方、同じスタブファイル内の`RestSiteSynchronizer`（`RunMetaSystemsNoOpStubs.cs:563-636`）は空no-opでは
なく、`RestSiteOption.Generate()`/`option.OnSelect()`という実物のCommand/Modelを直接呼ぶ、正式な
single-player向け再実装になっている。`OneOffSynchronizer.DoLocalMerchantCardRemoval`も同様。
**Rest/Shopはこの「single-player向け再実装」が既に書かれているためGameInstanceから正しく駆動できるが、
Treasureだけこの再実装が書かれておらず空のままになっている** — これがPR #11の症状のさらに根本にある
原因であり、`GameInstance.IsCurrentRoomResolved()`にTreasureRoomのcaseがないことは、その症状の一部に
過ぎない。

**したがって本fixのスコープは「`GameInstance`から既存のTreasureRoomRelicSynchronizerを駆動する」ではなく
「`RestSiteSynchronizer`と同じパターンで、`TreasureRoomRelicSynchronizer`/
`OneOffSynchronizer.DoLocalTreasureRoomRewards`のsingle-player向け実装を新規に書き、その上で
`GameInstance`から駆動する」に修正する。**

### 3.a 新規実装: `RunMetaSystemsNoOpStubs.cs`にTreasureのsingle-player実装を追加する

**（2026-08-10さらに改訂: `treasure_room_fix_implementation_plan_review_stub_rootcause_assessment_20260810.md`
の指摘を反映。(1) `HasPendingRelic`はデフォルト極性が逆でNotStarted/Completedを区別できない欠陥があった
ため3状態管理に変更。(2) RNG/grab bag配線と(3) gold gateの根拠は、`RunManager.cs:456`の実際のコンストラクタ
呼び出しを確認したことでプレースホルダなしに確定できた。）**

`RunManager.cs:456`で確認した実際の呼び出し:

```csharp
TreasureRoomRelicSynchronizer = new TreasureRoomRelicSynchronizer(
    State, netId, ActionQueueSynchronizer, State.SharedRelicGrabBag, State.Rng.TreasureRoomRelics);
```

`State.SharedRelicGrabBag`と専用RNG stream`State.Rng.TreasureRoomRelics`は、stub版・本物版どちらの
`TreasureRoomRelicSynchronizer`が使われるかに関わらず`RunManager.InitializeShared()`側が既に正しく
渡してくれている。現行のno-opスタブがこれらを`object`型で受け取って捨てているだけなので、**新たな配線を
発明する必要はなく、コンストラクタ引数の型を正しくするだけでよい。**

`RestSiteSynchronizer`と同型のパターンで、`MegaCrit.Sts2.Core.Multiplayer.Game`名前空間内の
`TreasureRoomRelicSynchronizer`を書き換える:

```csharp
public sealed class TreasureRoomRelicSynchronizer
{
    private enum RelicFlowState { NotStarted, Pending, Complete }

    private readonly RunState _runState;
    private readonly RelicGrabBag _sharedGrabBag;
    private readonly Rng _rng;
    private RelicModel? _pendingRelic;
    private RelicFlowState _state = RelicFlowState.NotStarted;

    public TreasureRoomRelicSynchronizer(RunState runState, ulong localPlayerId, ActionQueueSynchronizer actionQueueSynchronizer, RelicGrabBag sharedGrabBag, Rng rng)
    {
        _runState = runState;
        _sharedGrabBag = sharedGrabBag;
        _rng = rng;
    }

    public bool IsComplete => _state == RelicFlowState.Complete;

    public void BeginRelicPicking()
    {
        // 二重実行防止（4.2）。現行の呼び出し順序では実際に二重実行される経路はないと考えられるが、
        // 将来の変更に対する防御的invariantとしてコストなく追加できる。
        if (_state != RelicFlowState.NotStarted)
        {
            throw new InvalidOperationException($"Treasure relic flow already started: {_state}");
        }

        // multiplayer拒否はここが一次防衛線 — BeginRelicPicking()はTreasureRoom.EnterInternal()経由で
        // room入室と同時に自動的に呼ばれ、ChooseRoom()自身のTreasure分岐コードより先に実行されるため、
        // RNG消費/grab bag変異が起きる前に、ここで最初にmultiplayerを拒否する必要がある
        // （ChooseRoom()側にも同じguardを残す場合は、あくまで防御的な二重チェックであり一次防衛線ではない）。
        if (_runState.Players.Count != 1)
        {
            throw new InvalidOperationException("Treasure auto-claim is single-player only.");
        }

        var player = _runState.Players[0];
        if (Hook.ShouldGenerateTreasure(_runState, player))
        {
            var rarity = RelicFactory.RollRarity(_rng);
            // 順序は本物の実装のまま維持する: tutorial relic → shared grab bag → fallback。
            // TryGetRelicForTutorialを移植する場合はここに追加する（3.a末尾の注記参照）。
            _pendingRelic = TryGetRelicForTutorial(player) ?? _sharedGrabBag.PullFromFront(rarity, _runState) ?? RelicFactory.FallbackRelic;
            _state = RelicFlowState.Pending;
        }
        else
        {
            _state = RelicFlowState.Complete;   // relic対象外 = 直ちに完了
        }
    }

    public async Task GrantPendingRelicIfAny()
    {
        switch (_state)
        {
            case RelicFlowState.NotStarted:
                throw new InvalidOperationException(
                    "Treasure relic flow has not started (BeginRelicPicking was never called).");
            case RelicFlowState.Complete:
                return;   // 冪等 — 0-reward pathや既にCompleteに達した後の二重呼び出しに対応
            case RelicFlowState.Pending:
                if (_pendingRelic == null)
                {
                    throw new InvalidOperationException("Treasure relic flow is Pending but no relic exists.");
                }
                await RelicCmd.Obtain(_pendingRelic, _runState.Players[0]);   // 実物のCommand。AfterObtained()も本物が走る
                _pendingRelic = null;   // Obtain成功後にのみクリアする（例外時に不整合stateを残さない）
                _state = RelicFlowState.Complete;
                return;
        }
    }

    public void OnRoomExited()
    {
        // fault/interruptedパスでstateが漏れないことを保証する防御的リセット
        _pendingRelic = null;
        _state = RelicFlowState.NotStarted;
    }

    // 【必須・削除禁止】PickRelicAction.ExecuteAction()（MegaCrit.Sts2.Core.GameActions、
    // Sts2Imported.Stage1で除外されておらず実際にコンパイルされる既存コード）が
    // `treasureRoomRelicSynchronizer.OnPicked(_player, _relicIndex)` を直接呼ぶため、
    // このメソッドを削除するとプロジェクト全体がコンパイル不能になる（CS1061）。
    // 新設計ではこの経路を一切使わない（single-playerではPickRelicActionを構築せず、
    // GrantPendingRelicIfAny()がRelicCmd.Obtain()を直接呼ぶ）ため、到達したら明確にfaultさせる。
    public void OnPicked(Player? player, int? relicIndex)
    {
        throw new NotSupportedException(
            "PickRelicAction-based relic picking is not used by this single-player Treasure implementation " +
            "(GrantPendingRelicIfAny() calls RelicCmd.Obtain() directly). Reaching this method indicates an " +
            "unexpected multiplayer/network code path.");
    }
}
```

`HasPendingRelic`（`_pendingRelic != null`のみで判定）ではなく`IsComplete`という明示的な3状態管理にした
理由: `_pendingRelic == null`は「まだ`BeginRelicPicking()`が呼ばれていない(NotStarted)」と「relic付与が
完了した(Complete)」の両方で成立してしまい、区別できない。`IsCurrentRoomResolved()`は`Step()`末尾だけで
なく`SaveState()`独自の`EagerExitCurrentRooms()`呼び出し（3.c参照）からも呼ばれる汎用ユーティリティである
ため、`_shopLeft`/`_restSiteResolved`と同様に「未着手ならfalse」が安全な初期値になるよう、明示的な状態を
持たせる。`GrantPendingRelicIfAny()`が`NotStarted`から呼ばれた場合は黙って`Complete`にせず明示的に
faultする（`BeginRelicPicking()`を経ずに解決済み扱いにする不正な遷移を防ぐ）。

`TryGetRelicForTutorial`（初回treasure chestでのGorget強制付与、本物の実装にのみ存在）は、このシリーズの
一連のレビューで確立してきた「除外されたクラスの動作をできる限り忠実にstubへ移植する」という原則に従い、
**移植する（確定）**。relic選択順序（tutorial relic → shared grab bag → fallback）
は本物の実装のまま維持し、変更しない — CallingBell fixture（4.d参照）を組み立てる際は、tutorial条件
（`player.UnlockState.NumberOfRuns == 0`等）を満たさない状態にして、実際にshared grab bag側の
`PullFromFront`経路が使われることを保証する必要がある。

`OneOffSynchronizer.DoLocalTreasureRoomRewards()`も同様にno-opから実装へ差し替える。これは
除外済みの本物`OneOffSynchronizer.DoTreasureRoomRewards()`をそのまま踏襲したもの（relic生成が無効な
状況ではgoldも0にする、という実ゲームの仕様をそのまま反映）:

**authoritative callerの確認（既に完了・再調査不要）:** no-opを実装化する際、`ChooseRoom()`からの
新規呼び出しと既存の未知のcallerとで二重にgoldが付与されないかを検証済み。`TreasureRoom.DoNormalRewards()`
（実物・コンパイル対象、`OneOffSynchronizer.DoLocalTreasureRoomRewards()`を呼ぶ唯一の外部経路）
自体を呼ぶ箇所がリポジトリ全体（`Sts2Emulator`/`Sts2Imported.Stage1`双方）に存在しないことをgrepで
確認済み。したがって`ChooseRoom()`が実装後の唯一のauthoritative callerになる（callerが存在しないケース
— 二重付与の懸念はない）。

```csharp
public async Task<int> DoLocalTreasureRoomRewards()
{
    var player = _runState.Players[0];
    if (!Hook.ShouldGenerateTreasure(_runState, player))
    {
        return 0;
    }
    double gold = player.PlayerRng.Rewards.NextInt(42, 53);
    if (AscensionHelper.HasAscension(AscensionLevel.Poverty))
    {
        gold *= AscensionHelper.PovertyAscensionGoldMultiplier;
    }
    await PlayerCmd.GainGold((int)gold, player);
    return (int)gold;
}
```

**`SilverCrucible`フィクスチャに関する注記:** `SilverCrucible`は`ShouldGenerateTreasure`をfalseにする
relicであり、上記の通りgold/relic両方が同じhookでgateされているため、`SilverCrucible`所持時のTreasureは
「0-relicケース」ではなく**「gold・relic両方が0になるケース」**である。4.dのテスト記述はこれに合わせる。

この設計により、`PickRelicAction`/`ActionQueueSynchronizer`/`CompletionTask`待ちは一切不要になる
（`RestSiteSynchronizer.ChooseOption`同様、`GameInstance`から直接`await`できる）。multiplayer分岐
（投票/RPS）も実装不要（単一プレイヤー専用の再実装のため、そもそも存在しない）。`AwardRelics()`
（除外済みの本物の実装）が`RelicCmd.Obtain`以外に持つ副作用がないかも確認済み — `AwardRelics()`は
`RelicPickingResult`を構築して`RelicsAwarded`イベントを発火するだけで、そのイベントへの購読は
リポジトリ全体でゼロ件（＝実質的に死んでいる経路）だったため、この経路を丸ごとバイパスして
`RelicCmd.Obtain()`を直接呼ぶ新設計に伴う副作用の見落としリスクはない。

### 3.c `ChooseRoom()`内でTreasureRoomを完全自動解決し、既存の`TryEagerExitResolvedRoom()`機構で`map_select`へ戻す

`GameInstance`には、resolved roomを実際に離脱させ`CurrentRoom`を`null`（→`boundary == "map_select"`）に
戻す既存の機構が既にある:

```csharp
// GameInstance.cs:5199-5209（既存）
private bool IsCurrentRoomResolved(AbstractRoom? currentRoom)
{
    return currentRoom switch
    {
        CombatRoom => _combatState == null && _pendingCardReward == null,
        EventRoom => RunManager.Instance.EventSynchronizer.GetLocalEvent()?.IsFinished == true,
        MerchantRoom => _shopLeft,
        RestSiteRoom => _restSiteResolved || RunManager.Instance.RestSiteSynchronizer.GetLocalOptions().Count == 0,
        _ => false   // TreasureRoomはここに落ちる
    };
}

// GameInstance.cs:5211-5223（既存）— 呼び出し元はStep()末尾（3094行目）のみ
private void TryEagerExitResolvedRoom()
{
    if (!HasMap || _pendingTarget != null || _pendingChoice != null || _pendingCardReward != null
        || !IsCurrentRoomResolved(_runState!.CurrentRoom))
    {
        return;
    }
    EagerExitCurrentRooms();   // RunManager.Instance.ExitCurrentRooms() → CurrentRoom = null
}
```

Combat/Event/Shop/Restは全てこの`IsCurrentRoomResolved()`にcaseを持ち、`Step()`の末尾で呼ばれる
`TryEagerExitResolvedRoom()`経由で`map_select`へ復帰する。**TreasureRoomにはcaseがなく、かつTreasureは
`Step()`を一切使わない設計のため、この機構が発火する経路が存在しなかった。** 修正は2箇所:

```csharp
// (1) IsCurrentRoomResolved() に1ケース追加（3.aの新実装のIsCompleteを使う）
TreasureRoom => RunManager.Instance.TreasureRoomRelicSynchronizer.IsComplete,
```

```csharp
// (2) ChooseRoom() 内、isCombat分岐と同じ並び — 3.aの新実装を直接待つだけで、
// action queue/CompletionTask待ちは不要（RestSiteSynchronizer.ChooseOptionと同じ形）
else if (currentRoom is TreasureRoom treasureRoom)
{
    // 防御的な二重チェック（一次防衛線は3.aのBeginRelicPicking()自身）。
    if (_runState!.Players.Count != 1)
    {
        throw new InvalidOperationException("Treasure auto-claim is single-player only.");
    }

    RunManager.Instance.OneOffSynchronizer.DoLocalTreasureRoomRewards().GetAwaiter().GetResult();       // gold
    RunManager.Instance.TreasureRoomRelicSynchronizer.GrantPendingRelicIfAny().GetAwaiter().GetResult(); // relic（あれば）

    TryEagerExitResolvedRoom();   // ← 既存の確立された機構をそのまま再利用し、map_selectへ戻す
}
```

**`ChooseRoom()`の同期API契約は確定（実装時判断ではない）:** public `ChooseRoom()`のシグネチャ
（`RoomEnterResult`を同期的に返す）は変更しない。`GameInstance.cs`は`EagerExitCurrentRooms()`
（`RunManager.Instance.ExitCurrentRooms().GetAwaiter().GetResult()`）や`ProceedAfterRewards()`
（`RunManager.Instance.ProceedFromTerminalRewardsScreen().GetAwaiter().GetResult()`、
`action.CompletionTask.GetAwaiter().GetResult()`）など、**publicメソッドを同期のまま維持しつつ内部で
`GetAwaiter().GetResult()`により非同期呼び出しを待つ**という慣習を既にファイル全体で一貫して採用して
おり、Treasure処理もこれにそのまま従う。`ChooseRoom()`を`Task<RoomEnterResult>`化することはしない —
Python bridge/`WholeRunSession.choose_room()`/worker process/testsへの外部契約変更は発生しない。

**CallingBellへの対応と`map_select`契約の正確な定義（重要 — 「常に`map_select`」という無条件表現は誤り）:**
3.aの新実装は`RelicCmd.Obtain(relic, player)`を直接`await`するため、`AfterObtained()`（`CallingBell`の
場合`RewardsCmd.OfferCustom(...)`まで含む）は`GrantPendingRelicIfAny()`の`await`が完了するまでに実際に
走り切る。この追加reward選択が`_pendingChoice`/`_pendingCardReward`等の既存`GameInstance`状態に反映
される場合、`TryEagerExitResolvedRoom()`はその既存guardにより**意図的にeager exitを保留する**。

したがって正確な契約は次の通り:

> `choose_room(Treasure)`はsingle-playerでgold/relicのauto-claimを同期的に実行する。**通常ケース**
> （取得したrelicが追加のdecisionを発生させない場合）では、戻り値の時点で
> `observation.boundary == "map_select"`になっている。**ただし**、取得したrelicの`AfterObtained()`
> （`CallingBell`等）が既存の一般的なdecision boundary（`_pendingChoice`/`_pendingCardReward`が反映する
> もの）を発生させた場合は、`TryEagerExitResolvedRoom()`の既存guardによりeager exitが保留され、
> `choose_room()`はその一般的なboundaryを返す。呼び出し側はそのboundaryを既存の仕組み（`step()`等）で
> 解決した後、最終的に`map_select`へ到達する。**Treasure専用のboundary/actionは追加しない** — 既存の
> 一般的なdecision boundaryをそのまま経由するだけである。

`_pendingChoice`/`_pendingCardReward`が実際に`RewardsCmd.OfferCustom()`由来の追加rewardを反映するかは
読解だけでは確定できないため、実機テストで検証する（4.d参照）。`IsCurrentRoomResolved()`の呼び出し元は
`TryEagerExitResolvedRoom()`（`Step()`末尾）と`SaveState()`自身の独立したeager-exitチェック
（3.d参照）の2箇所のみであることを確認済みで、**両方とも`IsCurrentRoomResolved()`を呼ぶ前に同じ
`_pendingChoice`/`_pendingCardReward`フィールドを個別にガードしている**ため、CallingBell由来の
pending decisionがどちらか一方にでも正しく反映されれば、2箇所とも対称に保護される。

この設計では`_treasureResolved`のような専用flagは不要になる — `TryEagerExitResolvedRoom()`が
CombatRoom/EventRoom/MerchantRoom/RestSiteRoomと全く同じ経路でTreasureRoomも`CurrentRoom == null`へ
戻すため、`ComputeObservationBoundary()`側の変更は一切不要（既存の`if (HasMap && currentRoom == null) return "map_select";`
がそのまま機能する）。これにより`SaveState()`側も、他のresolved room typeと全く同じ`CurrentRoom == null`
状態を通るため、Treasure固有の追加対応が不要になる（3.d参照）。

**`room_resolved`のタイミングに関する注記:** `GameInstance.cs:3159`の`RoomResolved = IsCurrentRoomResolved(currentRoom)`
は、`TryEagerExitResolvedRoom()`実行後（`currentRoom == null`）には`IsCurrentRoomResolved(null)`が
`_ => false`に落ちるため`false`を返す。つまり`ChooseRoom()`の戻り値時点（eager exit後）で
`get_room_context().room_resolved`を読んでも`True`にはならない — これは「今まさに未解決の部屋がある
わけではない」ことを示す値であり、「直前のTreasureが解決した」ことを示す値ではない。4.d/6の受け入れ条件は
これに合わせて修正する。

### 3.d `SaveState()`

3.cで`TryEagerExitResolvedRoom()`を明示的に呼ぶことにしたため、`ChooseRoom()`が返った時点で
`CurrentRoom`は既にCombat/Event/Shop/Rest解決後と全く同じ`null`状態になっている。したがって
`SaveState()`側でTreasure固有の新しい分岐は不要 — 既存の「`HasMap && currentRoom == null`」に
対応する保存パス（他room type解決後のmap snapshot保存と同じコード）がそのまま通る。

---

## 4. STS2_RL側の修正（確定版）

新boundary/action typeが不要になったため、RL側の修正は**room discovery（探索）バグの修正のみ**に縮小する。
これはEmulator側の設計とは独立に、既存のTreasureルーム探索が壊れているという別問題であり、引き続き修正が必要。

### 4.a `Run/choice_branch_runner.py:179-181`

```python
if room["point_type"] == "Treasure" and target_room_type != "TreasureRoom":
    continue
```

（原案の`!= "Treasure"`は誤り。既存呼び出し規約（`search_for_room_type("EventRoom", ...)`等）に合わせ
`"TreasureRoom"`と比較する。）

### 4.b `Run/worker_pool.py:471`

同種の無条件skipを同様に修正する。`request.target_room_types`に`"TreasureRoom"`が含まれる場合のみ
probeを許可する形に変更する。

```python
for room in rooms:
    if room["point_type"] == "Treasure" and "TreasureRoom" not in remaining:
        continue
```

### 4.c `whole_run_session.py` / `instance_whole_run.py`

**変更なし。** `BOUNDARY_VALUES`に新boundaryを追加する必要はない — Treasure専用のboundary/actionは
存在せず、既存のboundary語彙（`map_select`が通常ケース、`CallingBell`等が既存の一般的なdecision
boundaryを発生させた場合はそちらを経由する — 3.c「CallingBellへの対応」参照）のみで表現される。

### 4.d `Run/tests/test_treasure_room_stable_gap.py`

現行の2テスト（stuck状態を固定していたテスト）は、Emulator側修正後に**意図的に失敗する**ため、
正常系テストへ置き換える:

- known-affected seedでTreasure roomに入室 → `choose_room()`の呼び出しが正常に完了する
  （`entered["room_type"] == "TreasureRoom"`であることも合わせて確認 — eager exit後も`RoomEnterResult`が
  正しくTreasureRoomを報告することのregressionガード）
- 入室直後に`get_observation().boundary`が**`"map_select"`**であること（`"stable"`のままではない —
  3.cの`TryEagerExitResolvedRoom()`呼び出しにより、Combat/Event/Shop/Rest解決後と同じ経路で復帰する）
- `get_map_rooms()`が空でないこと
- `save_state()`が例外なく成功すること（`WholeRunInstance`層の`_maybe_capture_map_snapshot()`が
  `boundary == map_select`時に呼ぶのと同じ操作)
- `API/instance_whole_run.py`経由（`WholeRunInstance`）でも、root viewのlegal actionsに`map_room`
  actionが現れること（`_map_rooms_as_legal_actions`が`boundary == MAP_SELECT`の時だけ有効になるため、
  低レベルAPIのテストだけでなくこの経路も確認する）
- gold/relicが exactly once 付与されていること（`get_run_state()`のgold/relics差分で確認）
- 「gold・relic両方が0になる」ケースを`inject_relic`で`SilverCrucible`を付与した状態から決定的に再現し
  （seed探索に頼らない — `SilverCrucible`は`Hook.ShouldGenerateTreasure()`をオーバーライドする既存relic。
  gold/relic両方が同じhookでgateされる仕様のため、これは「0-relicのみ」のケースではないことに注意 —
  3.a注記参照）、同様に即座に`map_select`へ進むことを確認する
- CallingBellが実際にTreasureで取得された場合、`RewardsCmd.OfferCustom()`由来の追加reward選択が
  `TryEagerExitResolvedRoom()`の既存guardで正しく防がれ、その追加rewardを解決した後に初めて`map_select`
  へ進むことを実機で確認する（3.c「CallingBellへの対応」参照 — 読解だけでは検証不可能なため実機テストが
  必須）。**`inject_relic`はプレイヤー所有relicへの追加用ヘルパーであり、「次にTreasureで引かれるrelic」
  を制御する用途には使えない** — 実装時に(a)`SaveState()`のJSON中の共有grab bag相当フィールドを直接
  編集する、(b)`TreasureRoomRelicSynchronizer`にtest-only setterを用意し`_pendingRelic`を直接CallingBell
  へ固定する、のいずれかで確定的なfixtureを用意する。`TryGetRelicForTutorial()`を移植する場合はgrab bag
  から`Gorget`を横取りする可能性があるため、CallingBell fixtureとの相互作用も確認する
- その後`choose_room()`（または`WholeRunInstance`の`map_room` action経由）で正常に次roomへ進めること
- 2回目以降のTreasure room訪問でも`TreasureRoomRelicSynchronizer`の状態が正しく`NotStarted`から
  再初期化されていること（`OnRoomExited()`のregressionガード）

**`room_context.room_resolved`について:** `TryEagerExitResolvedRoom()`実行後（`ChooseRoom()`の戻り値
時点）は`currentRoom == null`のため`room_resolved`は`False`に戻る（3.a注記参照）。「Treasureが解決した
こと」自体はboundaryが`map_select`になっていること・gold/relicが付与されていることで確認し、
`room_resolved == True`を直接のassertionとしては使わない。

### 4.e `search_for_room_type`/discovery系のテスト追加

4.a/4.bの修正により`search_for_room_type("TreasureRoom", ...)`と`worker_pool`のTreasure discoveryが
機能するようになるため、それぞれについて成功ケースのテストを追加する。

### 4.f `Run/room_progression_driver.py`: map snapshot lifecycleバグ（production code、確認済み）

`drive_rooms()`のMAP_SELECT分岐（139-158行目付近）は、`choose_room()`成功後に`last_map_snapshot`/
`tried_room_ids_at_current_map`を**意図的にクリアしない**（「本当の`step()`が成功するまではこのmap
forkは進行中」というコメント付きの前提）。この前提は、Treasureが`choose_room()`内で自動的に次の
`map_select`まで進んでしまう新設計と食い違う:

1. Treasure roomを選択 → `choose_room()`が内部でauto-claim + eager exitまで完了し、戻り値の時点で
   新しいMap forkに到達している
2. `session.get_observation()`は新しい`map_select`を返す
3. ループ先頭に戻り`MAP_SELECT`分岐に再度合致するが、`last_map_snapshot`は**古い（Treasure選択前の）
   Map**のまま、`tried_room_ids_at_current_map`も古いままなので、新しいsnapshotが再取得されない
4. 後続でこの新しいMap内の別roomが「unsupported」判定された場合、`session.load_state(last_map_snapshot)`
   が**Treasure選択前の古いMapへロールバックし、Treasure解決を含むそれ以降の進行を丸ごと破棄する**

**修正:** `choose_room()`直後、`get_observation()`の結果が`MAP_SELECT`であれば（＝roomが
`choose_room()`内で自動解決してすでに次のMap forkに進んでいれば）、その場でスナップショット追跡状態を
リセットする。

```python
obs = session.get_observation()
if obs["boundary"] == MAP_SELECT:
    last_map_snapshot = None
    tried_room_ids_at_current_map = set()
continue
```

モジュールdocstring・`pick_room()`のコメント（「Treasureは現在public resolve APIを持たないため
非Treasureを優先する」）もこの前提を踏まえて更新する。

### 4.g 既存テストの2件の追加修正（確認済み）

- `Run/tests/test_whole_run_connectivity.py:95`の
  `assert all(r["room_type"] == "TreasureRoom" for r in summary["unsupported_rooms"])`は、修正後
  `unsupported_rooms`が空リストになると`all()`が空リストに対し自明にTrueを返すため、regressionを
  検出できない空文になる。`assert summary["unsupported_rooms"] == []`へ強化する。
- `Run/choice_branch_runner.py:275-276`の`holder_sibling_isolated`チェック
  （`holder_state["current_room_type"] == holder_entered["room_type"]`）は、`choose_room()`内で
  自動的にroomを離脱するTreasureが map branch選択の候補に偶然含まれると、`current_room_type`が
  もはや`"TreasureRoom"`ではなくなるためスプリアスに失敗する。Treasureのように`choose_room()`内で
  自動的に離脱するroom typeをこの等式チェックの例外として扱うよう修正する。

**PR #11の状態（確認済み）:** `Run/tests/test_treasure_room_stable_gap.py`は`main`には存在せず、
未マージのPR #11（`agent/treasure-room-stable-boundary-gap`ブランチ）にのみ存在する。実装着手前に
PR #11を継続利用して本fixを追加するか、`main`から新規branchを切ってPR #11の内容を取り込みsupersede
するかを決定する。

---

## 5. 実装順序とリポジトリ運用（3.4反映）

`STS2_Emulator`は権利関係により`gh`管理外のため、通常のPRレビューフローが適用できない。

| # | 作業 | リポジトリ | 運用 |
|---|------|-----------|------|
| 1 | `RunMetaSystemsNoOpStubs.cs`: `TreasureRoomRelicSynchronizer`をno-opから実装へ差し替え（3.a）。既存の`OnPicked`は`PickRelicAction.cs`のコンパイル互換のため削除せず、faultするシムとして残す | STS2_Emulator | mainへ直接コミット |
| 2 | `RunMetaSystemsNoOpStubs.cs`: `OneOffSynchronizer.DoLocalTreasureRoomRewards()`をno-opから実装へ差し替え（3.a） | STS2_Emulator | 同上 |
| 3 | `IsCurrentRoomResolved()`に`TreasureRoom => IsComplete`のcaseを追加（3.c） | STS2_Emulator | 同上 |
| 4 | `ChooseRoom()`: Treasure入室検出 + single-player guard + `DoLocalTreasureRoomRewards()`/`GrantPendingRelicIfAny()`のawait（3.c） | STS2_Emulator | 同上 |
| 5 | `ChooseRoom()`のTreasure分岐末尾で`TryEagerExitResolvedRoom()`を呼ぶ（3.c） | STS2_Emulator | 同上 |
| 6 | Emulator側テスト（relic付与経路、0件relic経路、gold付与、`map_select`復帰、`SaveState()`成功、multiplayer guard、CallingBell経路の実機確認） | STS2_Emulator | 同上 |
| 7 | PR #11を継続利用するかsupersedeするかを決定する（4.f末尾参照 — `main`未マージのため） | STS2_RL | 方針決定 |
| 8 | `choice_branch_runner.py`修正（4.a） | STS2_RL | **pull request** |
| 9 | `worker_pool.py`修正（4.b） | STS2_RL | 同一PR内で可 |
| 10 | `room_progression_driver.py`のmap snapshot lifecycle修正（4.f） | STS2_RL | 同一PR内で可 |
| 11 | 実機smoke test（known-affected seedでTreasure roomが`map_select`まで自動解決されることを確認） | STS2_RL | PR内 |
| 12 | `test_treasure_room_stable_gap.py`置き換え（4.d） + discoveryテスト追加（4.e） | STS2_RL | 同一PR |
| 13 | `test_whole_run_connectivity.py`のunsupported_rooms assertion強化 + `choice_branch_runner._attempt_map_branch()`のholder_sibling_isolated修正（4.g） | STS2_RL | 同一PR |
| 14 | `test_whole_run_connectivity.py`等の回帰確認 | STS2_RL | 同一PR |

1-6（Emulator）が完了し、更新されたEmulatorビルドをSTS2_RLが参照できる状態になってから7-14（RL、PR）に着手する。
初版計画にあったDTO文書更新（`whole_run_api_reference_20260803.md`）はwire schema変更が不要な点では変わらないが、
3.c「CallingBellへの対応と`map_select`契約の正確な定義」に記載した契約（**通常ケースでは**戻り値の時点で
`map_select`、relicの`AfterObtained()`が既存の一般的なdecision boundaryを発生させた場合はそれを経由してから
`map_select`へ到達する — 無条件の「常に`map_select`」ではない）をそのまま一文追記することを推奨する。

---

## 6. 受け入れ条件（確定版）

- `treasure_choice`というboundary文字列も`choice_treasure_*`というaction typeも、DTO/APIのどこにも現れない
- `choice_treasure_skip`はいかなる形でも公開されない（そもそも公開action自体が存在しない）
- multiplayer分岐（複数投票、relic fight解決）は実装しない。誤ってmultiplayer構成で起動された場合は
  Treasure自動解決の入口で明示的にfaultする（silent partial handlingにしない）
- TreasureRoomへの入室（`choose_room()`）が完了した時点で、gold・relic（あれば）の付与が常に完了している。
  **通常ケース**（取得relicが追加decisionを発生させない場合）では`observation.boundary`が`"map_select"`
  に戻っており`get_map_rooms()`が空でない（`"stable" + []`のまま留まらない —
  `IsCurrentRoomResolved()`/`TryEagerExitResolvedRoom()`というCombat/Event/Shop/Restと共通の既存機構を
  経由することを`TreasureRoom`のcase追加で保証する）。**`CallingBell`等でrelicの`AfterObtained()`が
  既存の一般的なdecision boundaryを発生させた場合はその限りではなく**、その一般的boundaryを既存の仕組み
  で解決した後に`map_select`へ到達する（3.c「CallingBellへの対応」参照 — 「常に`map_select`」という
  無条件の記述はしない）
- `save_state()`が（通常ケースのTreasure解決直後、または一般的なdecisionを解決してmap_selectへ到達した
  直後に）例外なく成功する
- `API/instance_whole_run.py`（`WholeRunInstance`）経由でもroot viewのlegal actionsに`map_room`が現れる
- gold/relicが exactly once 付与される（二重付与なし）
- `search_for_room_type("TreasureRoom", ...)`と`worker_pool`のroom-type discoveryの両方が
  Treasure roomを正しく発見できる
- `TreasureRoomRelicSynchronizer`/`OneOffSynchronizer.DoLocalTreasureRoomRewards()`が
  `RunMetaSystemsNoOpStubs.cs`内でno-opではなく実際にgold/relicを付与する実装になっている
  （`RestSiteSynchronizer`と同型の、実物Commandを直接呼ぶsingle-player向け再実装 — 3.a参照）
- `TreasureRoomRelicSynchronizer.OnPicked(Player?, int?)`が削除されずに残っている（`PickRelicAction.cs`
  という既存のコンパイル対象コードが直接呼び出すため、削除するとプロジェクト全体がコンパイル不能になる —
  3.a参照。新設計では到達しないため`NotSupportedException`をfaultとして投げる）
- `GrantPendingRelicIfAny()`の`await`完了後に`IsComplete == true`であることを確認してから
  `TryEagerExitResolvedRoom()`を呼ぶ
- `CallingBell`（`RelicCmd.Obtain`経由の`AfterObtained()`が引き起こす追加reward割り込み）が
  `TryEagerExitResolvedRoom()`の既存guard（`_pendingChoice`等）で正しく防がれることを実機で確認している
  （3.c「CallingBellへの対応」参照）
- `entered["room_type"] == "TreasureRoom"`がeager exit後も正しく報告される（`RoomEnterResult`の
  regressionテストで保証）
- multiplayer guardは`BeginRelicPicking()`自身の先頭（RNG/grab bag変異より前）に置かれている
  （`ChooseRoom()`側にも同じguardを残す場合は防御的な二重チェックに過ぎない）
- `TryGetRelicForTutorial()`を移植する（確定 — 3.a参照）
- `room_progression_driver.py`が、`choose_room()`内で自動的に次の`map_select`まで進むroom type
  （Treasure）に遭遇した際、古いmap snapshotへ誤ってロールバックしない（4.f参照）
- `test_whole_run_connectivity.py`の`unsupported_rooms`アサーションが、空リストであることを積極的に
  検証する形になっている（`all()`の空リスト自明成立に頼らない — 4.g参照）
- `choice_branch_runner._attempt_map_branch()`の`holder_sibling_isolated`チェックが、Treasureのように
  `choose_room()`内で自動的にroomを離脱するroom typeに対してスプリアスに失敗しない（4.g参照）
- Emulator側変更はmainへ直接反映、RL側変更は通常のpull requestフローで反映される
- 既存のDTO契約（`whole_run_api_reference_20260803.md`、schema v0.7）に対するwire shape変更は一切不要
