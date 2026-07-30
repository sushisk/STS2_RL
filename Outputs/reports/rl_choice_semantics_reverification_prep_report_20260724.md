# RL報告: Emulator調査完了までの待機・再検証準備 — 2026-07-24

Choice Policy用データ生成は、Emulator担当のorigin漏洩調査・修正判断が完了するまで
**開始していない**。本報告は待機中に許可された準備作業(現在結果の固定、
再検証manifest、比較手順)の完了報告。

---

## 0. 結論

* 前回監査結果を**provisional**として固定・記録した(1節)。
* 全カテゴリを網羅した再検証manifest(41 Scenario)を構築した(2節)。
* Emulator修正後との比較に使う「BEFORE(現行版)」スナップショットを取得した——
  比較には最低2時点のデータが必要なため、現行版での実行結果を先に固定する
  ことは「手順の準備」に含まれると判断した(3節)。**Emulator側の修正結果を
  推測・シミュレートしていない**——修正後のAFTER実行はEmulator担当の成果物
  到着後にのみ行う。
* 比較スクリプトを実装し、自己比較(同一runをbefore/afterに指定)でゼロ差分に
  なることを確認した(4節)。
* Choice Policy用データ生成へは進んでいない。

---

## 1. 現在結果の固定(provisional)

`Combat/policy_baseline/choice_semantics_provisional_status_v1_20260724.json`
に記録(既存ファイルは無変更、参照のみ):

| 項目 | 値 |
|---|---|
| Choice Semantics baseline | v1(`choice_semantics_baseline_v1_20260724.json`参照) |
| 275 Choiceの監査結果 | Safe 144 / Origin-dependent 121 / Suspect 10 |
| **暫定学習適格(`provisional_eligible`)** | **265件**(Safe+Origin-dependent。
  「`training_eligible`」ではなく明示的に`provisional_eligible`とラベル付けし、
  正式学習データではないことを明記) |
| 暫定除外 | 10件(全て`suspected_context_leak`) |
| origin null置換の反実仮想結果 | 意味論変化121件——Origin-dependent件数(121)と
  完全一致(内部整合性チェック済み、前回報告4節) |

**265件は正式な学習データとして扱っていない。**

---

## 2. 再検証用manifest(41 Scenario、重複なし)

`Combat/evaluation/online_eval/choice_semantics_reverification_manifest.jsonl`
(新規、コード・lookup変更なし)。

| カテゴリ | 件数 |
|---|---|
| Origin-dependentを含む全Scenario | 33(全件) |
| Suspectが発生した3 Scenario | 3(`1934-19`, `7413-9`, `7551-16`、上記に一部重複) |
| Safe代表Scenario | 5(重複除外後の残りプールから決定的サンプリング、seed=20260724) |
| GamblingChipDiscard | 3(Suspect 3件と同一) |
| GUARDS後の連続Choice | 1(`1934-19`) |
| POWER_POTION後のChoice | 2(`5709-8`, `7551-16`) |
| SKILL_POTION後のChoice | 2(`4352-3`, `7413-9`) |
| 1 Step内で複数Choice | 12(全てOrigin-dependent/Suspect集合に包含済み) |
| Card/Potion/Relic起因Choice | Card 39・Potion 16・Relic 3(TOOLBOX、いずれも
  Origin-dependent集合に包含済み) |
| **nested Choice** | **1(合成Scenario、下記参照)** |

重複Scenarioはtrajectory_id単位で除外済み(和集合構築、`Combat/evaluation/
online_eval/build_reverification_manifest.py`)。

### nested Choiceについての重要な注記

Stage A・B累計275 Choice全件を走査したが、**同一Step内でoriginが異なる
実entityへ切り替わる真の「入れ子choice」(例: DECISIONS_DECISIONS選択後に
BURNING_PACTが自動再生されorigin再帰属する事例)はteacher2000由来の実データに
一件も存在しなかった**。このため、`Combat/tests/test_choice_semantics.py::
test_nested_choice_reattributes_origin_and_classification`と同一の手作り
Scenario(REGENT、DECISIONS_DECISIONS+BURNING_PACT)を**合成Scenarioとして
明示的にタグ付けして追加**した(`synthetic: true`)。teacher2000由来ではない
ことをmanifest上で明記している。

---

## 3. BEFORE(現行版)スナップショットの取得

再検証には最低2時点(修正前後)のデータが要る。比較を実際に行えるようにする
準備として、**現行版(修正前)Emulatorでの実行結果を先に固定**した——
Emulator側の結果を推測・シミュレートしたものではなく、実際にlocal `0d16130`
(現行版)で41 Scenario全件を実行した結果である。

出力: `Combat/evaluation/reports/choice_reverification_BEFORE_20260724/`

| 指標 | 結果 |
|---|---|
| 実行Scenario数 | 41/41(quarantined 0) |
| illegal action | 0 |
| exception | 0 |
| action mapping mismatch | 0 |
| unknown率 | 0.0%(254 choice decision中) |
| normalized/passthrough | 192 / 62 |
| emulator_commit記録 | `0d1613050e3d6396004d328ba77f72177b6e872d` |

合成nested Choice Scenarioも実際に実行し、期待通り
`DECISIONS_DECISIONS`(passthrough, select_to_replay)→
`BURNING_PACT`(normalized, exhaust、origin再帰属)が観測された
(両arm、7 choice decision)。

このBEFOREスナップショットは、Emulator担当のAAFTER版データが揃うまで
**このまま保持**する(上書きしない)。

---

## 4. 比較スクリプト(準備のみ、未実行の本比較)

`Combat/evaluation/online_eval/compare_choice_semantics_reverification.py`
(新規)。

```text
python compare_choice_semantics_reverification.py --before <BEFORE_dir> --after <AFTER_dir>
```

比較項目(指示3節)を全て実装済み:

* 最終行動(`final_action_diffs`)
* 最終状態(`final_state_diffs`)
* Choice decision数と順序(`choice_count_or_order_diffs`)
* raw originEntityType/originEntityId、resolutionPath、operationMode、
  normalizedChoiceOperation、exceptionEntityKey、originValidationStatus
  (`choice_field_diff_counts`/`choice_field_diff_examples`)
* origin漏洩の解消状況(`origin_leak_resolved_count` /
  `origin_leak_degraded_to_missing_count` / `origin_leak_still_leaking_count`)
* 採用条件チェックリストを出力末尾に付与(4節の8項目、自動判定はせず
  比較結果を見て手動確認する設計——determinism・semantic mismatchの一部は
  別途afterディレクトリ単体での追加検証が必要なことも明記)

**自己比較テスト**(同一run同士をbefore/afterに指定)で全差分項目がゼロに
なることを確認済み(`choice_field_diff_counts: {}`, `final_action_diffs: []`等)。
これによりスクリプト自体の正しさを検証した。

**Emulator担当の修正版に対する実際の比較はまだ実行していない**——
実行にはEmulator担当から提示される修正済みcommit・DLL hashが必要。

---

## 5. 次のアクション(Emulator担当の結果待ち)

Emulator担当から修正済みcommit・DLL hashが提示され次第:

1. 同一manifest(`choice_semantics_reverification_manifest.jsonl`)を
   修正版Emulatorで実行し、`choice_reverification_AFTER_<date>/`へ保存。
2. `compare_choice_semantics_reverification.py --before
   choice_reverification_BEFORE_20260724 --after choice_reverification_AFTER_<date>`
   を実行。
3. 4節の採用条件8項目を確認。
4. 満たせば: 修正版Emulatorをbaseline固定 → Choice Scenario再実行 →
   正式な学習適格性再判定 → Training用export設計(指示書5節「その後の分岐」)。
   満たさなければ: Choiceデータ生成へ進まず、差分と未解決Scenarioを報告。

---

## 6. 禁止事項の遵守状況

* Emulator/Trainingは無変更。
* lookup/schema/正規化ruleは無変更(監査・manifest構築・比較スクリプトは
  全て既存ルールを読み取り専用で使用)。
* Suspect origin(GUARDS/POWER_POTION/SKILL_POTION漏洩)は推測で修復していない。
* 現在の265件(`provisional_eligible`)は正式学習データとして扱っていない。
* Azure未使用。
* Choice Policy用データ生成・Training開始は行っていない。

---

## 7. 出力ファイル一覧

* `Combat/policy_baseline/choice_semantics_provisional_status_v1_20260724.json`
* `Combat/evaluation/online_eval/build_reverification_manifest.py`(新規)
* `Combat/evaluation/online_eval/choice_semantics_reverification_manifest.jsonl`(41件)
* `Combat/evaluation/online_eval/compare_choice_semantics_reverification.py`(新規)
* `Combat/evaluation/reports/choice_reverification_BEFORE_20260724/`
  (`summary.json`, `combats.jsonl`, `choice_log.jsonl`等)

停止する。Choice Policy用データ生成には進んでいない。Emulator担当の
調査結果を待つ。
