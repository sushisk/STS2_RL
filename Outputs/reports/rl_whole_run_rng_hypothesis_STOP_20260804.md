# RL担当報告（停止）：Whole Run `rng_id` Hypothesis実装

- RL基準commit: `9e9ee01`
- Emulator基準commit: `fca2f06`
- 対象指示: 「RL担当指示：Whole Run `rng_id` Hypothesis実装」

## 結論

**実装を停止し、報告します。** 指示の停止条件のうち以下3つに該当することを、Emulator側のソースコード調査で確認しました。

* 未消費Futureだけを安全に変更できない
* 過去または現在の公開状態まで変化する
* Hypothesis生成にEmulator DTO契約変更が必要

独自の代替疑似乱数処理（RL側で勝手にMap/Encounter/Boss内容を再生成する等）は追加していません。コードへの変更は一切行っておらず、作業ツリーはPhase前と同じくcleanです（`Training/`の既存差分以外に変更なし）。

---

## 1. 調査内容

### 1.1 既存の公開RNG操作APIの範囲

Whole Runで公開されている唯一のRNG状態操作APIは以下の2つのみです（`GameInstance.cs:3138-3156`）。

```csharp
public EventRngSnapshot GetEventRngState()
public void SetEventRngState(EventRngSnapshot state)
```

`GetEventRngState()`は内部で`GetCurrentEventModelOrThrow()`を呼んでおり、**現在Eventの中にいない場合は例外を送出します**。つまりこのAPIは「今まさに進行中のEvent 1つ」に紐づく以下4ストリームだけを対象とした、意図的に狭いスコープの機構です。

* `EventRng`（そのEvent自身の抽選）
* `PlayerRewardsRng`（報酬関連）
* `PlayerShopsRng`（Shop関連）
* `PlayerTransformationsRng`（変換関連）

`SetEventRngState()`も、渡された`EventId`が現在Eventと一致しない場合は例外になります。Map／Encounter／Boss／Ancient等、Event以外の文脈でRNG状態を部分的に読み書きできる公開APIはEmulatorに存在しません（`GameInstance.cs`内を`RngState`／`RngSnapshot`で検索した結果、上記2メソッド以外に該当なし）。

### 1.2 Map／Encounter／Boss／Ancient等の「Hidden Future」の生成タイミング

`RunRngType`（`MegaCrit.Sts2.Core.Entities.Rngs/RunRngType.cs`）は12種のRun全体スコープRNGストリームを定義しており、その筆頭が`UpFront`です。ドキュメントコメントに明記されています。

```csharp
/// Determines everything that's generated upfront when a run first starts. This includes:
/// * Which monsters you'll fight.
/// * Which events you'll run into.
/// * Which relics you'll be offered.
public Rng UpFront => GetRng(RunRngType.UpFront);
```

実際の呼び出し箇所（`RunManager.cs`）を確認すると、Act生成時に`UpFront`ストリームから**そのAct全体**の内容が一括生成されていることが分かります。

```csharp
act.GenerateRooms(State.Rng.UpFront, State.UnlockState, State.Players.Count > 1);
```

つまり、あるActに入った時点で、そのAct内の全Room（Map接続構造）・各Roomに紐づくEncounter・Event種別・Boss（`AllBossEncounters`からの選出含む）は**既に1回のupfront抽選で全て確定済み**であり、Roomを訪問するたびに個別に新規抽選される「未消費Queue」のような構造にはなっていません。プレイヤーへ見せる`point_type: "Unknown"`（Part A監査で確認済み）は情報の**表示制御**であって、対応するRNG抽選自体が「まだ行われていない」ことを意味しません。

### 1.3 なぜ安全なHypothesis生成が成立しないか

指示の「変更してよいもの」（将来のRNG Stream、未消費のEvent／Encounter候補、未到達のBoss／Ancient等のHidden Future）を実現するには、`UpFront`ストリームの状態を書き換えて再生成する必要があります。しかし：

1. `UpFront`は**Act全体の内容を1回の抽選でまとめて生成**するため、「まだ訪問していない部分だけ」を選択的に再抽選する、ストリーム位置ベースの安全な分離点が存在しません（Combat側の`Shuffle`ストリームのように「DrawPileの残り部分だけ」という明確な境界がありません）。
2. `UpFront`の状態を変更して`Map`を再生成した場合、既に訪問済み・確定済みのRoom内容（`point_type`が既に判明しているNode、既に発生したEvent、既に選んだ選択肢の結果等）まで意図せず変化する可能性を、外部から安全に排除する手段がありません。Combat側の`derive_substituted_snapshot`が持つ「PUBLIC_MULTISETとの整合性チェック」（`_draw_pile_instances_for_hypothesis`の`requested != available`検証）に相当する、Whole Run側の同種の安全弁が存在しません。
3. これを安全に行うための唯一の公開APIである`GetEventRngState`/`SetEventRngState`は、Map／Encounter／Boss／Ancientの生成には一切関与しません（Event進行中の副次的なRNGのみ対象）。

以上より、**指示にある「未消費Futureだけを安全に変更できない」「Emulatorの生成規則を無視したQueue編集が必要」「Hypothesis生成にEmulator DTO契約変更が必要」の3条件すべてに該当**すると判断しました。

---

## 2. 部分的に安全な範囲（参考、未実装）

`GetEventRngState`/`SetEventRngState`が扱う4ストリーム（Event自身の抽選・報酬・Shop・変換）に限定すれば、Combat同様の「公開Multisetに整合する範囲でのHypothesis生成」に近い、限定的に安全な実装は技術的に可能です。ただし対象はあくまで**「今まさに進行中のEvent 1つ」の内部結果**に限られ、指示が主眼としているMap／Encounter／Boss／Ancientの分岐は含まれません。この限定範囲での実装が有用であれば、別途指示をお願いします。

## 3. 今回変更したファイル

なし（調査のみ、コード変更ゼロ）。`git status`は`Training/`の既存差分以外に変更なしです。

## 4. 提案

* 選択肢A: 上記2.の限定範囲（Event内部RNGのみ）でHypothesis実装を進める
* 選択肢B: Emulator側に「Act未消費分のRNG状態を安全に部分置換できるAPI」の新設を依頼した上で、Whole Run側のHypothesis実装を再開する
* 選択肢C: Whole Runの`rng_id`は当面ボーキーピングのみ（既存実装のまま）とし、本作業は保留する

ご判断をお願いします。
