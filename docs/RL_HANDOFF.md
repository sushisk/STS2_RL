# STS2 RL 引き継ぎ資料 (2026-07-21)

## 2026-07-21 追記: Emulator修正版 + SlotName復元後の最新状況

後任初動タスクのうち、StarsのRL側反映、NEOWS_BONES専用quarantineラベル解除、
decision時間予算系フィールド追加、JSONL逐次flush追加、STUNNED forced moveの
非学習遷移分類、quarantine理由分類、truncated分類、SlotName復元は実施済み。

最新Emulator DLLは `2026/07/21 11:56:18`、
SHA256 `6E3D97425D629506559CE8898C8053FB1EB058FA50612B834EE3DCB204EA3FEE`。
Queen/Amalgam死亡後復元、`LARGE_CAPSULE`、`NEOWS_TORMENT`、
`TOUCH_OF_OROBAS`のEmulator修正を反映済み。

固定50は `Combat/data/trajectories_fixed50_slotname_emulator_20260721_115618/` へ
最初から再実行し、全50件の処理は完了した。結果は `ok=50`, `quarantined=0`,
`exclude_state_mismatch=0`, `illegal_action=0`, `emulator_step_exception=0`,
`timeout=0`, `determinism=5/5`, `truncated=11`, `cycle_detected=0`,
`no_progress_detected=4`。主模倣学習に使うべき `usable_complete` は 33/50、
`usable_partial` は 16/50、`exclude_heuristic_exception` は 1/50。

旧Heuristic例外4件はすべて `STUNNED` forced move復元不能で同一原因。
Observationではforced moveとして検出できるが、Emulatorの
`CombatScenario.ForcedMove`復元は動的MoveState `STUNNED` を通常move idとして
解決できないため、RL側ではHeuristic評価前に `non_learning_transition` として
隔離する。教師データ上は通常選択ラベルにしない。

8件の旧quarantineはすべて解除された。一方、`fixed50:5483-41`
(`DEFECT`/`MECHA_KNIGHT_ELITE`) が新たに初手候補評価で全候補Timeoutとなり、
Emulator stderrでは `RunicCapacitor.AfterSideTurnStart -> OrbCmd.AddSlots` の
NullReferenceが再現している。100戦闘へはまだ進まない。詳細は
`Outputs/reports/fixed50_slotname_emulator_revalidation_report.md` を参照。

これは後任Agentが最初に読む正式な引き継ぎ資料である。過去の会話ログを参照しなくても、
この1ファイルと以降に明記する参照ファイルだけで作業を再開できるよう構成している。

**引き継ぎの理由**: 現担当Agentのセッション有効期限・利用可能枠の都合による交代であり、
作業品質や進行上の問題によるものではない。データ監査、フロア時点状態復元、Emulator
反映、`CombatEnv`実装、教師データ生成基盤の構築はいずれも検証済みで、プロジェクトは
順調に進展している。

---

## 1. プロジェクト概要

### 1.1 目的

`STS2_RL`は、Slay the Spire 2をプログラムで高勝率攻略するAIを構築するプロジェクトの
実装領域である。最終的にはラン全体(カード報酬・マップ・ショップ・イベント選択)の
方策も学習するが、**現在は戦闘中の意思決定AI(戦闘AI)を最優先**している。

### 1.2 長期方針

```text
Heuristic AI(既存、STS2_RL/Combat直下)
    ↓ 状態→行動の教師データを生成
模倣学習によるPolicy
    ↓
Actor–Critic方式の強化学習(PPO等)
    ↓
Policy・Valueを用いた複数ターン探索
    ↓
固定した戦闘AI → ラン全体方策の学習(カード報酬・マップ等)
```

現在地は「Heuristic AIによる教師データ生成基盤の構築」であり、模倣学習そのものは
まだ着手していない。

### 1.3 依存関係の原則

```text
STS2_RL (このリポジトリ、AI・学習・探索・評価)
    ↓ pythonnet経由でDLLをロード
STS2_Emulator (戦闘遷移の正本、C#/.NET)
    ↓ 実装の参照元(直接依存はしない)
STS2_Decompiled_v0109 (現行ゲーム実装の参照ソース、read-only)
```

* **`STS2_Emulator`が戦闘ロジックの正本**。RL側はゲームロジックを再実装しない。
* Python側は`pythonnet`(`clr`)経由で`Sts2Emulator.dll`をロードし、
  `GameInstance`クラスのAPI(`Reset`/`ResetFromScenario`/`Step`/`GetObservation`/
  `GetLegalActions`)を呼ぶ。直接のゲームロジック実装はしない。
* `STS2_Decompiled_v0109`は現行(v109)ゲーム実装の参照ソースであり、**Canonical ID
  基準もv109**(理由: `STS2_Emulator/Sts2Emulator/Imported/Source`がv109から
  コピーされていることをファイル数一致で確認済み)。
* 既存の人間プレイデータ(`STS2_Data/runs-all-before-2026-06.json`)は、
  **主に状態分布・人間性能基準・カリキュラム用途であり、直接の「状態→行動」教師
  データではない**。行動教師データは、このリポジトリのHeuristic AIが
  `STS2_Emulator`上でプレイして生成する(4.2節・9節参照)。

### 1.4 元データの取り扱い

`C:\STS2_Data\runs-all-before-2026-06.json`(154MB、6,796ラン)は**元データであり、
削除・上書き禁止**。加工結果はすべて`STS2_RL`側の別ファイルへ出力している
(4.3節参照)。

---

## 2. 主要ディレクトリと責務

```text
STS2_RL/
├── Common/
│   ├── ids/           # v109 Canonical ID辞書(カード・レリック・ポーション・モンスター・パワー)
│   ├── schemas/        # JSON Schema定義(実装から逆算、想像で書いていない)
│   └── versioning/      # v108/v109差分、間接継承修正の差分レポート
├── Combat/
│   ├── battle_emulator.py, emulator_bridge.py   # Emulator低レベルPythonバインディング(本番共通基盤)
│   ├── heuristic_agent.py, beam_search.py, lookahead.py, state_evaluator.py,
│   │   potion_value_table.py, battle_result.py, scenario_set.py, main.py
│   │                                             # 既存Heuristic AI(Phase1-3)。まだ`Combat/heuristic/`
│   │                                             # サブディレクトリへ整理されておらず、Combat直下に平置き。
│   │                                             # 8.1節参照。
│   ├── env/
│   │   └── combat_env.py    # CombatEnv(本番教師データ生成が経由すべき唯一の窓口)
│   ├── data/
│   │   ├── scenario_from_runs.py, reconstruct_floor_state.py, reconstruct_all_runs.py,
│   │   │   validate_reconstruction_staged.py, validate_reconstructed_scenarios_live.py,
│   │   │   validate_reconstructed_scenarios_at_scale.py, revalidate_lostcoffer_fix.py
│   │   │                                        # データ復元・検証パイプライン(4節参照)
│   │   ├── preflight_validate.py                # Scenario投入前検証(7節参照)
│   │   ├── generate_heuristic_trajectories.py, run_trajectory_batch.py
│   │   │                                        # 教師データ生成オーケストレーション(9節参照)
│   │   ├── full_reconstruction/                 # 復元済み戦闘状態の本番出力(4.3節参照)
│   │   ├── full_reconstruction_PRE_ID_FIX/       # ID辞書修正**前**のスナップショット(比較用)
│   │   ├── trajectories_fixed50/, trajectories_fixed10_smoke/
│   │   │                                        # 教師データ生成の試験出力(10節、未完了)
│   │   ├── raw/, converted/, heuristic/          # 未使用の空プレースホルダ(計画書の推奨構成の名残)
│   │   └── audit_runs_dataset.py                # 元データ監査(Phase0、初期の全体監査)
│   ├── evaluation/
│   │   ├── benchmark_states/                    # 固定50Scenario(回帰・比較用、10節参照)
│   │   └── reports/emulator_hang/                # LOST_COFFER系バグの調査記録一式(過去のバグ報告)
│   └── tests/
│       └── test_scenario_v2.py                  # 実機Emulator結合テスト(11件、15節参照)
└── Outputs/
    └── reports/                                  # 各フェーズの正式報告書(14節に一覧・目的を記載)
```

**本番経路 vs 旧実装・監査用・参考用の判断基準**:
* **本番経路**: `Combat/env/combat_env.py`(CombatEnv) + `Combat/data/generate_heuristic_trajectories.py`
  + `Combat/data/preflight_validate.py` + `Combat/battle_emulator.py`/`emulator_bridge.py`
  (低レベル共通基盤) + `Common/ids`・`Common/schemas`(現行仕様)。
* **既存Heuristic実装**(`heuristic_agent.py`等): 教師データ生成に**現役で使用中**だが、
  `CombatEnv`より前に書かれたコードで内部は`BattleEmulator`を直接操作する(意図的、
  7節参照)。
* **監査・調査用**: `Combat/evaluation/reports/emulator_hang/`、
  `Combat/data/audit_runs_dataset.py`、`Common/ids/v0109_raw_PRE_TRANSITIVE_FIX/`等の
  `PRE_*`ファイル群 — いずれも**過去のスナップショット/調査記録**であり、現行パイプラインは
  参照していない。

---

## 3. 外部依存と参照関係

* **Emulator DLLパス**: `C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll`
* **ロード方法**: `STS2_RL/Combat/emulator_bridge.py::ensure_loaded()`が
  `pythonnet.load("coreclr", runtime_config=...)` → `clr.AddReference(dll_path)` を実行。
  1プロセスにつき`GameInstance`は1個のみ生成可能(`battle_emulator.py`モジュール
  docstring参照)。
* **DLLビルド日時・ハッシュの確認方法**:
  ```powershell
  [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
  (Get-Item 'C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll').LastWriteTime
  ```
  正式なバージョン番号は存在しないため、ビルド日時を`emulator_version`として使用
  している(`generate_heuristic_trajectories.py::emulator_version()`)。
  **2026-07-21 07:26:37ビルドが最新確認版**(Stars対応・NEOWS_BONES修正込み、5.4/5.6節参照)。
* **参照ソース**: `C:\STS2_Decompiled_v0109`(現行v109、Canonical基準)、
  `C:\STS2_Decompiled`(旧v108、比較用のみ)。いずれも**read-only、改変しない**。
* **元データ**: `C:\STS2_Data\runs-all-before-2026-06.json`(1.4節参照)。

---

## 4. 現在のデータ資産

### 4.1 元データ

* 総ラン数: **6,796件**(`schema_version` 8・9混在)
* 利用可能ラン数: **5,997件**(`was_abandoned=false`, `_isCheated=false`,
  `game_mode=="standard"`, `players`あり)
* **重要な訂正**: 当初「ラン最終サマリのみでHP情報なし」と判断したが誤りだった。
  `map_point_history`(各フロア時点の詳細ログ)には`current_hp`/`max_hp`が
  **全ポイントの100%に存在**することを確認済み。「HP復元不能」の判断は**撤回済み**。

### 4.2 フロア時点状態復元(完了)

`map_point_history`をシーケンシャルに再生し、各戦闘**直前**の状態
(デッキ・レリック・ポーション・現在HP・最大HP・Gold・Act・Floor)を復元する
処理を実装・全件適用済み。

```text
処理ラン数:          5,997 / 5,997 (失敗0)
復元戦闘状態数:       95,626
HP復元率:            100% (exact)
デッキ一致率(強化込み): 87.09% (残差は元データ自体の「どの個体が強化されたか」
                        という本質的曖昧性のみ、13.2節参照)
レリック一致率:        100%

restore_status内訳:
  exact:               86,706 (90.67%)
  unsupported_id:       2,838 (2.97%、13.3節参照)
  ambiguous_upgrade:    5,767 (6.03%、13.2節参照)
  history_inconsistent:   315 (0.33%)
```

run単位(`source_run_id`)でtrain/validation/test/benchmarkへ分割済み。**同一ラン由来の
状態が複数分割へ跨がることはない**(`reconstruct_all_runs.py::split_for_run()`が
`source_run_id`のSHA256ハッシュで決定論的に分割)。

```text
split        run数   戦闘状態数
train        4,749   75,628
validation      629    9,973
test            304    4,755
benchmark        315    5,270
```

### 4.3 出力ファイル

`Combat/data/full_reconstruction/`配下:

| ファイル | 内容 | 再生成コマンド |
|---|---|---|
| `floor_states_{train,validation,test,benchmark}.jsonl` | run単位分割済みの復元戦闘状態(1行=1戦闘直前状態) | `python reconstruct_all_runs.py` |
| `scenario_manifest.jsonl` | 軽量インデックス(フィルタ・サンプリング用、フルレコードなし) | 同上 |
| `conversion_errors.jsonl` | 復元自体が失敗したラン(現状0件) | 同上 |
| `emulator_validation.jsonl` | 実機Emulator検証結果(baseline: 各ラン最低1戦闘+重点カテゴリ追加サンプリング) | `python validate_reconstructed_scenarios_at_scale.py` |
| `reconstruction_summary.json` | 上記の集計統計(4.2節の数値の出典) | `reconstruct_all_runs.py`実行時に自動生成 |

**floor_states 1レコードの主要フィールド**: `source_run_id, map_point_index,
combat_index(0起算), schema_version, build_id, character, ascension, act, floor,
encounter_id, monster_ids, pool_type, player_hp, player_max_hp, deck, relics, potions,
gold, restore_status, hp_restore_status, warnings, unsupported_ids`。

**修正前スナップショットとの比較方法**: `*.PRE_LOSTCOFFER_FIX.*`
(LOST_COFFER系Emulator修正前)、`full_reconstruction_PRE_ID_FIX/`
(ID辞書間接継承修正前)、`Common/ids/*.PRE_TRANSITIVE_FIX.json`
(同上のID辞書自体)。いずれも同名ファイルとの単純diffで比較可能
(JSON/JSONLなので`diff`または`jq`での構造比較を推奨)。**現行の正式な数値は
`PRE_*`が付いていないファイルの方**。

---

## 5. Emulator反映済み仕様

以下はすべて**現在のDLL(2026-07-21 07:26ビルド)で確認済み**の仕様。古い仕様
(プレーン文字列ピル、ポーション設定不可、HP両方必須、Stars非対応等)へ**戻さない
こと**。

### 5.1 カードアップグレード

`CombatScenario.HandCards` / `DrawPileCards` / `DiscardPileCards` / `ExhaustPileCards`
(各要素 `{CardId, IsUpgraded}`)。旧来のプレーン文字列版(`Hand`/`DrawPile`/等)も
引き続き使用可能だが、**同一ピルで両方式を同時指定するとEmulator側が例外を投げる**
(意図的な仕様、`ArgumentException`)。強化不可能カードへ`IsUpgraded=true`を指定した
場合も例外。

**RL側の反映状況**: `battle_emulator.py::build_scenario_from_spec()`
(`initialize()`用)と`build_scenario_from_state()`(`apply_action()`の復元用、
**8.1節の重大バグ修正箇所**)の両方で反映済み。

### 5.2 ポーション

`CombatScenario.Potions`(要素`{Slot, PotionId}`)。未記載スロットは空。
Potion Belt等でスロット数が増える場合、レリックがPotionsより先に適用される
(Emulator側の`CombatScenario.Potions`ドキュメントコメントで確認済み)。

**RL側の反映状況**: 5.1と同様、`build_scenario_from_spec`/`build_scenario_from_state`
両方で反映済み(8.1節のバグ修正で後者を追加)。

### 5.3 HP

`PlayerHp`/`PlayerMaxHp`は独立してnullable。両方明示時は、最大HP変更レリックの
取得効果と二重適用されない(Emulator側でHP適用をレリック付与の**後**に行うよう
設計されている)。元データ(`map_point_history`)から戦闘直前HPをexactで取得可能
(4.1/4.2節)。

### 5.4 Stars(★新規、後任Agentの初動タスク)

**`CombatScenario.Stars`(nullable int)がEmulator側に追加された**(検証済み、
2026-07-21 07:26ビルドの`Sts2Emulator/Dto/CombatScenario.cs:202`)。対応する
Observationフィールドは`state["stars"]`(`GameInstance.cs:2087`)。

* 未指定時は自然な戦闘開始値(例: `DIVINE_RIGHT`所持なら自然付与後の値)
* 両方明示時と同様、レリックの自然付与完了後に明示値が上書き適用される(二重適用なし)
* 負値は`ArgumentException`で拒否、上限なし

**RL側は未反映**。`battle_emulator.py`の`build_scenario_from_spec`/
`build_scenario_from_state`は`Stars`を一切扱っていない。`HeuristicAgent.
choose_action_with_detail`の候補単位try/except(8.2節)は、この欠損に対する
**暫定ワークアラウンド**であり、Stars反映後は本来撤去できる(が、他の未知の
復元漏れに対する保険としてワークアラウンド自体は残しておく価値がある — 撤去は
必須ではなく判断事項、13節参照)。

### 5.5 敵識別(index / enemyIndex)

`state["enemies"][i]["index"]`と、対象選択アクションの
`Parameters["enemyIndex"]`が対応する。**同一Observation内での対象識別にのみ
使用可能**。実装検証済みの事実: 敵の死亡時、エンジンは死者を`CombatState.
_enemies`から物理的に`Remove`する(`isAlive=false`フラグだけではない)ため、
**次のStepやObservationでは残存個体のindexが再採番される**。複数Stepをまたぐ
恒久個体IDとして使用しないこと(13.4節も参照)。

### 5.6 レリック復元(取得時副作用の自動処理)

以下のレリックを含むScenarioが、`ResetFromScenario`時に正常初期化されることを
確認済み(取得時報酬の二重付与なし):

```text
LEAD_PAPERWEIGHT, CLAWS                          (対話型CardSelectCmd系、AutoSkipCardSelectorで自動辞退)
LOST_COFFER, TOY_BOX, SMALL_CAPSULE, ORRERY,
CAULDRON, CALLING_BELL                            (RewardsCmd.OfferCustom系、SkipRewardsSetSelectorで自動辞退)
NEOWS_BONES                                       (★新規修正、下記参照)
```

`NEOWS_BONES`は上記2方式では対応しきれない特殊ケースだった
(`AfterObtained()`の中で「2レリック報酬(自動辞退可能)」の**後に**
「無条件で呪いカードをデッキへ追加」という、報酬とは独立した副作用を持つため)。
Emulator側は`NEOWS_BONES`を`RelicsRestoredWithoutAfterObtained`という別集合に登録し、
`RelicCmd.Obtain`ではなく`Player.AddRelicInternal`(セーブ/ロード経路と同じ、
`AfterObtained()`を一切実行しない)で付与するよう変更した
(`GameInstance.cs:624-652`)。

**RL側の対応状況**: `preflight_validate.py`に残っていた
`known_issue:neows_bones_reward_duplication`の専用ラベル付与は解除済み。
通常のdeck/relic差分検出は維持し、回帰テストでNEOWS_BONESが専用quarantineに
入らないことを確認済み。

---

## 6. ID辞書(`Common/ids`)

* **v109をCanonical基準とする**(1.3節)。
* `Common/ids/build_id_dictionaries.py`が生成元。v108は`STS2_Data/json/*.json`
  (既存の静的抽出結果、再生成しない)を読み込み、v109は本スクリプト内で
  `TransitiveExtractor`によりインライン再抽出する。
* **`TransitiveExtractor`**: 元の`extract_static_data.py`の`Extractor.is_model_base()`は、
  クラスの**直接**基底クラスが目的の基底(例: `MonsterModel`)と一致する場合のみ
  マッチする一段階判定だった。`TransitiveExtractor`はディレクトリ内の全クラスの
  `クラス名→基底クラス名`マップを構築し、間接継承(例: `MysteriousKnight :
  FlailKnight : MonsterModel`)を連鎖的に解決する。抽象・補助クラスの除外は
  既存の`abstract`修飾子検出ロジックをそのまま利用しており、誤って含めることはない
  (実証済み: 新規解決は`MYSTERIOUS_KNIGHT`1件のみ、誤検出0件)。
* **効果**: `unsupported_id`が**3,000件→2,838件(-162件、-5.4%)**へ減少
  (`MYSTERIOUS_KNIGHT`関連の162件が`exact`/`ambiguous_upgrade`へ再分類)。
* **辞書再生成手順**:
  ```powershell
  cd C:\STS2_RL\Common\ids
  python build_id_dictionaries.py
  ```
  出力: `Common/ids/{cards,relics,potions,monsters,powers}.json`(canonical)、
  `Common/versioning/id_mapping_v108_v109.json`(v108/v109差分)、
  `Common/versioning/transitive_inheritance_fix_diff.json`
  (間接継承修正の新規解決/非解決差分)。
* **既知の未対応ID**(2,838件、`unsupported_id`)の主因:
  ```text
  card:FOLLOW_THROUGH, card:GRAPPLE, card:PREPARE   v109で削除済みの旧カード(既知、対応不要)
  monster:DOORMAKER, monster:DOOR                    v109ソースに該当クラスが実在しない
                                                       (削除済みコンテンツ、抽出漏れではないことを確認済み)
  ```
  頻度順は`Outputs/reports/full_scale_floor_state_reconstruction_report.md`参照。

---

## 7. CombatEnv

**実装ファイル**: `Combat/env/combat_env.py`

```text
CombatEnv.reset(scenario_spec) -> observation       # 新規initialize()
CombatEnv.adopt_state(battle_state, scenario_spec)  # 既にinitialize済みのBattleStateを
                                                     # 二重初期化せず引き継ぐ(preflight_validate用)
CombatEnv.get_legal_actions() -> list[dict]
CombatEnv.step(action, target_enemy_index=None, target_index=None) -> transition dict
CombatEnv.battle_state (property)                   # Heuristic探索がBattleEmulatorの
                                                     # ステートレスAPIへアクセスする窓口
```

**設計方針(意図的)**: 実際にコミットされた軌跡(教師データになる部分)は
`CombatEnv`経由に統一する。Heuristic内部の「仮想探索」(候補評価、未コミット分岐)は
`BattleEmulator`のステートレスAPIを直接使用する — `CombatEnv`はこの分岐機能を
再実装しない。理由: `CombatEnv`は1エピソード分の状態しか保持しないステートフル
設計であり、探索が必要とする「同じ状態から複数の仮想未来を試す」機能は
`BattleEmulator.apply_action(state, action, ...)`(常に明示的なstate引数を取り、
呼び出し元のstateを変更しない)でなければ実現できない。

**Emulator BridgeやGameInstanceへの直接アクセスが残っている場所**:
* `heuristic_agent.py` / `beam_search.py` / `lookahead.py` — `BattleEmulator`を
  直接呼ぶ(8節、意図的)。
* それ以外の直接アクセスは確認していない(`CombatEnv`と`BattleEmulator`が
  低レベルAPIの唯一の経路)。

**preflight validation**: `Combat/data/preflight_validate.py::preflight_validate(spec,
emulator)`。`CombatEnv.reset()`/`adopt_state()`より**前**に呼ぶ。
`emulator.initialize(spec)`を実行し、結果状態とspecを照合(デッキ・レリック・
ポーション・HP・敵の一致確認、ID正規化済み)。不一致があれば`status="quarantined"`
として理由付きで返す(`reasons`リスト、`diffs`に詳細)。

**timeout/例外処理**: `preflight_validate`は`init_exception:<型名>`として
Emulator初期化例外を捕捉する(タイムアウト含む、`ScenarioInitializationTimeoutException`も
ここで捕捉される)。`Combat/evaluation/reports/emulator_hang/worker_timeout_policy.md`
に、タイムアウト発生時のワーカー破棄方針を文書化済み(**現状は単一プロセス実装のため
実際のワーカープール破棄は未実装** — マルチワーカー化する際に適用する契約として
残している)。

**Scenario hash**: `generate_heuristic_trajectories.py::scenario_hash(spec)`
(spec全体のSHA256先頭16文字)。教師データレコードの`scenario_hash`フィールドに
記録し、同一Scenarioの再現・追跡に使う。

**決定論性**: `CombatScenario.Seed`が同一であれば、`BattleEmulator`は決定論的
(`run_trajectory_batch.py`の`determinism_checked`/`determinism_matched`で
毎回検証、10節参照)。ただし`LookaheadSearcher`は`ShuffleRngSeed`を独立して
サンプリングするため、こちらはPython側`random.Random`のseed管理に依存する。

---

## 8. Heuristicコードの現状

`Outputs/reports/heuristic_code_audit.md`に完全版あり。ここでは特に重要な点のみ。

**実行入口**: `Combat/main.py::main()` → `run_greedy_baseline` /
`run_turn_beam_search` / `run_sampled_lookahead_search`(Phase1/2/3)。

**行動候補生成**: `HeuristicAgent`が合法行動 × `BattleEmulator.target_candidates()`
(位置ベースの`target_index`、0..生存敵数-1)の全組み合わせを評価。**安定
`enemyIndex`は`choose_action_with_detail`が候補ごとに解決して`action_scores`へ
含めるが、探索の内部ループ自体は位置ベースのまま**(5.5節のindex非恒久性と
整合的な設計)。

**評価関数**: `state_evaluator.py::StateEvaluator`(1手先読み、特徴量の線形結合、
勝敗はボーナス/ペナルティとして加算)。

**探索**: `beam_search.py::TurnBeamSearcher`(ターン内カード順序探索)、
`lookahead.py::LookaheadSearcher`(将来ドロー仮説サンプリング+複数ターンビーム)。

**キャッシュ**: `BattleState._cached_legal_actions`(Reset/StepResultの副産物を
再利用)、`dedup_by_state`(等価状態の統合)。

**乱数**: `LookaheadSearcher.rng`のみ(将来ドロー仮説のseed生成)。他は決定論的。

**tie-break**: `score > best.score`(厳密不等号、同点は先勝ち)。明示的な
ルール名はなく、コード上の暗黙規則。

### 8.1 修正済みの重大バグ: `build_scenario_from_state`の状態復元漏れ

`apply_action()`が内部で使う復元関数`build_scenario_from_state`
(`battle_emulator.py`)が、**ポーションを一切復元せず、カードの強化状態も
破棄していた**。`build_scenario_from_spec`(`initialize()`専用)は先に修正
済みだったが、この姉妹関数の更新を見落としていた。

**影響範囲**: `apply_action`は`TurnBeamSearcher`・`LookaheadSearcher`・
`HeuristicAgent`の全候補評価が使う共通復元経路。今回の教師データ生成で
偶然発見されるまで、既存Heuristic探索全体が現実的な(ポーション・強化カード
を含む)Scenarioで静かに不正確な評価を行っていた可能性がある。

**修正済み**。回帰テスト`test_upgrade_and_potions_survive_apply_action_restore`
(`Combat/tests/test_scenario_v2.py`)で固定。

### 8.2 Stars欠損ワークアラウンド(撤去は判断事項)

`HeuristicAgent.choose_action_with_detail`は、候補単位で`apply_action`の例外を
捕捉し、評価不能な候補を`skipped`として記録して決定全体を継続する
(発見の経緯: Regentの"Stars"資源が5.4節の修正**前**は観測・復元不可能だった
ため、`enumerate_legal_actions`時点では合法な行動が復元後に非合法になる
ケースがあった)。

5.4節のEmulator修正(Stars対応)が入ったため、この特定の失敗モードは今後
発生しなくなるはずだが、**候補単位try/exceptの仕組み自体は他の未知の復元漏れに
対する保険として有用**なので、Stars対応をSchema/Adapterへ反映した後も
この防御機構自体は残すことを推奨する(撤去するのはStars欠損を前提とした
コメント・想定のみ)。

### 8.3 修正済み: 人間可読ログの`None`スコアクラッシュ

`skipped`候補(スコア`None`)を`run_trajectory_batch.py::human_readable_log`が
数値としてソートしようとして`TypeError`。スコアありとスキップ済みを分離して
表示するよう修正済み。

### 8.4 対策済み・要フォローアップ: 意思決定単体の性能爆発

`ENTOMANCER`(敵召喚を伴うエリート)× 27枚デッキ × 16レリックの組み合わせで、
**1つの意思決定だけで8分以上**かかるケースを確認(greedy探索は全合法行動×全対象を
個別に`apply_action`で評価するため、手札枚数×生存敵数に比例してコストが増大)。

対策: `choose_action_with_detail(battle_state, deadline=...)`が候補評価ループの
**都度**(意思決定と意思決定の"間"だけでなく)経過時間を確認するよう修正。
予算超過後の候補は`skipped`(`skipped_reason="deadline_exceeded"`)として記録し、
それまでの最良候補で決定を確定する。`generate_heuristic_trajectories.py`の
`max_wall_seconds`(デフォルト120秒)がトラジェクトリ全体の予算を決定し、
`deadline = start_time + max_wall_seconds`として各意思決定へ渡される。

**未解決**: この対策を適用した状態での固定50Scenarioフルバッチの
完全なクリーン実行が、本引き継ぎ時点で完了していない(10節参照)。

---

## 9. 教師データ生成パイプライン

### 9.1 現在の実装

* **実装場所**: `Combat/data/generate_heuristic_trajectories.py`
  (1トラジェクトリ生成の中核関数`generate_trajectory()`)、
  `Combat/data/run_trajectory_batch.py`(バッチ実行・集計・ログ出力)
* **入力セット**: `--source fixed50`(固定50Scenario)または
  `--source reconstructed --n <件数> --seed <seed>`
  (`full_reconstruction/floor_states_train.jsonl`から`exact`/`ambiguous_upgrade`の
  みサンプリング — **validation/test/benchmarkは教師データ生成のサンプリング元
  として使わない**、学習データ汚染防止のため)
* **処理フロー**: `spec → preflight_validate → (okならば) CombatEnv.adopt_state
  → ループ: get_legal_actions → choose_action_with_detail(deadline付き) →
  step(target_enemy_index優先) → 次状態` (7節の設計方針通り)
* **出力先**: `run_trajectory_batch.py --out <dir>`で指定(デフォルトは
  `Combat/data/trajectories_fixed50/`または`trajectories_dev<N>/`)
* **1意思決定ごとの保存形式**: `<out>/trajectories.jsonl`(1行=1意思決定、
  9.2節のSchema)
* **trajectory単位の保存形式**: `<out>/trajectory_meta.jsonl`(1行=1戦闘、
  意思決定0件でも記録される。`warnings`・`truncated`・`final_outcome`はここにのみ
  残る — `trajectories.jsonl`側は個別decisionレコードの`warnings`が常に空リストな
  ので注意)
* **人間可読ログ**: `<out>/human_readable_logs/*.md`(先頭10トラジェクトリのみ、
  各意思決定の合法行動・上位候補スコア・選択理由を表示)
* **再開・途中失敗時の扱い**: **現状チェックポイント/再開機能なし**。
  バッチが中断された場合は最初からやり直す必要がある(10節の教訓: 長時間
  バッチはバックグラウンド実行+進捗監視を推奨)。`trajectories.jsonl`等は
  各行write後にflushするよう更新済みなので、line countで概算進捗を確認できる。

### 9.2 保存Schema(実装済み)

```text
trajectory_id, source_run_id, source_combat_index, decision_index,
emulator_version, scenario_hash, state, legal_actions, selected_action,
selected_action_index, action_scores, reward, next_state, done, outcome,
heuristic_version, random_seed, warnings, selected_enemy_index,
decision_budget_exceeded, elapsed_ms, evaluated_action_count,
total_legal_action_count, total_candidate_count, search_depth_reached,
fallback_used, fallback_reason
```

時間予算・部分評価関連フィールドは各decisionレコードへ保存済み。
`run_trajectory_batch.py`の`summary.json`にも
`decision_budget_exceeded_count`, `fallback_count`, `skipped_candidate_count`,
`evaluated_candidate_count`を集約する。

### 9.3 データ品質区分(部分実装)

現在明示的に区別できているもの:
* `restore_status`(floor_states側): `exact` / `ambiguous_upgrade` /
  `unsupported_id` / `history_inconsistent`
* `preflight_validate`の`status`: `ok` / `quarantined`(理由は`reasons`リスト、
  `relic_mismatch` / `deck_mismatch` / `potion_mismatch` / `hp_mismatch` /
  `stars_mismatch` / `enemy_mismatch` / `no_legal_actions` / `init_exception:*`等)
* `action_scores`内: 評価成功(`score`が数値) / スキップ
  (`score=None`, `skipped_reason`あり)

**まだ明示的でないもの**(9.2節と同じギャップ): illegal action(構造的に
発生しないよう設計されているため専用フラグは未実装、`run_trajectory_batch.py::
check_illegal_action()`で事後検査のみ)、emulator exception / heuristic exception /
timeout / state mismatchは`trajectory_meta.jsonl`の`warnings`文字列
(`"heuristic_exception:...", "step_exception:...", "truncated_at_time_budget:..."`
等のプレフィックス)からしか判定できず、専用の列挙型フィールドがない。

---

## 10. 現在の検証状況(正直な現状)

**過去に停止した実行や部分実行を最終結果として扱わないこと** — 以下は
本引き継ぎ時点で確定している事実のみを記す。

* ✅ **10戦闘のクリーン検証は完了**(`Combat/data/trajectories_fixed10_smoke/`):
  ```text
  init成功率: 90% (9/10、1件はFAKE_*系レリック起因のrelic_mismatchで正しく隔離)
  戦闘完走率: 100%
  illegal_action: 0 / heuristic_exception: 0 / emulator_exception: 0 / timeout: 0
  決定論性: 5/5 (100%)
  勝敗: 8勝1敗、1決定あたり0.44秒
  ```
* ⚠️ **固定50戦闘のEmulator修正版 + SlotName復元後フルバッチは完了、ただし100戦闘へ進む条件は未達**
  (`Combat/data/trajectories_fixed50_slotname_emulator_20260721_115618/`):
  ```text
  total_scenarios: 50
  ok: 50
  quarantined: 0
  illegal_action_count: 0
  heuristic_exception_count: 1
  emulator_step_exception_count: 0
  timeout_count: 0
  determinism: 5/5
  truncated_count: 11
  cycle_detected_count: 0
  no_progress_detected_count: 4
  usable_complete: 33
  usable_partial: 16
  exclude_state_mismatch: 0
  exclude_heuristic_exception: 1
  ```
  - 旧Heuristic例外4件はすべて `STUNNED` forced move復元不能。RL側で
    `non_learning_transition` として隔離済み。
  - 旧quarantine 8件はすべて解除済み。レリック差分5件とSlotName欠落3件は
    preflight OK。
  - truncated 11件は A正常長期戦7件、B Heuristic停滞4件、C状態/実装ループ0件へ
    分類済み。
  - 新規問題: `fixed50:5483-41` が初手候補評価で全候補Timeout。
    Emulator stderr上の主シグナルは `RunicCapacitor.AfterSideTurnStart ->
    OrbCmd.AddSlots` のNullReference。
* ⬜ **100戦闘規模の開発用セットは未着手**
* **Phase 2初期基盤は部分完成であり、正式完了ではない**。

---

## 11. 後任Agentが最初に行うべき作業(推奨順序)

1. 本ファイル(`RL_HANDOFF.md`)を読む
2. `Outputs/reports/`の主要報告書(14節)、特に直近3件
   (`emulator_fix_revalidation_report.md` → `phase2_teacher_data_pipeline_report.md`
   → 本ファイル)を読む
3. 最新Emulator DLLのパス・日時・ハッシュを確認(3節のコマンド)し、5.4/5.6節の
   Stars対応・NEOWS_BONES修正が実際に入っているか再確認(ソース確認方法:
   `grep -n "Stars" C:\STS2_Emulator\Sts2Emulator\Dto\CombatScenario.cs`)
4. `Outputs/reports/fixed50_slotname_emulator_revalidation_report.md` を読み、最新の
   固定50結果と新規 `fixed50:5483-41` 問題を確認する
5. `fixed50:5483-41` の `RunicCapacitor.AfterSideTurnStart -> OrbCmd.AddSlots`
   NullReference/候補TimeoutをEmulator側修正候補として報告または修正する
6. B Heuristic停滞4件を主教師データから分離し、必要なら評価関数改善対象にする
7. 固定50の代表軌跡を追加監査し、12節の条件を満たせる状態になったら
   100戦闘規模の開発用セット
    (`python run_trajectory_batch.py --source reconstructed --n 100`)へ進む

---

## 12. 固定50戦闘の完了条件

以下を**すべて**満たすまで、100戦闘規模の開発用セットへ進まないこと。

* [ ] 50件すべての処理が終了している(タイムアウトで打ち切ったものを結果に含めない)
* [ ] Illegal action: 0件
* [ ] 未捕捉例外(バッチプロセス自体を停止させる例外): 0件
* [ ] プロセス全体が途中で停止・ハングしない
* [ ] カード強化状態が探索分岐(`apply_action`経由の全restore)で維持される
* [ ] ポーション状態が探索分岐で維持される
* [ ] HPが維持される
* [ ] Stars対応後は、Stars状態が探索分岐で維持される(反映前は対象外)
* [ ] 状態復元の差分検査(`preflight_validate`)が主要ケースで成功
* [ ] 時間予算到達率を把握・記録している(9.2節のSchema拡張を推奨)
* [ ] fallback件数(skipped候補で確定した意思決定の件数)を把握している
* [ ] 戦闘ごとの所要時間を把握している(`avg_time_per_combat_s`)
* [ ] 代表軌跡(人間可読ログ)に明白な異常行動がない
* [ ] 不完全ラベル(quarantined、skipped候補が多い決定等)を模倣学習用の
      主データから分離できる

---

## 13. 未解決・判断保留事項

これらは**現担当が独断で結論を出していない**事項。後任Agentも、プロジェクト全体の
方針に関わる判断(特に13.1)はEmulator担当・プロジェクト全体の合意を得てから
進めることを推奨する。

### 13.1 `AfterObtained`の無条件副作用を持つ他レリック

5.6節で対応済みのレリック群とは別に、**取得時に無条件でデッキ等を変更する
副作用を持つレリックが他にも存在する可能性がある**(`CombatScenario.Relics`の
ドキュメントコメントで名指しされている例: `NeowsSacrifice`, `DowsingRod`,
`SereTalon`, `TanxsWhistle`, `Storybook`, `ScrollBoxes`, `PreservedFog`,
`PaelsHorn`, `NeowsTorment`, `LargeCapsule`, `JewelryBox`, `DustyTome`,
`DollysMirror`, `CursedPearl`, `CallingBell`, `BloodSoakedRose`, `BiiigHug`,
`HeftyTablet`)。これらは「対話は伴わないが無条件で状態を変える」という、
5.6節のNEOWS_BONESとは異なるカテゴリで、**Emulator側は意図的にこの副作用を
そのまま発火させる設計**(Scenarioでの再現ではなく「本当に今取得した」
扱いのため)。

**RL側の対応方針**: これらのレリックを含むScenarioでは、`preflight_validate`が
実際にデッキ・レリック差分を検出して`quarantined`にするはずだが、**網羅的な
検証はまだ行っていない**。頻度・影響度を実データで計測し、対象を記録すること。
**全レリックを独断で除外したり、「これはEmulator側のバグだ」と断定して修正依頼を
出したりしないこと** — 意図的な仕様である可能性があるため、実際に教師データの
質へ悪影響を与えているか確認してから判断する。

### 13.2 アップグレード個体の曖昧性

元データ(`map_point_history`)は最終的な強化枚数を保持しているが、
`upgraded_cards`イベントは対象カードの`id`のみを記録し、`floor_added_to_deck`
での個体特定ができない。複数の同名カードが存在する場合、**どの個体が
強化されたか元データからは一意に決まらない**(`ambiguous_upgrade`、5,767件)。

`ambiguous_upgrade`のシナリオを完全な教師ラベルとして扱わないこと。
模倣学習では`exact`のみを主データとし、`ambiguous_upgrade`は補助データ
または要検討として分離することを推奨(独断で完全に破棄するかは
プロジェクト判断)。

### 13.3 未対応ID(2,838件)

6節の`unsupported_id`。頻度上位は判明している(`FOLLOW_THROUGH`/`GRAPPLE`/
`PREPARE`は削除済みカードで対応不要、`DOORMAKER`/`DOOR`もv109に実在しない)。
**Emulator実装依頼を出すか、対象シナリオを除外し続けるかは未決定**。
実際の学習への影響度(頻度×重要度)を見て判断すること。

### 13.4 恒久的な敵個体ID

5.5節の通り、現行`enemyIndex`は同一Observation内でのみ有効。**複数Stepを
またいだ同一敵個体の追跡が将来必要になった場合**(例: 「このターン最初に
狙った敵が今どうなっているか」を学習信号にしたい場合)、Emulator側のAPI拡張
(真に恒久的なcreature ID)が必要になる。現時点では未要求・未実装。

### 13.5 未復元・未検証の状態要素

以下は`floor_states`の復元対象に含まれていない、または検証していない
(影響度は未評価):

* 一時的なコスト変更(カードのその場限りのコスト修正)
* Retain(手札保持)フラグの復元
* Exhaust関連の個体状態
* Enchant(付与エンチャント)の完全な復元(`cards_enchanted`イベントは
  捕捉しているが、`encounter_to_scenario_spec`は`enchantment`情報を
  CombatScenarioへ渡していない — Emulator側にもEnchantment設定用の
  Scenarioフィールドがあるか未確認)
* カードの`DynamicVars`(カード内部の動的変数、例: 一部カードの効果量カウンタ)
* パワーの内部状態(`PowerStack.DynamicVars`はEmulator側に存在するが、
  RL側の復元パイプラインは`amount`のみ復元しDynamicVarsは未対応)
* レリックの`DynamicVars`
* ポーション固有の内部状態(スタック等)

これらは教師データの精度に影響しうるが、**優先度・影響度の評価は未実施**。

---

## 14. 主要報告書一覧

すべて`Outputs/reports/`配下。**時系列順**、後ろのものほど新しく、内容が
更新されている場合がある。

| ファイル | 内容 | 状態 |
|---|---|---|
| `dataset_audit_report.json` | 元データ(6,796ラン)の初期監査(Phase0) | 現行有効、上位互換なし |
| `phase0_summary.md` | Phase0(ID辞書・スキーマ固定・データ監査)のまとめ | 現行有効 |
| `phase1_emulator_v2_reflection_report.md` | Emulator優先度2(カード強化/ポーション/nullable HP/敵index)反映報告 | 現行有効、ただしindex安定性の記述は5.5節で更新済み |
| `floor_state_reconstruction_report.md` | フロア時点状態復元の**試作**(50ラン規模)報告 | **数値は全件版で更新済み**、経緯の記録として有効 |
| `full_scale_floor_state_reconstruction_report.md` | フロア時点状態復元の**全件**(5,997ラン)報告 | 4.2節の数値の一次出典。**ただし冒頭に記載の通り、Emulator修正前のunsupported_id数(3,000件)を含む古いスナップショット** — 最新は`emulator_fix_revalidation_report.md`と本ファイル |
| `emulator_fix_revalidation_report.md` | LOST_COFFER系Emulator修正の反映・再検証、ID辞書間接継承修正 | **現行最新**の`unsupported_id`(2,838件)等の数値はここが一次出典 |
| `heuristic_code_audit.md` | 既存Heuristicコードの構造監査 | 現行有効 |
| `phase2_teacher_data_pipeline_report.md` | 教師データ生成パイプライン構築、発見バグ4件 | 現行有効、ただし50戦闘フルバッチは未完了(10節で最新状況を更新) |
| `stars_fixed50_revalidation_report.md` | Stars反映後の固定50初回再検証 | 歴史的記録。最新の固定50判断は`fixed50_failure_classification_report.md`を参照 |
| `fixed50_failure_classification_report.md` | 固定50のquarantine/heuristic例外/truncated分類、A長期戦max100追試 | 歴史的記録。最新の固定50判断は`fixed50_slotname_emulator_revalidation_report.md`を参照 |
| `fixed50_slotname_emulator_revalidation_report.md` | Emulator修正版反映、SlotName復元、固定50再検証、新規RunicCapacitor問題 | **現行最新**の固定50品質判断 |
| `docs/RL_HANDOFF.md`(本ファイル) | 引き継ぎ資料 | **最新の到達点はこのファイルを正とする** |

`Combat/evaluation/reports/emulator_hang/`は、LOST_COFFER系バグの**発見時**の
詳細調査記録(Emulator修正**前**)。歴史的記録として保持、現行仕様の参照には
使わないこと(現行仕様は5節参照)。

---

## 15. テスト一覧と実行方法

| テスト | ファイル | 実行コマンド | 期待結果 |
|---|---|---|---|
| Emulator結合テスト一式(Schema validation, Scenario conversion, Emulator bridge, カード強化復元, ポーション復元, HP復元, Stars復元, NEOWS_BONES回帰, 同種敵対象, LEAD_PAPERWEIGHT/CLAWS, apply_action復元でのポーション/強化/Stars維持) | `Combat/tests/test_scenario_v2.py` | `cd C:\STS2_RL\Combat\tests && python test_scenario_v2.py` | `13 passed, 0 failed` |
| 固定50Scenario生成・検証 | `Combat/evaluation/benchmark_states/generate_fixed_50.py` | `cd C:\STS2_RL\Combat\evaluation\benchmark_states && python generate_fixed_50.py` | 60候補中50件successfulな`fixed_50_scenarios.json`生成 |
| フロア状態復元の段階的検証(v8/v9/mix/50件) | `Combat/data/validate_reconstruction_staged.py` | `cd C:\STS2_RL\Combat\data && python validate_reconstruction_staged.py` | `reconstruction_validation_report.json`出力、relic一致100%/HP exact100% |
| 復元Scenarioの実機初期化検証(サンプル) | `Combat/data/validate_reconstructed_scenarios_live.py` | 同上ディレクトリで`python validate_reconstructed_scenarios_live.py` | 高成功率(前回56件中54件) |
| LOST_COFFER系修正の的確な再検証 | `Combat/data/revalidate_lostcoffer_fix.py` | 同上で`python revalidate_lostcoffer_fix.py` | `lostcoffer_fix_revalidation.json`、init_ok高比率 |
| 教師データ生成(小規模) | `Combat/data/run_trajectory_batch.py` | `python run_trajectory_batch.py --source fixed50`(または`--source reconstructed --n <N>`) | `summary.json`、12節の完了条件参照 |
| 全件フロア状態復元(重い、~30秒) | `Combat/data/reconstruct_all_runs.py` | `python reconstruct_all_runs.py` | `reconstruction_summary.json`、4.2節の数値 |
| ID辞書再生成 | `Common/ids/build_id_dictionaries.py` | `cd C:\STS2_RL\Common\ids && python build_id_dictionaries.py` | 6節の数値 |

**`NEOWS_BONES`単体の対話型リワード自動辞退テストは専用ファイルなし** —
`test_scenario_v2.py::test_lead_paperweight_and_claws_no_longer_hang`と同型の
テストを追加することを推奨(11節の初動タスク7)。

**注意**: すべてのテストは実機Emulator(CoreCLR)を起動するため、初回ロードに
数秒かかる。1プロセスにつき`GameInstance`は1個のみ生成可能なため、
複数テストファイルを同時並行実行しないこと。

---

## 16. 既存ドキュメントとの整合性

* `Common/schemas/README.md`: 2026-07-20 15:49ビルドの変更点に加え、
  Stars対応と教師データの時間予算/fallback系フィールドも反映済み。
* `Combat/README.md`: 元のHeuristic AI設計書(Phase0-4のASCIIアート図解)。
  **教師データ生成パイプライン・CombatEnv・本引き継ぎ資料への言及なし**、
  更新推奨(必須ではない)。
* ルート`README.md`・`Combat/data/README.md`・`Combat/env/README.md`・
  `Combat/evaluation/README.md`・`Outputs/reports/README.md`は**現状存在しない**。
  本引き継ぎ資料(`docs/RL_HANDOFF.md`)が実質的にこれらの役割を代替している。
  作成するかは後任Agentの判断に委ねる。

---

## 17. 機械可読な現状情報

`docs/rl_status.json`を参照(本引き継ぎ資料の作成と同時に生成)。
`current_phase`, `emulator_dll`, `dataset_summary`, `reconstruction_summary`,
`emulator_validation_summary`, `teacher_pipeline_status`, `completed_tasks`,
`pending_tasks`, `known_issues`, `next_actions`, `important_paths`, `reports`,
`tests`を機械可読形式で保持。数値は本ファイルの4節・6節・10節と同期している
(生成時点: 2026-07-21)。
