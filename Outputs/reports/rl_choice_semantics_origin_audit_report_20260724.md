# RL報告: Choice Semantics固定・origin依存監査 — 2026-07-24

Choice Policy用データ生成へ進む**前**の、現行版(baseline v1)に対するorigin依存
リスク監査。**Scenario再実行・ルール変更なし**。保存済みStage Bログ
(`choice_log.jsonl`、275 choice decision)を`emulator_fact`から現行
`ChoiceSemanticsTable.resolve()`で再解決し、静的分類と反実仮想再解決の
両方で検証した。

---

## 0. 結論

* baseline v1として現行版を固定・記録した(1節)。
* 275 choice decision全件を7分類の`resolutionPath`へ分類(2節)。
* **Safe 144件(52.4%) / Origin-dependent 121件(44.0%) / Suspect 10件(3.6%)**。
* 反実仮想再解決(null origin)は静的分類と**完全に整合**——origin-dependentと
  分類した121件が、originをnullにすると**過不足なく全件**意味論的に変化した
  (4節、重要な内部整合性チェック)。
* **学習適格265件(96.4%)、除外10件(3.6%、全てGamblingChipDiscardの
  suspected_context_leak)**。
* Choice Policy用データ生成へは**まだ進んでいない**。

---

## 1. Choice Semantics baseline v1の固定

`Combat/policy_baseline/choice_semantics_baseline_v1_20260724.json`に記録
(既存ファイルは無変更、参照のみ):

| 項目 | 値 |
|---|---|
| RL commit | N/A(`C:\STS2_RL`はgitリポジトリではない) |
| `choice_semantics.py` SHA256 | `48912bdb01cf9a56d8af7e1cc09246c4428be1de6bdb1c374927bd5308f18e6d` |
| Choice Semantics lookup version/SHA256 | `choice_semantics.v1` / `5e7d260f...8d44` |
| origin type alias lookup version/SHA256 | `choice_semantics_origin_type_aliases.v1` / `a456f0a2...3814`(7件) |
| テスト | `test_choice_semantics.py` 20/20合格 |
| 関連報告書 | Stage A・Stage B・rule update report(3件、パスは baseline JSON参照) |
| Stage B保存済み`choice_log.jsonl` SHA256 | `81bb3c75...8a7cd`(275行) |
| Stage B保存済み`combats.jsonl` SHA256 | `8ca9747f...ee3f1bf` |

---

## 2. 275 Choiceの解決経路監査

`Combat/evaluation/online_eval/choice_semantics_resolution_path_audit.jsonl`
(275行、指示された11項目全てを保存)。

### resolutionPath別件数(7分類)

| resolutionPath | 件数 | 判定基準 |
|---|---|---|
| `emulator_choice_operation` | 53 | Emulatorの具体的choiceOperationのみで解決(lookup裏付けなし) |
| `combined_rule` | 55 | Emulatorの具体的choiceOperation + lookup行による裏付け一致 |
| `dedicated_choice_type_rule` | 46 | GamblingChipDiscard専用ルール |
| `origin_entity_rule` | 81 | origin lookup(raw origin typeがそのままcanonical、alias不要) |
| `origin_type_alias` | 25 | origin lookup(alias変換が必要、Potion類) |
| `passthrough_rule` | 15 | origin lookupの結果がpassthrough分類(GUARDS/SNAP/SCULPTING_STRIKE等) |
| `unknown` | 0 | 未解決 |

合計275件、`unknown`は0件(rule更新報告4節の再解決結果と整合)。

---

## 3. origin依存リスク分類

| リスク区分 | 件数 | 率 | 内訳 |
|---|---|---|---|
| **Safe** | 144 | 52.4% | `emulator_choice_operation`(53) + `combined_rule`(55) + `dedicated_choice_type_rule`のうち非suspect分(36) |
| **Origin-dependent** | 121 | 44.0% | `origin_entity_rule`(81) + `origin_type_alias`(25) + `passthrough_rule`(15)、全件 |
| **Suspect** | 10 | 3.6% | `dedicated_choice_type_rule`のうち`originValidationStatus=suspected_context_leak`(10件) |

### Suspect 10件の内訳

| 漏洩元entity | 件数 | 対象choiceType |
|---|---|---|
| `card:GUARDS` | 6 | GamblingChipDiscard(1 Step内で6連続、rule update報告2.2節の実例) |
| `potion:POWER_POTION` | 2 | GamblingChipDiscard |
| `potion:SKILL_POTION` | 2 | GamblingChipDiscard |

3 Scenario(`7551-16`, `7413-9`, `1934-19`)に分布。**suspect判定基準
「originValidationStatus=suspected_context_leak」のみが実際に発火**——
「異なるchoiceType間でorigin共有」「1 Step内で不自然に固定」の追加検知
(GUARDS漏洩6件は実質この基準にも該当するが、既に
`suspected_context_leak`で捕捉済みのため二重計上していない)、
「sourceZone矛盾」はorigin-dependent 121件中0件で発火(lookup行の
`source_zone`と実観測値に矛盾なし——rule update報告のsemantic mismatch
0件という結果と整合)。

**Suspect行は学習適格から除外した**(5節)。

---

## 4. 反実仮想再解決

`Combat/evaluation/online_eval/choice_semantics_counterfactual_audit.jsonl`
(275行)。raw originを次の3パターンへ置換し、`operationMode` /
`normalizedChoiceOperation` / `exceptionEntityKey` / `matchedRuleId`
の4項目で比較(Scenario再実行なし、保存済み`emulator_fact`のみ使用)。

| 置換パターン | 対象件数 | 4項目のいずれかが変化 | 意味論(3項目、matchedRuleId除く)が変化 |
|---|---|---|---|
| origin → null | 275 | 176(64.0%) | **121(44.0%)** |
| origin → 直前Choiceのorigin | 185(直前行が存在する場合のみ) | 81 | 39 |
| origin → 無関係な既知entity(`card:HOLOGRAM`固定) | 275 | 220(80.0%) | 80(29.1%) |

### 重要な整合性チェック: originをnullにした場合の意味論的変化件数(121件)は、
### 3節のOrigin-dependent件数(121件)と完全一致した

静的分類(resolutionPathベース)と反実仮想再解決(実際にoriginを変えて
再計算)という独立した2つの手法が**完全に一致**——Origin-dependentと分類した
行は例外なく全てorigin nullで意味論が変わり、Safe/Suspectと分類した行は
1件も変わらなかった。監査ロジック自体の内部整合性を裏付ける。

4項目比較(176件)と3項目比較(121件)の差55件は、`combined_rule`の55件と
完全一致——これらの行はorigin無効化で`matchedRuleId`(裏付け情報)のみ
失うが、実際の意味判定(`operationMode`/`normalizedChoiceOperation`)は
Emulatorの具定値のまま変化しない(3節のSafe判定根拠そのもの)。

### 「無関係entity」置換の解釈上の注意

固定した代替entity(`card:HOLOGRAM`、`normalized_choice_operation=
retrieve_to_hand`)を使った結果、意味論的変化が80件(121件のOrigin-dependent
行のうち)にとどまった。原因を確認したところ、**Stage Bデータ自体に
`retrieve_to_hand`へ正規化される行が41件存在し**、これらはHOLOGRAM置換後も
偶然「同じ正規化操作」になるため差分として検出されない(121-80=41で
完全一致)。これは監査手法上の限界であり、**「無関係entity」置換は
origin依存性の下限値**として扱うべきである——origin nullによる検証
(4節冒頭表)の方がorigin依存性の有無を厳密に検出できる。

---

## 5. 学習適格・除外集計

| 区分 | 件数 | 率 |
|---|---|---|
| 学習適格(Safe + Origin-dependent) | 265 | 96.4% |
| 学習除外(Suspect) | 10 | 3.6% |

除外理由: 全10件が`origin_validation_suspected_context_leak`
(3節のGamblingChipDiscard origin漏洩)。

---

## 6. 禁止事項の遵守状況

* Emulator/Trainingは無変更。
* lookup rule(`choice_semantics_lookup.v1.json`、
  `choice_semantics_origin_type_aliases.v1.json`)は追加・変更していない
  (監査は既存ルールを読み取り専用で使用したのみ)。
* suspect origin(GUARDS/POWER_POTION/SKILL_POTION漏洩)は推測で修復して
  いない——`originValidationStatus`として記録し学習対象から除外したのみ。
* Scenarioは再生成・再実行していない(保存済み`emulator_fact`から
  オフライン再計算のみ)。
* オンライン行動(`policy_agent.py`/`online_policy_eval.py`の行動選択)は
  本作業で一切変更していない。

---

## 7. 出力ファイル一覧

* `Combat/policy_baseline/choice_semantics_baseline_v1_20260724.json`(baseline記録)
* `Combat/evaluation/online_eval/audit_choice_semantics_origin_dependency.py`(監査スクリプト、新規)
* `Combat/evaluation/online_eval/choice_semantics_resolution_path_audit.jsonl`(275行、2節)
* `Combat/evaluation/online_eval/choice_semantics_counterfactual_audit.jsonl`(275行、4節)
* `Combat/evaluation/online_eval/choice_semantics_origin_audit_summary.json`(集計値)

停止する。Choice Policy用データ生成には進んでいない。
