# 山札順不明入力とRNG shuffleに関する仕様案 v1 (2026-08-01)

**位置づけ**: これは実装提案・仕様案であり、Emulator担当の判断を経ていない。RL担当としての
「こう実装できるはずだ」という仕様素案であり、コード変更は一切含まない。
現行DTOとの対応は`emulator_dto_contract_rl_required.v1.md`§5・§6・§9を参照。

## 背景・動機

Combat Mermaidベースライン(`docs/architecture/combat/mermaid_combat_rng_hypothesis_detail.mermaid`)の
RNG Hypothesis設計は、「候補分類・枝刈り完了後に生存した候補に対してのみ、Restore前にSnapshot JSONの
RNGフィールドを直接編集する(方式B)」という前提で構築されている。この前提が成立するのは、
**Restore対象のSnapshotが常に「山札順が既に確定したOrdered状態」であること**が暗黙の条件になっている
ためである。

一方、探索や評価の入力として「山札の中身(Card Instance集合)は分かっているが、その並び順は不明
(あるいは意図的に不確定として扱いたい)」という状態を表現したい場面が今後生じうる
(例: Event/Card Reward等でデッキ構成が変わった直後、実際の山札shuffleがまだ行われていない状態を
仮説的に評価したい場合)。現行のDTO・APIには、この「順不明入力」を表現する手段が一切存在しない
(`emulator_dto_contract_rl_required.v1.md`§5「不足」参照)。本書はこれを埋めるための仕様案を示す。

## 前提として確認した既存の制約(重要)

`MegaCrit.Sts2.Core.Extensions.ListExtensions.UnstableShuffle`(Fisher-Yatesシャッフル)の
実装コメントに以下の記載がある(意訳):

> このシャッフルは「unstable」である。すなわち、結果はシャッフル対象リストの**初期順序に依存する**。
> 同じカード集合でも初期順序が異なる2つのリストを、同じRNGでシャッフルしても、結果は異なりうる。

**これは本仕様案の設計を強く制約する事実である。** 「Card Instance集合(Unordered)＋現在のRNG状態」を
入力としてEmulator内部のシャッフルアルゴリズムに渡しても、それだけでは実際のゲームが持っていた
「真の現在順」を再現できる保証はない。真の現在順を再現するには、RNG状態だけでなく、
**シャッフル直前の真の順序(pre-shuffle order)も必要**である。

したがって、本仕様案が提供できるのは以下のいずれかであり、両者は明確に区別されなければならない:

- **(a) 決定論的だが真の順序の再現ではない仮説的順序**: 呼び出し側が任意に(だが再現可能な形で)
  Card Instance集合を並べ、それをそのままpre-shuffle orderとしてEmulatorのシャッフルアルゴリズムへ
  渡す。同じ入力(集合＋順序付け規約＋RNG状態)であれば常に同じ結果になる(決定論的)が、
  実際のゲームが持つ真の山札順と一致する保証はない。
- **(b) 真の順序の再現**: 呼び出し側が実際のゲームのpre-shuffle orderを何らかの方法で
  正確に知っている場合にのみ、(a)と同じ機構で真の順序を再現できる。ただし、これは
  「順不明入力」というユースケースの前提と矛盾する(順が分かっているなら最初からOrdered入力を使えばよい)。

**結論: 本仕様案は主に(a)を提供する。**「順不明の山札を仮説的・決定論的な1つの順序へ確定させて
評価に使う」という用途(RNG Hypothesis的な「未来の可能性の1つとして扱う」用途)には適合するが、
「本当の山札順を後から復元する」という用途には使えない。この区別を仕様書・実装双方で明記すること。

## 仕様案

### 入力: Card Instance集合(Unordered)の表現

`CombatStateSnapshot.PlayerSnapshot`の各Pile(特に`DrawPile`)に、既存の
`list[CardInstanceSnapshot]`(Ordered、後方互換の既定)に加えて、新しいOptionalな入力形として
以下を追加する:

```jsonc
// 既存(後方互換・既定): Ordered配列そのまま
"DrawPile": [ { "InstanceId": "...", "CardId": "...", ... }, ... ]

// 新規(Optional): Unordered入力を明示する包み型
"DrawPile": {
  "Ordered": false,
  "Cards": [ { "InstanceId": "...", "CardId": "...", ... }, ... ],  // 順序は無視される
  "ShufflePolicy": "use_captured_shuffle_rng"  // 下記参照
}
```

後方互換のため、`DrawPile`が素の配列であれば従来通りOrdered、オブジェクト形式で`Ordered: false`が
明示された場合のみ新経路に入る、という判別で対応可能(JSON Schemaの`oneOf`で表現可能)。

### Card Instance情報の内容

新規入力でも`CardInstanceSnapshot`と同じフィールド集合(`InstanceId, CardId, Type, Rarity, Cost,
TargetType, IsUpgraded, UpgradeLevel, Zone?, TinkerTimeType?, TinkerTimeRider?`)を要求する。
Unorderedである以上、`InstanceId`の重複がないことがEmulator側のバリデーションで保証される必要がある
(現行のReference Validation機構、`SnapshotRestorer`の一部を再利用できる可能性が高い)。

### 決定論的shuffle: 使用するRNGとRNG状態更新方法

- `ShufflePolicy: "use_captured_shuffle_rng"`の場合、Restore対象Snapshotの
  `Rng.RunRng["Shuffle"]`(既存の`SerializableRngSnapshot`)を種として、Emulator内部の
  `CardPileCmd.Shuffle`相当のロジック(`ListExtensions.UnstableShuffle`と同一アルゴリズム)を
  **入力されたCardsの記載順をpre-shuffle orderとして** 適用する。
- Shuffle実行によって`RunRngType.Shuffle`ストリームのCounter/State0-3が実際に消費・前進する
  (通常のゲーム内shuffleと同じRng消費経路を通す。新しい乱数アルゴリズムを発明しない)。
- Restore完了後、通常の`CaptureSnapshotJson()`で得られる`RngSnapshotSet`には、
  このshuffle消費後の`RunRng["Shuffle"]`状態が反映される(既存のCapture機構をそのまま使う。
  新しいRNG状態取得APIは不要)。

### Capture後の表現

- Shuffle確定後の内部状態は、通常のCaptureSnapshotと全く同じ形(`list[CardInstanceSnapshot]`の
  Ordered配列、index 0が山札上)として保存・返却される。**「Unordered入力だった」という情報は
  Capture後のSnapshotには残らない**(既存のOrdered Snapshotと区別がつかない)。
- これはCombat Mermaid設計の要求(「Capture後は確定したOrdered状態として保存する方式」)と一致する。
  Search Coordinator/Branch Worker Poolから見れば、Unordered入力を経て確定したDecision Contextも、
  最初からOrderedだったDecision Contextも、以降は完全に同一の型・同一の扱いになる。

### Policyへ山札順を漏らさない方法

前提として、現行実装では`Observation`(`state["drawPile"]`)自体が既に山札の**真の**Ordered配列を
そのまま含んでおり、Policyや評価ロジックが直接この生Observationを読めば、既に山札順(=将来の
draw順)が丸見えになっている(`Training/sts2_training/encoding.py`のモデル入力エンコーダは現状
`len(drawPile)`のようなカウントのみを使っており生の順序は使っていないが、これは実装上の自己規律に
依存しており、DTO契約としての保証ではない)。

本仕様案のUnordered入力機構を導入する場合、これを「未来のRNGをPolicyに漏らさない」という
Combat Mermaid設計の既存原則(RNG Hypothesis詳細図のMain RNG非公開責任境界、
`mermaid_combat_main_loop_detail`のPENDING_STATIC等)と整合させるには、以下が必要:

1. **Evaluator/Policyの入力契約を、生のSnapshot DTO／`state["drawPile"]`の直接参照から独立させる。**
   既存のRNG Hypothesis設計の`BOUNDARY_TABLE`が既に「Evaluatorは生のSnapshot DTO／RNGフィールドを
   直接受け取ってはならない」と明記しているので、この原則をUnordered入力機構にもそのまま適用し、
   Evaluatorへは山札順そのものではなく、山札由来の集計特徴量(枚数・カード種別の頻度等、
   `Training/sts2_training/encoding.py`が現在使っているのと同種の特徴)のみを渡す設計を維持する。
2. Unordered入力から確定させた仮説的順序(§「決定論的shuffle」)についても、これがHypothesis由来である
   ことを`RNG Hypothesis ID`と紐づけて記録し(既存のRNG Hypothesis設計の枠組みをそのまま使う)、
   Main-observed Pending(Mainの実RNG直下)には本機構を一切適用しない
   (`PENDING_STATIC`の制限と同様の扱いとする)。

### Emulator側に必要な変更(まとめ)

1. `CombatStateSnapshot`のPile入力(特に`DrawPile`。将来的には他Pileへも拡張可能)に、
   Unordered入力形式(`{Ordered: false, Cards: [...], ShufflePolicy: ...}`)を受け付けるバリアントを
   JSON Schema・DTOデシリアライザ双方に追加する。
2. `RestoreSnapshotJson`/`RestoreSnapshot`の内部処理に、Unordered入力を検出した場合、
   指定された`RunRng["Shuffle"]`状態を種として`UnstableShuffle`相当のロジックで確定順序を得てから、
   通常のRestore処理(既存のOrdered Pileと同じ経路)へ引き渡すステップを追加する。
3. `RestoreCapabilities`に、この新機能のサポート有無を示すフィールド(例:
   `supports_unordered_pile_input: bool`)を追加する。

### Emulator担当の判断が必要な事項

1. **shuffle対象を`DrawPile`のみに限定するか、他Pile(Hand/DiscardPile/ExhaustPile)にも
   拡張するか。** 本仕様案はまずDrawPileのみを対象とする最小案として提示している。
2. **`ShufflePolicy`のバリエーションをどこまで用意するか。** 本書は`use_captured_shuffle_rng`
   (Snapshot内のRunRng["Shuffle"]を使う)のみを提案しているが、独立した新規RNG種を渡す
   バリエーション(RNG Hypothesisの「独立集合」評価用)が必要かどうかはEmulator担当と要相談。
3. **UnstableShuffleの入力順依存性を、呼び出し側にどう明示するか。** 本書が指摘した
   「pre-shuffle orderへの依存」を、API利用者(RL側)に誤解なく伝えるためのドキュメント・
   命名(例: `ShufflePolicy`ではなく`PreShuffleOrderPolicy`のような名前の方が実態に近いか)は
   Emulator担当のAPI設計方針に委ねる。
4. **`CombatScenario.ShuffleRngSeed`との統合可否。** 既存の`ShuffleRngSeed`(単一int)による
   簡易上書き機構と、本提案の`RunRng["Shuffle"]`(Counter+State0-3のフル状態)による精密な制御を
   将来的に一本化すべきか、両方を別ユースケース向けに併存させるべきかはEmulator担当の判断が必要
   (`emulator_dto_contract_rl_required.v1.md`§6の「要確認」項目と同一)。
5. **バリデーション責任の範囲。** `InstanceId`重複禁止・カード集合の整合性(既存デッキとの
   参照整合性等)チェックを、既存の`ValidateRestoreSnapshotJson`のどの段階(JSON構造検証／
   オブジェクトDTO事前検証)に組み込むか。
