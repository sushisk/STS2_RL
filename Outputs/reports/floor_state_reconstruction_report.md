# フロア時点状態復元 報告 (2026-07-20)

`map_point_history`をリプレイし、ラン最終状態ではなく各戦闘直前の実際の状態(デッキ/
レリック/ポーション/現在HP/最大HP/Gold/Act/Floor)を復元する処理を実装した。

## 1. `map_point_history`の構造

```text
map_point_history: list[act_index] -> list[floor_index] -> {
  map_point_type: str,       # "monster"/"elite"/"boss"/"rest_site"/"shop"/"treasure"/"ancient"/"unknown"
  player_stats: [PlayerStats],  # 常に長さ1(シングルプレイヤー、1000ラン全確認で0件の例外)
  rooms: [Room],              # 1点に複数room(例: イベント経由の戦闘)もあり得る(1000ラン中52件)
}

Room: {model_id, room_type, monster_ids?, turns_taken}
  room_type in {monster, elite, boss, event, rest_site, treasure, shop}
  -> 戦闘判定は map_point_type ではなく rooms[].room_type を使用(より正確)

PlayerStats: {
  current_hp, max_hp,                    # 絶対値、100%存在(v8: 1335/1335, v9: 1130/1130確認)
  damage_taken, hp_healed,
  max_hp_gained, max_hp_lost,
  current_gold, gold_gained/lost/spent/stolen,
  cards_gained, cards_removed, cards_transformed, upgraded_cards, downgraded_cards, cards_enchanted,
  card_choices,                          # was_picked付き。cards_gainedと完全重複(5700件で不一致0)
  relic_choices, bought_relics, relics_removed,
  potion_choices, bought_potions, potion_used, potion_discarded,
  bought_colorless,
  ancient_choice, event_choices, rest_site_choices,
}
```

**発見した重要な事実**: `current_hp`/`max_hp`は**全ポイントの100%に存在**する。トップレベルの
`players[0]`サマリにはHPが一切なかったため、以前は「HP復元不能」と結論していたが、これは
誤りだった(トップレベルのみを見ていたため)。

## 2. 復元できた項目

* デッキ(カードID・アップグレード状態) — `cards_gained`/`cards_removed`/`cards_transformed`/
  `upgraded_cards`/`downgraded_cards`をシーケンシャルに再生
* レリック取得・喪失 — `relic_choices`(was_picked)/`relics_removed`
* ポーション取得・使用・破棄 — `potion_choices`/`potion_used`/`potion_discarded`
* 現在HP・最大HP — `current_hp`/`max_hp`(絶対値、直前ポイントの値をそのまま使用)
* Gold — `current_gold`
* Act・Floor — `map_point_history`のインデックスから直接
* 実際のエンカウンターID・敵構成 — `rooms[].model_id`/`monster_ids`(推測ではなく実データ)

## 3. 復元できなかった項目(既知の制約)

* **手札/山札の分割**: どのカードが「手札」だったかは記録されていないため、デッキ全体から
  シャッフルして5枚を割り当てる(擬似ランダム、実データではない)
* **ポーションのスロット位置**: 記録されていないため保持順で0番から詰めて割り当て
* **モンスターHP**: Ascension10基準の代表値のまま(該当ランの実際のAscensionへのスケーリングは未実装)
* **Ascension**: `CombatScenario`に設定フィールドが存在しないため適用不可(metadata記録のみ)
* **カードのアップグレード"どの個体か"の曖昧性**: `cards_removed`/`upgraded_cards`は
  `id`(+`cards_removed`のみ`floor_added_to_deck`)しか持たず、同一id・同一floorの複数個体
  (例: 開始デッキの複数Strike)がある場合、どの個体が対象かは元データ自体に記録がない。
  本実装は先頭一致個体を採用(推測ではなく、データの本質的な曖昧性であり、これ以上の
  一意化は不可能)

## 4. HP関連フィールドの有無・復元件数

`current_hp`/`max_hp`は調査した全ポイントで100%存在。50ラン(911戦闘)の復元結果:

```text
hp_restore_status: exact = 911 / 911 (100%)
```

`exact`以外(`reconstructed`/`partial`/`unavailable`/`inconsistent`)は**0件**。

## 5. schema version 8・9の違い

`player_stats`のキー集合はv8・v9でほぼ同一(`current_hp`/`max_hp`含む全HP関連フィールドは
両バージョンで100%存在)。相違はv9のみ`downgraded_cards`が(サンプル中)出現した程度で、
実質的な構造差はない。**修正前の初期実装ではv9のデッキ一致率が1/8と極端に低かったが、
これはv8/v9仕様の違いではなく、次項の実装バグが原因**だった(修正後は両バージョンとも
7/8で同水準)。

## 6. 実装中に発見・修正したバグ(いずれも本プロジェクト側の実装漏れ、データの問題ではない)

1. **開始デッキ/開始レリック未シード**: `map_point_history`にはキャラクターの初期10-12枚
   デッキ・開始レリック(例: Ironcladの`BURNING_BLOOD`)が「取得イベント」として記録
   されない(ラン開始前提のため)。`STS2_Decompiled_v0109`の各キャラクター`StartingDeck`/
   `StartingRelics`定義から復元して解決。
2. **Ascender's Bane未シード**: Ascension5-10で自動付与される呪いカードも同様にイベント
   未記録。ラン開始時に`ascension`条件で追加するよう修正。
3. **`bought_relics`/`bought_potions`/`bought_colorless`の二重カウント**: これらは
   ショップ購入の記録だが、`relic_choices`/`potion_choices`/`cards_gained`と完全に重複
   していた(1000ラン・1217レリック購入・448ポーション購入・345カード購入で不一致0件)。
   独立イベントとして両方加算していたため、購入分が2倍にカウントされるバグがあった。
   重複側の処理を削除して解決。
4. **レリックの多重付与**: 同一レリックが2回「取得」イベントとして記録されるケース
   (実際のゲームではスタックとして既存エントリに合算される)で、素朴な実装は重複
   エントリを追加していた。既知のレリックIDが既にリストにあれば追加しないよう修正。

これら4件の修正により、デッキ一致率(サイズ)100%・レリック一致率100%を達成。

## 7. 50件での復元成功率

50ラン・911戦闘直前状態を復元(`Combat/data/reconstructed_encounters_50runs.json`)。

```text
デッキサイズ完全一致:        50/50 (100%)
デッキ内訳(強化状態含む)完全一致: 44/50 (88%) — 残り6件は上記6節の「どの個体が
                              強化されたか」という元データ自体の曖昧性による差分のみ
                              (カード種別・枚数自体はすべて一致)
レリック完全一致:            50/50 (100%)
HP復元(exact):              911/911 (100%)
警告(warnings)発生:          0/911
```

段階的検証(v8サンプル8件・v9サンプル8件・勝敗/abandoned混在9件・50件)すべてで
同水準の結果を確認(`Combat/data/reconstruction_validation_report.json`)。

## 8. 実機Emulatorで初期化できた件数

30ランから各2件(計56件)を`CombatScenario`形式に変換し、実機`BattleEmulator.initialize()`
で検証(`Combat/data/validate_reconstructed_scenarios_live.py`)。

```text
成功: 54/56 (96.4%)
失敗: 2/56
  - Unknown cardId: FOLLOW_THROUGH (v109に存在しない旧カードID — 既存データ監査で
    判明済みの0.126%不一致カテゴリに該当、新規の問題ではない)
  - NullReferenceException at MegaCrit.Sts2.Core.Rewards.CardReward (固定50Scenario
    生成時にも見られた既知のカテゴリ、Emulator側調査を推奨した案件と同種)
```

新規の失敗パターンは検出されなかった。

## 9. 既知の制約(まとめ)

* 手札/山札の実際の分割・ポーションの実スロット位置は元データに記録がなく、
  復元時は擬似ランダム/挿入順で代替(明示的に非実データと分かる形で保持)
* モンスターHPはAscension10代表値のまま(実際のAscensionへのスケーリングは未実装)
* `CombatScenario`にAscension設定フィールドが存在しないため、適用不可(記録のみ)
  — 引き続きEmulator側への申し立て事項
* カードアップグレードの個体特定は元データ自体が曖昧(修正不可能な既知の限界)
* 敵の実際の戦闘中スロット割当・行動履歴(SlotName/ForcedMove/StateLog)は未記録

## 10. 全件処理へ進めるかの判断材料

以下の理由から、全利用可能ラン(5997件、既存監査で確認済み)への本格適用を推奨する:

* HP復元: 100%(exact)、v8/v9で差なし
* デッキ復元: サイズ100%、内訳(強化状態込み)88%で残差は元データの本質的曖昧性のみ
* レリック復元: 100%
* 実機Emulator初期化成功率: 96.4%(失敗は既知の2カテゴリのみ、いずれも
  ラン最終状態ベースの生成器でも同様に発生する既存の既知課題)
* 警告(データ不整合)発生率: 0%(911件中0件)

新たなブロッカーは見つかっていない。全件処理へ進める場合、想定される戦闘直前状態は
おおよそ 5997ラン × 平均18戦闘/ラン ≈ 10万件規模(既存監査の`acts_signature`等から
概算、正確な平均戦闘数は未計測)。

## 成果物一覧

| ファイル | 内容 |
|---|---|
| `Combat/data/reconstruct_floor_state.py` | 復元エンジン本体(`ReplayState`, `reconstruct_encounters_for_run`, `encounter_to_scenario_spec`, `validate_run_reconstruction`) |
| `Combat/data/validate_reconstruction_staged.py` | 段階的検証スクリプト(v8→v9→mix→50件) |
| `Combat/data/reconstruction_validation_report.json` | 上記の生結果 |
| `Combat/data/validate_reconstructed_scenarios_live.py` | 実機Emulator検証スクリプト |
| `Combat/data/reconstructed_scenarios_live_validation.json` | 実機検証の生結果(54/56成功) |
| `Combat/data/reconstructed_encounters_50runs.json` | 50ラン・911戦闘の復元済み状態(section 5の出力項目形式) |
