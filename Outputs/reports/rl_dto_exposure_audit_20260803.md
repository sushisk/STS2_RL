# Emulator DTO公開範囲監査 — 承認待ち報告書

対象: RL基準commit `235eb8b`、Emulator基準commit `fca2f06`。

**本書は監査結果の報告のみであり、公開Decision DTOの実装は行っていない。承認を待って
次工程(Training向けDecision API実装)へ進む。**

基本方針: 公開情報である限りEmulator DTOを変形せず横流しする。RL独自DTOへの不必要な
コピー・改名・再定義は避け、RL固有情報(decision_id、Worker/Lease内部識別子等)は外側の
Envelopeへ追加する。判断基準は「Emulatorの内部状態か」ではなく「実際のプレイヤーがその
Decision時点で知り得るか」。

## 0. 参照した一次ソース

* `C:\STS2_Emulator\docs\api\whole_run_api_reference_20260803.md`(baseline `87a0962`、
  `dd8c800`/`fca2f06`で更新済み)
* `Sts2Emulator/Api/GameInstance.cs`(`BuildFullStateDict`/`BuildObservation`/
  `BuildLegalActions`/`BuildMapDict`/`BuildEnemiesDict`/`BuildPendingChoiceDict`等)
* `Sts2Emulator/Dto/*.cs`
* `C:\STS2_RL\Common\contracts\combat_state_contract.v0.3.md`(§9 Snapshot必須フィールド)
* `Combat/search/decision_context.py`(`DecisionSignature`/`SemanticAction`)
* `Combat/search/candidate_pipeline.py`(`OrderMaskedObservation`/`Candidate`)
* `Run/run_emulator_bridge.py`(Whole Run DTO→dict変換)

## 1. 分類凡例

1. そのまま公開 / 2. 公開するがマスク・削減 / 3. RL内部限定 / 4. Trainingへ非公開 /
5. 判断保留

## 2. フィールド単位の表

### 2.1 `GameObservation`(`Sts2Emulator/Dto/GameObservation.cs`)

| プロパティ | パス | 区分 | 理由 | マスク処理 | 対象Decision |
|---|---|---|---|---|---|
| `Seed`/`SeedText` | GameObservation.cs:5,7 | 1 | Run開始時にプレイヤーが選択/確認する値そのもの | 無し | 全種別 |
| `CharacterId` | :9 | 1 | プレイヤー自身が選択したキャラクター | 無し | 全種別 |
| `Ascension` | :11 | 1 | プレイヤー自身が選択した難易度 | 無し | 全種別 |
| `StepIndex` | :13 | 3(RL内部) | Decision識別用カウンタ。プレイヤーには意味を持たないRL/Emulator内部の同期用途だが値自体に隠匿情報は無いため、decision_id構成要素として使うのは可 | 無し | 全種別 |
| `ChoiceScope` | :20 | 1 | 「TopLevelかActionContinuationか」は実際に選択肢の見た目(通常操作か連鎖効果中か)として表れる | 無し | Combat Pending/Start-of-Combat Pending |
| `Boundary` | :29 | 1 | 現在の状態種別そのもの(実プレイでも「マップ選択中/戦闘中/報酬選択中」等は画面遷移で自明) | 無し | 全種別 |
| `CombatSessionId` | :37 | 3(RL内部) | Restore毎に新規発行される内部識別子。プレイヤーは関知しない | 無し | Combat系 |
| `Turn` | :39 | 1 | 画面に表示されるターン数 | 無し | Combat |
| `IsTerminal`/`Outcome` | :41,43 | 1 | Run終了/勝敗は当然プレイヤーが知る | 無し | 全種別 |
| `Player`/`Enemies`/`Hand`/`Relics`/`StatusEffects` | :45-53 | 1 | 「後方互換の型付きフィールド」(doc comment記載)。内容は`State`の集計と同一情報で、画面表示相当 | 無し | Combat |
| `Metrics`/`Extras` | :55,57 | 5(判断保留) | 内容がEmulatorのバージョンにより変わりうる自由形式dict。現時点で具体的に何が入るか個別確認していない | 個別フィールドの中身次第 | 全種別 |
| `State`(`Dictionary<string, object?>`) | :67 | 2(混在、下記2.2で分解) | `BuildFullStateDict()`の生成物。大部分は公開情報だが、mid-combat時のPile順序など一部非公開情報を含む | 2.2節参照 | 全種別 |

### 2.2 `Observation.State`(`GameInstance.BuildFullStateDict`)

| フィールド | 区分 | 理由 | マスク処理 |
|---|---|---|---|
| `seed`/`seedText`/`characterId`/`ascension`/`stepIndex`/`choiceScope`/`boundary` | 1 | 2.1と同じ | 無し |
| `gold`/`hp`/`maxHp`/`block`/`energy`/`stars`/`orbSlots`/`maxEnergy` | 1 | 画面に常時表示される数値 | 無し |
| `relics`(`id`/`rarity`/`status`/`stackCount`/`displayAmount`) | 1 | 所持Relicは常時表示、`displayAmount`も表示カウンター相当(`ShowCounter`が偽の場合のみ`null`— 実装側で既にマスク済み) | 無し |
| `potions`(`id`/`rarity`/`targetType`) | 1 | 所持Potionは常時表示 | 無し |
| `deck`(`id`/`type`/`rarity`/`cost`/`targetType`/`upgraded`/`upgradeLevel`/`tinkerTimeType`/`tinkerTimeRider`) | 1 | 永続デッキの構成(順序に意味なし、山札順とは別物)は常時確認可能 | 無し |
| `playerPowers`(`id`/`amount`/`type`) | 1 | 常時表示されるPlayer Power | 無し |
| `orbs`(`index`/`id`/`basePassiveValue`/`baseEvokeValue`) | 1 | 常時表示されるOrb内容・数値 | 無し |
| `currentActIndex`/`actFloor`/`totalFloor`/`currentRoomType`/`isGameOver` | 1 | 画面に表示される進行状況 | 無し |
| `map`(`points[].pointType`含む全ノード) | 1 | 実際のSlay the Spireのマップ画面は最初からAct全体のノード種別(Monster/Elite/Shop/Rest/Treasure/Unknown)を表示する。未解決ノードは実際に`"Unknown"`のまま返ることを実機確認済み(seed=18のAct1、66ノード中12件が`Unknown`) | 無し(ただし将来の実装で`Unknown`が正しく維持されているかは継続的な回帰確認が必要) |
| `hand`(`BuildCardListDict`、`turnNumber`時点) | 1 | 手札は画面に表示される | 無し |
| **`drawPile`** | **4(非公開)** | 山札の並び順は実際のプレイヤーには見えない(次に引くカードは伏せられている) | **2で扱う場合はOrdered→Multiset(card_id出現数)へ変換必須。現行のexternal_control resolverには一切渡していないため実質的に非公開(7節参照)** |
| **`discardPile`** | 2(公開するがマスク) | 捨て札の「中身の集合」は実際のプレイヤーが確認可能(捨て札を見るUIが存在する構成が一般的)だが、Emulatorが返す配列の「順序」自体は捨てた順という追加情報を持つ可能性があり、順序に依存した推論をTrainingにさせないためMultiset化を推奨 | Multiset化推奨 |
| **`exhaustPile`** | 2 | 同上(除外カードは確認可能、順序の意味は薄いがMultiset化を推奨) | Multiset化推奨 |
| `playPile` | 5(判断保留) | 「場に出したカード置き場」がどこまで実際の画面に表示されるか(Orbや一部カードの一時置き場的挙動)は実装依存で未確認 | 個別確認要 |
| `turnNumber`/`combatRoundNumber` | 1 | 画面表示のターン数 | 無し |
| `enemies`(`index`/`id`/`name`/`hp`/`maxHp`/`block`/`isAlive`/`slotName`/`powers`/`intent`/`stateLog`) | 1(大部分) | 敵のHP/Block/Power/Intentは常時表示。`intent`の`attackDamage`/`attackRepeats`はゲーム内Intentアイコンのツールチップ相当情報で公開情報。`stateLog`(過去の技IDの履歴)は「既に実行された過去の行動記録」でありUI上のCombat Logに相当、非公開情報ではない | 無し |
| `pendingChoice`(`choiceType`/`scope`/`scenarioRestorable`/`minSelect`/`maxSelect`/`selectedCount`/`options`/`choiceOperation`/`sourceZone`/`destinationZone`/`originEntityType`/`originEntityId`/`remainingSelectCount`/`canSkip`/`canConfirm`) | 1 | 現在提示されている選択肢そのもの。`options`は`BuildCardListDict`(現在提示中の候補カードのみ、将来のカードではない) | 無し |

### 2.3 `LegalAction`(`Sts2Emulator/Dto/LegalAction.cs`)

| プロパティ | 区分 | 理由 |
|---|---|---|
| `ActionId` | 1 | 現在のDecision内でのみ有効な選択肢番号(Emulator契約: 「DecisionFrame内でのみ有効」)。番号自体に隠匿情報は無い。ただし「Decision Frameを跨いで永続化しない」ことをTraining側にも周知する必要がある(7節Open Question参照) |
| `ActionType`/`Label`/`IsAvailable`/`Parameters` | 1 | 選択肢の種別・表示名・利用可否・パラメータ(cardId等)は全て「今まさに選べる選択肢」の説明であり公開情報 |

### 2.4 `StepResult`(`Sts2Emulator/Dto/StepResult.cs`)

| プロパティ | 区分 | 理由 |
|---|---|---|
| `ActionId`/`Reward`/`Done`/`Observation`/`LegalActions` | 1 | 直前に適用したActionの結果。`Reward`は現状Combat専用の1.0/-1.0(勝敗)で、Training側の報酬設計とは別物である点に注意(5節Open Question) |
| `RoomContext` | 1(2.6参照) | |
| `Transition`(`TransitionOutcome?`) | 2.5参照 | |
| `Info`(`Dictionary<string,object?>`) | 5(判断保留) | `requestedActionId`/`combatSessionId`/`stepIndex`/`choiceScope`/`choiceKind`等、RL内部識別子とプレイヤー可視情報が混在する自由形式dict。個別キー単位で2.7の対応表に従って分離が必要 |

### 2.5 `TransitionOutcome`(`Sts2Emulator/Dto/TransitionOutcome.cs`)

| プロパティ | 区分 | 理由 |
|---|---|---|
| `Kind`/`Victory`/`FinalPlayerHp`/`FinalPlayerMaxHp`/`FinalEnemies` | 1 | 戦闘終了の事実・勝敗・終了時点のHP/敵情報は全てプレイヤーが直接目にする内容 |
| `FinalObservation` | 2(混在) | `GameObservation`まるごと(2.1/2.2の分解に従う。この時点のdrawPile等が含まれるなら同様に非公開) |
| `CombatSessionId` | 3(RL内部) | 終了したCombatの内部識別子 |

### 2.6 `RoomContext`/`EventRoomContext`(`Sts2Emulator/Dto/RoomContext.cs`,`EventRoomContext.cs`)

| プロパティ | 区分 | 理由 |
|---|---|---|
| `Boundary`/`RoomType`/`InRoom`/`RoomResolved`/`AtMapBoundary`/`ActIndex`/`ActFloor`/`Column`/`Row` | 1 | 現在地・部屋種別・進行状況は画面に表示される |
| `Event.EventId`/`IsFinished`/`CurrentOptionTextKeys` | 1 | 現在提示中のEvent選択肢のテキストキー。将来のページ内容は含まれない(現在ページのみ) |

### 2.7 `MapRoomOption`(`Sts2Emulator/Dto/MapRoomOption.cs`)

| プロパティ | 区分 | 理由 |
|---|---|---|
| `RoomId`/`Column`/`Row`/`PointType` | 1 | 実際にマップ画面で表示される、現在到達可能なノードの座標と種別(`Unknown`含む) |

### 2.8 `RunResetResult`/`RoomEnterResult`/`RunStateSummary`/`RunSummary`

| プロパティ | 区分 | 理由 |
|---|---|---|
| `RunResetResult`(`Seed`/`SeedText`/`CharacterId`/`Ascension`/`AvailableRooms`/`Metadata`) | 1(`Metadata`は5) | 開始時パラメータと初期マップ候補 |
| `RoomEnterResult`(`RoomId`/`RoomType`/`IsCombat`/`Observation`/`LegalActions`/`AvailableRooms`/`Info`) | 1(`Observation`/`Info`は該当DTOの分解に従う) | 入室結果 |
| `RunStateSummary`(`Gold`/`Hp`/`MaxHp`/`DeckSize`/`Relics`/`CurrentRoomType`/`AvailableRooms`) | 1 | 全て画面表示相当のサマリ |
| `RunSummary`(`Outcome`/`FloorReached`/`Score`) | 1 | Run終了後の結果画面相当 |

### 2.9 SaveState/Run Snapshot関連DTO(`RunSnapshot`/`SerializableRun`等)

| 項目 | 区分 | 理由 |
|---|---|---|
| `RunSnapshot`全体(JSON文字列) | 3(RL内部限定) | Snapshotそのものは「復元用の内部データ」であり、Training側の意思決定入力ではない。Whole Run層は既にMap Boundaryでのみ捕捉し、Choice待ち状態のSnapshotを直接扱わない契約を維持している |
| `SerializableRun.rng`(Run RNG 12 streams)/`SerializablePlayer.rng`(Player RNG 3 streams) | 4(非公開) | 将来の乱数結果に直結する内部状態そのもの |
| `SerializableRoomSet.event_ids`/`normal_encounter_ids`/`elite_encounter_ids`/`boss_id`/`second_boss_id`/`ancient_id` | 4(非公開) | 事前生成済みの将来Event/Encounter列そのもの。実機確認の結果、`Observation.State`にはこれらのキーは一切含まれないことを確認済み(7節) |
| `SerializableRoomSet.events_visited`/`normal_encounters_visited`/`elite_encounters_visited`/`boss_encounters_visited` | 3(RL内部) | 訪問済みかどうかの内部フラグ。プレイヤー体験としては「訪問済み」は自明だが、この形での提供は不要 |
| `visited_map_coords`/`map_point_history` | 1相当(ただし現状はSnapshot内部) | 訪問済みマップ座標は実際に画面上の軌跡として表示される公開情報。将来の公開Observationに含めても問題ない候補 |

### 2.10 RNG関連DTO(`SerializableRng`等)

| 項目 | 区分 | 理由 |
|---|---|---|
| `counter`/`s0`/`s1`/`s2`/`s3`(内部状態) | 4(非公開) | 将来の乱数結果に直結 |
| `EventRngSnapshot`全体 | 3(RL内部)/一部4 | Event中のRNG復元用。内部状態自体は非公開相当だが、現状Whole Run外部制御には一切渡していない |

### 2.11 Combat Observation固有(`DecisionSignature`、`Combat/search/decision_context.py`)

| フィールド | 区分 | 理由 |
|---|---|---|
| `combat_session_id`/`step_index`/`continuation_step_index` | 3(RL内部) | Decision識別用。値自体はプレイヤー非公開だが隠匿情報ではない(乱数結果等を含まない) |
| `semantic_action`/`resolved_action_id`/`resolved_card_id`/`resolved_target_type`/`resolved_target_index`/`resolved_target_slot_index` | 1 | 実際に選択・解決されたActionそのものの説明 |
| `boundary`/`choice_scope`/`choice_kind`/`candidate_semantic_keys` | 1 | 現在のDecision種別と候補のSemantic Key集合(Ordered DrawPile等の真の内部状態は含まない、既存の構造的テストで保証済み) |

`DecisionSignature`は既存設計により「HP/Block/Energy/Pile構成/Power/Relic/敵状態」等の
盤面情報を一切含まないことが構造的テスト(`test_decision_context.py`)で保証されている
既存の模範的な設計であり、本監査でも修正不要と判断した。

### 2.12 `OrderMaskedObservation`(`Combat/search/candidate_pipeline.py`)

| フィールド | 区分 | 理由 |
|---|---|---|
| `hand_size`/`draw_pile_size`/`discard_pile_size`/`exhaust_pile_size`/`play_pile_size`/`alive_enemy_count`/`player_energy` | 1 | 枚数・数値のみ、公開情報 |
| `hand_card_id_counts`/`hand_card_type_counts` | 1 | 手札は公開情報(Multiset化されているが手札自体は元々全て見える) |
| `draw_pile_card_id_counts`/`discard_pile_card_id_counts`/`exhaust_pile_card_id_counts` | 2(既にマスク済み) | 既にOrdered→Multisetへ変換済みの設計(本監査が推奨する2.2節のマスク処理と同一の考え方が、Combat側では既にPhase 4で実装されている) |
| `pending_choice_type`/`pending_min_select`/`pending_max_select`/`pending_selected_count` | 1 | 現在提示中の選択肢の説明 |

`OrderMaskedObservation`は、本監査が2.2節で指摘した「drawPile等のOrdered→Multiset変換」を
Combat側で既に実践している設計であり、将来のWhole Run向け「公開Observation」DTOの
直接の参考実装として推奨する(10節)。

### 2.13 Worker/Replay/Lease関連RL内部DTO

| 項目 | 区分 | 理由 |
|---|---|---|
| `Combat`側`Lease`(`worker_id`/`worker_generation`/`context_id`/`search_hypothesis_id`/`state_epoch`/`combat_session_id`/`step_index`/`decision_result_digest`) | 3(RL内部限定) | Branch実行基盤の内部管理情報。Training側の意思決定に不要 |
| `Whole Run`側`Lease`(`worker_slot`/`worker_generation`/`pid`/`context_id`/`combat_session_id`/`run_seed`/`step_signature`) | 3(RL内部限定) | 同上 |
| `ChoiceWorkItem`/`WorkItem`(`map_snapshot`/`action_prefix`/`decision_context`等) | 3(RL内部限定) | Snapshot JSON・Replay Prefixを含み、Trainingへは非公開 |
| `BranchResult`/`ExplicitBranchDispatchResult`(`work_item`/`branch_result`/`work_item_state`) | 3(RL内部限定、ただし`branch_result`内の`reach`/`step`が示す最終状態は公開情報の集合) | Branch結果そのものはRL内部形状だが、内部の`Observation`/`LegalActions`断片は公開情報 |

## 3. Decisionごとの公開payload例

以下はいずれも「現状到達可能な最小構成」(`legal_actions`+`decision_id`+境界情報)を示す。
Training向け正式DTOの構造そのものはまだ設計・実装していない(10節Open Question参照)。

### Map

```json
{
  "decision_id": {"seed": 18, "character_id": "Ironclad", "step_index": 12, "boundary": "map_select", "combat_session_id": null},
  "boundary": "map_select",
  "legal_actions": [
    {"room_id": 0, "column": 0, "row": 2, "point_type": "Monster"},
    {"room_id": 1, "column": 1, "row": 2, "point_type": "Unknown"}
  ]
}
```

### Event

```json
{
  "decision_id": {"seed": 18, "step_index": 15, "boundary": "event_choice"},
  "boundary": "event_choice",
  "room_context": {"room_type": "EventRoom", "event": {"event_id": "WELLSPRING", "is_finished": false, "current_option_text_keys": ["WELLSPRING.pages.INITIAL.options.BOTTLE", "WELLSPRING.pages.INITIAL.options.BATHE"]}},
  "legal_actions": [
    {"action_id": 0, "action_type": "choice_event_option", "label": "WELLSPRING.pages.INITIAL.options.BOTTLE", "parameters": {"eventId": "WELLSPRING", "choiceId": "..."}}
  ]
}
```

### Combat(通常)

```json
{
  "decision_id": {"combat_session_id": "...", "step_index": 3, "boundary": "stable"},
  "boundary": "stable",
  "legal_actions": [
    {"action_id": 0, "action_type": "system", "label": "End Turn", "parameters": {"kind": "end_turn"}},
    {"action_id": 1, "action_type": "card", "label": "STRIKE_IRONCLAD", "parameters": {"cardId": "STRIKE_IRONCLAD", "cost": 1, "targetType": "AnyEnemy"}}
  ],
  "public_observation": {
    "hp": 68, "maxHp": 80, "block": 0, "energy": 3,
    "hand_card_id_counts": {"STRIKE_IRONCLAD": 2, "DEFEND_IRONCLAD": 1},
    "enemies": [{"index": 0, "id": "CALCIFIED_CULTIST", "hp": 40, "maxHp": 48, "intent": {"intentTypes": ["Attack"], "attackDamage": 6}}]
  }
}
```

(注: `public_observation`は`OrderMaskedObservation`相当の未実装DTOの例示であり、
drawPile/discardPile/exhaustPileは常にMultiset化する。)

### Combat Pending(mid-combat Action Continuation)

```json
{
  "decision_id": {"combat_session_id": "...", "step_index": 5, "boundary": "pending_choice"},
  "boundary": "pending_choice",
  "choice_scope": "ActionContinuation",
  "choice_kind": "WishDrawToHand",
  "legal_actions": [
    {"action_id": 0, "action_type": "choice_card", "label": "STRIKE_IRONCLAD", "parameters": {"cardId": "STRIKE_IRONCLAD"}},
    {"action_id": 1, "action_type": "choice_skip", "label": "Skip", "parameters": {"minSelect": 0, "maxSelect": 1}}
  ]
}
```

### Reward

```json
{
  "decision_id": {"seed": 18, "step_index": 20, "boundary": "reward_select"},
  "boundary": "reward_select",
  "legal_actions": [
    {"action_id": 0, "action_type": "choice_reward_card", "label": "IRON_WAVE", "parameters": {"cardId": "IRON_WAVE"}},
    {"action_id": 3, "action_type": "choice_reward_skip", "label": "Skip", "parameters": {}}
  ]
}
```

### Shop

```json
{
  "decision_id": {"seed": 18, "step_index": 30, "boundary": "shop_choice"},
  "boundary": "shop_choice",
  "legal_actions": [
    {"action_id": 0, "action_type": "choice_shop_buy_card", "label": "MOLTEN_FIST", "parameters": {"cost": 50}},
    {"action_id": 10, "action_type": "choice_shop_leave", "label": "LEAVE_SHOP", "parameters": {}}
  ]
}
```

### Rest

```json
{
  "decision_id": {"seed": 18, "step_index": 35, "boundary": "rest_choice"},
  "boundary": "rest_choice",
  "legal_actions": [
    {"action_id": 0, "action_type": "choice_rest_option", "label": "HEAL", "parameters": {}},
    {"action_id": 1, "action_type": "choice_rest_option", "label": "SMITH", "parameters": {}}
  ]
}
```

### Start-of-Combat Pending

```json
{
  "decision_id": {"combat_session_id": "...", "step_index": 0, "boundary": "pending_choice"},
  "boundary": "pending_choice",
  "choice_scope": "TopLevel または ActionContinuation(実測: TOOLBOX注入では ActionContinuation - Whole Run接続試験フェーズでの実機確認結果)",
  "choice_kind": "ToolboxChooseCard",
  "legal_actions": [
    {"action_id": 0, "action_type": "choice_card", "label": "EQUILIBRIUM", "parameters": {"cardId": "EQUILIBRIUM"}},
    {"action_id": 3, "action_type": "choice_skip", "label": "Skip", "parameters": {}}
  ]
}
```

## 4. 判断保留事項(要確認)

1. **`GameObservation.Metrics`/`Extras`**: 自由形式dictの実際の中身を、稼働中に観測された
   キー単位で全数確認できていない。過去の耐久試験ログを精査すれば具体的なキー一覧を
   抽出できるが、本監査の期限内では未完了。
2. **`Observation.State.playPile`**: 「場に出したカード置き場」が実際のゲーム画面で
   プレイヤーにどこまで可視か(Orbや特定カードの一時滞留領域として画面表示されるか)を
   実装ソース(`STS2_Decompiled_v0109`)側で確認する必要がある。
3. **`discardPile`/`exhaustPile`の順序自体**: 中身(Multiset)は公開情報だが、
   Emulatorが返す配列の順序(捨てた順/破棄した順)にプレイヤーがゲーム内で実際に
   アクセスできるかは未確認(捨て札を「積まれた順」に見るUIがあるかどうか)。
   安全側に倒し、公開する場合はMultiset化を推奨する扱いとした。
4. **`Info`(`StepResult.Info`/`RoomEnterResult.Info`)の個別キー**: `requestedActionId`
   等、RL内部識別子と公開情報が混在する自由形式dictであり、Emulator側のドキュメントに
   全キーの網羅的な一覧が無い。実装時にキー単位で本表の分類へ再マッピングする必要がある。
5. **`StepResult.Reward`**: 現状Combat専用の1.0(勝利)/-1.0(敗北)固定値であり、
   Whole Run全体やTraining側の実際の報酬設計とは無関係な、既存Combat検索用の内部値
   である可能性が高い。Trainingへそのまま渡してよい値なのか、Envelope側で別名か
   除外にすべきかは設計判断が必要(重大な曖昧さとまでは言えないが、5節に明記)。
6. **Emulator文書と実ゲーム表示の不一致の可能性**: 本監査は
   `whole_run_api_reference_20260803.md`とソースコードのみを一次資料とし、実際の
   Slay the Spire 2クライアント画面そのものとの逐一の突き合わせは行っていない
   (`STS2_Decompiled_v0109`は参照可能だが本監査では未実施)。「実プレイヤーが知り得るか」
   という判断基準の一部は、既存のRL/Emulator双方の文書に基づく推定である。

## 5. 停止条件に該当する事項の有無

「Emulator DTOの公開可否に重大な曖昧さがある」「Hidden Informationを除くためEmulator DTO
契約自体の変更が必要」に明確に該当する事項は本監査では見つからなかった。理由:

* Ordered DrawPile等の非公開情報は、Emulator DTO自体を変更せずとも、RL側のEnvelope構築時に
  Multiset化するだけで対処可能(Combat側`OrderMaskedObservation`が既に実証済み)。
* 事前生成済みEvent/Encounter列は、そもそも`Observation.State`に一切含まれておらず
  (Run Snapshotの内部専用フィールドのみに存在)、公開Observationの構築時に単純に触れなければ
  よい。
* 4節の判断保留事項はいずれも「未確認・要individual確認」レベルであり、DTO契約自体を
  変更しなければ解決できない構造的な曖昧さではない。

## 6. 成果物

* 本報告書(フィールド単位監査表、Decisionごとの公開payload例、判断保留事項)

**本報告の承認を待って、Training向け公開Decision DTOの実装(10節に記載した`OrderMaskedObservation`
相当のWhole Run版DTO新設を含む)へ進む。承認が得られるまで、本タスクではB(Combat Worker
Respawn)・C(Branch Cancel/Release)のみを実装する。**
