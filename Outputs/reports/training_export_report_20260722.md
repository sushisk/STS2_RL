# 500件教師データ試験: 学習担当向けexport報告

## 使用元run

- run directory: `C:\STS2_RL\Combat\data\trajectories_train500_20260722_w4`
- manifest: `C:\STS2_RL\Combat\data\trajectories_train500_20260722_w4\scenario_manifest.jsonl`
- manifest SHA256: `4373CEB0EA0CE3258D6FFCFA780F2D947ADAA6F9B73AA0F4F0455940637EED9F`
- Emulator commit: `2c6dc8844cb5940f8b450b8f8f621ef5f3735a57`
- DLL SHA256: `67D4ABD46E5F1987E22184E01349A7A969A41F11C97EB48BCBAFBA0BEE5FFA69`
- Heuristic version: `greedy_v1_default_weights`
- workers: `4`

## export成果物

- export root: `C:\STS2_RL\Training\exports\train500_export_20260722_v1`
- contract: `C:\STS2_RL\Training\DATA_CONTRACT.md`
- handoff: `C:\STS2_RL\Training\HANDOFF.md`
- schema:
  - `C:\STS2_RL\Training\schemas\training_decision.schema.json`
  - `C:\STS2_RL\Training\schemas\split_manifest.schema.json`
  - `C:\STS2_RL\Training\schemas\id_dictionaries.schema.json`
- validator: `C:\STS2_RL\Training\validate_export.py`
- sample loader: `C:\STS2_RL\Training\sample_read_export.py`

## export件数

- complete trajectories: `418`
- partial trajectories: `66`
- complete decisions: `9530`
- partial decisions: `3251`

### split

Complete:

- train: `334`
- validation: `42`
- test: `42`

Partial:

- train: `52`
- validation: `7`
- test: `7`

## 構造検証

- `validate_export.py` 実行成功
- complete rows:
  - row_count: `9530`
  - duplicate decision ids: `0`
  - missing required fields: `0`
- partial rows:
  - row_count: `3251`
  - duplicate decision ids: `0`
  - missing required fields: `0`
- split manifest overlap:
  - complete: `0`
  - partial: `0`

注: この段階の validation は、export schema と契約に沿った必須項目・重複・split整合性の検証である。`jsonschema` パッケージはローカル環境に未導入のため、外部依存を増やさずに構造検証を行っている。

## 品質集計

Complete:

- action type:
  - `card`: `7016`
  - `system`: `1899`
  - `potion`: `601`
  - `choice_card`: `14`
- End Turn count: `1899`
- End Turn rate: `19.93%`
- potion action count: `601`
- choice count: `14`
- outcome:
  - `victory`: `7691`
  - `defeat`: `1839`
- decision count distribution:
  - min: `1`
  - max: `50`
  - avg: `22.8`

Partial:

- action type:
  - `card`: `2524`
  - `system`: `621`
  - `potion`: `105`
  - `choice_card`: `1`
- End Turn count: `621`
- End Turn rate: `19.10%`
- potion action count: `105`
- choice count: `1`
- outcome:
  - `in_progress`: `3251`
- decision count distribution:
  - min: `5`
  - max: `50`
  - avg: `49.26`

Top selected labels in complete export:

- `End Turn`: `1899`
- `DEFEND_REGENT`: `408`
- `DEFEND_SILENT`: `370`
- `DEFEND_IRONCLAD`: `341`
- `DEFEND_DEFECT`: `328`
- `DEFEND_NECROBINDER`: `292`
- `STRIKE_REGENT`: `253`
- `STRIKE_IRONCLAD`: `242`
- `STRIKE_SILENT`: `228`
- `STRIKE_DEFECT`: `197`

## action辞書

- version: `v1`
- dictionaries:
  - `action_type_dict`
  - `card_dict`
  - `potion_dict`
  - `power_dict`
  - `relic_dict`
  - `enemy_dict`
  - `encounter_dict`
  - `character_dict`
- `__UNKNOWN__` を id `0` に予約
- 生成規則: lexical sort + `__UNKNOWN__` fixed at `0`

unknown ID件数:

- current export corpus: `0` by construction

注: 辞書は今回の export corpus から生成しているため、export 内の既知トークンはすべて辞書化済み。`__UNKNOWN__` は将来の未見トークン用の予約枠であり、今回の corpus では使用していない。

## 残存7件の分類

- heuristic exception 4件
  - `6304-18`: `RL側修正`
  - `787-23`: `RL側修正`
  - `2365-21`: `RL側修正`
  - `4419-24`: `RL側修正`
  - 根拠: 候補評価で敵全滅後の terminal state を `no living enemies` 例外として扱っている
- timeout 1件
  - `5362-18`: `Emulator側調査依頼`
  - 根拠: candidate evaluation 中の `TimeoutException`
- init exception 1件
  - `7678-9`: `データ不足による隔離`
  - 根拠: `Unknown enemyId: OSTY`
- cycle 1件
  - `6588-3`: `RL側修正`
  - 根拠: potion 分岐まわりで同一進展なしループ

## 既知の制約

- `action_id` は状態ごとの一時IDであり、固定 class id ではない
- `usable_partial` は別export保持のみで、初期学習 complete へ混ぜていない
- `missing_mad_science_state` と `missing_associated_card` は推測せず隔離方針
- encounter 分布は現 export では `observation.source.encounter` 欠損のため `unknown` 集計になる
- 学習側 tensor 化、embedding、loss、normalization は未確定であり学習担当の責任範囲

## 読み込み確認

- `sample_read_export.py` で複数 row の読み込み成功
- Observation / LegalActions / teacher action を Emulator なしで確認可能

## 学習開始前の未決事項

- RL側 terminal candidate evaluation 例外の補正
- potion分岐 loop の早期打ち切りまたは continuation 改善
- `OSTY` を含む入力資産の修正方針
- encounter 集計に必要な source metadata の補完方針
