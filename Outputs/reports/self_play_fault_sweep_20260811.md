# 実run50回フォールト調査・Emulator側修正報告(2026-08-11)

対象: `python -m API.tcp_server`(このリポジトリ)経由で`C:\STS2_Training`の実意思決定エンジン
(beam search + `HeuristicCombatSelector`)を使い、`sts2_training.runner.self_play`でランダム
seed・複数キャラクターのWhole Runを実行し、`Action execution faulted`で終了したrunのログを
解析した調査報告。Emulator側の詳細な原因・修正内容は
`C:\STS2_Emulator\docs\reports\self_play_fault_sweep_20260811.md`を参照(本書はそのRL側の
入口・手順・GitHub issue化のみを記録)。

## 手順・結果

1. `--num-runs 10 --concurrency 1 --max-decisions 400`で、ascension 0・ランダムseedのバッチを
   IRONCLAD/SILENT/DEFECT/NECROBINDERの4キャラクターで各10 run(計40 run)実行
2. 各バッチの`RequestFaultedError`トレースバックをexception型+投げた箇所で分類し、4バッチ目で
   新規カテゴリが出なくなったことを確認(収束)
3. 判明した4つの原因のうち2つ(`DenseVegetation.TrudgeOn`/`Trial.AddVfxAnchoredToPortrait`の
   `NullReferenceException`)が、Emulator側の自前スタブ`PackedScene.Instantiate<T>()`が常に
   `null`を返していたという単一の根本原因に起因すると判明、Emulator側で修正
   (`Sts2Emulator`commit `eaacfa8`)
4. 修正後、検証用バッチ(REGENT、10 run)を実行 → 該当NullReferenceExceptionは0件、
   `Combat/tests`全246件regressionなし

## 発見したバグ(GitHub issue化対象)

| # | 内容 | 状態 |
|---|---|---|
| A | `RewardsSet.Offer()` — 「The RewardsSet is not complete after rewards were selected!」。`Wellspring.Bottle`/`PotionCourier.GrabPotions`/`HealRestSiteOption.ExecuteRestSiteHeal`など、通常のコンバット終了/宝箱報酬フロー以外から`RewardsCmd.OfferCustom`が呼ばれると再現 | 未修正(decompiled) |
| B/C | `PackedScene.Instantiate<T>()`が常に`null`を返す(`Imported/Stubs/GodotStubs.cs`) — `DenseVegetation.TrudgeOn`/`Trial.AddVfxAnchoredToPortrait`のNullReferenceExceptionとして表面化 | **修正済み**(`eaacfa8`) |
| D | `LocManager.GetTable(name)`が常に空の`LocTable`を返す(`Imported/Stubs/RunMetaSystemsNoOpStubs.cs`) — `DollRoom.OptionFromChoice`のLocExceptionとして表面化(複数の人形名キーで再現確認) | 未修正 |

A/Dは修正がdecompiledソースの書き換え、またはstub設計の作り直しを要するため、このラウンドの
スコープ外として報告のみ行った。詳細な根本原因の追跡はEmulator側報告書を参照。
