# PendingChoiceのRNG hypothesis依存性調査(alfa/beta実証実験) — 2026-08-15

対象: Fix C(「正しいbranch作成」`stable_root-(rng_0)->pendingChoice->(rng_1で進行)`が現行
Emulator APIで達成可能か)の投機的調査に続く、alfa/beta 2経路の実証実験。**本ラウンドは調査・
実証実験と、1件の永続回帰テスト追加のみ。RL/Emulatorの本体ロジックは一切変更していない。**

## 0. 背景・用語

- **alfa**: `stable-(rng_0)->PendingChoice->stable->(再復元してrng_1)` — 真のrng_0で到達した
  PendingChoiceを、hypothesis rng_1で**解決(resolve)**した場合に効果が変わるか(効果の
  RNG依存性)。
- **beta**: `root-(rng_1)->PendingChoice->stable->(rng_1)` — hypothesis rng_1でroot直後から
  進行した場合に、PendingChoiceの**発生・候補**そのものが変わるか(発生のRNG依存性)。
- どちらか一方でもRNG非依存であれば、Fix Cで検討していた新Emulator API
  (`OverwriteHiddenDrawState`、「新Emulator APIは追加しない」ポリシーの破棄が必要)は
  不要になる可能性が高い、という仮説のもとで実証した。
- `derive_substituted_snapshot()`(`Combat/search/rng_hypothesis.py:370`)は
  **`Rng.RunRng["Shuffle"]`と`Player.DrawPile`の2つのみ**を書き換える設計(docstringに明記)。
  他の全RNGストリーム(`CombatCardSelection`/`CombatCardGeneration`/`CombatTargets`等、
  `RunRngType`列挙で定義)はhypothesis間で完全に素通し(未変更)。

## 1. 調査対象の選定(構造的絞り込み)

「山札(DrawPile)から候補を作る」構造を持つ実装をソースから網羅的に特定した(hypothesis置換が
影響しうるのはこの部分集合のみ)。

- `CardSelectCmd.FromCombatPile(...PileType.Draw...)` / `CardSelectCmd.From*(...Draw...)` の
  grep(`Sts2Emulator/Imported/Source`)で8ファイルに絞り込み: `Tutor.cs`(複数人プレイ専用、
  対象外)、`DropletOfPrecognition.cs`(potion)、`StratagemPower.cs`、
  `ForegoneConclusionPower.cs`、`SeekerStrike.cs`、`Seance.cs`、`Cleanse.cs`、`Charge.cs`
  (以上card)。
- うち`SeekerStrike.cs`のみが`StableShuffle(...CombatCardSelection).Take(3)`という
  絞り込み型(残り7件は山札**全体**を候補にする方式)。
- relic側は`PileType.Draw`と`CardSelectCmd`の交差を取ったところ`BiiigHug.cs`(デッキ全体の
  除去選択、山札ではなくデッキ)のみがヒット。追加でカード生成系relic(`Toolbox.cs`、
  `ChoicesParadox.cs`)、手札系relic(`GamblingChip.cs`)、pickup時選択relic
  (`HeftyTablet.cs`)を対象に加えて計5relicを検証。

## 2. beta(発生のRNG非依存性)の検証結果

### 2-1. 山札全体選択型(Tutor/DropletOfPrecognition/StratagemPower/ForegoneConclusionPower/
Seance/Cleanse/Charge)

`FromCombatPile`が山札**全体**(`PileType.Draw.GetPile(...)`そのもの、Take/Skipによる
絞り込みなし)を候補にするため、構造的に順序非依存。`DROPLET_OF_PRECOGNITION`
(potion)を2seed×2順序で実機検証し、候補6/6枚が完全一致することを確認。

### 2-2. `SeekerStrike`(`StableShuffle(...CombatCardSelection).Take(3)`)

最も疑わしい反例だったが、実機検証で強く支持された。

- 3seed×6ランダム順序=18試行、さらに重複ID混在(5x STRIKE+4x DEFEND+1x ARMAMENTS)で
  3seed×8試行=24試行、いずれも**候補セットがseedごとに完全一致**(順序に非依存)。
- 理由: `StableShuffle`(`ListExtensions.cs:22`)は`list.Sort()`でカードを正規化してから
  `UnstableShuffle`にかける実装のため、入力リストの元の並び順を捨てて処理する。したがって
  `derive_substituted_snapshot`が`Player.DrawPile`の並び順だけを変えても出力は変わらない。
- **instance粒度の懸念も検証・反証済み**: 同一cardIdでもアップグレード状態が異なる複製
  (未アップグレード2枚+アップグレード済み1枚のStrike)を混在させても、選ばれる**具体的な
  個体**自体が順序に完全に非依存だった(3seed×8試行で常に同じ個体パターン)。
  `candidate_semantic_keys`がcardIdレベルの比較である点(独立レビュー指摘)から
  「候補セットは一致するが実際の個体が違う」という隠れた分岐を懸念したが、実害なしと判明。

### 2-3. relic 5件

- `TOOLBOX`/`CHOICES_PARADOX`: `CardFactory.GetDistinctForCombat(..., CombatCardGeneration)`
  で専用カードプールから新規生成。山札を一切参照しないため、hypothesis対象の
  `Shuffle`/`DrawPile`とは無関係(自明にRNG非依存)。
- `GAMBLING_CHIP`: `FromHandForDiscard`で手札全体(山札ではない)から選択、自明に非依存。
  ただし解決(`DiscardAndDraw`)がドローを伴うため**効果**は山札順序に依存する
  (§3-2で扱う、betaではなくalfa側の論点)。
- `BIIIG_HUG`: `FromDeckForRemoval`でデッキ全体(山札ではない)から選択、自明に非依存。
- `HEFTY_TABLET`: `CardFactory.CreateForReward`(専用の報酬RNGストリーム)による生成、
  かつ`AfterObtained`(コンバット外のpickup時)発火のため自明に非依存。

### 2-4. 本番パイプラインでの再検証(方法論の穴を解消)

上記2-2までの検証は、Emulatorシナリオを直接構築して「同じ構成・違う順序」を模しただけで、
本番の`derive_substituted_snapshot()`→Restoreを一切経由していなかった。これを本番の
`CombatInstance.emulate_action()`(`API.combat_rng_mapping.build_single_hypothesis_work_item`
→`apply_hypothesis_to_context`→`derive_substituted_snapshot`→Restore→replay)経由で
再検証した。

真のrng(root, `commit_action`)で`SEEKER_STRIKE`を再生してPendingChoiceに到達し、その
候補action_idが、5種類の異なるhypothesis `rng_id`いずれの`emulate_action`でも
`status == "completed"`として受理されることを確認(不一致0件)。

**この結果を永続的な回帰テストとして追加した**:
[API/tests/test_pending_choice_hypothesis_order_independence.py](../../API/tests/test_pending_choice_hypothesis_order_independence.py)
(pytest実行、pass確認済み)。将来`derive_substituted_snapshot`やStableShuffleの契約が
崩れた場合にこのテストが検知する。

### 2-5. Attack Potionでの最終確認

`ATTACK_POTION`(`CardFactory.GetDistinctForCombat(..., CombatCardGeneration)`、山札を
一切参照しない生成型)についても本番パイプラインで検証し、真のrngで得た候補
`['BREAKTHROUGH', 'CINDER', 'THUNDERCLAP']`が3種類のhypothesis `rng_id`いずれでも
同一に受理されることを確認。DrawPileを介さない生成型は「入力自体がhypothesis間で
変化しない」という、SeekerStrikeより単純な理由でbetaが成立する。

**なお「盤面が同一」というのはPendingChoice自体の候補・解決結果に限った話であり、
DrawPileの並び順自体はhypothesis間で意図的に異なったままなので、この後の通常ドロー等は
引き続き分岐する(hypothesis探索の本質、想定通り)。**

## 3. alfa(効果のRNG非依存性)の検証結果

### 3-1. 山札選択型(SeekerStrike等)の解決は非RNG

解決処理は`CardPileCmd.Add(card, PileType.Hand)`(デフォルト`position = CardPilePosition.
Bottom`)であり、RNGを一切消費しない(`Random`ポジション指定時のみ`Rng.Shuffle`/
`Rng.CombatCardSelection`を消費するが、これらの呼び出しは明示的な`position`引数を渡さない
デフォルト呼び出し)。実機で2回再現し、`hand`/`drawPile`/`energy`/`hp`/`boundary`が
完全一致、山札は「元の構成から選んだ1枚を除いた残り」と厳密一致することを確認。

### 3-2. 例外: `GamblingChip`の解決(`DiscardAndDraw`)

手札からの選択自体はRNG非依存(beta成立)だが、解決処理の`DiscardAndDraw`は捨てた後に
山札から補充ドローするため、その補充ドローの結果は山札順序(hypothesisが変える対象)に
依存する。ただしこれは通常のカードドローと全く同じ性質であり、hypothesis探索
アーキテクチャ全体がそもそも前提としている依存性である。`consume_check()`
(`Combat/search/rng_hypothesis.py:196`)が`choice_card`型アクションを保守的に
「hypothesis必須」として扱っているため、既存の安全機構で捕捉済み。PendingChoice特有の
新しい欠陥ではない。

## 4. Fix Cへの結論

betaは構造的にも本番パイプライン実証でも支持された。すなわち
`stable_root-(rng_0)->pendingChoice`と`stable_root-(rng_1)->pendingChoice`が
**同一のPendingChoiceに到達する**(検証した母集団について)。現行アーキテクチャ
(rootでhypothesis置換→prefix再生→Pendingに到達)と、理論的に「正しい」方式
(true rngでPendingに到達→そこからhypothesis)は、この母集団に関しては同じ結果を生む。
**Fix Cで検討していた新Emulator API(`OverwriteHiddenDrawState`)は、今回特定した
「山札由来PendingChoice」の全カード/potion/relicに関しては不要**と判断する。

### スコープ上の留保

- 全カード/relicを1件ずつ動かしたわけではなく、「山札の構成・順序が候補に影響しうる」
  構造を持つものを構造的に特定して網羅した(それ以外は`Player.DrawPile`/`Shuffle`
  ストリームに触れないため自明にhypothesis非依存)。
- 検証したのはサンプル数(rng_id 5種類、順序試行24回等)による経験的裏付けであり、
  全rng_id空間の数学的証明ではない。ただし`StableShuffle`の実装(正規化ソート後に
  シャッフル)という構造的根拠が経験的結果を裏付けている。
- 将来、新しいカード/potion/relicが**山札から**`UnstableShuffle`で候補を絞り込む形で
  追加された場合(SeekerStrikeの`StableShuffle`とは異なり入力順序依存)、この結論は
  再検証が必要(§5参照)。

## 5. 副次的発見: Attack Potion型の「非決定的branch展開」への示唆(将来課題、未実装)

ユーザーからの将来課題(Attack Potion使用のbranch展開を非決定的にして探索を改善したい)の
ための情報収集として、`CardFactory.GetDistinctForCombat`の内部を調査した。

```csharp
cards = FilterForPlayerCount(player.RunState, cards);
return FilterForCombat(cards).TakeRandom(count, rng)
    .Select(c => player.Creature.CombatState.CreateCard(c, player));
```

- **候補プール**: `player.Character.CardPool.GetUnlockedCards(...)`を`Type`等で絞り込んだ、
  キャラクターのアンロック状況のみで決まる**完全に既知・固定**の集合(DrawPileのような
  隠された情報ではない)。
- **選択アルゴリズム**: `TakeRandom` = `UnstableShuffle`(`ListExtensions.cs:45`)であり、
  SeekerStrikeが使う`StableShuffle`とは異なり**正規化ソートなしの素のFisher-Yates**
  (公式コメント: "the result is dependent on the initial order of the list")。今回
  Attack Potionでbetaが成立したのは、入力(CardPool由来のリスト)自体がhypothesis間で
  一切変化しないためであり、`StableShuffle`の安定性とは別の理由である。
- **RNGストリーム**: `player.RunState.Rng.CombatCardGeneration`(`RunRngType`列挙の1つ、
  `Shuffle`とは独立)。ラン開始時の単一ルートSeedから`Rng(Seed, "combat_card_generation")`
  として派生し、**ラン全体を通じた累積カーソル**(このコンバットより前の生成回数に依存)。
  `derive_substituted_snapshot`は現状このストリームを一切書き換えないため、常に同じ
  3枚になる。
- **エンジン自身の先例**: `TestSupport/TestRngInjector.cs`に
  `SetCombatCardGenerationOverride(List<CardModel>)` / `ConsumeCombatCardGenerationOverride()`
  という、`CardFactory.GetDistinctForCombat`の先頭で最優先チェックされるテスト用
  override機構が既に存在する。ただしstatic・プロセスグローバル・使い捨てのC#内部フックで、
  生の`CardModel`オブジェクトを要求するため、CombatScenario/CombatStateSnapshotの
  JSON DTOには含まれておらず、RL側からは現状到達不可能。

### 実装方針の選択肢(未実装、判断のみ記録)

| 方針 | 概要 | ポリシーとの整合性 |
|---|---|---|
| A: RNGストリーム置換 | `derive_substituted_snapshot`と同じ設計で`Rng.RunRng["CombatCardGeneration"]`も対象に加える | 新Emulator API不要、既存Method Bの延長 |
| B: TestRngInjector相当の公開API | override機構をCombatScenario/SnapshotのDTOとして公開 | Fix Cと同種の「新Emulator API追加」判断が必要 |

方針Aが筋が良いと考えられる: DrawPileのhypothesis生成はPUBLIC_MULTISET(観測済み構成)との
整合性を保つ制約があったが、Attack Potionの候補プールはそもそも最初から完全にpublicであり、
「隠れた真の構成に矛盾しないよう並べ替える」制約が不要 — 有効なdistinct 3枚のサンプルで
あれば何でも正当なhypothesisになるため、DrawPileより単純に実装できる可能性が高い。
**本ラウンドでは調査のみで実装はしていない。**

## 6. 変更ファイル一覧

- 追加: `API/tests/test_pending_choice_hypothesis_order_independence.py`
  (永続回帰テスト、pytest pass確認済み)
- 追加: 本報告書ファイルのみ
- Emulator側(`C:\STS2_Emulator`)は無変更(調査のみ、`git status --short`で確認済み)

## 7. 今後の扱い

RNG関連の調査・コーディングは本ラウンドで一区切り。§5の非決定的branch展開(方針A)は
将来着手する際に本報告書を起点にすること。
