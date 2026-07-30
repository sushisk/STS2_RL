# Phase 0 進捗サマリ (2026-07-20)

計画書セクション14の直近実装指示 1〜4番、および6番(既存資産の再整理方針)に対応。

## 1. v108 / v109 ID差分 (`Common/versioning/id_mapping_v108_v109.json`)

`STS2_Data`(v108, `C:\STS2_Decompiled`から抽出済み)と、`STS2_Data/extract_static_data.py`を
`C:\STS2_Decompiled_v0109`に対して再実行して新規生成した抽出結果を比較。

- **重要な前提の確認**: `STS2_Emulator/Sts2Emulator/Imported/Source`配下の.csファイル数
  (Cards 603, Relics 300)は v109 (603/300) と一致し、v108 (600/298) とは一致しない。
  つまり**現在動いているEmulatorはv109ベース**であり、`STS2_Data`(v108)のID辞書は
  Emulatorの実際の受理IDセットとは完全には一致しない。
- カード: v108=593 / v109=596。追加3件 (ABUNDANCE, DOWSING, TUTOR)、削除0件、
  値変更8件 (例: ACCELERANT rarity Rare→Uncommon, BLADE_SYMPHONY cost 1→2,
  BLOODLETTING rarity Common→Uncommon)。
- レリック: v108=297 / v109=299。追加2件 (DOWSING_ROD, NEOWS_SACRIFICE)。
- ポーション: v108=64 / v109=65。追加1件 (AMBERGRIS)。
- パワー: 追加1件 (AMBERGRIS_POWER)、削除1件 (DIAMOND_DIADEM_POWER)、
  stack_type変更2件。
- モンスター: 差分なし(119件で一致)。

## 2. 正規ID辞書 (`Common/ids/{cards,relics,potions,monsters,powers}.json`)

v109を正(current)としつつ、各エントリに`present_in: ["v108","v109"] / ["v109"]`を
付与した辞書。`STS2_Data`の抽出機構(`extract_static_data.py`)は元々ポーションを
扱っていなかったため、`Common/ids/build_id_dictionaries.py`内でローカルに
`PotionExtractor`サブクラスを追加して対応(共有スクリプト本体は無改変)。

## 3. データセット監査 (`Outputs/reports/dataset_audit_report.json`)

対象: `C:\STS2_Data\runs-all-before-2026-06.json` (6796ラン、ラン単位の最終状態
スナップショット。戦闘内カード使用ログではない点に注意)。

- `build_id`はv0.98.0〜v0.106.1の17ビルドにまたがる。**「v108」「v109」という
  ディレクトリ名の呼称と、この`build_id`のセマンティックバージョンは別の採番体系**
  である可能性が高く、直接の対応関係は未確認(要注意点として明記)。
- `schema_version`は8(3950件)と9(2846件)の2世代が混在。
- キャラクター分布: REGENT 1629 / SILENT 1330 / IRONCLAD 1305 / DEFECT 1272 /
  NECROBINDER 1260 — 5キャラほぼ均等。
- 勝率: 2117勝/6796 (31.2%)。abandoned 714件(10.5%)、cheated 62件(0.9%)。
- **シナリオ生成に利用可能な件数: 5997件 (88.2%)** (not abandoned, not cheated,
  game_mode==standard, players[0]あり)。
- **v109正規ID辞書とのID一致率**: カード99.87%一致(不一致はFOLLOW_THROUGH/
  GRAPPLE/PREPAREの3種、計207参照 — 現行v109には存在しないカード)、
  レリック100%一致、ポーション100%一致。→ 実データのカード/レリック/ポーションIDは、
  ほぼそのままEmulatorのResolveCard/ResolveRelic/ResolvePotion相当に渡せることを確認。

## 4. ディレクトリ雛形

計画書セクション9の推奨構成のうち、`Common/{schemas,ids,versioning}`と
`Combat/data/{raw,converted,heuristic}`、`Combat/evaluation/reports`、
`Outputs/reports`を作成。

## 5. スキーマ定義 (`Common/schemas/`)

`combat_state_schema.json` / `legal_action_schema.json` / `transition_schema.json`を、
**実装(`STS2_Emulator/Sts2Emulator/Dto/*.cs`、`GameInstance.BuildFullStateDict()`/
`BuildLegalActions()`)から逆算する形で**作成(想像で仕様を決めていない)。
副次的に判明した既知のギャップ:

- `emulator_bridge.py:observation_to_dict()`は`{turn, is_terminal, outcome, state}`
  のみを返し、`reward`/`legal_actions`/`info`を含む完全な`StepResult`相当を返して
  いない。計画書Phase1の`CombatEnv`はこのギャップを埋める形で設計するとよい。
- `CombatScenario`(Emulator側入力DTO)には**ポーションを設定するフィールドが
  存在しない**。また手札/山札の各カードは素のID文字列のみで、**アップグレード状態を
  指定する手段がない**。実データからの戦闘開始盤面再現において、この2点は
  Emulator側APIの拡張が必要な既知の制約として`Common/schemas/README.md`に記録。

## 6. 実データ由来のシナリオ生成器 (`Combat/data/scenario_from_runs.py`)

`scenario_set.py`が現状「Ironclad vs CalcifiedCultist」1パターンのみだった点を、
実データで拡張。`runs-all-before-2026-06.json`の実デッキ/レリック(使用可能5997件)と、
`act_encounter_pools.json`+`monsters.json`由来の実エンカウンター(Act別・
weak/normal/elite/boss別プール)を組み合わせ、`battle_emulator.py`が直接消費できる
シナリオ仕様dictを生成する。既知の簡略化(要修正ではなく、Emulator API制約への対応):

- ポーション情報は破棄(Emulator側に受け皿がないため)
- カードのアップグレード状態は破棄(同上)
- プレイヤーHPは各キャラの基礎StartingHp固定(ラン中の実際の最大HP蓄積は
  runs-all-before-2026-06.jsonに含まれていないため)
- 敵HPはAscension10基準の代表値(記録されたAscensionへの正確なスケーリングは未実装)
- デッキは「ランの最終デッキ」全体(特定フロア時点への復元はスコープ外 —
  それは`sts2-agent`のcard_reward_picker側が`map_point_history`リプレイで別途実施)

**実機Emulator検証結果**: 5件のサンプル(seed=42, act1, normal)のうち4件は
`BattleEmulator.initialize()`経由で正常に成功。1件(DEFECT, `FLYCONID_NORMAL`)が
`ResetFromScenario`のハング(`ScenarioInitializationTimeoutException`、30秒)を
引き起こした。**その後、`Combat/evaluation/reports/emulator_hang/`で徹底的な
切り分け調査を実施し、根本原因を完全に特定済み**(詳細は同ディレクトリの
`result.json`/`reproduction_steps.md`/`emulator_team_report.md`を参照):

- 3体エンカウンターは無関係(単体でも同じ13レリック構成ならハングする)。
- デッキ/手札も無関係(レリックを外せば同一デッキでも即成功)。
- 疑われた`CRACKED_CORE`(Defectの開始レリック)も無関係。
- **真犯人はレリック`LEAD_PAPERWEIGHT`と`CLAWS`**。両者とも`AfterObtained()`が
  `CardSelectCmd.FromChooseACardScreen`/`FromDeckForTransformation`という
  **対話型カード選択UI**を`await`しており、`CombatScenario.Relics`経由の付与は
  本物の`RelicCmd.Obtain`(＝本物の`AfterObtained`フック)を発火させる設計のため、
  UIのないヘッドレス環境ではこの選択が永遠に解決されずハングする。
- 同種の`AfterObtained`をawaitする他のレリックも同じ問題を起こす可能性が高く、
  全件洗い出しはEmulator側のフォローアップとして推奨事項に記載。
- RL側の暫定対応として`scenario_from_runs.py`に既知の問題レリックを除外する
  フィルタを実装し、修正後に同シナリオが1.3秒で成功することを確認済み。
- タイムアウト発生時のワーカー破棄方針を`worker_timeout_policy.md`として確定
  (Phase 1のCombatEnv/マルチワーカープールが従うべき契約として記録)。

## 未着手・次の候補

- 実機Emulatorでのシナリオ検証の完了確認
- 既存Heuristicコード(`Combat/*.py`)の`heuristic/{greedy,beam,lookahead,evaluator,dedup,benchmarks}`
  への再配置(import修正とリグレッション確認が必要なため、今回は着手を見送り、
  方針のみ提示)
- Heuristic AIによるstate→action教師データ生成(計画書Phase2)
