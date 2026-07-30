# RL報告: Gambling Chip・Potion Choice Semantics ルール追加 — 2026-07-24

Stage Bの2つの未解決事項に対するルール実装、テスト、保存済みStage Bログへの
オフライン再解決差分報告。**Scenarioは再実行していない**(保存済み
`choice_log.jsonl`の`emulator_fact`をそのまま新ルールへ再投入)。

---

## 0. 訂正: Stage A/B報告の「Potion 11種確認」は誤りだった

実装前にconfirmed件数を再検証したところ、Stage A報告(3節)の「11種のPotionを
確認」は**二重カウントによる誤り**だったことが判明した。probeで追加確認した6種
(COLORLESS_POTION/SKILL_POTION/POWER_POTION/ATTACK_POTION/TOUCH_OF_INSANITY/
ASHWATER)のうち5種は、Stage Aで既に確認済みの5種と**同一**であり、新規は
ASHWATER 1種のみ。Stage A・Bの実データ全体(`choice_log.jsonl`)を再走査し、
実際にlive観測で確認できた一意なPotion由来クラスは**7種**
(ASHWATER/ATTACK_POTION/COLORLESS_POTION/**GAMBLERS_BREW**/POWER_POTION/
SKILL_POTION/TOUCH_OF_INSANITY——`GAMBLERS_BREW`はTier1(Emulator fact)で
最初から解決できていたため`miss`として顕在化せず、これまでの報告で見落として
いた)。**lookupへは、報告していた11種ではなく、実証済みの7種のみを登録した。**

---

## 1. 実装内容

### 1.1 Gambling Chip専用ルール(`choice_semantics.py::CHOICE_TYPE_RULES`)

```text
choiceType == "GamblingChipDiscard"
  → operationMode = "passthrough"
  → exceptionEntityKey = "relic:GAMBLING_CHIP"
  → semanticSource = "hardcoded_entity_rule"
```

origin(`originEntityType`/`originEntityId`)は一致条件に一切使用しない
(choiceTypeのみで判定)。raw originはそのまま`emulator_fact`へ保持し、別途
`originValidationStatus`で状態を記録:

```text
valid                 : origin == (relic, GAMBLING_CHIP)
missing                : origin が両方null
suspected_context_leak : 上記いずれでもない(無関係なentityのorigin)
```

origin異常があっても`operationMode`/`exceptionEntityKey`は変更されない
(2.1節の再解決結果で確認)。

### 1.2 Potion origin type正規化(`choice_semantics.py::_normalize_origin_type` +
新規`Common/schemas/choice_semantics_origin_type_aliases.v1.json`)

Choice operationの正規化とは別処理として実装。既存の`choice_semantics_lookup.v1.json`
/`choice_semantics_schema.json`は**無変更**、新規ファイルのみ追加。

```text
rawOriginEntityType = "ColorlessPotion"  (常にそのまま保持)
normalizedOriginEntityType = "potion"    (新規フィールド、登録済み7種のみ)
```

未登録の`*Potion`風クラスは文字列末尾だけで自動分類せず、
`normalizedOriginEntityType = "unknown"`となる(5節のテストで確認)。
`originEntityId`はEmulatorの値をそのまま使用(変更なし)。

`normalizedOriginEntityType`はTier3(既存operation lookup)のkey構築にも使われ、
これによりPotion起因choiceが正しくlookup行へ到達できるようになった
(2.1節)。card/relic/power/monsterは元々正しい小文字値を返すため、
このtypeは常に恒等写像(変更なし)。

### 1.3 優先順位の更新

```text
1. CHOICE_TYPE_RULES(choiceTypeキー、現状GamblingChipDiscardのみ)
2. Emulatorの具体的choiceOperation
3. origin entity lookup(normalizedOriginEntityTypeでkey構築)
4. unknown
```

GamblingChipDiscardは常に1が最優先(Emulatorが将来concrete operationを返す
ようになっても上書きされないことをテストで確認、5節)。

---

## 2. オフライン再解決差分(Stage B保存済み275 choice decision、Scenario再実行なし)

### 2.1 確認項目(指示4節)

| 確認項目 | 結果 |
|---|---|
| GamblingChipDiscard全件がpassthroughになる | **達成**(46/46件、`operationMode=passthrough`) |
| exceptionEntityKey = relic:GAMBLING_CHIP | **達成**(46/46件) |
| 通常discardへ分類される件数 | **0件**(指示通り) |
| 確認済みPotion(7種、0節参照)が normalizedOriginEntityType=potion | **達成**(43/43件) |
| raw origin値が変更されていない | **達成**(275/275件で`emulator_fact`完全一致) |
| semantic mismatch | **0件**(新解決結果をtable evidenceと再突合) |
| ambiguous match | **0件** |
| 通常Choiceの既存解決結果に変更なし | **達成**(GamblingChipDiscard・確認済みPotion以外の
  全行で`resolved`が新旧完全一致——重要フィールドのみでなく全項目を比較) |
| オンライン行動コードに変更なし | **達成**(`policy_agent.py`・`online_policy_eval.py`の
  行動選択部分は本作業で一切変更していない) |

### 2.2 重要な副次的発見: GamblingChipDiscard件数が指示時点の40件→46件に修正された

Stage B報告時点では`lookupStatus`が`no_origin`(36)+`miss`(4)の**40件**のみを
GamblingChipDiscardとして把握していた。しかし今回の再解決で、**残り6件が
choiceType未確認の旧ロジックにより`card:GUARDS`のpassthrough判定へ
誤って吸収されていた**ことが判明した(`1934-19/heuristic`decision24、1 Step内で
6連続発生、Stage B報告5.4節の「最大連続choice数6件」の実体はこれだった)。

これはStage B時点のorigin漏洩がPotionだけでなく**任意の直前entity(この場合は
card GUARDS)にも及ぶ**ことを意味し、GamblingChipDiscardを`choiceType`単独で
判定する今回の設計(originを一致条件に使わない)がまさに必要だったことを
裏付ける実例。旧ロジックでは「GamblingChipDiscardの選択が実はGUARDSの
transform_to_specific_cardである」という**誤った意味付け**が6件分ログに
残っていたが、新ロジックでは正しく`GamblingChipDiscard`(passthrough、
`exceptionEntityKey=relic:GAMBLING_CHIP`)として再分類される。

修正後の内訳: `originValidationStatus` = `missing` 36件、
`suspected_context_leak` 10件(Potion漏洩4件 + card:GUARDS漏洩6件)。

### 2.3 全体の指標変化(275 choice decision)

| 指標 | 旧 | 新 |
|---|---|---|
| normalized率 | 68.7% | **77.8%** |
| passthrough率 | 5.5% | **22.2%** |
| unknown率 | 25.8% | **0.0%** |
| miss件数 | 35 | **0** |
| no_origin件数 | 36 | **0**(全てGamblingChipDiscard ruleへ吸収) |
| ambiguous_match件数 | 0 | 0 |

`lookupStatus`内訳(新): `resolved_lookup` 121、
`resolved_emulator_fact_confirmed_by_lookup` 55、`resolved_choice_type_rule` 46、
`resolved_emulator_fact` 53(合計275)。

Stage Bで確認されていた35件の`miss`のうち29件(Potion由来`Unsupported`)は
1.2節の正規化により解決、残り6件(`GamblingChipDiscard`+`ToolboxChooseCard`の
一部)は1.1節のルールまたは正規化により解決。**unknown率がStage Bの25.8%から
0.0%まで低下した。**

---

## 3. テスト結果

`Combat/tests/test_choice_semantics.py`: 既存12件 + 新規8件 = **20/20 合格**。

新規8件:

| テスト | 内容 |
|---|---|
| test_gambling_chip_discard_origin_null | GamblingChipDiscard + origin null → passthrough、originValidationStatus=missing |
| test_gambling_chip_discard_correct_relic_origin | GamblingChipDiscard + 正しいrelic origin → passthrough、originValidationStatus=valid |
| test_gambling_chip_discard_leaked_potion_origin | GamblingChipDiscard + 無関係Potion origin → passthrough(不変)、originValidationStatus=suspected_context_leak、discardへの誤分類なし |
| test_gambling_chip_rule_overrides_concrete_emulator_operation | choiceType ruleがEmulatorの具体的operationより優先されることを確認 |
| test_confirmed_potion_origin_type_normalization | 確認済み7種が`potion`へ正規化、COLORLESS_POTION/TOUCH_OF_INSANITYの実際のlookup解決を確認 |
| test_unregistered_potion_like_class_not_auto_classified | 未登録の`SomeBrandNewPotion`風クラスが自動分類されず`unknown`のまま |
| test_raw_origin_always_preserved | 4パターン(leak/通常/potion/no origin)でraw値が常に保持されることを確認 |
| test_corrupted_origin_aliases_falls_back_cleanly | origin-type-aliasesファイル破損時も主lookup・GamblingChipDiscardルールは独立して機能継続 |

---

## 4. 禁止事項の遵守状況

* Emulator/Trainingは無変更。
* Gambling Chipは通常discardへ統合していない(`normalizedChoiceOperation`は
  常に`None`、`operationMode=passthrough`固定)。
* raw originは一切上書きしていない(2.1節で275件全て確認)。
* origin異常(2.2節のcard:GUARDS漏洩含む)を推測で修復していない——
  `originValidationStatus`として記録するのみ。
* Choice Semanticsはオンライン行動を変更していない(`policy_agent.py`/
  `online_policy_eval.py`の行動選択コードは本作業で無変更)。
* Azure未使用。

---

## 5. 変更ファイル一覧

* `Common/schemas/choice_semantics_origin_type_aliases.v1.json`(新規)
* `Combat/choice_semantics.py`(拡張: `CHOICE_TYPE_RULES`、
  `ORIGIN_TYPE_ALIASES_PATH`読み込み、`_normalize_origin_type()`、
  `resolve()`優先順位更新、`_resolved()`へ`normalizedOriginEntityType`/
  `originValidationStatus`追加)
* `Combat/tests/test_choice_semantics.py`(テスト8件追加、20/20合格)
* 既存の`choice_semantics_schema.json`・`choice_semantics_lookup.v1.json`は
  **無変更**

停止する。追加のScenario実行・ルール変更は行っていない。
