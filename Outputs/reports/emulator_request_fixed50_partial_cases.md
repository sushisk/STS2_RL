# Emulator担当 調査依頼

対象:

- `fixed50:1642-31`
- `fixed50:3342-27`
- `fixed50:6485-37`

使用スクリプト:

- [probe_fixed50_1642_31_stagnation.py](C:/STS2_RL/Combat/data/probe_fixed50_1642_31_stagnation.py)
- [probe_fixed50_3342_27_stagnation.py](C:/STS2_RL/Combat/data/probe_fixed50_3342_27_stagnation.py)
- [probe_fixed50_6485_37_no_legal_actions.py](C:/STS2_RL/Combat/data/probe_fixed50_6485_37_no_legal_actions.py)
- 共通本体: [probe_fixed50_partial_case.py](C:/STS2_RL/Combat/data/probe_fixed50_partial_case.py)

実行例:

```powershell
cd C:\STS2_RL
& 'C:\Users\Hatsune Miku\AppData\Local\Programs\Python\Python312\python.exe' 'C:\STS2_RL\Combat\data\probe_fixed50_1642_31_stagnation.py'
& 'C:\Users\Hatsune Miku\AppData\Local\Programs\Python\Python312\python.exe' 'C:\STS2_RL\Combat\data\probe_fixed50_3342_27_stagnation.py'
& 'C:\Users\Hatsune Miku\AppData\Local\Programs\Python\Python312\python.exe' 'C:\STS2_RL\Combat\data\probe_fixed50_6485_37_no_legal_actions.py'
```

---

## 1. fixed50:1642-31

症状:

- `decision_count = 50`
- `warnings = ["truncated_at_max_decisions:50"]`
- `classification = B_heuristic_stagnation`
- 敵HPが `399` から変化しない
- プレイヤーHPが `66` から変化しない
- 最後の10手がすべて `End Turn`
- legal actions は継続して存在する

補足:

- RL側暫定判断では heuristic 停滞の可能性が高いです
- ただし、`ChoicesParadoxAddToHand` を含む開始時選択とその後の状態遷移が関与しているため、Emulator側でも状態遷移上の不自然さがないか確認をお願いしたいです

---

## 2. fixed50:3342-27

症状:

- `decision_count = 50`
- `warnings = ["truncated_at_max_decisions:50"]`
- `classification = B_heuristic_stagnation`
- 敵HPが `155` から変化しない
- プレイヤーHPが `82` から変化しない
- 最後の10手がすべて `End Turn`
- legal actions は継続して存在する

補足:

- RL側暫定判断では heuristic 停滞の可能性が高いです
- ただし、`GamblingChipDiscard` を含む戦闘開始時 choice 後の盤面で、進展しないまま legal actions が回り続けるため、Emulator側でも状態遷移やカード可用性に不自然さがないか確認をお願いしたいです

---

## 3. fixed50:6485-37

症状:

- `decision_count = 11`
- `warnings = ["no_legal_actions_while_non_terminal"]`
- `final_is_terminal = false`
- progression:
  - enemy HP `247 -> 244 -> 232 -> 229`
  - player HP `75` 固定
- continuation 修正後も再現

直前の流れ:

- `FALLING_STAR`
- `COSMIC_INDIFFERENCE`
- `VOID_FORM`

最終記録状態:

- non-terminal
- `pendingChoice = null`
- 進行中なのに次の `LegalActions` が空

補足:

- 以前の `ActionContinuation` 中の `Illegal action` は RL側修正で解消済みです
- そのため、本件は continuation 消失とは別の異常として残っています

---

## RL側の現在判断

- `fixed50:1642-31`
- `fixed50:3342-27`

この2件は RL 側では heuristic 停滞寄りに見えています。

ただし、どちらも戦闘開始時 choice を含み、長時間にわたって legal actions が存在し続ける一方で盤面進展が完全停止しているため、Emulator側でも不自然な状態固定がないか確認をお願いしたいです。

- `fixed50:6485-37`

こちらはより強く Emulator / 状態遷移側の異常候補です。
non-terminal かつ `pendingChoice = null` の状態で legal actions が空になる点を確認いただきたいです。
