# Emulator担当への依頼

## 件名
戦闘開始時のカード選択 pending choice を Scenario 復元できるようにしてください

## 背景
fixed50 の一部 Scenario は、`ResetFromScenario(spec)` では初期化できますが、その直後の Observation を RL 側が状態として保持し、同じ状態を再度 `ResetFromScenario` しようとすると失敗します。

RL 側では以下 2 件で再現しています。

- `fixed50:3342-27`
- `fixed50:1642-31`

どちらも `decision_index=0` から始まる、戦闘開始直後のカード選択状態です。

## 症状
- 初回の `ResetFromScenario(spec)` は成功
- Observation には `pendingChoice.choiceType = "Unsupported"` が返る
- その Observation ベースの状態を再度 Scenario 化して `ResetFromScenario` すると失敗
- RL の restore-based lookahead / candidate evaluation で復元不能になる

## 期待する対応
- 戦闘開始時のカード選択 pending choice を Observation / Scenario 往復で復元可能にする
- 少なくとも、`choiceType = "Unsupported"` ではなく、再適用可能な公開表現を返せるようにする
- `GAMBLING_CHIP` や `CHOICES_PARADOX` のような戦闘開始時カード選択 relic 起因の choice を対象に確認してほしいです

## 再現用スクリプト
RL リポジトリ内:

```powershell
python C:\STS2_RL\Combat\data\probe_start_of_combat_card_choice.py
```

個別実行:

```powershell
python C:\STS2_RL\Combat\data\probe_start_of_combat_card_choice.py --trajectory-id fixed50:3342-27
python C:\STS2_RL\Combat\data\probe_start_of_combat_card_choice.py --trajectory-id fixed50:1642-31
```

## 確認したい点
- `ResetFromScenario(spec)` 直後に返す `pendingChoice` の公開表現
- その状態を再度 `ResetFromScenario` できるか
- 戦闘開始時のカード選択が `Unsupported` に落ちていないか

## 参考 relics

### `fixed50:3342-27`
- `CRACKED_CORE`
- `LEAD_PAPERWEIGHT`
- `VENERABLE_TEA_SET`
- `STRAWBERRY`
- `ETERNAL_FEATHER`
- `CALLING_BELL`
- `BRONZE_SCALES`
- `PANTOGRAPH`
- `RAINBOW_RING`
- `DATA_DISK`
- `JUZU_BRACELET`
- `CHANDELIER`
- `BING_BONG`
- `MEAL_TICKET`
- `JEWELRY_BOX`
- `FORGOTTEN_SOUL`
- `GAMBLING_CHIP`
- `STONE_CALENDAR`
- `STRIKE_DUMMY`
- `HAPPY_FLOWER`

### `fixed50:1642-31`
- `BOUND_PHYLACTERY`
- `CURSED_PEARL`
- `BOOKMARK`
- `CHANDELIER`
- `ODDLY_SMOOTH_STONE`
- `CLOAK_CLASP`
- `VERY_HOT_COCOA`
- `BIG_HAT`
- `BOOK_OF_FIVE_RINGS`
- `LUCKY_FYSH`
- `TUNING_FORK`
- `CHOICES_PARADOX`
- `STONE_CALENDAR`
- `NUNCHAKU`
- `WAR_PAINT`
