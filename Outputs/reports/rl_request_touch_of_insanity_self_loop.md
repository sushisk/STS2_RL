# RL側調査依頼

`fixed50:4228-34` で `TOUCH_OF_INSANITY` が legal action として提示され、実行も通りますが、実行前後で観測 state が変化せず self-loop になります。

## 再現対象

- `trajectory_id`: `fixed50:4228-34`
- `decision_index`: `3`
- 症状:
  - `LegalActions` に `TOUCH_OF_INSANITY` が存在
  - `Step(TOUCH_OF_INSANITY)` は例外を返さない
  - しかし action 後の state key が action 前と同一
  - fixed50 実行では `cycle_detected` として停止

## 再現スクリプト

- [probe_touch_of_insanity_loop.py](/abs/path/C:/STS2_RL/Combat/data/probe_touch_of_insanity_loop.py)

実行例:

```powershell
cd C:\STS2_RL
python Combat\data\probe_touch_of_insanity_loop.py
```

期待結果:

- `TOUCH_OF_INSANITY` が legal
- action 実行後も `same_state_key=true`
- `state diff` が空、またはカード追加/消費に相当する差分を含まない

## 調査観点

- `TOUCH_OF_INSANITY` 使用時に本来発生すべきカード追加または関連 state 更新が欠落していないか
- potion 消費が観測 state に反映されているか
- action 成功扱いだが内部効果が未適用のまま次 state を返していないか

## 異常直前の主な状態

- character: `IRONCLAD`
- relics:
  - `BURNING_BLOOD`
  - `LARGE_CAPSULE`
  - `GREMLIN_HORN`
  - `VENERABLE_TEA_SET`
  - `CANDELABRA`
  - `LETTER_OPENER`
  - `PARRYING_SHIELD`
  - `SEA_GLASS`
  - `ANCHOR`
  - `ODDLY_SMOOTH_STONE`
  - `RIPPLE_BASIN`
  - `KUSARIGAMA`
- player powers:
  - `DEXTERITY_POWER:1`
  - `ENERGY_NEXT_TURN_POWER:2`
- hand:
  - `GLOW`
- enemy:
  - `DEVOTED_SCULPTOR` intent=`FORBIDDEN_INCANTATION_MOVE`
