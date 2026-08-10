# DollRoom LocException(issue #26)修正・検証報告(2026-08-11)

対象: `sushisk/STS2_RL#26`。詳細な根本原因・修正内容・監査はEmulator側報告書を参照
(`C:\STS2_Emulator\docs\reports\dollroom_locexception_fix_plan_20260811.md`)。本書は
RL側からの検証手順と結果のみを記録する。

## 修正内容(Emulator側、`Sts2Emulator`commit `ff0a1b9`)

`LocString.GetRawText()`(`Imported/Source/MegaCrit.Sts2.Core.Localization/LocString.cs`)
を`try/catch (LocException) { return LocEntryKey; }`で包み、このharnessにローカライズ
データが無いことに起因する`LocException`を、生キー文字列を返すことで吸収するように
変更。`LocString.Exists()`が既に同じ理由で受けていた修正(2026-08-05頃)の兄弟メソッドへの
機械的な延長。`DollRoom.OptionFromChoice()`(issue #26の直接原因)と、副産物として発見した
`Trial.cs`の`TrialStory`/`TrialResult`経路(未観測の潜在バグ)が、いずれも同じ
`GetRawText()`を直接呼んでいることをソース上で確認済みのため、1箇所の修正で両方を解消する。

## 検証手順・結果(RL側)

1. `Sts2Emulator.Cli`をビルドし直し(`dotnet build Sts2Emulator.Cli/Sts2Emulator.Cli.csproj`)、
   `run_emulator_bridge.py`が参照する`Sts2Emulator.Cli/bin/Debug/net8.0/Sts2Emulator.dll`が
   更新されたことを確認
2. `python -m pytest Combat/tests`(246件)を実行 → 全件pass、regressionなし
3. `python -m API.tcp_server`を起動し、`sts2_training.runner.self_play`で修正前の
   IRONCLAD・ランダムseed 15 runバッチを実行 → `BING_BONG.title`/`MR_STRUGGLES.title`の
   `LocException`が2件再現することを確認(修正前の挙動の再確認)
4. Emulatorをビルドし直し、TCPサーバーを再起動した上で、同条件の別バッチ(15 run)を再実行
   → `LocException`は0件。うち1 run(`ironclad-1786406022-00009-seed-1784532432-b7ff3628`)は
   実際にDollRoomイベントに到達したことをJSONLログで確認(`DollRoom`/`DOLL_ROOM`を含む
   イベント記録3件)、かつクラッシュせずその後235手以上runを継続したことを確認 —
   生きたseedでの直接検証
5. `Trial`イベントの`TrialStory`/`TrialResult`経路は今回のバッチでは到達しなかったが、
   Emulator側報告書のソースコード監査により、同一の`GetRawText()`を経由することを
   コードレベルで確認済み

## 検証中に観測した無関係な事象(本issueのスコープ外、別issue化を検討)

* `DenseVegetation.TrudgeOn()`で新規の`NullReferenceException`
  (`VfxCmd.PlayNonCombatVfx`の戻り値が、以前修正した`PackedScene.Instantiate<T>`とは
  別の経路でなおnullになりうる可能性、要別調査)
* `StrikeIronclad`再生時に`CreatureCmd.Damage`で`OverflowException`
  (`Decimal`→`Int32`変換オーバーフロー)

## codex敵対的レビューについて(運用上の注記)

当初はcodex(GPT 5.6 Luna high)による修正計画の敵対的レビューを予定していたが、
codexのWindowsサンドボックス(`CreateProcessAsUserW`)がこの実行環境(Claude Codeの
入れ子サンドボックス内)では権限不足のため一切のファイル読み取りに失敗し、実施
できなかった。ユーザー承認の上、Claude自身による敵対的自己レビュー(ソースコードでの
呼び出しチェーン直接確認、代替経路の洗い出し)に切り替えて実装した。

## 結論

GitHub issue #26はこのPRのマージ後にcloseする。
