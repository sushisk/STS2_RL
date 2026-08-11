# 実run40回フォールト調査・第2ラウンド報告(2026-08-11)

対象: issue #26(DollRoom LocException)修正後の第2ラウンド。詳細な根本原因調査・監査は
Emulator側報告書を参照(`C:\STS2_Emulator\docs\reports\self_play_fault_sweep_20260811b.md`)。
本書はRL側からの手順・結果とissue化のみを記録する。

## 手順・結果

1. `--num-runs 10 --concurrency 1 --max-decisions 400`で、ランダムseedのバッチを
   SILENT/DEFECT/NECROBINDER/REGENTの4キャラクターで各10 run(計40 run)実行
2. 各バッチの`failures`を分類し、直近2バッチで新規カテゴリが出なくなったことを確認(収束)
3. 前ラウンドで修正済みのカテゴリB/C(`PackedScene.Instantiate<T>`)・D(`LocManager`)は
   40 run中0件を確認、修正の有効性を再確認
4. 新規に2種類のfault原因を発見、GitHub issueとして報告

## 発見したバグ(GitHub issue化対象)

| # | 内容 | Issue |
|---|---|---|
| E | `DenseVegetation.TrudgeOn()` — `VfxCmd.PlayNonCombatVfx`がTestMode下で意図的にnullを返す設計に対し、呼び出し元がnullチェックせず`NullReferenceException` | [#28](https://github.com/sushisk/STS2_RL/issues/28) |
| F | `CreatureCmd.Damage`(Strike再生時) — `decimal`→`int`キャストで`OverflowException` | [#29](https://github.com/sushisk/STS2_RL/issues/29) |

カテゴリA(RewardsSet)も13件観測したが、既存の`#24`と同一原因のため重複起票していない。

いずれも修正にはdecompiled `Imported/Source`側の書き換えを要するため、本ラウンドでは
原因の特定・issue化までに留めた。
