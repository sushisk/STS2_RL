# RL報告: Combat状態契約の統一方針 — RL担当案 — 2026-07-26

RL・Emulator共同作業指示の「RL担当」5項目について、契約**案**のみを作成する。
コード修正・schema実装・Snapshot実装・データ再生成は行っていない。
Emulator担当分(C# live state一覧化・Capture分類・DTO案・hook整理・decision
boundary条件)は本報告の対象外——別セッション/別担当が作成する。

参照した必須ファイル: 監査input報告書一式(`rl_combat_execution_flow_and_
architecture_audit_input_20260726.md`、`combat_flow/normal_online_
sequence.md`、`combat_flow/legal_actions_sequence.md`、
`combat_flow/state_lifecycle.md`、`combat_flow/state_restore_coverage.csv`)、
および今回追加で`Common/schemas/combat_state_schema.json`・
`Common/schemas/transition_schema.json`・`Common/schemas/legal_action_
schema.json`・`Training/sts2_training/encoding.py`(読み取り専用、Policy/
Valueが実際にどのフィールドを消費しているかを正確に確認するため)。

---

## 0. 先に発見した既存の契約ドリフト(今回の提案の動機を裏付ける実例)

`Common/schemas/legal_action_schema.json`および`combat_state_schema.json`の
`pendingChoice`定義は、**Emulator commit 12df954/0d16130が追加した
`originEntityType`/`originEntityId`/`sourceZone`/`choiceOperation`/
`destinationZone`/`choiceType`/`remainingSelectCount`を一切文書化していない**
——にもかかわらず`Combat/choice_semantics.py`はこれらのフィールドを
実際に読み取って動作している(`pending_choice.get("remainingSelectCount")`等)。
**「schemaに書かれていない値が実際には流れてきて、既に本番ロジックが
依存している」という状態が現時点で既に発生している** — これは今回の
共同作業指示が解決しようとしている問題そのものの実例であり、統一契約に
これらのフィールドを正式に組み込む必要がある。

---

## 1. 現在Python側で保持している全状態フィールドの一覧

### 1a. `engine_state`(= `GameObservation.State`、Python dict、schema文書化済み)

`combat_state_schema.json`より(定義済み):
`seed`/`seedText`/`characterId`/`ascension`/`stepIndex`/`gold`/`hp`/`maxHp`/
`block`/`energy`/`stars`/`maxEnergy`/`relics[]`/`potions[]`/`deck[]`/
`playerPowers[]`/`hand[]`/`drawPile[]`/`discardPile[]`/`exhaustPile[]`/
`playPile[]`/`pendingChoice`/`turnNumber`/`combatRoundNumber`/`enemies[]`/
(map専用: `currentActIndex`/`actFloor`/`totalFloor`/`currentRoomType`/
`isGameOver`/`map` — 現在のCombat実行はcombatのみのresetのため常に不在)。

各ネスト定義: `card`(id/type/rarity/cost/targetType/upgraded/
upgradeLevel/tinkerTimeType/tinkerTimeRider)、`relic`(id/rarity/status/
stackCount/displayAmount)、`potion`(id/rarity/targetType)、`power`
(id/amount/type)、`enemy`(index/id/name/hp/maxHp/block/isAlive/slotName/
powers/intent{stateId/intentTypes/attackDamage/attackRepeats}/stateLog)、
`pendingChoice`(choiceType/scope/scenarioRestorable/minSelect/maxSelect/
selectedCount/options)。

### 1b. `engine_state`中、**schemaに未文書化だが実際に流れてきて使用されている**フィールド

`pendingChoice.originEntityType`/`originEntityId`/`sourceZone`/
`choiceOperation`/`destinationZone`/`choiceType`/`remainingSelectCount`
(`choice_semantics.py`が読む)。`legal_action_schema.json`側にも同様の
未文書化パラメータが`choice_card`/`choice_skip`/`choice_confirm`の
`parameters`に存在する(`choice_policy_agent.py`が
`legal_actions`の`parameters`から`originEntityType`等を読む箇所あり)。

### 1c. `BattleState`(Python専用ラッパー、`battle_emulator.py`、engine_stateではない)

`engine_state`(上記1a+1b)、`is_terminal`(engine自身のIsTerminalを
`coerce_terminal_observation()`で補正した**Python側の値**)、`outcome`
(同様に補正済み)、`turn`(**Pythonが独自に加算管理する値** — engine内部の
`turnNumber`とは別物、`system`アクション時のみ+1)、`enemy_max_hps`
(**Pythonが個別に保持する、restoreで失われるenemyの真のmaxHpを都度
patchするための辞書**、engine_stateには存在しない)、`shuffle_rng_seed`
(lookahead分岐専用、通常運用では`None`)、`_cached_legal_actions`
(直前の`ResetResult`/`StepResult`のLegalActions、ActionContinuation時のみ
再利用)。

### 1d. `CombatEnv`固有のbookkeeping

`_scenario_spec`(元spec、観測provenance用に保持のみ)、`_step_index`
(実decision番号、trajectoryの`decision_index`に相当)。

### 1e. Scenario spec(入力側、`combat_scenario_input_schema.json`、
`build_scenario_from_spec()`/`build_scenario_from_state()`が消費)

`character_id`/`player_hp`/`player_max_hp`/`player_block`/
`hand`・`draw_pile`・`discard_pile`・`exhaust_pile`(id文字列リスト、旧形式)/
`hand_cards`・`draw_pile_cards`・`discard_pile_cards`・`exhaust_pile_cards`
(構造化`{card_id, is_upgraded, tinker_time_type, tinker_time_rider}`)/
`potions`(`{slot, potion_id}`)/`orbs`・`orb_slots`/`pending_choice`/
`player_powers`(`{id, amount, associated_card}`)/`energy`/`stars`/
`step_index`/`shuffle_rng_seed`/`relics`(idリストのみ)/
`enemies`(`monster_id`/`hp`/`max_hp`/`block`/`slot_name`/`powers`/
`frog_knight_has_beetle_charged`/`waterfall_giant_current_pressure_gun_
damage`)/`seed`。

---

## 2. Policy／Value／ログ／restoreでの実使用分類

`Training/sts2_training/encoding.py::ExportEncoder`を直接確認した結果
(Policy/Valueは同一の共有エンコーダを使用 — `policy_baseline_v1_
20260724.json`の`encoder_note`参照)、モデルが実際に読むのは以下のみ:

| 分類 | フィールド | 根拠 |
|---|---|---|
| Policy/Value入力(数値スカラー) | `hp`/`maxHp`/`block`/`energy`/`maxEnergy`/`stars`/`gold`/`turnNumber`/`combatRoundNumber`/`stepIndex`/各pileの**件数のみ**(`hand`/`drawPile`/`discardPile`/`exhaustPile`/`deck`)/生存enemy数/`potions`件数/`relics`件数/`playerPowers`件数/`pendingChoice`の**有無のみ**(bool) | `encoding.py::_state_scalars()` |
| Policy/Value入力(bag-of-id) | `hand`/`drawPile`/`discardPile`/`exhaustPile`/`playPile`/`deck`の**id部分のみ**(type/rarity/cost/targetType/upgraded/upgradeLevel/tinkerTime*は不使用)、`relics`/`playerPowers`/`potions`の**id部分のみ**(rarity/status/stackCount/displayAmount/amount/type等は不使用) | `encoding.py::encode_state()`のループ、`_ids_from_items()`がitem.get("id")のみ抽出 |
| Policy/Value入力(one-hot) | `characterId` | `encoding.py:56` |
| Policy/Value入力(enemy特徴、MAX_ENEMIES=6まで) | `hp`/`maxHp`/`block`/`isAlive`/`intent.attackDamage`/`intent.attackRepeats`/`id`(one-hot) — `index`/`name`/`slotName`/`powers`/`intent.stateId`/`intent.intentTypes`/`stateLog`は**不使用** | `encoding.py:65-80` |
| Policy/Value **完全不使用** | `seed`/`seedText`/`ascension`/`pendingChoice`の中身(choiceType等)/map系全フィールド/enemyの`powers`(!) | `encoding.py`全体を読んでも該当箇所なし |
| Choice Policy追加入力 | 上記のシェアエンコーダ出力 + candidate `card_id`(vocab化) + `remainingSelectCount` + Choice Meaning token(`normalizedChoiceOperation`/`exceptionEntityKey`をmerge_map経由) | `Training/sts2_training/choice_inference.py::ChoiceDecision.__call__()` |
| action入力(Policy側、legal_actionsから) | `card_id`(=`parameters.cardId`)/`potion_id`/`target_type`/`target_enemy_index`/`is_available`/`raw_parameters.potionSlot`/`raw_parameters.cost` | `encoding.py::encode_action()`、`policy_agent.py::normalize_legal_action()` |
| restore専用(`build_scenario_from_state()`が送る、モデルは不使用) | `hp`(clamp付)/`maxHp`/`block`/`energy`/`stars`/`stepIndex`/各pileの**構造化カード全体**(id+upgraded+tinkerTime)/`potions`(id+slot)/`orbs`/`orbSlots`/`pendingChoice`(条件付き)/`playerPowers`(id+amount+associatedCard)/`relics`(idのみ)/`seed`/enemyの`id`/`hp`/`maxHp`/`block`/`slotName`/`powers`/`intent.stateId`/`stateLog` | `battle_emulator.py::build_scenario_from_state()` |
| ログ専用(いずれのモデルにもrestoreにも使わないが、trajectory/評価ログには残す) | `seedText`/`ascension`/`gold`(Policy未使用と判明)/カードの`rarity`・`cost`・`targetType`・`upgradeLevel`(モデルはidのみ、ログは全フィールド保持)/`relic.rarity`・`status`・`stackCount`・`displayAmount`/`power.amount`・`type`/`enemy.name`・`enemy.index`/`pendingChoice`の全フィールド(Choice Semantics解決用、モデル入力ではなくログ+ルール解決用) | `generate_heuristic_trajectories.py`の`state`/`next_state`保存、`choice_semantics.py`の`resolve()` |
| 未使用(確認できた範囲でどこからも参照されていない) | `enemy.powers`(!Policy/Value双方とも読まない。Choice Semantics/Heuristicでも未参照を確認)、map系全フィールド(combat専用resetのため現状常に不在) | `encoding.py`全体・`choice_semantics.py`・`heuristic_agent.py`を横断確認 |

**重要な発見**: `enemy.powers`(敵にかかっているpower効果)は、Policy/Value/
Choice Policyのいずれのモデル入力にも**含まれていない**——スコアリング
(`state_evaluator.py`)側での使用有無は本タスクでは未確認。契約統一の場で
「本当に不要なのか、モデル改善の余地として残すべきか」を確認事項として
提起したい。

---

## 3. Python側Snapshot schema案

### 設計方針

* `Canonical CombatStateSnapshot`を**既存の`combat_state_schema.json`の
  拡張**として設計する(ゼロから作り直さない——既存のPolicy/Value
  checkpointの入力契約と齟齬を起こさないため)。
* **未文書化だが実際に使用中のフィールド(1b節)を正式に組み込む** —
  `pendingChoice`に`originEntityType`/`originEntityId`/`sourceZone`/
  `choiceOperation`/`destinationZone`/`choiceType`/`remainingSelectCount`
  を追加。
* **Python側が現在restore不能なフィールドを個別workaroundするのではなく、
  Snapshot自身が持つ明示フィールドへ格上げする**(4節と連動):
  * `turnNumber`(現状Python側`BattleState.turn`が別管理) →
    Snapshotが真の値を保持し、Emulator側が正しく復元できるかどうかは
    Emulator担当の回答待ち事項として明記(6節)。
  * `enemies[].maxHp`(現状Python側`enemy_max_hps`辞書でpatch) →
    Snapshot自体がEnemyのMaxHpを保持するフィールドを持ち、
    Python側の後付けpatchロジックを不要にする(Emulator側でMaxHpを
    受理・保持できることが前提、これもEmulator担当への確認事項)。
  * `relics[]`の内部消費/カウンタ状態 → 現状「idのみ」の`relic`定義に
    `internalState`(型・意味は完全にEmulator担当の裁量、RL側は
    「存在するなら不透明payloadとして往復させてほしい」とだけ要求する
    — 詳細は関知しない、`opaque_state: object | null`のような
    汎用フィールドを提案)。
* `null`は「値が存在しない」ときのみ使用する(共同作業指示の大原則)。
  現状の`combat_state_schema.json`は既にこの原則にほぼ従っているが、
  一部(`enemy.name`の空文字列 — headless実行の既知の制約として
  ドキュメント化はされている)は「未実装/取得失敗」を`""`という
  非null値で隠している例として、契約統一の場で扱いを再検討したい。

### スキーマ案(差分形式、`combat_state_schema.json`をベースに)

```jsonc
{
  "$id": "sts2_rl:combat_state_snapshot_schema:v2",
  "schema_version": "v2",  // 新規: canonical契約であることを明示する版番号
  // v1 (combat_state_schema.json) の全フィールドを継承...
  "properties": {
    // ...(v1のプロパティ全て)...

    // 新規: pendingChoiceへの未文書化フィールドの正式組み込み
    "pendingChoice": {
      "properties": {
        // ...(v1の既存フィールド)...
        "originEntityType": {"type": ["string", "null"]},
        "originEntityId": {"type": ["string", "null"]},
        "sourceZone": {"type": ["string", "null"]},
        "choiceOperation": {"type": ["string", "null"]},
        "destinationZone": {"type": ["string", "null"]},
        "remainingSelectCount": {"type": ["integer", "null"]}
      }
    },

    // 新規: relicの不透明内部状態(Emulator担当の設計次第でnullのまま運用も可)
    "relics[].opaqueState": {"type": ["object", "null"], "description": "Emulator-defined, RL側は解釈しない。round-trip検証でのみ使用。"},

    // 新規: snapshot自体のメタ情報
    "snapshotMeta": {
      "type": "object",
      "properties": {
        "captureKind": {"type": "string", "enum": ["fresh_combat_start", "mid_combat_resume", "hypothetical_branch"], "description": "このSnapshotが何を意図して作られたかを明示 - 4節の経路分離と直結"},
        "completeness": {"type": "string", "enum": ["complete", "partial_known_gaps", "unknown"], "description": "6節の『completeness判定』『不完全Snapshotの拒否方法』と連動"},
        "schemaVersion": {"type": "string"}
      }
    }
  }
}
```

### RL側の提案する原則

1. Snapshotは**エンジンから見て「新規combat開始」と「中断からの再開」を
   区別できる情報を持つ**べき(`snapshotMeta.captureKind`)——現状の
   `ResetFromScenario`はこの区別が一切できない設計であり、監査input
   報告書の最優先リスク項目と直結する。
2. **`schemaVersion`を必須フィールドとして持つ** — 現状
   `combat_state_schema.json`自体には版番号があるが、実際に流れる
   `engine_state`辞書には版番号が一切埋め込まれておらず、スキーマとの
   乖離(0節)が実行時に検知できない。
3. Python側は`combat_state_schema.json`のパース時に
   `schemaVersion`不一致または未知フィールドを**警告ログとして
   記録する**(拒否はしない、後方互換重視)——ただし`completeness`が
   `unknown`または`partial_known_gaps`のSnapshotを検索/restore用途に
   使う場合は明示的にフラグを立てる。

---

## 4. ライブ実行経路と探索restore経路の分離案

監査input報告書(`combat_flow/call_count_summary.csv`)が示す通り、
現状は**「本当に必要なrestore」と「実質不要な冗長restore」が同じ
`_restore()`関数に混在**している。以下の2経路への分離を提案する。

### 経路A: ライブ実行経路(実際にcommitされる進行のみ)

* 対象: `CombatEnv.step()`が呼ぶ、実際に採用されたactionの適用。
* 提案: **同一エピソード内で同一の`game`(GameInstance)ハンドルを
  使い回し**、`ResetFromScenario`による再構築を**エピソード開始時の
  1回のみ**に限定する。`Step()`の戻り値(`Observation`/`LegalActions`)を
  そのまま次のdecisionへ引き継ぐ(5節と連動)。
* 現状との差分: 現在は`CombatEnv.get_legal_actions()`と`CombatEnv.step()`
  の両方が個別に`_restore()`(=`ResetFromScenario`)を呼んでおり、
  1 decisionあたり2回の不要な再構築が発生している
  (`combat_flow/legal_actions_sequence.md`)。
* 前提条件(Emulator担当への確認事項、6節): 単一の`game`ハンドルを
  エピソード全体で保持し続けることが、現在の「共有singleton
  GameInstance」設計(プロセスあたり1インスタンス)と衝突しないか。
  現状でも実質的に同じ`game`オブジェクトを使い回しているが、
  **`Step()`のたびに`ResetFromScenario`で中身を上書きしている**点が
  「ライブ実行」の意味を損なっている——ここを「本当に`Step()`だけを
  積み重ねる」形に変更できるかどうかがEmulator側の設計次第。

### 経路B: 探索/restore経路(仮説的分岐、コミットしない)

* 対象: `HeuristicAgent.choose_action_with_detail()`の候補スコアリング
  (`heuristic_sequence.md`)、`beam_search.py`/`lookahead.py`の分岐探索、
  shadow評価(`heuristic_sequence.md`F節)。
* これらは**設計上、同一局面から複数の仮説的未来を独立に試す**必要が
  あるため、restoreベースの分岐は正当かつ必要——**この経路自体を
  廃止する提案ではない**。
* 提案: 経路Aと経路Bを**コード上明確に別関数/別クラスとして分離**し、
  「このrestoreは実際の進行を確定させるものか、それとも破棄前提の
  仮説評価か」を呼出側で取り違えないようにする(現状は両方とも
  同じ`apply_action()`/`_restore()`を通るため、コードを読むだけでは
  区別しづらい)。

---

## 5. StepResultのObservation／LegalActionsを継続利用する処理案

### 現状の問題

`CombatEnv.step()`(`combat_env.py:161`)は、`apply_action()`が
`step_live_action()`経由で既に取得済みの`StepResult.LegalActions`
(`next_state._cached_legal_actions`に格納済み)を使わず、
**`self._emulator.enumerate_legal_actions(next_state)`を改めて呼んで
おり、これが(継続待ちでない限り)必ずfresh restoreを引き起こす**
(`combat_flow/legal_actions_sequence.md`)。

### 既知の障害: なぜ現状は「常にfresh」なのか

`enumerate_legal_actions()`自身のコード内コメント:
「we have live cases where StepResult.LegalActions after a complex card
play is stale」——過去に**StepResultのLegalActionsが実際に古い/
不正確だった実例がある**ため、意図的に「継続待ち以外は常にfresh
restoreする」設計にした、という経緯が確認できる
(`combat_flow/known_risks.md`項目9)。

### 提案(3段階、Emulator側の保証状況次第で選択)

1. **最有力案**: Emulator担当に対し、「`Step()`が返す`LegalActions`が
   その`Observation`に対して常に正確である」という不変条件を保証
   できるかを確認する。保証できるなら、経路A(4節)は`StepResult`の
   `LegalActions`をそのまま次decisionへ引き継ぎ、fresh restoreは
   一切不要になる。
2. **次善案**: 「complex card play後は不正確になる」という既知の
   条件が特定のaction_type/効果に限定できるなら、**その条件に該当する
   ときだけ**fresh restoreし、それ以外は`StepResult`を信頼する
   ハイブリッド案。ただし「どの効果が該当するか」の一覧はEmulator側の
   知識が必須——RL側だけでは条件を特定できない。
3. **保守案(現状維持+可観測性向上)**: 不変条件が保証できない場合、
   現状の「常にfresh restore」を維持しつつ、**`StepResult.LegalActions`
   とfresh restoreの結果を比較するデバッグモードを追加**し
   (恒久コードにはcommitしない、5節の「診断用ログ」注記通り)、
   実際にどの程度・どんな状況で乖離するかを定量化してから再判断する。

本タスクでは上記のいずれを採るかを**決定しない**——Emulator担当の
回答(「Step()後のLegalActionsは信頼できるか」)を待って3者で決める。

---

## 6. 共同で決める事項へのRL側からの入力

* **RNG**: 現状`scenario.Seed`は元シードのみで、restoreをまたぐ
  RNGカーソル連続性は保証されない(`state_restore_coverage.csv`)。
  Snapshotに「現在のRNG内部カーソル」を含められるか、Emulator側に
  確認したい。
* **Relic/Power**: 3節の`opaqueState`案の通り、RL側は内部状態の
  意味を解釈する必要はなく、往復できれば十分。
* **Choice/Continuation**: 現状`ActionContinuation`はrestore不能という
  仕様(`is_action_continuation_pending_choice()`)を維持することに
  RL側は異存ない——無理に復元可能にするより、「ライブGameInstance上でのみ
  解決される」という現在の制約を契約として明文化する方が安全と考える。
* **command queue**: RL側では該当概念の存在有無を確認できていない
  (`known_risks.md`項目6)——Emulator担当からの一覧化を待つ。
* **action_idの有効範囲**: `legal_action_schema.json`に明記の通り
  「stateが変わるたびに再取得が必要、cacheしてはいけない」という
  既存の制約をそのままcanonical契約に引き継ぐことを提案する。
* **decision boundary**: RL側の提案は「`CombatEnv.get_legal_actions()`が
  返した`legal_actions`と、直後に`CombatEnv.step()`へ渡すactionが
  参照する`legal_actions`が、**同一のGetLegalActions呼出結果**である
  ことを保証する」("同一decision boundary"の定義) — 現状は2回の
  独立したrestore経由の呼出になっており、理論上(未確認だが)この間に
  何らかの不整合が起きる余地がある。
* **round-trip受け入れ条件**: RL側の提案は「同一`CombatStateSnapshot`を
  2回restoreし、2回とも同一の`GetLegalActions()`結果が得られること」を
  最低限の受け入れテストとする("round-trip and same-action-result"
  一致テスト、共同作業指示の4番目の柱と直結)。
* **不完全Snapshotの拒否方法**: 3節の`snapshotMeta.completeness`が
  `unknown`の場合、少なくとも**探索/restore経路(経路B)では使用を
  拒否**(例外を投げる)し、ライブ経路(経路A)では警告ログのみで
  進行を止めない、という段階的な扱いを提案する(全面拒否は既存
  パイプラインの後方互換性を壊すリスクが高いため)。

---

## 7. 未回答のまま残す確認事項(RL側では判断できない)

* `Step()`後の`LegalActions`は本当に常に正確か(5節)。
* `ResetFromScenario`が新規combat開始と中断再開を区別できるように
  なり得るか、その場合のC#側API変更の規模(4節)。
* RNGカーソルをSnapshotに含めることが技術的に可能か。
* Relic/Powerの内部状態を「不透明payload」として往復させる実装コストと、
  それがEmulator側の既存アーキテクチャとどう整合するか。
* `enemy.powers`が現在どのモデル/ロジックにも使われていない
  (2節)ことについて、今後利用する計画があるかどうか。

---

現段階ではコード修正・schema実装・Snapshot実装・データ再生成のいずれも
行っていない。本報告は契約案の提示のみであり、Emulator担当の報告・
監督者確認を経て最終契約が確定するまで、これ以上の実装作業には
進まない。
