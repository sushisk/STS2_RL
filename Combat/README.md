╔══════════════════════════════════════════════════════════════════════╗
║ 【Phase 0】事前準備                                                  ║
║ ─────────────────────────────────────────────────────────────────── ║
║ 1. 評価用盤面を作成                                                  ║
║    - train / validation / test に分割                                ║
║    - 例: train 100, validation 100, test 500                         ║
║                                                                      ║
║ 2. 戦闘エミュレーターを準備                                           ║
║    - 状態取得                                                        ║
║    - 合法アクション列挙                                              ║
║    - 状態コピー                                                      ║
║    - アクション適用                                                  ║
║    - 敵ターン処理                                                    ║
║    - 勝敗判定                                                        ║
║                                                                      ║
║ 3. 評価関数を定義                                                    ║
║    - HP                                                              ║
║    - ブロック                                                        ║
║    - 敵HP                                                            ║
║    - 被ダメ予測                                                      ║
║    - リーサル可能性                                                  ║
║    - オーブ / Focus / バフ / デバフ                                  ║
║    - 手札・山札・捨て札の質                                          ║
║    - ポーション使用価値                                              ║
║                                                                      ║
║ 4. ポーション価値テーブルをハードコード                              ║
║    - common / uncommon / rare などのレアリティごとに点数を設定        ║
║    - 初期段階では人間の直感値でよい                                  ║
║                                                                      ║
║ 5. 初期重み W0 を設定                                                ║
╚══════════════════════════════════════════════════════════════════════╝
                                │
                                ▼
╔══════════════════════════════════════════════════════════════════════╗
║ 【PotionValueTable】ポーション価値テーブル                            ║
║ ─────────────────────────────────────────────────────────────────── ║
║ 目的: 残ったポーションを「個数」ではなく「価値」に変換する             ║
║                                                                      ║
║ 例:                                                                  ║
║   common   = 1.0                                                      ║
║   uncommon = 1.5                                                      ║
║   rare     = 2.5                                                      ║
║                                                                      ║
║ あるいは、ポーション名ごとに直接点数を設定してもよい                  ║
║                                                                      ║
║ 例:                                                                  ║
║   Fire Potion        = 1.2                                            ║
║   Block Potion       = 1.0                                            ║
║   Focus Potion       = 2.5                                            ║
║   Duplication Potion = 3.0                                            ║
║                                                                      ║
║ 関数:                                                                ║
║   get_potion_value(potion)                                            ║
║   get_remaining_potion_value(state)                                  ║
╚══════════════════════════════════════════════════════════════════════╝
                                │
                                ▼
╔══════════════════════════════════════════════════════════════════════╗
║ 【Phase 1】最小AIの構築                                               ║
║ ─────────────────────────────────────────────────────────────────── ║
║ 目的: まずエミュレーターと評価関数が正しく動くことを確認する          ║
║                                                                      ║
║ 現在状態                                                             ║
║   ↓                                                                  ║
║ 合法アクションを列挙                                                  ║
║   ↓                                                                  ║
║ 各アクションを1手だけ仮実行                                           ║
║   ↓                                                                  ║
║ 評価関数でスコア計算                                                  ║
║   ↓                                                                  ║
║ 最大スコアのアクションを実行                                          ║
║                                                                      ║
║ ※ この段階では未来ターン探索・重み最適化は使わない                    ║
╚══════════════════════════════════════════════════════════════════════╝
                                │
                                ▼
╔══════════════════════════════════════════════════════════════════════╗
║ 【Phase 2】ターン内ビームサーチ                                       ║
║ ─────────────────────────────────────────────────────────────────── ║
║ 目的: 1ターン中のカード順序を探索する                                 ║
║                                                                      ║
║ 現在状態                                                             ║
║   ↓                                                                  ║
║ 合法アクションを列挙                                                  ║
║   ↓                                                                  ║
║ 各アクションを仮実行                                                  ║
║   ↓                                                                  ║
║ 評価値上位 B 個だけ残す                                               ║
║   ↓                                                                  ║
║ さらに合法アクションを展開                                            ║
║   ↓                                                                  ║
║ End Turn 候補を含めて、ターン終了状態を複数生成                       ║
║   ↓                                                                  ║
║ 最良のアクション列を選択                                              ║
║                                                                      ║
║ 推奨初期値:                                                          ║
║   - ターン内ビーム幅 B = 5〜10                                        ║
║   - 最大プレイ数 = 8 程度                                             ║
║   - End Turn は常に候補に含める                                       ║
╚══════════════════════════════════════════════════════════════════════╝
                                │
                                ▼
╔══════════════════════════════════════════════════════════════════════╗
║ 【Phase 3】未来ドローサンプリング + ターン間探索                      ║
║ ─────────────────────────────────────────────────────────────────── ║
║ 目的: 次ターン以降のドロー不確実性を考慮する                          ║
║                                                                      ║
║ 現在状態                                                             ║
║   ↓                                                                  ║
║ 未来ドロー順の仮説を K 個生成                                         ║
║   ↓                                                                  ║
║ 各ドロー仮説 p について探索                                           ║
║   ↓                                                                  ║
║   ターン内ビームサーチ                                                ║
║     ↓                                                                ║
║   End Turn                                                           ║
║     ↓                                                                ║
║   敵ターン処理                                                        ║
║     ↓                                                                ║
║   次ターン開始状態へ                                                  ║
║     ↓                                                                ║
║   評価値上位 M 個を残す                                               ║
║     ↓                                                                ║
║   深さ D まで繰り返す                                                 ║
║                                                                      ║
║ 各候補アクションについて、K 個の仮説の平均スコアを計算                ║
║   ↓                                                                  ║
║ 平均スコア最大のアクション、またはアクション列を実行                  ║
║                                                                      ║
║ 推奨初期値:                                                          ║
║   - K = 5〜10                                                         ║
║   - D = 2                                                            ║
║   - M = 3                                                            ║
║                                                                      ║
║ 高速化後:                                                            ║
║   - K = 20〜50                                                        ║
║   - D = 3〜5                                                          ║
║   - M = 5〜10                                                         ║
╚══════════════════════════════════════════════════════════════════════╝
                                │
                                ▼
╔══════════════════════════════════════════════════════════════════════╗
║ 【Phase 4】重み最適化                                                 ║
║ ─────────────────────────────────────────────────────────────────── ║
║ 目的: 評価関数の重み W を探索器に合わせて調整する                     ║
║                                                                      ║
║ 初期重み W0                                                          ║
║   ↓                                                                  ║
║ W0 周辺にノイズを加えて候補重みを N 個生成                           ║
║   ↓                                                                  ║
║ 各重み W_i で train 盤面をプレイ                                      ║
║   ↓                                                                  ║
║ 戦闘結果を記録                                                        ║
║   - 勝敗                                                            ║
║   - 残りHP                                                           ║
║   - 残りポーション                                                   ║
║   - 残りポーション価値                                               ║
║   - 敵HP削り割合                                                     ║
║   - ターン数                                                         ║
║   ↓                                                                  ║
║ 適応度を計算                                                          ║
║   ↓                                                                  ║
║ 上位個体を選択                                                        ║
║   ↓                                                                  ║
║ 上位個体の平均・分散から次世代の重み分布を更新                        ║
║   ↓                                                                  ║
║ 規定世代まで繰り返す                                                  ║
║                                                                      ║
║ 推奨初期値:                                                          ║
║   - 個体数 N = 15〜30                                                 ║
║   - 上位採用数 = 5〜10                                                ║
║   - 世代数 = 20〜50                                                   ║
║   - 最適化中は K, D, M を軽めにする                                  ║
╚══════════════════════════════════════════════════════════════════════╝
                                │
                                ▼
╔══════════════════════════════════════════════════════════════════════╗
║ 【FitnessCalculator】適応度計算                                       ║
║ ─────────────────────────────────────────────────────────────────── ║
║ 目的: W1, W2, W3... のどの重みセットが良いかを比較する                ║
║                                                                      ║
║ 基本方針:                                                            ║
║   1. 勝利数を最優先                                                  ║
║   2. 勝った戦闘の残りHPを評価                                        ║
║   3. 勝った戦闘の残りポーション価値を評価                            ║
║   4. 負けた戦闘は、敵HPをどれだけ削ったかを補助評価                  ║
║                                                                      ║
║ 初期案:                                                              ║
║   fitness =                                                          ║
║       win_count * 10000                                              ║
║     + sum_remaining_hp_on_wins * 10                                  ║
║     + sum_remaining_potion_value_on_wins * 100                       ║
║     + sum_enemy_hp_removed_ratio_on_losses * 300                     ║
║                                                                      ║
║ 注意:                                                                ║
║   - ポーション価値は get_potion_value() で計算する                    ║
║   - 残りポーション個数ではなく、残りポーション価値を使う              ║
║   - 勝利数の重みは圧倒的に大きくする                                  ║
║   - ポーション温存を評価しすぎて、使うべき場面で使わないAIにしない    ║
╚══════════════════════════════════════════════════════════════════════╝
                                │
                                ▼
╔══════════════════════════════════════════════════════════════════════╗
║ 【Phase 5】検証・採用判定                                             ║
║ ─────────────────────────────────────────────────────────────────── ║
║ 1. train で高成績の重み候補を取得                                     ║
║ 2. validation 盤面で再評価                                            ║
║ 3. validation 成績が最も良い重みを採用候補にする                      ║
║ 4. 最後に test 盤面で一度だけ最終評価                                 ║
║                                                                      ║
║ 注意:                                                                ║
║   - train だけで採用しない                                            ║
║   - test を重み調整に使わない                                         ║
║   - Slay the Spire 2 の更新ごとに再評価できるようにする               ║
║   - ポーション価値テーブルもバランス変更に合わせて更新可能にする      ║
╚══════════════════════════════════════════════════════════════════════╝
```

実装単位としては、次の構成がよいです。

```text
main
├─ load_eval_scenarios()
├─ load_initial_weights()
├─ load_potion_value_table()
├─ build_emulator()
├─ build_state_evaluator()
├─ build_fitness_calculator()
│
├─ Phase 1: run_greedy_baseline()
├─ Phase 2: run_turn_beam_search()
├─ Phase 3: run_sampled_lookahead_search()
├─ Phase 4: optimize_weights()
└─ Phase 5: validate_and_test()
```

クラス構成はこうです。

```text
BattleEmulator
├─ get_state()
├─ clone_state()
├─ enumerate_legal_actions(state)
├─ apply_action(state, action)
├─ process_enemy_turn(state)
├─ is_terminal(state)
└─ get_result(state)

Action
├─ type: play_card / use_potion / end_turn
├─ card_index
├─ target_id
└─ potion_index

StateEvaluator
├─ extract_features(state)
└─ evaluate(state, weights)

PotionValueTable
├─ get_value_by_rarity(rarity)
├─ get_value_by_potion_name(potion_name)
├─ get_potion_value(potion)
└─ get_remaining_potion_value(state)

TurnBeamSearcher
├─ search_best_action_sequence(state, weights)
└─ expand_actions(state)

LookaheadSearcher
├─ sample_future_draw_orders(state, K)
├─ search_with_pattern(state, pattern, weights)
└─ choose_best_action_by_average_score(state, weights)

BattleResult
├─ win
├─ remaining_hp
├─ remaining_potions
├─ remaining_potion_value
├─ enemy_hp_removed_ratio
└─ turn_count

FitnessCalculator
├─ calculate_fitness(results)
├─ calculate_win_count(results)
├─ calculate_remaining_hp_on_wins(results)
├─ calculate_remaining_potion_value_on_wins(results)
└─ calculate_loss_progress(results)

WeightOptimizer
├─ generate_candidates(center_weights)
├─ evaluate_candidate(weights, scenarios)
├─ update_distribution(top_candidates)
└─ optimize()

ScenarioSet
├─ train
├─ validation
└─ test
```

中核の流れはこうです。

```text
optimize_weights():

  center_weights = initial_weights

  for generation in range(max_generations):

      candidates = generate_candidates(center_weights)

      scored_candidates = []

      for weights in candidates:

          results = []

          for scenario in train_scenarios:

              result = simulate_battle(
                  scenario=scenario,
                  weights=weights,
                  search_config=light_search_config
              )

              results.append(result)

          fitness = FitnessCalculator.calculate_fitness(results)

          scored_candidates.append((weights, fitness))

      top_candidates = select_top_candidates(scored_candidates)

      center_weights = update_distribution(top_candidates)

  return best_weights
```

`simulate_battle()` の中では、戦闘終了時にポーション価値も記録します。

```text
simulate_battle(scenario, weights, search_config):

  state = emulator.initialize(scenario)

  while not emulator.is_terminal(state):

      action = searcher.choose_action(
          state=state,
          weights=weights,
          search_config=search_config
      )

      emulator.apply_action(state, action)

      if action.type == "end_turn":
          emulator.process_enemy_turn(state)

  result = emulator.get_result(state)

  result.remaining_potion_value =
      PotionValueTable.get_remaining_potion_value(state)

  return result
```

適応度計算はこうです。

```text
calculate_fitness(results):

  win_count = count wins

  hp_score =
      sum remaining_hp
      over won battles only

  potion_score =
      sum remaining_potion_value
      over won battles only

  loss_progress_score =
      sum enemy_hp_removed_ratio
      over lost battles only

  fitness =
      win_count * 10000
    + hp_score * 10
    + potion_score * 100
    + loss_progress_score * 300

  return fitness
```

ポーション価値テーブルは、初期段階ではこの程度で十分です。

```
PotionValueTable:

  rarity_value:
    common: 1.0
    uncommon: 1.5
    rare: 2.5

  name_override:
    Focus Potion: 3.0
    Duplication Potion: 3.0
    Fire Potion: 1.2
    Block Potion: 1.0
```

考え方としては、

```
まずレアリティで基本価値を決める
↓
特に強い・弱いポーションだけ名前別に上書きする
```

が扱いやすいです。

```text
get_potion_value(potion):

  if potion.name in name_override:
      return name_override[potion.name]

  return rarity_value[potion.rarity]


```
探索器:
  戦闘中に良さそうな行動を選ぶ

評価関数:
  未来盤面の良し悪しを点数化する

重み最適化:
  どの評価関数の重みが強いかを、固定盤面で比較する

PotionValueTable:
  戦闘後に残ったポーションを、個数ではなく価値として評価する

FitnessCalculator:
  勝利数・残りHP・残りポーション価値・敗北時進行度から、
  重みセット全体の成績を1つの数値にする
```
