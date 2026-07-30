# RL報告: Choice Semantics統合・初期検証 Stage A (20 Scenario) — 2026-07-24

## 0. 結論

* `Combat/choice_semantics.py` 実装、直接テスト12/12合格。
* Stage A(20 Scenario)完走、illegal/exception/mismatch **全て0**。
* **統合前後の行動一致: 完全一致**(20 Scenario×2arm、最終結果・decision数・選択行動列すべて同一)——
  Choice Semanticsはログ専用であり行動選択に一切影響しないことを実測で確認。
* **決定論性: 完全一致**(134件のchoice-log entryを再実行し全件一致)。
* **semantic mismatch: 0件**(lookup解決した61件をtableの`evidence`と突合、全件一致)。
* 重要な発見2件(4節): (a) `GamblingChipDiscard`という**lookup未収録のchoiceType**が
  36件出現、(b) potion起因choiceの`originEntityType`が仕様の`"potion"`ではなく
  **C#クラス名(`ColorlessPotion`等)で返る**ため5種のpotionで系統的にlookup missとなる。
  いずれもコード側で推測補完せず、未解決として記録。
* **50 Scenario(Stage B)へ進めることを推奨**(5節)。

---

## 1. 実装範囲

* `Combat/choice_semantics.py`(新規): schema/lookup読込・検証、rule matching、
  normalized/passthrough/unknown判定、load error時のfallback、SHA256記録。
* `Combat/tests/test_choice_semantics.py`(新規): 直接テスト12件、実Emulator
  (`0d16130`)経由7件+純dict単体テスト5件。**12/12 合格**。
* `Combat/evaluation/online_eval/online_policy_eval.py`(拡張、行動選択ロジック無変更):
  - トップレベルdecisionの`legal_actions`に`choice_card`/`choice_skip`/`choice_confirm`が
    含まれる場合、`pendingChoice`をChoice Semanticsへ渡しログへ`choice_semantics`
    サブオブジェクトを追加。
  - **重要な追加設計**(3節で詳述): `env.step()`へ渡す`continuation_resolver`を
    ロギング専用ラッパーで包み、ActionContinuation内で自動解決される
    choice(HOLOGRAM/SURVIVOR/discard/exhaust/upgrade/NIGHTMARE等)も同様に記録できる
    ようにした。実際の選択ロジック(`BattleEmulator._default_choose_action_continuation_live`、
    既存・無変更)はラッパー内部でそのまま呼び出すのみ。
* `Combat/evaluation/online_eval/build_choice_scenarios_manifest.py`(新規):
  teacher2000からChoice Scenario抽出manifest生成。
* `Combat/policy_baseline/baseline_v1_20260724.json`(新規): 統合作業着手前のbaseline記録。

---

## 2. baseline記録・lookup version・ログフィールド(実装前報告からの変更なし)

* baseline: `Combat/policy_baseline/baseline_v1_20260724.json`
* lookup version: `choice_semantics.v1`(ファイルの`table_version`を実行時に動的取得、
  SHA256: `5e7d260f...`)
* ログフィールド: 指示された15項目全てを`choice_semantics.emulator_fact` /
  `choice_semantics.resolved` / `choice_semantics.lookup_provenance` の3分割で実装
  (5節の一覧と完全一致)。

---

## 3. 重要な設計上の発見: ActionContinuation choiceはtrajectories.jsonlに一切現れない

`build_choice_scenarios_manifest.py`の実行前調査で判明: teacher2000の
`trajectories.jsonl`(51,173 decision)には`choice_card`が**43件のみ**存在し、
**全て`decision_index=0`**(StartOfCombat scope)だった。HOLOGRAM/SURVIVOR/
BURNING_PACT/ARMAMENTS/discard/exhaust/upgrade/NIGHTMARE等のActionContinuation
scope choiceは、`BattleEmulator.apply_action()`内の継続ループで**単一のenv.step()
呼び出し内に吸収され、独立したdecision行として一切記録されない**構造だった
(既存パイプライン・本評価harness共通の挙動)。

このため、当初想定していた「trajectories.jsonlをgrepしてChoice Scenarioを探す」
方式ではHOLOGRAM等の優先カテゴリを一切捕捉できないことが判明。対応として、
`env.step()`の`continuation_resolver`をロギング専用ラッパーで包む方式を追加実装した
(1節)。実測(4節)でも、134件のchoice-log entry中**128件(95.5%)がaction_continuation
経由**、topレベルdecisionとして直接見えたのはわずか6件——この追加実装なしでは
Choice Semantics検証のほとんどが不可能だったことが数値でも裏付けられた。

---

## 4. Stage A実行結果(20 Scenario)

manifest: `choice_scenarios_stage_a_manifest.jsonl`
(teacher2000全体の候補プール1,261件から8カテゴリ全てを含むよう選定、
SHA256: `9e920b30b7a759e5f9e7a26e86359981f6a8a492ca67359074e034281a540096`)

### 4.1 基本結果

| | Policy | Heuristic |
|---|---|---|
| victory | 16/20 | 16/20 |
| 正常完走率 | 100% | 100% |
| illegal action | 0 | 0 |
| exception | 0 | 0 |
| action mapping mismatch | 0 | 0 |
| 平均decision数 | 33.85 | 34.85 |
| 平均戦闘時間 | 4.91s | 10.77s |

### 4.2 Choice Semantics集計(134 choice decisions、20 Scenario合計)

| 指標 | 値 |
|---|---|
| Choice decision数 | **134**(topレベル6 + action_continuation 128) |
| pendingChoice取得率 | **100%**(134/134) |
| origin取得率 | 73.1%(98/134) |
| operation既知率(Emulator側) | 32.1%(43/134) |
| **normalized率** | 53.0%(71/134) |
| **passthrough率** | 9.7%(13/134) |
| **unknown率** | 37.3%(50/134) |
| ambiguity率 | 0%(0件、real tableに重複keyなし) |
| semantic mismatch | **0件**(lookup解決61件をtable evidenceと突合、全件一致) |
| exception | **0件** |
| determinism | **完全一致**(134/134 entry、再実行と1件残らず同一) |
| 統合前後の行動一致 | **完全一致**(20 Scenario×2arm、outcome/HP/decision数/選択行動列すべて同一) |

`lookupStatus`内訳: `resolved_lookup` 41 / `resolved_emulator_fact_confirmed_by_lookup` 20 /
`resolved_emulator_fact`(tableに個別行なし、Tier1のみ) 23 / `no_origin` 36 / `miss` 14。

`passthrough`内訳: GUARDS(transform_to_specific_card) 6、SCULPTING_STRIKE
(apply_effect_in_place) 5、SNAP(apply_effect_in_place) 2。

---

## 5. 未解決Choiceの一覧(entity・field欠落・曖昧rule別)

### 5.1 field欠落 — `GamblingChipDiscard`(36件、lookupStatus=no_origin)

`choiceType`が`GamblingChipDiscard`という、`choice_semantics_lookup.v1.json`に
**一切収録されていない**choiceType。`originEntityType`/`originEntityId`が両方null
(`sourceZone`は"hand"が30件、null が6件)。既存の`CardSelectCmd.From*`
call site censusの対象外である可能性が高い(Gambling Chip関連の専用実装と推測されるが、
**推測で補完せず**未解決として記録)。Choice Semantics担当・Emulator担当への
確認依頼候補。

### 5.2 entity型名不一致(field不一致) — potion起因choice 5種(14件、lookupStatus=miss)

`originEntityType`が仕様(`choice_semantics_schema.json`のenum: `card`/`relic`/
`power`/`potion`/`monster`)の**小文字`"potion"`ではなく、C#クラス名
(PascalCase)で返っている**ことを確認:

| originEntityId | 実際のoriginEntityType | table収録有無 |
|---|---|---|
| COLORLESS_POTION | `ColorlessPotion` | 収録あり(`origin_entity_type: "potion"`) |
| SKILL_POTION | `SkillPotion` | 収録あり |
| POWER_POTION | `PowerPotion` | 収録あり |
| ATTACK_POTION | `AttackPotion` | 収録あり |
| TOUCH_OF_INSANITY | `TouchOfInsanity` | 収録あり |

いずれもlookup table自体には該当entityが**正しく収録されている**が、
`origin_entity_type`の文字列表現が一致しないため機械的にmissする。cardの
`originEntityType`は一貫して`"card"`(小文字)であり、potionのみ異なる挙動——
系統的な型名不一致であり、個別entityの欠落ではない。**コード側でPascalCase→
小文字への変換を推測で追加することはせず**(禁止事項)、未解決miss として記録した
うえで本報告に明記する。lookup table側の`origin_entity_type`許容値拡張
(`ColorlessPotion`等をpotionの別名として登録)か、Emulator側でのcard同様の
小文字統一が必要——判断はChoice Semantics/Emulator担当に委ねる。

### 5.3 曖昧rule — 0件

実データでは0件(ambiguous_rateも0%)。`test_ambiguous_rule`により、実際に重複keyが
発生した場合の検出ロジック自体は検証済み。

---

## 6. 検証(直接テスト、6節指示分)

`Combat/tests/test_choice_semantics.py`、**12/12 合格**:

| テスト | 内容 | 結果 |
|---|---|---|
| test_survivor_discard | Tier1(Emulator fact)、discard | PASS |
| test_burning_pact_exhaust_prompt_confirmed | Tier1、exhaust(prompt-key解決) | PASS |
| test_armaments_upgrade | Tier1、upgrade | PASS |
| test_hologram_tier2_lookup | Tier2(lookup)、retrieve_to_hand、当初spec例 | PASS |
| test_headbutt_tier2_lookup | Tier2、retrieve_to_draw_pile_top | PASS |
| test_guards_passthrough | passthrough分類(実DLL) | PASS |
| test_nested_choice_reattributes_origin_and_classification | nested choice、
  origin再帰属+分類切替(DECISIONS_DECISIONS→BURNING_PACT) | PASS |
| test_lookup_miss | 純dict単体、miss | PASS |
| test_no_origin | 純dict単体、no_origin | PASS |
| test_ambiguous_rule | 一時ファイルで重複key構成、ambiguous_match検出 | PASS |
| test_corrupted_lookup_falls_back_cleanly | 破損/欠落lookup、resolve()が例外を投げず
  unknown/load_errorへ縮退 | PASS |
| test_real_table_has_no_duplicate_keys | 実tableの重複keyなしを確認 | PASS |

---

## 7. 禁止事項の遵守状況

* Emulator/Trainingは無変更(直接テストはread-onlyでEmulatorを起動するのみ)。
* Azure未使用(local `0d16130`のみ)。
* 通常Policyの行動ロジック無変更(4節「統合前後の行動一致」で実測確認)。
* Choice fallbackは無変更で維持(`continuation_resolver`ラッパーは実際の選択ロジックを
  そのまま呼び出すのみ)。
* Valueは行動選択に不使用(既存仕様のまま)。
* unknownは推測で補完せず(5節、GamblingChipDiscard/potion型名不一致とも未解決のまま記録)。
* 旧teacher2000全体は再生成せず(Stage A 20 Scenarioのみ実行)。

---

## 8. 50 Scenario(Stage B)へ進めるか

**進めることを推奨する。** Stage A時点で:

* illegal/exception/mismatch: 全て0
* 統合前後の行動一致: 完全一致(アーキテクチャ上の不変条件を実測でも確認)
* 決定論性: 完全一致
* semantic mismatch: 0件
* 未解決2件(GamblingChipDiscard、potion型名不一致)は明確に切り分け済みで、
  Stage Bでも同じ形で追跡可能(件数増加によりGamblingChipDiscard以外の新規
  未収録choiceTypeが出現するかを確認できる)

`choice_scenarios_stage_b_manifest.jsonl`(50件、Stage Aの20件を含む入れ子構成、
8カテゴリ全て含む)は生成済み。コード変更は不要。
