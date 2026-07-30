# RL報告: Emulator `722b019` AFTER再検証・採用判断 — 2026-07-24

固定済み41 Scenario manifestを使い、Emulator修正版(`722b019`)のAFTERスナップショットを
取得しBEFOREと比較した。**全採用条件を満たした。**

---

## 0. 結論

**`722b019`をChoiceデータ生成用Emulator baseline候補として記録することを推奨する。**

* gate指標(illegal/exception/mismatch/semantic mismatch): 全てBEFORE/AFTERとも0
* Scenario単位の差分(legal action数列・選択action列・最終戦闘状態・戦闘結果): **0件**
* determinism: AFTER同士の再実行で254/254 choice完全一致
* 差分分類: 全13件が`expected_choice_type_fix`、**`unintended_*`/`unresolved`は0件**
* origin依存監査: Suspect **10件→0件**、`provisional_eligible` **237→247件**
* Reset直後Gambling Chip null: 36件、既知の許容事項として全件確認

Choiceデータ生成・Training開始・lookup変更・Azure反映へは**進んでいない**。

---

## 1. BEFORE／AFTERのprovenance

| | BEFORE | AFTER |
|---|---|---|
| Emulator commit | `0d1613050e3d6396004d328ba77f72177b6e872d` | `722b019051e6f7ea368fef488abcc6451d6c9d47` |
| 実行日 | 2026-07-24 | 2026-07-24 |
| 出力先 | `Combat/evaluation/reports/choice_reverification_BEFORE_20260724/` | `Combat/evaluation/reports/choice_reverification_AFTER_722b019/` |

実行前にcommit・DLL hashを確認済み(完全一致):

| | 提示値 | 実測値 | 一致 |
|---|---|---|---|
| commit | `722b019` | `722b019051e6f7ea368fef488abcc6451d6c9d47` | ✓ |
| `Sts2Emulator.dll` SHA256 | `e3c3d26d...44c036` | `e3c3d26d7499e93e89f2718ccb51e18a2d66559021bbb5cdca33980bb644c036` | ✓完全一致 |
| `Sts2Imported.Stage1.dll` SHA256 | `c176109a...5900` | `c176109aa6c9887057c09e02245ebedaaf476fb6e604267d36e787bd3c055900` | ✓完全一致 |

実行条件はBEFOREと完全に揃えた: 同一41 Scenario manifest(`choice_semantics_
reverification_manifest.jsonl`、SHA256固定済み)、同一順序、同一seed(manifest内に
既に固定済み)、同一実行引数(`--choice-semantics --max-decisions 150
--max-wall-seconds 240`)、同一Choice Semantics lookup(`choice_semantics.v1`)・
origin type alias(`choice_semantics_origin_type_aliases.v1`)・RLコード・Heuristic
選択ロジック(いずれも今回の作業で無変更)。manifest・lookup・schemaは無変更。

比較用に新規スクリプト`Combat/evaluation/online_eval/analyze_reverification_722b019.py`
を作成した(既存の`compare_choice_semantics_reverification.py`は無変更のまま維持)。

---

## 2. 41 Scenarioの実行結果

| | BEFORE | AFTER |
|---|---|---|
| 実行Scenario数 | 41/41(quarantined 0) | 41/41(quarantined 0) |
| illegal action | 0 | 0 |
| exception | 0 | 0 |
| action mapping mismatch | 0 | 0 |

**Choice decision総数**: BEFORE 254、AFTER **254**(完全一致、実データ247 + 合成
nested Choice 7)。

---

## 3. 差分件数と分類

Scenario単位・Choice単位で全項目を比較(2節の比較項目リスト全て)。

### Scenario単位

| 比較項目 | 差分件数 |
|---|---|
| 最終戦闘状態(outcome/HP/maxHP/potion数) | **0** |
| 戦闘結果 | **0** |
| 選択action列(全decision) | **0** |
| legal action数列(`legal_action_count`、41 Scenario×2arm) | **0** |
| Choice decision数(Scenario単位) | **0** |

**実データ・合成Scenarioともに差分0**。合成nested Choice Scenario
(`synthetic:nested_choice_decisions_decisions_burning_pact`)も含め、
実際に選ばれた行動・最終状態は一切変化していない——Emulator修正が
Choice意味情報(pendingChoiceのorigin/choiceType)のみに限定され、
実際のゲームメカニクスには影響していないことを裏付ける。

### Choice単位(7分類、実データ247 Choice中)

| 分類 | 件数 |
|---|---|
| `expected_origin_fix` | 0 |
| `expected_choice_type_fix` | **13** |
| `known_reset_gambling_chip_null`(既に正常だった36件は差分としてカウントせず) | 0(差分なし。3.2節で別途確認) |
| `unintended_semantic_change` | **0** |
| `unintended_action_change` | **0** |
| `unintended_state_change` | **0** |
| `unresolved` | **0** |

**`unintended_*`・`unresolved`は0件——採用条件を満たす。**

### 3.1 `expected_choice_type_fix` 13件の内訳と重要な訂正

| origin | 件数 | choiceType変化 |
|---|---|---|
| `card:GUARDS` | 6 | `GamblingChipDiscard` → `Unsupported` |
| `potion:POWER_POTION` | 2 | `GamblingChipDiscard` → `Unsupported` |
| `potion:SKILL_POTION` | 2 | `GamblingChipDiscard` → `Unsupported` |
| `potion:COLORLESS_POTION` | 2 | `ToolboxChooseCard` → `Unsupported` |
| `card:DISCOVERY` | 1 | `ToolboxChooseCard` → `Unsupported` |

前回報告(`rl_choice_semantics_origin_audit_report_20260724.md`)ではこの10件
(GUARDS 6 + POWER_POTION 2 + SKILL_POTION 2)を「origin漏洩」
(`suspected_context_leak`)と分類していたが、**AFTERの結果により根本原因が
判明・訂正された**: raw originは実際には**修正前から常に正しかった**
(GUARDS/POWER_POTION/SKILL_POTION自身の本来のorigin)。真の不具合は
**`pendingChoice.ChoiceType`自体が誤って`GamblingChipDiscard`と報告されていた**
ことで、RL側の`CHOICE_TYPE_RULES`がchoiceType最優先(Tier1)で判定するため、
本来GUARDS/POWER_POTION/SKILL_POTION自身の正しいorigin解決結果を
`passthrough`+`relic:GAMBLING_CHIP`へ強制的に上書きしてしまっていた。
`722b019`でchoiceTypeが正しく`Unsupported`に修正されたことで、
本来のorigin依存解決(GUARDS→`transform_to_specific_card`、
POWER/SKILL_POTION→`add_generated_to_hand`)が正しく機能するようになった。

ToolboxChooseCard側でも同型の3件(未把握だった追加発見)を確認した——
前回報告では`ToolboxChooseCard`の`miss`2件のみ言及していたが、
今回の全件比較で計3件の同型誤分類修正を確認した。

### 3.2 Reset直後Gambling Chip null(既知の許容事項)

AFTERの`GamblingChipDiscard`は**36件、全件が`decision_index=0`・
`originEntityType=None`・`originEntityId=None`・`originValidationStatus=missing`・
`operationMode=passthrough`**(BEFOREの36件から変化なし)。指示4節の通り、
これは修正失敗ではなく既知の許容事項として扱った。RL側の既存ルール
(`choiceType=GamblingChipDiscard → operationMode=passthrough,
exceptionEntityKey=relic:GAMBLING_CHIP`)は無変更のまま正しく機能している。

---

## 4. Suspect 10件の変化

| Scenario | arm | 漏洩origin(BEFORE) | AFTER choiceType | AFTER origin | AFTER originValidationStatus |
|---|---|---|---|---|---|
| `1934-19` | heuristic | `card:GUARDS`×6 | `Unsupported` | `card:GUARDS`(不変・正しい) | `null`(GamblingChipDiscardルール対象外) |
| `7413-9` | policy/heuristic | `potion:SKILL_POTION`×2 | `Unsupported` | `potion:SKILL_POTION`(不変・正しい) | 同上 |
| `7551-16` | policy/heuristic | `potion:POWER_POTION`×2 | `Unsupported` | `potion:POWER_POTION`(不変・正しい) | 同上 |

**10件全てが解消——choiceType修正により、これらはもはやGamblingChipDiscard
ルールの対象外(origin依存の通常解決経路)となり、`suspected_context_leak`
判定自体が発生しなくなった。**

---

## 5. origin依存監査の再集計(AFTER、実データ247 Choice)

| 区分 | BEFORE | AFTER |
|---|---|---|
| Safe | 111 | 111(不変) |
| Origin-dependent | 126 | **136**(+10、旧Suspectが正しい解決経路へ移行) |
| Suspect | **10** | **0** |
| `provisional_eligible` | 237 | **247** |
| `provisional_excluded` | 10 | **0** |

**旧Suspect 10件は0件になった**(指示8節の期待通り)。無関係entity漏洩による
除外も0件。Gambling ChipのReset直後null(36件)は`dedicated_choice_type_rule`
経由でSafe分類のまま(専用choiceTypeで意味が一意に決まるため、指示8節の
方針通り学習適格として扱う)。

---

## 6. 必須確認ケース(指示5節)の結果

| ケース | 結果 |
|---|---|
| Stage BのSuspect 10件 | 全件解消(4節) |
| `card:GUARDS`漏洩6件 | 全件`Unsupported`+正しいorigin(GUARDS自身)へ修正確認 |
| `POWER_POTION`漏洩2件 | 同上 |
| `SKILL_POTION`漏洩2件 | 同上 |
| GamblingChipDiscard全件(46→36) | 36件は全て正当なReset直後null、10件は誤分類修正で他choiceTypeへ |
| SURVIVOR自動確定後のChoice | 24件、BEFORE/AFTERで差分0(discard、安定) |
| DAGGER_THROW自動確定後のChoice | 11件、差分0(discard、安定) |
| GAMBLERS_BREW早期return後のChoice | 8件、差分0(discard、安定) |
| Wish関連(WishDrawToHand) | 12件、差分0(retrieve_to_hand、安定) |
| Toolbox関連(ToolboxChooseCard) | 6件中3件を誤分類として修正確認(3.1節) |
| 1 Step内の連続Choice | 差分0(GUARDS 6連続分は3.1節の修正に包含) |
| synthetic nested Choice | 差分0(DECISIONS_DECISIONS→BURNING_PACT再帰属、両バージョンで同一動作) |
| GAMBLING_CHIP所持中のGUARDS | `1934-19`が該当、6件の修正を確認(3.1節) |
| Choices Paradox関連 | 41 Scenario manifest中に該当Choiceの発生なし(確認対象外、
  発生していれば同型の検証を行う予定だったが今回は出現しなかった) |
| `FromDeckForRemoval`候補0件後のChoice | 該当パターンを個別に切り分け特定できず
  (41 Scenario中に明確な一致ケースを識別できなかった——具体的な回避策付き
  Scenarioの追加が必要な可能性。全体差分は0件のため今回の判定には影響しない) |

---

## 7. 学習適格件数の更新

| | BEFORE(旧集計) | AFTER(今回) |
|---|---|---|
| `provisional_eligible` | 237/247(95.9%) | **247/247(100%)** |
| `provisional_excluded` | 10/247(4.0%) | **0/247(0%)** |

---

## 8. 決定論性

AFTERを同一条件で2回実行し、254 choice decision全件を突合。
**0 mismatch、完全に決定論的。**

---

## 9. `722b019`採用可否

### 採用条件チェック(指示6節、全項目)

| 条件 | 結果 |
|---|---|
| illegal action 0 | ✓ |
| exception 0 | ✓ |
| mapping mismatch 0 | ✓ |
| semantic mismatch 0 | ✓(BEFORE/AFTERとも) |
| legal actions完全一致 | ✓(`legal_action_count`数列で確認、3節) |
| 選択action完全一致 | ✓ |
| 最終戦闘状態完全一致 | ✓ |
| 戦闘結果完全一致 | ✓ |
| determinism維持 | ✓(254/254) |
| 古いoriginの漏洩0 | ✓(Suspect 10→0) |
| 無関係Choiceの専用choiceType誤分類0 | ✓(13件修正、残り全て正当) |
| 本物の専用Choiceは正しく識別 | ✓(Reset直後Gambling Chip 36件、正しくmissing) |
| Safe Choiceに意図しない意味変更なし | ✓(`unintended_semantic_change`0件) |

**全条件を満たした。`722b019`をChoiceデータ生成用Emulator baseline候補として
記録することを推奨する。**

---

## 10. 注記: 「legal actions完全一致」の検証範囲について

既存の`decisions_policy.jsonl`/`decisions_heuristic.jsonl`は`legal_action_count`
(件数)のみを保存しており、`legal_actions`の**内容**(各actionのlabel/parameters等)
までは記録していなかった。今回の比較は件数の数列一致で代替検証している——
`chosen_action`(実際に選ばれたaction、詳細フィールド込み)は全件完全一致して
いるため実質的なリスクは低いと判断するが、より厳密な内容比較が必要な場合は
今後のharness側のログ拡張を検討する余地がある(BEFORE時点のログ形式を
変更すると比較の前提が崩れるため、今回は変更していない)。

---

## 11. 禁止事項の遵守状況

* Emulator/Trainingは無変更(722b019は既にEmulator担当が提示・反映済みのcommit)。
* lookup/schema/正規化ruleは無変更。
* Choiceデータ生成・Training開始は行っていない。
* Azure反映は行っていない。

---

## 12. 出力ファイル一覧

* `Combat/evaluation/online_eval/analyze_reverification_722b019.py`(新規)
* `Combat/evaluation/online_eval/reverification_722b019_analysis.json`(全差分の詳細)
* `Combat/evaluation/reports/choice_reverification_AFTER_722b019/`
* `Combat/evaluation/reports/choice_reverification_AFTER_722b019_determinism/`

停止する。Choiceデータ生成、Training、lookup変更、Azure反映には進んでいない。
`722b019`のbaseline確定判断を待つ。
