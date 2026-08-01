# 山札順DTO契約 調査報告書 (2026-08-01)

前提: Combat Mermaid正式ベースライン`40d2c2c`(`docs/architecture/combat/`)。
本調査はコード変更を一切含まない。成果物は本報告書と以下2つの仕様書。

- `Common/contracts/emulator_dto_contract_rl_required.v1.md`
  (RL側が要求するEmulator DTO契約書＋現行DTOとの対応表)
- `Common/contracts/deck_unordered_input_shuffle_proposal.v1.md`
  (山札順不明入力とRNG shuffleに関する仕様案)

## 調査方法

1. `Combat/`配下のRL-Emulator境界コード(`combat_env.py`・`live_combat_session.py`・
   `battle_emulator.py`・`emulator_bridge.py`・`combat_state_snapshot.py`)を実コードベースで確認。
2. `Common/contracts/combat_state_contract.v0.8.md`(既存の最新契約文書)・
   `Common/schemas/*.json`(既存スキーマ群)を確認。
3. `Outputs/azure_stage_20260723_122305/STS2_Emulator/`配下のvendored元ゲームエンジンソース
   (MegaCritオリジナル実装、2500件超のC#ファイル)から、山札(`CardPile`)・RNG(`SerializableRng`・
   `RunRngType`)・シャッフル(`ListExtensions.UnstableShuffle`)の実装を直接確認。
4. `Combat/tests/`(特に`test_restore_snapshot_phase3c1.py`)でPile名・RNGフィールドの実際の
   使われ方を確認。

## 回答事項

### 1. 現在の山札DTO型と順序方向

- **Snapshot側(Capture/Restore用)**: `Combat/combat_state_snapshot.py::CardInstanceSnapshot`
  (`InstanceId, CardId, Type, Rarity, Cost, TargetType, IsUpgraded, UpgradeLevel, Zone?,
  TinkerTimeType?, TinkerTimeRider?`)。`PlayerSnapshot`が`Hand, DrawPile, DiscardPile,
  ExhaustPile, PlayPile, Deck`をそれぞれ`list[CardInstanceSnapshot]`として持つ。
- **Observation側(engine_state)**: `Common/schemas/combat_state_schema.json`定義の
  プレーンなcard dict配列(`id, cost, targetType, upgradeLevel, tinkerTimeType, tinkerTimeRider`等)。
- **Scenario入力側(Start用)**: `CardInstanceScenario`(`CardId, IsUpgraded, TinkerTimeType,
  TinkerTimeRider`のみ。`InstanceId`等はReset時にEmulatorが採番するため持たない)。
- **順序方向**: 全ての表現で**配列のindex 0が「山札の一番上/次に引かれるカード」**。
  `combat_state_schema.json`の`drawPile`定義コメント「Index 0 is top-of-pile / next drawn」、
  および実エンジン`CardPile.MoveToTopInternal`(`_cards.Insert(0, card)`)・
  `CardPileCmd.DrawInternal`(`drawPile.Cards.FirstOrDefault()`)で確認済み。

### 2. 順序不明入力を現在表現できるか

**できない。** エンジン内部(`List<CardModel>`)・Snapshot DTO(`list[CardInstanceSnapshot]`)・
Scenario入力(`List<CardInstanceScenario>`)のいずれも例外なくOrderedなリストであり、
Unordered(集合・多重集合)としてカード群を渡す手段は現行のどの経路にも存在しない。

### 3. 後方互換を維持して追加可能か

**可能と考える。** `Common/contracts/deck_unordered_input_shuffle_proposal.v1.md`で提案した通り、
既存の「素の配列=Ordered」という表現はそのまま残し、`{Ordered: false, Cards: [...],
ShufflePolicy: ...}`という新しいオブジェクト形式をOptionalなバリアントとして追加する形であれば、
既存の全ての呼び出し元・既存のSnapshotデータに影響を与えずに追加できる
(JSON Schemaの`oneOf`、Pythonデシリアライザの型分岐で対応可能)。

### 4. Unordered入力を受けるべきAPI

`RestoreSnapshotJson`/`RestoreSnapshot`(Restore経路)を対象とすべきと判断する。理由:

- Combat Mermaidの既存RNG Hypothesis設計(方式B)が既にRestore経路を「Snapshot JSONを編集してから
  Restoreする」という形で使っており、同じ経路に統合するのが最も既存設計との整合性が高い。
- Scenario入力(Start/Reset)側にも同様の需要はありうるが、Startは1エピソードにつき1回のみの
  操作であり、探索中に繰り返し呼ばれるRestore経路ほど優先度が高くない。まずRestore経路に限定し、
  必要になった時点でScenario入力側への拡張を検討するのが妥当(調査時点ではRestore経路のみを
  提案。詳細は仕様案の「Emulator担当の判断が必要な事項」参照)。

### 5. 使用するRNGとshuffle時のRNG更新方法

- 使用すべきRNGは既存の`RunRngType.Shuffle`ストリーム(`RngSnapshotSet.RunRng["Shuffle"]`、
  `SerializableRngSnapshot`型)。これは既に山札shuffle専用として他の用途(カード生成・
  モンスター行動等)から独立しており、新規RNG種を発明する必要はない。
- 更新方法: Emulator内部の`ListExtensions.UnstableShuffle`(Fisher-Yates)と同一アルゴリズムを
  用い、実際にRNGストリームのCounter/State0-3を消費・前進させる。この消費は通常のゲーム内shuffle
  (`CardPileCmd.Shuffle`)と全く同じRng消費経路を通すべきであり、専用の別アルゴリズムを新設しない。
  消費後の状態は、通常の`CaptureSnapshotJson()`で得られる`RngSnapshotSet`にそのまま反映される
  (新しいRNG状態取得APIは不要)。

### 6. 現在RNGからのshuffleで実ゲームの現在順を保証できるか

**保証できない。** これが本調査で最も重要な発見である。エンジンの`UnstableShuffle`実装コメントに
明記されている通り、このシャッフルは「unstable」であり、**結果はシャッフル対象リストの初期順序
(pre-shuffle order)に依存する**。同じカード集合でも初期順序が異なれば、同じRNG状態でシャッフルしても
結果は異なりうる。

「山札順不明のCard Instance集合」を入力とする以上、定義上その真のpre-shuffle orderは分からない。
したがって、RL側が任意の順序でCard Instance集合を並べてEmulatorのシャッフル機構に渡しても、
得られる結果は「決定論的で再現可能な1つの仮説的順序」ではあっても、「実際のゲームが持っていた
真の現在順」と一致する保証はない。この区別は`deck_unordered_input_shuffle_proposal.v1.md`の
「前提として確認した既存の制約」に詳述した。

この性質は、Combat Mermaid設計のRNG Hypothesis機構(「未来の可能性の1つとしてRNGを仮説的に
扱う」という既存の設計原則)とはむしろ自然に整合する。「真の山札順の復元」ではなく
「決定論的な仮説的順序の生成」としての位置づけであれば、既存のRNG Hypothesis ID管理・
標準/診断タグ分離の枠組みにそのまま統合できる。

### 7. Capture後の山札表現

Shuffle確定後、`CaptureSnapshotJson()`で得られる山札は、通常のOrdered Snapshot
(`list[CardInstanceSnapshot]`、index 0が山札上)と完全に同一の形になる。「Unordered入力から
確定させた」という出自情報はCapture後のSnapshotそのものには残らない。これはCombat Mermaid設計の
要求(「Capture後は確定したOrdered状態として保存する方式」)と一致しており、Search Coordinator/
Branch Worker Poolから見れば、Unordered入力経由のDecision Contextも最初からOrderedだった
Decision Contextも以降は完全に同一の扱いになる。出自(Hypothesis由来かどうか)を追跡する必要が
あれば、それは既存のRNG Hypothesis ID(Decision Context側のメタデータ)で管理すべきであり、
Snapshot自体にフラグを持たせるべきではないと判断する。

### 8. Policyへ山札順を漏らさない方法

まず前提として、**現行実装で既に一部リスクがある**ことを確認した。`Observation`
(`state["drawPile"]`)は真のOrdered配列をそのまま含んでおり、生Observationを直接読めば
山札順(=将来の draw順)がそのまま見える。`Training/sts2_training/encoding.py`のモデル入力
エンコーダは現状`len(drawPile)`のようなカウントのみを使い生順序は使っていないが、これは実装上の
自己規律であり、DTO契約としての強制ではない。

Unordered入力・shuffle機構を導入する場合、Combat Mermaid設計の既存原則
(RNG Hypothesis詳細図のBOUNDARY_TABLE「Evaluatorは生のSnapshot DTO／RNGフィールドを直接受け取って
はならない」)をそのまま適用し、Evaluator/Policyへは山札順そのものではなく山札由来の集計特徴量
(枚数・カード種別頻度等)のみを渡す設計を維持・強化すべきである。加えて、Unordered入力由来の
仮説的順序もRNG Hypothesis IDに紐づけて管理し、Main-observed Pending(Mainの実RNG直下)には
本機構を一切適用しない(PENDING_STATICの制限と同じ扱い)ことを提案する。

### 9. Emulator側に必要な変更

1. `CombatStateSnapshot`のPile入力(まず`DrawPile`)にUnordered入力バリアントを追加
   (JSON Schema・DTOデシリアライザ両方)。
2. `RestoreSnapshotJson`/`RestoreSnapshot`内部に、Unordered入力検出時、指定`RunRng["Shuffle"]`
   状態を種として確定順序を得てから通常のRestore処理へ引き渡すステップを追加。
3. `RestoreCapabilities`に新機能のサポート有無を示すフィールド
   (例: `supports_unordered_pile_input: bool`)を追加。

詳細は`deck_unordered_input_shuffle_proposal.v1.md`参照。

### 10. Emulator担当の判断が必要な事項

1. shuffle対象を`DrawPile`のみに限定するか、他Pileにも拡張するか。
2. `ShufflePolicy`のバリエーション(独立RNG種を渡す診断用バリエーションの要否等)。
3. `UnstableShuffle`の入力順依存性を、API利用者に誤解なく伝えるための命名・ドキュメント方針。
4. 既存の`CombatScenario.ShuffleRngSeed`(単一int、`battle_emulator.py::with_shuffle_seed()`が
   使用)と、本提案の`RunRng["Shuffle"]`(Counter+State0-3のフル状態)による精密制御を統合すべきか、
   別ユースケース向けに併存させるべきか。**この`ShuffleRngSeed`が内部で`RunRng["Shuffle"]`を
   実際にどう扱っているか(上書きか、別経路の一時Seedか)自体が今回の調査で確認しきれておらず、
   Emulator担当への確認が必要な事項として残っている。**
5. `InstanceId`重複禁止等のバリデーション責任を、既存の`ValidateRestoreSnapshotJson`の
   どの段階に組み込むか。

## 対応区分の総括(現行Emulator DTOとの対応表より)

| 区分 | 件数 | 主な内容 |
|---|---|---|
| 一致 | 多数 | Start/Reset、Capture/Restore、RNG基本型・Purpose分離、Card Instance型・Pile順序方向 |
| Mappingで対応可能 | 数件 | choice_kind↔action_type、target制約↔LegalAction.parameters |
| 不足 | 3件 | Unordered Card Instance集合入力(中核)、choice_scopeの明示的フィールド、意味的Decision Signatureによるreplay mismatch検証 |
| 不整合 | 3件 | 単一StepResult化の未達(Observation/LegalActions分離)、Faultが例外表現であること |
| RL内部のみ | — | `BattleState`・`CombatEnv`の`{action_id,reward,done,...}`辞書・`DecisionFrame` |
| 要確認 | 1件 | `ShuffleRngSeed`と`RunRng["Shuffle"]`の関係 |

詳細は`Common/contracts/emulator_dto_contract_rl_required.v1.md`を参照。「不整合」に分類した
3件(単一StepResult化・Fault非例外化)は、Combat Mermaid設計が前提とする契約と現行実装の間の
構造的な差であり、山札順DTO契約の調査スコープを超えるより大きな検討事項として記録するに留めた。

## 結論

山札順DTOは現在「常にOrdered」が前提であり、山札順不明のCard Instance集合を表現する手段は
存在しない。この機能は後方互換を維持した形で追加可能であり、既存のRNG基盤(`RunRngType.Shuffle`・
`SerializableRng`)をそのまま再利用できる。ただし、Fisher-Yatesシャッフル(`UnstableShuffle`)が
入力順序に依存する「unstable」な性質を持つため、この機構は「真の山札順の復元」ではなく
「決定論的な仮説的順序の生成」としてのみ位置づけられる、という制約を設計上明記する必要がある。

実装には進まず、ここで停止する。上記5点のEmulator担当確認事項について、監督者経由での
調整を依頼する。
