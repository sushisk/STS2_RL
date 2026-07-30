# RL側調査依頼

`fixed50:2428-11` で、復元済み state から `End Turn` を要求した際に、次の選択または戦闘終了通知が返らず `TimeoutException` になります。

## 再現対象

- `trajectory_id`: `fixed50:2428-11`
- `decision_index`: `17`
- 症状:
  - `LegalActions` には `action_id=0` の `End Turn` が存在
  - `Step(0)` 実行後、次の choice / settled を待つ箇所で timeout
  - 同時に stderr へ `DoomPower.StartDoomAnim -> NullReferenceException` が出る

## 再現スクリプト

- [probe_end_turn_timeout.py](/abs/path/C:/STS2_RL/Combat/data/probe_end_turn_timeout.py)

実行例:

```powershell
cd C:\STS2_RL
python Combat\data\probe_end_turn_timeout.py
```

期待結果:

- `legal actions` に `action_id=0` が出る
- `attempting End Turn` の後に `TimeoutException` が出る

## 調査観点

- `End Turn` 受理後に敵ターン進行またはターン遷移処理が止まっていないか
- `WaitUntilChoiceOrSettled()` に到達後、次 decision / combat settled が発火しているか
- `BeforeSideTurnEnd -> DoomPower.DoomKill -> DoomPower.StartDoomAnim` の経路で例外後に進行不能になっていないか
- この state の power / relic / enemy intent の組み合わせで、ターン終了処理が未完了になる条件がないか

## 異常直前の主な状態

- character: `NECROBINDER`
- relics:
  - `BOUND_PHYLACTERY`
  - `SCROLL_BOXES`
  - `SWORD_OF_STONE`
  - `REPTILE_TRINKET`
  - `BAG_OF_PREPARATION`
- player powers:
  - `SHROUD_POWER:3`
- enemies:
  - `PHROG_PARASITE` intent=`LASH_MOVE`
  - `WRIGGLER` intent=`WRIGGLE_MOVE`
