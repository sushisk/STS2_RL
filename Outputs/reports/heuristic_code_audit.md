# 既存Heuristicコード監査 (2026-07-21)

`STS2_RL/Combat/*.py`(main.py, heuristic_agent.py, beam_search.py, lookahead.py,
state_evaluator.py, potion_value_table.py, battle_result.py, battle_emulator.py,
scenario_set.py)を対象に実施。大規模書き換えは行わず、現状の経路と問題点を整理する。

## 実行入口

`main.py::main()` → `run_greedy_baseline()` / `run_turn_beam_search()` /
`run_sampled_lookahead_search()`(それぞれPhase1/2/3に対応)→ 共通の
`_run_scenarios()` → `simulate_battle()`。

## 状態入力

`BattleEmulator.initialize(scenario_spec)`。現状`scenario_set.py`は
Ironclad vs CalcifiedCultistの単一パターンのみ生成しており、**新しい
`reconstruct_floor_state.py`由来の復元Scenarioとはまだ未接続**。

## LegalActions取得箇所

`BattleEmulator.enumerate_legal_actions(battle_state)`。呼び出し箇所:
`HeuristicAgent._choose_greedy`、`TurnBeamSearcher.expand_actions`/`_search`、
`LookaheadSearcher.choose_best_action_by_average_score`。

## 行動候補生成

合法行動 × `BattleEmulator.target_candidates()`(**位置ベース**の`target_index`、
0..alive数-1)。**新しい安定`enemyIndex`/`target_enemy_index`機構は現状未使用**
(前回セッションで`apply_action`に追加済みだが、Heuristic側からはまだ呼ばれていない)。

## 評価関数

`StateEvaluator.evaluate()`。1手先読み(候補行動を1つ適用した結果状態を評価)。
特徴量: player_hp_ratio, player_block, enemy_hp_ratio, predicted_incoming_damage,
lethal_bonus, buff_debuff_score, hand_quality, potion_value。勝敗は
`victory_bonus`/`defeat_penalty`で特徴量スコアに加算(置き換えではない、
複数ターン平均時の飽和を避ける設計)。

## 探索処理

* `TurnBeamSearcher`(Phase2): ターン内カード順序探索。beam_width=8, max_plays=8。
  `dedup_by_state`で等価状態を統合。
* `LookaheadSearcher`(Phase3): `ShuffleRngSeed`でK個の将来ドロー仮説をサンプリングし、
  D手番×M幅のビームで平均スコアを計算。K=5, D=2, M=3がデフォルト(設計書の
  「速度改善後にK=20-50等へ」との指示通り、現状は保守的な値)。

## Emulator呼び出し

**Heuristicスタック全体が`BattleEmulator`を直接呼び出しており、`CombatEnv`を
経由していない**(`CombatEnv`自体が今回のセッションで新規実装されたため、既存コードは
まだそれ以前の設計)。直接呼び出し箇所: `enumerate_legal_actions`, `apply_action`,
`is_terminal`, `target_candidates`, `with_shuffle_seed`, `get_result`。

## キャッシュ

`BattleEmulator.use_legal_action_cache`(デフォルトTrue): `ResetResult`/`StepResult`が
副産物として持つ`LegalActions`を`BattleState._cached_legal_actions`に保持し、
再取得の`ResetFromScenario`往復を省略。探索側は`dedup_by_state`(state_evaluator互換の
ハッシュキー)で等価状態を統合、重複探索を回避。

## 乱数利用

`LookaheadSearcher.rng`(Python `random.Random`)が将来ドロー仮説のseedを生成し
`CombatScenario.ShuffleRngSeed`経由でEmulatorへ渡す。`HeuristicAgent`/
`TurnBeamSearcher`/`StateEvaluator`自体に乱数はなく、同一入力に対し決定論的。

## ログ出力

`main.py::_run_scenarios(verbose=True)`が標準出力へ1行/意思決定のトレースを
print出力するのみ。**構造化・永続化されたログは存在しない**。

## 戦闘終了判定

`BattleEmulator.is_terminal(battle_state)`(`GameObservation.IsTerminal`由来)。
`simulate_battle`のループは`max_steps=200`の安全上限を持つが、**打ち切り発生を
`BattleResult`へ記録する仕組みがない**(打ち切られた戦闘も通常終了と区別つかない)。

## 既知の未実装・暫定処理(今回の教師データ生成で対処が必要な項目)

1. `max_steps`打ち切りが`BattleResult`に反映されない。
2. 対象選択が位置ベース`target_index`のみで、安定`enemyIndex`との対応付けがない。
3. `HeuristicAgent`は最良候補の`score`のみ保持し、**全候補の評価値
   (`action_scores`)を返す機構がない**。
4. `search_depth`/`nodes_evaluated`のカウンタが存在しない。
5. 同点時のタイブレークルールが暗黙(`score > best.score`の厳密不等号 =
   先勝ち)で、明示的に文書化・記録されていない。
6. 合法行動が空の状態(終端状態)で`choose_action`を呼ぶと`RuntimeError`。
   呼び出し側での終端チェックが必須(現状`simulate_battle`はループ条件で
   保証しているが、新オーケストレーションでも同様の保証が必要)。
7. `reconstruct_floor_state.py`由来の復元Scenarioとまだ接続されていない。
8. `apply_action`/`initialize`周りの例外処理がなく、1件の異常が
   バッチ全体を止めうる。

## 今回の対応方針

上記のうち1〜3・6〜8は、新規`Combat/data/generate_heuristic_trajectories.py`
(下記報告参照)で対処。4・5は将来のHeuristic改善時の課題として明記するに留め、
現時点では「取得可能な値のみ保存し、存在しない値は捏造しない」の原則に従い
`action_scores`はgreedyモードのみ(全候補スコア取得可能)で提供し、
`search_depth`/`nodes_evaluated`はbeam/lookahead導入後の課題として`null`のまま
記録する。
