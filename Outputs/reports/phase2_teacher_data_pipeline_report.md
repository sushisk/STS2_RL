# Phase 2: Heuristic整理・教師データ生成基盤 進捗報告 (2026-07-21)

## 0. 概要

`CombatEnv`統一の教師データ生成パイプラインを新規実装した。実装・検証の過程で
**3件の実質的なバグ**(うち1件は既存Heuristic探索全体に影響する重大なもの)を発見・
修正した。10戦闘のクリーンな検証は完了。50戦闘フルバッチは、一部の高負荷シナリオ
(大型デッキ×召喚系エリート)により複数回の是正が必要となり、性能面の対策を講じた
上で完了していない状態にある(詳細は9節)。

---

## 1. Heuristicコードの現状構造・実行入口・Emulator直接依存箇所

`Outputs/reports/heuristic_code_audit.md`に詳細を記録済み。要点:

* 実行入口: `main.py::main()` → `run_greedy_baseline / run_turn_beam_search / run_sampled_lookahead_search`
* Heuristicスタック(`HeuristicAgent`/`TurnBeamSearcher`/`LookaheadSearcher`)は
  **`BattleEmulator`を直接呼び出しており、`CombatEnv`を経由していなかった**
  (`CombatEnv`自体、今回のセッションで新規実装したもの)。

## 2. `CombatEnv`への移行状況

**実装完了・部分適用**。方針: 「本物のコミット済み軌跡」は`CombatEnv`経由、
Heuristic内部の仮想探索(候補評価)は`BattleEmulator`の状態分岐APIを直接使用
(これは意図的な設計判断 — `CombatEnv`はステートフルな1エピソードのみを保持し、
探索が必要とする「未コミットの仮想分岐」機能を再実装しない)。

新規追加:
* `CombatEnv.battle_state`(公開プロパティ): Heuristic探索がステートレスAPIへ
  アクセスするための窓口
* `CombatEnv.adopt_state()`: 事前検証(preflight)で既に`initialize()`済みの
  `BattleState`を二重初期化せずに引き継ぐ
* `HeuristicAgent.choose_action_with_detail()`: 全候補のスコア(`action_scores`)を
  返す新メソッド(既存`choose_action()`は最良候補のみ保持)

実際の教師データ生成ループ(`generate_heuristic_trajectories.py`)は指示通り:
`復元Scenario → preflight_validate → CombatEnv.adopt_state → get_legal_actions →
Heuristic評価 → CombatEnv.step → 次状態`

## 3. 教師データの出力スキーマ

指示のセクション7の全項目を実装(`trajectory_id, source_run_id, source_combat_index,
decision_index, emulator_version, scenario_hash, state, legal_actions, selected_action,
selected_action_index, action_scores, reward, next_state, done, outcome,
heuristic_version, random_seed, warnings`)。加えて`selected_enemy_index`を追加
(選択行動が対象とした敵の、その状態内でのみ有効な`enemyIndex`)。

出力ファイル構成:
```text
trajectories.jsonl       # 1行=1意思決定
quarantine.jsonl         # 事前検証で除外されたScenario
trajectory_meta.jsonl    # 1行=1戦闘(warnings/outcome/truncated等、決定0件でも記録)
summary.json             # 集計統計
human_readable_logs/*.md # 代表的軌跡の人間可読ログ
```

## 4. 発見・修正した重大バグ

### 4.1 `build_scenario_from_state`のポーション・強化状態消失(重大)

`apply_action()`が内部で使う復元関数`build_scenario_from_state`が、**ポーションを
一切復元せず、カードの強化状態も破棄していた**。前回セッションで
`build_scenario_from_spec`(`initialize()`時のみ使用)は修正済みだったが、この
姉妹関数の更新を見落としていた。

**影響範囲はこのパイプラインに留まらない**: `apply_action`は`TurnBeamSearcher`・
`LookaheadSearcher`・`HeuristicAgent`の全候補評価が使う共通の復元経路であり、
今回の教師データ生成で偶然発見しなければ、既存のHeuristic探索(Phase2/3含む)全体が
現実的な(ポーション・強化カードを含む)Scenarioで静かに誤動作し続けていた。

修正: `HandCards`等の構造化ピル+`Potions`を復元するよう変更。回帰テスト
(`test_upgrade_and_potions_survive_apply_action_restore`)を追加、11/11 PASS確認済み。

### 4.2 Regentの"Stars"資源がCombatScenario/Observationに存在しない(Emulator側API未対応、新規発見)

`ROYAL_GAMBLE`等、Regent固有の"Stars"資源を要求するカードが、`enumerate_legal_actions`
時点では合法と判定されるが、`apply_action`の復元後は非合法(`InvalidOperationException:
Illegal action`)になるケースを発見。原因: "Stars"が`GameObservation.State`に
含まれず、`CombatScenario`にも設定フィールドが存在しないため、復元のたびに
キャラクター既定値へリセットされる。

**RL側の対応**: `HeuristicAgent.choose_action_with_detail`に候補単位の例外捕捉を
追加。評価不能な候補は`skipped`として記録し、決定全体を中断しない(捏造せず、
理由を記録)。**根本修正はEmulator側の対応が必要**(Ascension同様、Common/schemas/READMEの
既知ギャップに追記推奨)。

### 4.3 人間可読ログの`None`スコアでのクラッシュ

`skipped`候補(スコア`None`)を`human_readable_log`が数値としてソートしようとし
`TypeError`でバッチ全体が停止していた。スコアありとスキップ済みを分離して表示するよう修正。

### 4.4 意思決定単体のパフォーマンス爆発(対策実装)

`ENTOMANCER`(敵召喚を伴うエリート)× 27枚デッキ × 16レリックの組み合わせで、
**1つの意思決定だけで8分以上**かかるケースを発見。greedy探索は全合法行動×全対象を
個別に`apply_action`(復元+Step)で評価するため、手札枚数×生存敵数の組み合わせ数に
比例して1決定のコストが増大する。

対策: `choose_action_with_detail`に`deadline`引数を追加し、**候補評価のループ内で
都度**時間予算を確認するよう変更(従来は意思決定と意思決定の"間"でしか確認して
おらず、1決定自体の暴走を防げていなかった)。予算超過後の候補は`skipped`として
記録し、それまでに見つかった最良候補で決定を確定する。

## 5. 固定50Scenarioでの動作結果

**10戦闘のクリーン検証(完了)**:
```text
init成功率: 90.0% (9/10、1件はFAKE_*レリック起因のrelic_mismatchで正しく隔離)
戦闘完走率: 100%
illegal_action: 0
heuristic_exception: 0
emulator_exception: 0
timeout: 0
決定論性: 5/5 (100%)
勝敗: 8勝1敗
1決定あたり時間: 0.44秒
```

**50戦闘フルバッチ(未完了)**: 上記4件のバグを発見・修正する過程で複数回
再実行が必要となり、最終的な対策(候補単位deadline)適用後の完全な50戦闘実行は
本報告時点では完了していない。10戦闘の結果から中核ロジックの健全性は確認できて
いるが、**50戦闘全件でのクリーンな結果は次回報告で提出する**。

## 6. `NEOWS_BONES`隔離件数

10戦闘の検証では0件(NEOWS_BONESを含むシナリオが含まれなかったため)。
前回セッションの調査で根本原因は特定済み(スキップ不可報酬によるレリック余分付与)。
`preflight_validate.py`に検出ロジックを実装済み(`known_issue:neows_bones_reward_duplication`
として`relic_mismatch`と併記)。

## 7. 代表的な軌跡ログ

`Combat/data/trajectories_fixed10_smoke/human_readable_logs/`に9件出力済み。
形式例(`fixed50_299-0.md`より抜粋):

```text
## decision 0: hp=80/80 block=0 energy=3 turn=1
  legal_actions: ['End Turn', 'SOLAR_STRIKE', 'DEFEND_REGENT', 'SPORE_MIND', 'VENERATE']
  top candidates (label, target_enemy_index, score):
    SOLAR_STRIKE         enemy_index=0 score=45.32
    VENERATE             enemy_index=None score=12.10
    ...
  -> SELECTED: SOLAR_STRIKE (enemy_index=0) reward=0.0
```

## 8. 本格生成前に修正が必要な問題

1. **50戦闘フルバッチの完全なクリーン実行**(次回優先課題)
2. 大型デッキ×召喚系エリートの組み合わせは、deadline対策後も1戦闘あたり
   比較的長時間を要する可能性がある — 100戦闘規模の開発セットでは、
   極端な複雑シナリオ(デッキ25枚以上×複数体エリート等)を初期段階では
   母集団から除外するフィルタの追加を検討
3. Regent Stars資源の扱いはRL側の防御的回避に留まる — Emulator側の恒久対応が
   望ましい(Common/schemas/READMEへの追記のみ実施、修正依頼は未実施)
4. `FAKE_*`レリック(装飾コピー生成メカニクス)の頻度・影響範囲を今後の
   バッチで計測する必要がある

## 9. 次回報告予定

50戦闘フルバッチのクリーンな最終結果、および100戦闘規模開発用セットの生成結果。
