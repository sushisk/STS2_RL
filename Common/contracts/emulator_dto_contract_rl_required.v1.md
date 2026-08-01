# RL側が要求するEmulator公開DTO契約 v1 (作成: 2026-08-01、再確認: 2026-08-01)

## 0. 再確認(2026-08-01、DrawPile Belief／Search Hypothesis ID／Exact State層導入後)

最新のCombat Mermaidベースライン(commit `81006b3`。DrawPile Belief・Search Hypothesis ID・
Exact State層／Belief-Search層分離・PUBLIC_MULTISET再定義・StableShuffle 3要素化を反映済み)を
基準に本契約書を再確認した。**結論: Emulator側へ新たに要求するDTO・フィールドの追加は無い。**
以下2点の記述更新のみを本書へ反映した。

1. **§5の「不足」判定を修正**: 従来「山札順不明のCard Instance集合を入力として受け付けられる
   こと」を新規要求(不足)としていたが、これは`Common/contracts/deck_unordered_input_shuffle_proposal.v1.md`
   が提案する「Emulator側でUnordered入力を受け取りサーバサイドでshuffleする」方式を前提とした
   記述だった。実際に採用されたDrawPile Belief設計(`mermaid_combat_rng_hypothesis_detail.mermaid`の
   `BELIEF_GEN`)は、Hypothetical OrderedDrawPileをSearch Coordinator側(RL側)で生成し、
   既存のOrdered Snapshot形式へ直接代入してから既存の`RestoreSnapshotJson`を呼ぶ(方式B、
   RNG Hypothesisと同じ経路)。**新しいEmulator入力形式は不要であり、既存のOrdered
   `CombatStateSnapshot`契約のみで足りる。** `deck_unordered_input_shuffle_proposal.v1.md`の
   提案自体は撤回しないが、DrawPile Belief設計には不要になったことを明記する(§5参照)。
2. **DrawPile Belief用のPUBLIC_MULTISET算出に必要な情報は、全て既存DTOで賄えることを確認**:
   `Hand`/`DrawPile`/`DiscardPile`/`ExhaustPile`/`PlayPile`(残多重集合算出の減算対象)、
   `CombatHistory`(`CardGeneratedEntry`、生成カード加算対象)、`Relics`/`Powers`
   (StableShuffleのhook関連状態)はいずれも`Combat/combat_state_snapshot.py`に既存のフィールドで
   あり、新規DTOフィールドの追加は不要(§5・§6参照)。

Search Hypothesis ID・DrawPile Belief(PUBLIC_MULTISET／BELIEF_GEN)・Exact State層／
Belief-Search層の分離・Concrete/Authoritative/Hypothetical OrderedDrawPileという用語は、
いずれもSearch Coordinator内部でのみ使われるRL内部概念であり、Emulator公開DTOへの要求は
生じない(§9「RL内部のみ」参照)。

---

**目的**: `docs/architecture/combat/`のCombat Mermaidベースライン(commit `40d2c2c`)で完成した設計フローから、
RL側(Search Coordinator／Branch Worker Pool／Main Process)がEmulatorへ要求する公開DTO契約を逆算し、
現行Emulator DTO(`Combat/combat_state_snapshot.py`・`Combat/battle_emulator.py`・
`Combat/live_combat_session.py`・`Combat/emulator_bridge.py`が実際に叩いている
`Sts2Emulator.Api.GameInstance`・`Sts2Emulator.Dto.*`)との対応を明らかにする。

**方針**: RL内部型(`BattleState`・`DecisionFrame`・`CombatEnv`の`{action_id, reward, done, ...}`辞書等、
`Combat/`配下のPython専用型)とEmulator公開DTO(Emulatorが実際に返す/受け取るデータ形状)は明確に分離する。
本書はEmulator公開DTO側のみを対象とする。RL内部型は`Common/schemas/transition_schema.json`等を正本とする。

**注記**: 本書は仕様書であり、コード変更は一切含まない。Emulator側への変更提案は
`deck_unordered_input_shuffle_proposal.v1.md`を参照。

---

## 1. Start／Reset

### RL側が要求する契約(Combat Mermaid設計から逆算)

- Combat開始時、以下のいずれかの起点から単一の`Decision Result`(§7参照)を返すこと:
  - 新規scenario(Party/Deck/Relic/Seed等)からの開始
  - 既存の`CombatStateSnapshot`(Restore、§3参照)からの開始
- 開始直後のRNG状態(RunRng/PlayerRng/MonsterRng)が、渡した入力(seedまたはSnapshot)から
  一意に決定論的に定まること。

### 現行Emulator DTOとの対応

| 要求 | 現行実装 | 対応 |
|---|---|---|
| 新規scenarioからの開始 | `GameInstance.ResetFromScenario(CombatScenario)`(`live_combat_session.py::start_combat`が1エピソードにつき1回のみ呼ぶ) | **一致** |
| Scenario入力のカード情報 | `CombatScenario.{Hand,DrawPile,DiscardPile,ExhaustPile}Cards: List<CardInstanceScenario>`(`CardId`・`IsUpgraded`・`TinkerTimeType`・`TinkerTimeRider`) | **一致**(ただしOrdered必須。§9参照) |
| Snapshotからの開始 | `RestoreSnapshotJson(string)`/`RestoreSnapshot(CombatStateSnapshot)` | **一致**(§3のRestoreと同一経路) |
| 開始直後のRNG決定論性 | `CombatScenario.Seed`(単一int)がRunRng等を決定。`ShuffleRngSeed`という追加の任意上書きフィールドも存在(`battle_emulator.py::build_scenario_from_spec/build_scenario_from_state`) | **一致**(ただし`ShuffleRngSeed`の内部動作は§9「要確認事項」参照) |

---

## 2. Step／StepResult

### RL側が要求する契約

Combat Mermaidの`main_loop_detail`/`snapshot_replay_detail`は、Step呼び出しが単一の`StepResult`
(Boundary・Observation・Choice候補・Terminal/Fault・CombatSessionId/StepIndex等を含む一括戻り値)を
返すことを前提とする。Faultも含めて「戻り値」として扱われることが設計上の要件である
(`main_loop_detail`のSTEP_FAULT_CHECKは戻り値のBoundaryを見て分岐する設計)。

### 現行Emulator DTOとの対応

| 要求 | 現行実装 | 対応 |
|---|---|---|
| 単一StepResult(Boundary+Observation+Choice候補+Terminal/Fault+metadata) | `GameInstance.Step(actionId)`はObservationとLegalActionsを別々に持つCLRオブジェクトを返す。**Faultは戻り値の一部ではなく例外**(`ActionFaultedException`→Python`ActionExecutionError`、`FaultedCombatSessionException`→`FaultedCombatSessionError`) | **不整合**。Mermaid設計は「Faultも含む一括戻り値」を前提にしているが、現行実装はFaultを例外で表現しており、正常系Observation/LegalActionsも2値に分かれている |
| Choice候補(choice_kind/choice_scope/制約) | `LegalActions: list[dict]`(`{action_id, action_type, label, is_available, parameters}`) | **Mappingで対応可能**。`action_type`が`choice_kind`相当だが、`choice_scope`(TopLevel/ActionContinuation)に相当するフィールドは別途`Common/schemas/choice_semantics_schema.json`/`choice_semantics_lookup.v1.json`側で扱われている(要突合) |
| CombatSessionId／StepIndex | `DecisionFrame(combat_session_id, step_index, continuation_step_index)`(Python側、`GameObservation.CombatSessionId/StepIndex`から`_wrap()`が構築) | **一致**(概念としては存在。ただしStepResultへ統合されておらず別オブジェクト) |
| choice_scope=ActionContinuationの表現 | `DecisionFrame.continuation_step_index`と`is_action_continuation_pending_choice`判定(`battle_emulator.py`) | **一致**(概念は存在するが、Mermaid設計の`choice_scope`フィールドとして正式にDTO化はされていない) |

---

## 3. Capture／Restore

### RL側が要求する契約

- `CaptureSnapshot`: 現在状態をStable Root Snapshotとして取得する非破壊操作。
- `RestoreSnapshotJson`/`ValidateRestoreSnapshotJson`: Snapshot JSONからの復元・事前検証。
- Pending状態自体はCapture/Restoreしない(Mermaid設計の前提)。

### 現行Emulator DTOとの対応

| 要求 | 現行実装 | 対応 |
|---|---|---|
| CaptureSnapshot | `GameInstance.CaptureSnapshotJson()`(`LiveCombatSession.capture_snapshot()`) | **一致** |
| RestoreSnapshotJson | `GameInstance.RestoreSnapshotJson(string)` | **一致** |
| ValidateRestoreSnapshotJson | `GameInstance.ValidateRestoreSnapshotJson(string)`(Phase 3C.4.1で追加済み) | **一致** |
| Restore失敗の例外分類 | `SnapshotRestoreRejectedException`→`SnapshotRestoreRejectedError`(事前検証で拒否)、`SnapshotRestoreFailedException`→`SnapshotRestoreFailedError`(Restore自体が失敗) | **一致**(fault_worker_detailの`validation rejection`/`post-teardown Restore failure`分類と対応) |
| Pending状態を直接Capture/Restoreしない | 現行実装もPending自体をCapture対象にしていない(Stable/Terminalのみが正規の評価境界) | **一致** |
| RestoreCapabilities(契約バージョン等のmetadata取得) | `GameInstance.GetRestoreCapabilities()` → `restore_api_version`・`contract_version`・`snapshot_schema_version`・`transaction_model`・`rollback_after_teardown`等 | **RL内部のみではなくEmulator側に既存**。Mermaid設計には未反映の項目(`rollback_after_teardown=false`はfault_worker_detailの「post-teardown Restore failure後はWorkerを信用しない」という運用ポリシーの根拠と一致) |

---

## 4. Observation／Choice Payload

### RL側が要求する契約

Observation・Choice Payloadは、GetObservation等の別経路での再取得を必要とせず、
StepResult/ResetResult/RestoreResultに含まれる一括戻り値の一部であること
(`main_loop_detail`のNOTE_NO_REREAD参照)。

### 現行Emulator DTOとの対応

| 要求 | 現行実装 | 対応 |
|---|---|---|
| Observation | `GameInstance.GetObservation()`(独立呼び出し) / `emulator_bridge.observation_to_dict()` → `{turn, is_terminal, outcome, state}` | **不整合**。Mermaid設計は「別経路の再取得をしない」ことを明示的な不変条件としているが、現行実装は`GetObservation()`という独立APIが存在し、`LiveCombatSession`はStep結果からObservationを都度読み出している(実質的にStep戻り値の一部として使われており、運用上は「別経路の再取得はしていない」が、API面ではStepと分離した独立メソッドとして存在する) |
| Choice Payload | `GetLegalActions()`(独立呼び出し) / `legal_action_to_dict()` | **不整合**(Observationと同様の理由) |
| choice_kind | `action_type`(LegalAction辞書のフィールド) | **Mappingで対応可能** |
| choice_scope(TopLevel/ActionContinuation) | 独立DTOフィールドとしては存在しない。`DecisionFrame.continuation_step_index`の有無や`is_action_continuation_pending_choice()`判定ロジックから間接的に導出可能 | **不足**。明示的なフィールドとしての追加が必要 |
| target制約(min/max selection等) | `LegalAction.parameters`(自由形式dict) | **Mappingで対応可能**(要スキーマ確認) |

---

## 5. Card／各Pile

### RL側が要求する契約

- Card Instanceは意味論的に安定な識別子(InstanceId)とカード定義ID(CardId)を持つこと。
- 各Pile(Hand/DrawPile/DiscardPile/ExhaustPile/PlayPile/Deck)はOrderedなリストとして表現され、
  DrawPileは先頭(index 0)が次に引かれるカードであること。
- ~~山札順不明のCard Instance集合を入力として受け付けられること~~ →
  **2026-08-01再確認により撤回**。DrawPile Belief設計(`BELIEF_GEN`)がSearch Coordinator側で
  Hypothetical OrderedDrawPileを生成し既存のOrdered入力形式へ代入するため、Emulator側への
  Unordered入力受け付け要求は不要と判明した(§9参照)。

### 現行Emulator DTOとの対応

| 要求 | 現行実装 | 対応 |
|---|---|---|
| Card Instance型 | `CardInstanceSnapshot`(Snapshot側): `InstanceId, CardId, Type, Rarity, Cost, TargetType, IsUpgraded, UpgradeLevel, Zone?, TinkerTimeType?, TinkerTimeRider?` | **一致** |
| Card Instance型(Scenario入力側) | `CardInstanceScenario`(Start入力側): `CardId, IsUpgraded, TinkerTimeType, TinkerTimeRider`(InstanceId/Zone/Rarity/Cost/TargetTypeは持たない。Reset時にEmulatorが採番) | **一致だが別型**。Snapshot側とScenario側で情報量が異なることに注意 |
| Pileの型 | `PlayerSnapshot.{Hand,DrawPile,DiscardPile,ExhaustPile,PlayPile,Deck}: list[CardInstanceSnapshot]` | **一致** |
| 順序方向(先頭=山札上) | `Common/schemas/combat_state_schema.json`の`drawPile`定義コメント「Index 0 is top-of-pile / next drawn」。エンジン側`CardPile.MoveToTopInternal`が`_cards.Insert(0, card)`、`CardPileCmd.DrawInternal`が`Cards.FirstOrDefault()`で確認済み | **一致** |
| Orderedが必須か | 現行、全てのPile表現(エンジン内部`List<CardModel>`、Snapshot DTOの`list`、Scenario入力の`List<CardInstanceScenario>`)が例外なくOrderedなリストであり、Unordered(集合・多重集合)としての表現は一切存在しない | **一致(2026-08-01再確認)**。DrawPile Belief設計はEmulator側のOrdered必須制約をそのまま前提とし、Hypothetical OrderedDrawPileの生成をRL側で完結させるため、この制約自体がむしろ設計の前提として機能する。Unordered入力を要求する必要はない(→§9) |
| PUBLIC_MULTISET算出に必要な情報(DrawPile Belief用) | `Hand`/`DiscardPile`/`ExhaustPile`/`PlayPile`(現在の他Pile。減算対象)＋`CombatHistorySnapshot.Entries`中の`CardGeneratedEntry`(生成カード。加算対象)。いずれも`combat_state_snapshot.py`に既存 | **一致**。新規DTOフィールド不要 |
| StableShuffleのhook関連状態 | `PlayerSnapshot.Relics`/`PlayerSnapshot.Powers`(既存フィールド) | **一致**。新規DTOフィールド不要 |

---

## 6. RNG Snapshot

### RL側が要求する契約

- RNGは複数のPurpose(用途)ごとに独立したストリームを持ち、各ストリームは
  Counter＋固定サイズの内部State(シリアライズ可能)を持つこと。
- Capture/Restoreで完全に往復可能であること。
- Shuffle専用のPurposeが独立して存在し、他の用途(カード生成・モンスター行動等)から
  分離されていること(RNG Hypothesis設計が特定Purposeのみを差し替えるために必要)。

### 現行Emulator DTOとの対応

| 要求 | 現行実装 | 対応 |
|---|---|---|
| RNG基本型(Counter+State) | `SerializableRng`(C#、`counter: int, state0-3: ulong`) / `SerializableRngSnapshot`(Python、`Counter, State0-3`) | **一致** |
| Purpose別ストリーム | `RunRngType`列挙(`UpFront, Shuffle, UnknownMapPoint, CombatCardGeneration, CombatPotionGeneration, CombatCardSelection, CombatEnergyCosts, CombatTargets, MonsterAi, Niche, CombatOrbs, TreasureRoomRelics`)。`PlayerRngType`(`Rewards, Shops, Transformations`)は別軸 | **一致** |
| Shuffle専用Purposeの独立性 | `RunRngType.Shuffle`が独立したenum値として既に存在し、`RngSnapshotSet.RunRng["Shuffle"]`として個別にCapture/Restore可能 | **一致**。山札shuffle用RNGは既に他用途から分離されている |
| RunRng／PlayerRng／MonsterRngの3系統 | `RngSnapshotSet(RunRng: dict, PlayerRng: list[PlayerRngSnapshot], MonsterRng: list[MonsterRngSnapshot])` | **一致** |
| Capture/Restoreでの完全往復性 | `CombatStateSnapshot.Rng: RngSnapshotSet`が必須トップレベルフィールドとして既に実装・テスト済み(`test_restore_snapshot_phase3c1.py`) | **一致** |
| RNG Hypothesis用の簡易上書き機構 | `CombatScenario.ShuffleRngSeed`(単一int、`battle_emulator.py::with_shuffle_seed()`)という**別系統**の簡易機構が既に存在。`SerializableRng`(Counter+State0-3)を直接編集する「方式B」とは異なる粒度・経路 | **要確認**(§9)。`ShuffleRngSeed`が内部で`RunRng["Shuffle"]`をどう扱っているか(上書きか、別経路の一時Seedか)をEmulator担当に確認する必要がある |

---

## 7. Fault／Terminal／metadata

### RL側が要求する契約

- Terminal/Faultは、Boundary種別としてStepResult/RestoreResult等の一括戻り値に含まれること。
- Fault種別(validation rejection/replay mismatch/post-teardown Restore failure/action fault)が
  区別可能であること。

### 現行Emulator DTOとの対応

| 要求 | 現行実装 | 対応 |
|---|---|---|
| Terminal | `Observation.is_terminal` / `outcome` | **一致** |
| Fault(戻り値としての表現) | 例外(`ActionFaultedException`等)として表現。戻り値のフィールドではない | **不整合**(§2で既出) |
| validation rejection | `SnapshotRestoreRejectedException`/`RestoreValidationResult.rejection_codes` | **一致** |
| post-teardown Restore failure | `SnapshotRestoreFailedException` | **一致** |
| action fault | `ActionFaultedException`/`ActionExecutionError`(`ActionFaultContext`: combat_session_id/step_index/action_id/action_type/card_id/target_index/target_enemy_index等の構造化情報を保持) | **一致**(構造化情報は既に充実している。表現形式が例外である点のみが設計とのギャップ) |
| replay mismatch(想定Signatureとの不一致) | `DecisionFrameMismatchError`(`live_combat_session.py:680`で`_current_frame`との不一致時に送出。比較対象は`combat_session_id`/`step_index`のみ、確認済み) | **不足**。Mermaid設計のExpected Post-Step Signature(Boundary種別・choice_kind・候補Semantic Key集合のcanonical multiset等の意味的複合値)に相当する比較は現行実装に存在しない。現行の`DecisionFrameMismatchError`はID一致のみを見る「State Identity」寄りの検証であり、「Decision Signatureによる意味的同一性検証」は別途RL側で実装するか、Emulator側に追加する必要がある |

---

## 8. 総括: 対応区分ごとの件数

| 区分 | 該当項目数(概算) | 主な内容 |
|---|---|---|
| 一致 | 多数 | Start/Reset、Capture/Restore、RNG基本型・Purpose分離、Card Instance型・Pile順序方向 |
| Mappingで対応可能 | 数件 | choice_kind↔action_type、target制約↔LegalAction.parameters |
| 不足 | 2件 | choice_scopeの明示的フィールド、意味的Decision Signatureによるreplay mismatch検証<br/>(Unordered Card Instance集合入力は2026-08-01再確認で撤回。§0・§9参照) |
| 不整合 | 3件 | 単一StepResult/Observation・LegalActionsの分離、Faultが例外表現であること |
| RL内部のみ | — | `BattleState`・`CombatEnv`の`{action_id,reward,done,...}`辞書・`DecisionFrame`(Python側専用の合成型)。<br/>2026-08-01再確認で追加: Search Hypothesis ID・DrawPile Belief(PUBLIC_MULTISET／BELIEF_GEN)・<br/>Exact State層／Belief-Search層の分離・Concrete/Authoritative/Hypothetical OrderedDrawPileという<br/>用語(§9参照) |
| 要確認 | 1件 | `ShuffleRngSeed`と`RunRng["Shuffle"]`の関係(Emulator担当への確認事項、§9参照) |

「不整合」に分類した3件(単一StepResult化・Fault非例外化)は、Combat Mermaid設計が前提とする契約と
現行実装の間の構造的な差であり、山札順DTO契約とは独立した、より大きなスコープの検討事項である。
本書はこれを「調査結果」として記録するに留め、対応方針の決定は別途監督者判断を仰ぐ。

---

## 9. RL内部型・要確認事項の一覧(2026-08-01再確認で整理)

### RL内部型(Emulatorへ要求しない項目)

以下はいずれもSearch Coordinator/Main Process内部でのみ用いる概念・型であり、Emulator公開DTOへの
新規要求を一切生じない。

- `BattleState`・`DecisionFrame`・`CombatEnv`の`{action_id, reward, done, observation, legal_actions,
  info}`辞書(RL内部の合成型。既存)。
- **Search Hypothesis ID**(RNG成分＋DrawPile Order成分の組。Decision Context/Lease/WorkItem/Commit
  図で使うRL内部の仮説識別子。Emulator側はこのIDの存在を一切知らず、単にRestore対象Snapshot JSONの
  中身[RNGフィールド・DrawPileフィールド]が具体的な値へ置き換わっているだけとして扱う)。
- **DrawPile Belief**(`PUBLIC_MULTISET`算出・`BELIEF_GEN`による Hypothetical OrderedDrawPile生成)。
  入力は既存DTO(§5のPUBLIC_MULTISET算出行参照)のみで完結し、出力も既存のOrdered
  `CombatStateSnapshot.DrawPile`形式へ代入されるだけであるため、Emulator側の新規処理は不要。
- **Exact State層／Belief-Search層の分離**、および**Concrete／Authoritative／Hypothetical
  OrderedDrawPile**という用語は、いずれもSearch Coordinatorが同じ既存DTOをどう扱うかについての
  RL側の内部規律であり、Emulatorから見れば全てのRestore呼び出しは同一の`RestoreSnapshotJson`で
  区別なく処理される。

### 要確認事項(Emulator担当への確認が必要、変更なし)

- `CombatScenario.ShuffleRngSeed`(単一int)と`RngSnapshotSet.RunRng["Shuffle"]`
  (Counter+State0-3のフル状態)の関係(上書きか、別経路の一時Seedかを`battle_emulator.py::
  with_shuffle_seed()`の実装から確認しきれていない)。2026-08-01再確認時点でも未解消。
- `PUBLIC_MULTISET`算出式(§5)が実際に全ての生成カード経路を網羅しているか
  (`CombatHistoryEntrySnapshot.CardGeneratedEntry`が常にCardIdを記録しているか、他に未把握の
  生成経路がないか)。`Common/contracts/deck_unordered_input_shuffle_proposal.v1.md`は撤回して
  いないが、DrawPile Belief設計には不要と判明したため、優先度は下げてよい。
