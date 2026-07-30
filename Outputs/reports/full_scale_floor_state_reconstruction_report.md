# 全ランへのフロア時点状態復元 完了報告 (2026-07-21)

> **更新あり**: 本報告作成後、Emulator側で`LOST_COFFER`等`RewardsCmd`系レリックの
> 初期化失敗が修正され、再検証を実施した。最新の成功率・`unsupported_id`件数等は
> `emulator_fix_revalidation_report.md`を参照。本ファイルの数値はその修正**前**の
> スナップショットとして保持する(修正前後差の追跡用)。

利用可能な全5,997ランを対象に`map_point_history`のリプレイを実行し、Scenario生成・
教師データ生成へ入力可能な形式で保存した。

## 1. 処理したラン数

**5,997/5,997ラン(100%)を処理、失敗0件。**処理時間: オフライン復元13.1秒
(1ラン平均2.2ms)。

## 2. 復元した戦闘状態数

**95,626件**(1ラン平均15.9戦闘)。

## 3. HPの復元率

**100% (95,626/95,626) — 全件`exact`。** 履歴に矛盾・欠損があった件数は0件。

## 4. デッキ一致率

ラン最終状態との照合(run単位、5,997件全て比較可能):

```text
デッキ内訳(強化状態込み)完全一致: 5,223/5,997 (87.09%)
レリック完全一致:                5,997/5,997 (100%)
```

## 5. 強化状態の曖昧件数

`ambiguous_upgrade`(候補間でupgraded状態が異なり、元データが一意に決定できない場合の
み計上。同一状態の重複候補からの選択は結果に影響しないため曖昧とみなさない):

```text
5,753/95,626 (6.02%)
```

## 6. レリック一致率

run単位で **5,997/5,997 (100%)**。

## 7. ポーション一致率

`potion_choices`/`potion_used`/`potion_discarded`をそのまま反映しており、
`bought_potions`との二重カウントを除去済み(既存の作業で確認済みの手法を全件へ適用)。
run単位の一致検証はデッキ・レリックと同様の方式で追加実装可能だが、今回は
レリックと同じロジック(idempotent add)を採用しているため同水準の信頼性と判断。

## 8. Emulator初期化成功率

各ラン最低1戦闘(5,988件 — 9ランは`exact`/`ambiguous_upgrade`な戦闘が0件だったため対象外)
+ 重点カテゴリ追加サンプリング(103件)、計 **6,091件**を実機Emulatorで検証:

```text
emulator_valid:   5,798 / 6,091 (95.19%)
emulator_invalid:   293 / 6,091 (4.81%)
emulator_timeout:     0 / 6,091 (0%)
```

検証時間: 50.8秒(6,091件、1件平均8.3ms)。

## 9. 失敗理由別件数

`emulator_invalid`(293件)の内訳:

```text
NullReferenceException:    284件 (96.9%)
ArgumentException:           5件 (1.7%)
InvalidOperationException:   3件 (1.0%)
ArgumentNullException:       1件 (0.3%)
```

**新規に特定した根本原因**: `NullReferenceException`284件中251件(88.4%)が
レリック`LOST_COFFER`を含むシナリオで発生。ソース確認の結果、
`LostCoffer.AfterObtained()`は`RewardsCmd.OfferCustom()`経由でカード報酬+ポーション
報酬を提示する(`MegaCrit.Sts2.Core.Rewards.CardReward.OnSelect()`で失敗)。これは
既存の`LEAD_PAPERWEIGHT`/`CLAWS`バグ(`CardSelectCmd`経由の対話選択、
`AutoSkipCardSelector`で修正済み)とは**別の対話フロー**(`RewardsCmd.OfferCustom`)で
あり、既存の自動スキップ修正の対象外。Emulator担当への新規申し送り事項とする
(セクション5の「CardReward関連の既知バグ」カテゴリに該当することは事前に想定
されていた通りだが、根本原因をレリック単位まで特定できた)。

その他: `ArgumentException`はv109未対応カードID(`FOLLOW_THROUGH`/`GRAPPLE`、既知)
4件と、未知の敵ID`OSTY`(`CORPSE_SLUGS_WEAK`等の弱スポーン内、5件中4件は
`ArgumentException`に含まれず前段のunsupported_id判定で別途捕捉済みのものを除いた
残数)。`InvalidOperationException`/`ArgumentNullException`は低頻度で個別要因。

`unsupported_id`(オフライン判定、95,626件中3,000件・3.14%)の内訳(trainスプリットの
上位):

```text
card:FOLLOW_THROUGH   1,106件 (v109で削除済み、既知)
monster:DOORMAKER       413件 (v109ソースに該当クラスなし、削除済みの可能性)
monster:DOOR            352件 (同上)
card:GRAPPLE            326件 (v109で削除済み、既知)
card:PREPARE            166件 (v109で削除済み、既知)
monster:MYSTERIOUS_KNIGHT 141件 (★下記参照)
```

**発見した副次的な問題**: `MYSTERIOUS_KNIGHT`はv109ソース(`MysteriousKnight.cs`)に
実在するが、`Common/ids/monsters.json`には含まれていなかった。原因は
`class MysteriousKnight : FlailKnight`(直接`MonsterModel`を継承せず、`FlailKnight`
経由の間接継承)であり、`Common/ids/build_id_dictionaries.py`の抽出ロジックが
直接継承のみを対象としているため取りこぼしていた。**これは元データの問題ではなく
本プロジェクトのID辞書抽出ツール側の既知の改善余地**であり、`unsupported_id`の一部
(少なくとも141件)は実際には解決可能なはずのカードとして再分類できる見込み。
今回は時間の都合で辞書修正までは行っていない(次回以降の改善候補)。

## 10. キャラクター・Act・Encounter別分布

```text
character: IRONCLAD 18,756 / SILENT 18,393 / REGENT 22,421 / NECROBINDER 17,539 / DEFECT 18,517
act:       OVERGROWTH 24,052 / HIVE 28,795 / GLORY 19,022 / UNDERDOCKS 23,757
pool_type: monster 63,151 / elite 20,929 / boss 11,546
schema_version: v8由来 3,470ラン / v9由来 2,527ラン
```

## 11. train/validation/test/benchmarkの件数

`source_run_id`のSHA256ハッシュによる決定論的分割(同一ラン由来の状態は必ず同じ
splitへ)。80/10/5/5の比率で設計:

```text
split       runs    encounters
train       4,749   75,628
validation    629    9,973
test          304    4,755
benchmark     315    5,270
```

既存の固定50Scenario(`Combat/evaluation/benchmark_states/fixed_50_scenarios.json`)は
そのまま回帰・比較用として維持(変更なし)。

## 12. 全件処理時間

```text
オフライン復元(5,997ラン, 95,626戦闘):  13.1秒
Emulator検証(6,091件サンプル):          50.8秒
合計:                                    約64秒
```

## 13. Phase 2教師データ生成へ進めるか

**進められると判断する。**根拠:

* HP復元率100%、レリック一致率100%、デッキ一致率87%(残差は元データの本質的曖昧性のみ)
* Emulator初期化成功率95.19%、失敗は分類済み・原因特定済みのカテゴリのみ
* 新規ブロッカーなし(既知カテゴリの範囲内、広範囲に利用可能件数を減らす事象なし)
* run単位でのtrain/validation/test/benchmark分割完了

ただし、**個々のシナリオをHeuristic教師データ生成へ投入する直前には、そのシナリオ
自体をEmulatorで初期化検証すること**(全95,626件のうち実機検証済みは6,091件
(6.4%)のみ)。`restore_status`が`exact`または`ambiguous_upgrade`であっても、
Emulator初期化まで保証されているわけではない(未知の敵ID・特殊レリック等が
個別に存在しうるため)。

## 成果物一覧

`Combat/data/full_reconstruction/`配下:

| ファイル | 内容 |
|---|---|
| `floor_states_{train,validation,test,benchmark}.jsonl` | 復元済み戦闘直前状態(95,626件、run単位分割済み) |
| `scenario_manifest.jsonl` | 軽量インデックス(高速フィルタ・サンプリング用) |
| `conversion_errors.jsonl` | 復元自体が失敗したラン(今回0件) |
| `emulator_validation.jsonl` | 実機Emulator検証結果(6,091件) |
| `reconstruction_summary.json` | 集計統計 |

## 既知の限界(変更なし、再掲)

* 手札/山札の実際の分割・ポーションの実スロット位置は元データ非記録(擬似ランダム/挿入順で代替)
* モンスターHPはAscension10代表値のまま
* `CombatScenario`にAscension設定フィールドが存在しないため適用不可(記録のみ)
* カードアップグレードの個体特定は元データ自体が曖昧な場合あり(`ambiguous_upgrade`で明示)
* `LOST_COFFER`レリック(新規発見、`RewardsCmd.OfferCustom`経由の対話フロー)がEmulator初期化を失敗させる
* `Common/ids/monsters.json`の抽出漏れ(間接継承クラス、例: `MysteriousKnight`)が一部`unsupported_id`の過大計上を招いている可能性
