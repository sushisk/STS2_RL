# RL調査依頼: `fixed50:6485-37`

## 事象

fixed50 の `fixed50:6485-37` で、`COSMIC_INDIFFERENCE` 後の継続choiceまわりに不整合があります。

- character: `REGENT`
- encounter: `OWL_MAGISTRATE`
- 問題の分岐:
  1. decision 9 で `COSMIC_INDIFFERENCE` を使用
  2. live replay では直後に `pendingChoice.scope = "ActionContinuation"` が発生
  3. `LegalActions` には `choice_card` が提示される
  4. その `choice_card` を同一 `GameInstance` に対して `Step()` すると `Illegal action` になる

fixed50 の集計上は `no_legal_actions_while_non_terminal` として出ていますが、RL側で fresh process replay したところ、手前で上記の action continuation 不整合を再現できました。

## 再現スクリプト

- [probe_no_legal_actions_non_terminal.py](C:/STS2_RL/Combat/data/probe_no_legal_actions_non_terminal.py)

実行:

```powershell
cd C:\STS2_RL
& 'C:\Users\Hatsune Miku\AppData\Local\Programs\Python\Python312\python.exe' 'C:\STS2_RL\Combat\data\probe_no_legal_actions_non_terminal.py'
```

## 再現結果

2026-07-22 の fresh process 実行で、以下を確認しました。

- initial state から decision 9 まで replay 可能
- decision 9:
  - selected action: `COSMIC_INDIFFERENCE`
  - action_id: `3`
- その直後の live state に以下の pending choice が立つ
  - `choiceType = "Unsupported"`
  - `scope = "ActionContinuation"`
  - `scenarioRestorable = false`
- `LegalActions` に `choice_card` が提示される
- そのうち 1 件を `Step()` すると、同一 `GameInstance` 上で `Illegal action` が返る

スクリプトはこの状態を `reproduced=action_continuation_illegal_action_mismatch` として終了します。

## pending choice の内容

live replay で見えた options:

- `I_AM_INVINCIBLE`
- `HIDDEN_CACHE`
- `GLOW`
- `CHARGE`
- `PARTICLE_WALL`
- `FALLING_STAR`

この継続choiceは保存済み trajectory の `next_state.pendingChoice` には残っておらず、記録資産と live replay の間にも差分があります。

## 直前盤面

- hp / block / energy / stars: `75 / 15 / 3 / 2`
- hand:
  - `ASCENDERS_BANE`
  - `VOID_FORM`
  - `CRASH_LANDING`
  - `FURNACE`
- enemy:
  - `OWL_MAGISTRATE`
  - hp `232 / 247`
  - intent `PECK_ASSAULT`
  - `attackDamage = 3`
  - `attackRepeats = 6`

relics:

- `DIVINE_RIGHT`
- `SILVER_CRUCIBLE`
- `WHETSTONE`
- `STORYBOOK`
- `ROYAL_STAMP`
- `BLESSED_ANTLER`
- `BRONZE_SCALES`
- `FENCING_MANUAL`
- `VENERABLE_TEA_SET`

## RL側の見立て

RL側では、`LegalActions` 取得と `Step()` は同一 fresh process / 同一 `GameInstance` で行っています。

そのため今回の最小事実は次の通りです。

- `COSMIC_INDIFFERENCE` 後に `ActionContinuation` choice が立つ
- その state で `LegalActions` に `choice_card` が出る
- しかし同じ action_id を `Step()` に渡すと `Illegal action` になる

この不整合が、fixed50 側では結果的に `no_legal_actions_while_non_terminal` として観測された可能性があります。
