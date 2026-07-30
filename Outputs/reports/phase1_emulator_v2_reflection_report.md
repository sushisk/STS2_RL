# Emulator優先度2 反映報告 (2026-07-20)

Emulator側の4機能(カードアップグレード状態/ポーション所持状態/nullable HP/敵index)完了を受けた
RL側反映作業の報告。すべての変更は実機Emulator(`Sts2Emulator.Cli/bin/Debug/net8.0/Sts2Emulator.dll`,
2026-07-20 15:49:09ビルド)に対して検証済み。

## 1. Emulator新仕様の反映箇所

作業開始前に、指示された4機能が実際にソース(`Sts2Emulator/Dto/CombatScenario.cs`,
`CardInstanceScenario.cs`(新規), `PotionScenario.cs`(新規), `Api/GameInstance.cs`)と
リビルド済みDLLの両方に存在することを確認してから着手した(未検証のまま反映作業を
始めることはしていない)。

反映先:
- `STS2_RL/Common/schemas/` — 3スキーマ+README更新、新規`combat_scenario_input_schema.json`追加
- `STS2_RL/Combat/emulator_bridge.py` — `CardInstanceScenario`/`PotionScenario`型を追加ロード
- `STS2_RL/Combat/battle_emulator.py` — `build_scenario_from_spec()`が`HandCards`等の構造化
  ピル入力・`Potions`・nullable`PlayerHp`/`PlayerMaxHp`を送出。`apply_action()`に
  `target_enemy_index`(安定index指定)を追加
- `STS2_RL/Combat/data/scenario_from_runs.py` — 全面更新(下記4節)
- `STS2_RL/Combat/env/combat_env.py` — 新規実装(下記5節)
- `STS2_RL/Combat/tests/test_scenario_v2.py` — 新規、10項目
- `STS2_RL/Combat/evaluation/benchmark_states/` — 固定50Scenario再生成

## 2. 更新したスキーマ

- `combat_scenario_input_schema.json`(新規) — Pythonシナリオspec辞書(v2)の入力スキーマ。
  `hand_cards`等の構造化ピル、`potions`、独立nullableな`player_hp`/`player_max_hp`を記述。
  v1(素の文字列配列)も引き続き受理される旨明記。
- `combat_state_schema.json` — `enemies[i].index`を追加。**重要な訂正**: Emulator側の
  doc commentは「死亡後もindexは変化しない」と主張しているが、実際にはエンジンが
  死亡クリーチャーを`CombatState._enemies`から`Remove`するため、**同一Step()呼び出し内でも
  死亡直後に後続の生存者のindexが詰められる(再採番される)**ことをテストで実証した。
  スキーマにこの相違を明記し、「1回の意思決定内でのみ有効、複数stepをまたいでは
  キャッシュしない」よう使用指針を記載。
- `legal_action_schema.json` — `choice_target`の`Parameters.enemyIndex`を追加、同じ
  相違点への参照を追加。
- `README.md` — 2026-07-20 15:49ビルドの変更点を「changelog」として明記、解消済みギャップと
  残存ギャップ(Ascensionが`CombatScenario`から設定不可)を整理。

## 3. Scenario生成器(`scenario_from_runs.py`)の変更

- `hand`/`draw_pile`(素の文字列)から`hand_cards`/`draw_pile_cards`(構造化、
  `current_upgrade_level >= 1 → is_upgraded=True`)へ移行
- `player.potions`(`id`+`slot_index`)から`potions`(`slot`+`potion_id`)を復元
- `LEAD_PAPERWEIGHT`/`CLAWS`の除外フィルタを削除(Emulator側の`AutoSkipCardSelector`修正により不要に)
- `source`に`ascension`を追加記録(適用はできないため metadata のみ、`README.md`にその旨明記)
- プレイヤーキャラクターの妥当性チェックは`KNOWN_PLAYABLE_CHARACTERS`による存在確認のみに変更
  (HP推測には使わない)

## 4. カード強化情報の利用件数

固定50Scenario中、カード総数1418枚のうち**383枚(27.0%)がアップグレード状態を復元**
(`current_upgrade_level >= 1`のカード)。多段階アップグレード(例: Searing Blow相当)は
`IsUpgraded`がbool型のため段階情報は失われる(既知の制約、`combat_scenario_input_schema.json`に明記)。

## 5. ポーションを復元できた件数

固定50Scenario中、**22/50シナリオ(44%)が1個以上のポーションを保持**、合計29個のポーションを
復元。残り28シナリオはランのポーション所持数が実際に0だった(推測ではなく実データ通り)。

## 6. HPを復元できた件数

**0/50** — 引き続き0件。`runs-all-before-2026-06.json`の`players[0]`は
`character`/`deck`/`id`/`max_potion_slot_count`/`potions`/`relics`のみを記録しており、
現在HP・最大HPのフィールドは存在しない(500件サンプルで再確認済み)。指示通り推測値は
入れず、`player_hp`/`player_max_hp`は常に`None`(nullable仕様の「自然な値」)としている。

## 7. `enemyIndex`への移行状況

`BattleEmulator.apply_action()`に`target_enemy_index`パラメータを追加し、
`Parameters.enemyIndex`と`state.enemies[i].index`を突き合わせて対象解決するよう実装、
`CombatEnv.step()`からも同パラメータを公開。ただし6節で述べた**index非安定性**
(死亡直後に再採番)により、「複数ターンにまたがる同一敵の追跡」という当初の想定用途は
**index単体では実現できない**ことが判明した。1回の意思決定内での同種敵の曖昧性解消には
問題なく使える(テストで確認済み)。この相違はEmulator担当への申し送り事項とする
(下記9節)。

## 8. 除外解除結果

`LEAD_PAPERWEIGHT`/`CLAWS`を含むシナリオが正常に(1秒未満で)初期化されることをテストで
確認(`test_lead_paperweight_and_claws_no_longer_hang`)。固定50Scenario生成でもこれらの
レリックは除外されておらず、実際に複数シナリオへ含まれている。

## 9. `CombatEnv.reset()`と`step()`の進捗

`STS2_RL/Combat/env/combat_env.py`を新規実装。`BattleEmulator`(ステートレス設計)の上に
1エピソード分の状態を保持するアダプタとして構成:

- `reset(scenario_spec) -> observation`
- `get_legal_actions() -> list[dict]`
- `step(action, target_enemy_index=None, target_index=None) -> {action_id, reward, done, observation, legal_actions, info}`
  (`transition_schema.json`のStepResult形状に準拠 — 従来`emulator_bridge.observation_to_dict()`が
  返していなかった`reward`/`legal_actions`/`info`を含む)

報酬関数は`default_reward_fn`としてプレースホルダのみ実装(勝敗±100、HP損失-1/HP)。
計画書Phase 5で正式設計するまでの暫定。

## 10. テスト結果

`STS2_RL/Combat/tests/test_scenario_v2.py` — 指示された10項目すべてを実装し、
**実機Emulatorに対して10/10 PASS**:

```
PASS test_upgraded_and_unupgraded_mixed
PASS test_potions_present
PASS test_potion_belt_expands_slots
PASS test_explicit_hp_not_double_applied_with_max_hp_relic
PASS test_duplicate_monster_stable_index
PASS test_target_tracking_after_death   (※期待値を実挙動に合わせて修正 — 9節参照)
PASS test_enemy_index_matches_choice_target_parameters
PASS test_legacy_plain_string_scenario_regression
PASS test_lead_paperweight_and_claws_no_longer_hang
PASS test_invalid_input_exception_types  (AggregateException化していないことを確認)
```

固定50Scenario生成では60候補中50件が実機検証を通過(10件は妥当な理由で除外— 12節参照)。

副産物として、`battle_emulator.py`の既存コード(`_wrap()`)に**同種モンスター複数体で
maxHpが衝突する既存バグ**を発見・修正した(`enemy_max_hps`辞書が`id`(種別共有)でキーされて
いたため、同種3体シナリオで全個体のmaxHpが最後に処理された1体の値に上書きされていた —
このEmulator反映作業とは独立の、本プロジェクト側の既存バグ)。新しい`index`フィールドで
`id`キーを`index`キーに置き換えて修正、テストで検証済み。

## 11. 依然として復元不能な項目

- **プレイヤーHP/最大HP**: ソースデータに存在しないため復元不能(0/50)。
- **Ascension**: `CombatScenario`に設定フィールドが存在しないため、記録のみで適用不可
  (Emulator側への追加要望として`Common/schemas/README.md`に記録)。
- **カードの多段階アップグレード**: `IsUpgraded`がbool型のため、2段階以上のアップグレード
  情報は「アップグレード済み」までしか表現できない。
- **敵のSlotName/ForcedMove/StateLog**: 元データに実際の戦闘中スロット割当や行動履歴が
  記録されていないため未設定(自然ロールに委ねる)。
- **特定フロア時点のデッキ/レリック/ポーション状態**: 引き続きラン最終状態のみ
  (`map_point_history`リプレイによる中間状態復元はスコープ外、`sts2-agent`側の
  `card_reward_picker`が別目的で実施済み)。

## 12. 追加で発見した事項(申し送り)

1. **`enemies[i].index`の非安定性**: Emulator側`GameInstance.cs`のdocコメントは
   「死亡後もindexは変化しない」と主張するが、`CombatState.cs`の`_enemies.Remove(creature)`
   (死亡時に実際にリストから削除)により、**同一Step()呼び出し内でも直後に再採番される**ことを
   実証(`test_target_tracking_after_death`)。ドキュメントと実装の相違であり、
   「敵死亡後も対象を追跡する」という当初のユースケースはindex単体では満たせない。
   Emulator担当への確認・修正提案を推奨。
2. 固定50Scenario生成時の10件の検証失敗のうち、6件は`MegaCrit.Sts2.Core.Rewards.CardReward`
   関連の`NullReferenceException`(特定レリックのAfterObtainedがヘッドレス環境で未対応の
   カード報酬フローに触れている可能性)、2件は既知の`FOLLOW_THROUGH`/`GRAPPLE`(v109に
   存在しない旧カードID、既存データ監査で判明済みの0.126%不一致に該当)、1件は
   `RunicCapacitor`(Defectのオーブスロット追加レリック)の`OrbCmd.AddSlots`内
   `NullReferenceException`、1件は`ArgumentNullException`(Dictionary検索)。
   いずれも該当シナリオは正しく除外され、`fixed_50_manifest.json`に記録済み。
   `CardReward`関連の6件は同種の原因の可能性が高く、Emulator側で追加調査の価値がある。

## 完了条件チェック

- [x] Emulator新仕様をSchemaとAdapterへ反映
- [x] Scenario生成器を更新
- [x] `reset()`を完成
- [x] Observation変換を完成
- [x] LegalActionsと敵対象対応を完成(ただしindex非安定性を発見・文書化)
- [x] `step()`を完成
- [x] 決定論性・異常系テスト(10/10 PASS)
- [x] 固定50Scenarioの再生成(50/50、10件は妥当な理由で除外)
- [ ] Heuristic教師データ生成準備 — 次フェーズ

index非安定性の扱い方針についてご判断をいただいた上で、Heuristic教師データ生成準備に
進みます。
