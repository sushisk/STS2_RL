# Heuristic arm — 詳細処理フロー(D節)+ 評価用shadow処理(F節)

対象: `Combat/heuristic_agent.py::HeuristicAgent.choose_action_with_detail()`
(60-181行)、greedy(Phase 1)経路のみ(`searcher`/`lookahead_searcher`未設定時)。
これがteacher2000生成・オンライン評価の両方で使われている実際の経路。

## D. Heuristic arm(1 decision)

```mermaid
sequenceDiagram
    participant Harness as Harness<br/>(generate_heuristic_trajectories.py /<br/>choice_policy_online_eval.py)
    participant HA as HeuristicAgent
    participant BE as BattleEmulator
    participant Eval as StateEvaluator
    participant GI as GameInstance(共有singleton)

    Harness->>HA: choose_action_with_detail(battle_state, deadline, historical_state_keys)
    HA->>BE: enumerate_legal_actions(battle_state)
    Note over BE,GI: 通常状態なら_restore()経由でResetFromScenario 1回
    BE-->>HA: legal: list[dict] (L件)

    loop 各 action in legal (L件)
        HA->>BE: target_candidates(battle_state, action)
        Note over BE: AnyEnemy+生存2体以上のみ複数候補、他は[None]
        BE-->>HA: target_index候補 (T件、通常1)
        loop 各 target_index (T件)
            alt deadline経過
                HA->>HA: skipped.append(reason="deadline_exceeded")
            else 未経過
                HA->>BE: apply_action(battle_state, action, target_index,<br/>continuation_resolver=self._choose_action_continuation_live)
                Note over BE,GI: _restore() (ResetFromScenario 1回、破棄・仮の状態)<br/>-> step_live_action() (Step 1回)<br/>-> ActionContinuation whileループ(継続があれば)
                BE-->>HA: resulting: BattleState (この候補を選んだ場合の未来、破棄予定)
                HA->>Eval: evaluate(resulting.engine_state, weights, outcome)
                Eval-->>HA: score: float
                HA->>HA: same_state_loop / historical_loop チェック(ペナルティ)
                HA->>HA: candidates.append(detail); best更新判定
            end
        end
    end

    HA-->>Harness: (best: ChosenAction, candidates+skipped: list[dict])
    Note over Harness: best.actionのみが実際にCombatEnv.step()へ渡され、<br/>他の(L×T-1)件の評価結果(resulting状態)は破棄される
```

**要点**: 1 decisionあたり`apply_action()`(= `_restore()`経由の
`ResetFromScenario` 1回 + `Step()` 1回)が**候補の数だけ**呼ばれる
(L×T回、Lは合法手数、Tは対象候補数)。うち採用される1候補分の結果だけが
実際にHarness側の`CombatEnv.step()`で**再度**適用される
(= もう1回`ResetFromScenario`+`Step()`) —
候補スコアリング時の`resulting`状態は再利用されず、破棄される。
詳細な回数根拠は`call_count_summary.csv`参照。

## Heuristic armでの clone/restore/ActionContinuation処理

* **clone**: `choose_action_with_detail()`自体は`battle_state`を
  clone/deepcopyしない — 各候補の`apply_action()`は`battle_state`を
  読み取り専用の入力として使い、新しい`BattleState`を作って返すだけ
  (元の`battle_state`は変更されない、`BattleEmulator`が状態を持たない
  設計だから — `battle_emulator.py`モジュールdocstring参照)。
* **restore**: 上記シーケンス図の通り、候補ごとに1回。
* **LegalActions再取得**: 各候補の評価では`apply_action()`が内部で
  `enumerate_legal_actions()`を呼ぶのはActionContinuation解決ループの中
  だけ(`battle_emulator.py:907`) — その場合もキャッシュヒットのため
  追加restoreなし(`legal_actions_sequence.md`参照)。
* **ResetFromScenario回数**: 1 decisionあたり ≈ L×T + 2
  (自身の`enumerate_legal_actions` 1回 + 候補評価L×T回 +
  Harness側の最終commit 1回)。
* **Step回数**: 候補評価でのL×T回(仮) + ActionContinuationがあれば
  候補ごとに追加 + 最終commit時にもう1回(+継続分)。
* **ActionContinuation処理**: 各候補評価の`apply_action()`呼出時にも
  `continuation_resolver=self._choose_action_continuation_live`
  (206-221行の`_choose_action_continuation_live`、選択ロジックは
  `battle_emulator.py`の`_default_choose_action_continuation_live`と
  ほぼ同一 — スコアベースでカード選択)が渡される。**候補評価中の
  継続解決も実際にGameInstanceを進める**(仮の未来を組み立てるため)。
* **スコア計算**: `StateEvaluator.evaluate(resulting.engine_state, weights,
  outcome)` — 純Python、追加のC#呼出なし。
* **最終action決定**: `best`(最高スコア、同点はsystem以外を優先
  `_prefer_candidate_over_best()`)。`deadline`超過で`best`が一つも
  決まらなかった場合は`fallback`(最初に見つかった候補、スコア0.0)を返す。

---

## F. 評価用shadow処理(Choice Policy armでのagreement計測)

対象: `Combat/evaluation/online_eval/choice_policy_online_eval.py::
run_episode_ab()`のtop-levelブロック(該当箇所)、および
`Combat/choice_policy_agent.py::make_ab_continuation_resolver()`の
`shadow_resolver`引数。

```mermaid
sequenceDiagram
    participant Loop as run_episode_ab (choice_policy arm)
    participant Main as 本番決定経路<br/>(ChoicePolicyAgent.decide / continuation resolver)
    participant HA as HeuristicAgent (shadow)
    participant Env as CombatEnv

    Loop->>Env: legal = env.get_legal_actions()
    Loop->>Main: record = choice_policy_agent.decide(battle_state, legal, deadline)
    Note over Main: 本番の選択(chosen_action)がここで確定
    opt shadow_top_level=True かつ choice_card含む
        Loop->>HA: heuristic_agent.choose_action_with_detail(env.battle_state, deadline=None)
        Note over HA: heuristic_sequence.mdのD節と同一の<br/>L×T回restoreを伴うフル評価<br/>(env.battle_stateはまだ変更されていない<br/>= 本番armへの副作用なし)
        HA-->>Loop: shadow_chosen, shadow_scores
        Loop->>Loop: agrees_with_heuristic = (record.chosen_action == shadow_chosen.action)
    end
    Loop->>Env: env.step(record.chosen_action, ...)
    Note over Env: ここで初めて実際にstateが進む -<br/>shadow評価はこの時点まで一切env._stateを書き換えていない
```

### 本番armへの副作用防止方法

* shadow呼出(`heuristic_agent.choose_action_with_detail(env.battle_state, ...)`)は
  **`env.battle_state`を読み取り専用で渡すだけ** — `HeuristicAgent`の
  候補評価は`BattleEmulator.apply_action()`経由で常に**新しい**
  `BattleState`を返す設計であり、渡した`battle_state`自体やその
  `engine_state`辞書を変更しない(Pythonオブジェクトとしての同一性は
  保たれるが、中身の書き換えは発生しない設計)。
* `env.step()`(本番の状態遷移)はshadow呼出の**後**に実行される —
  コード上、shadow評価の結果(`shadow_chosen`)は`agrees_with_heuristic`
  というログ用フィールドの計算にのみ使われ、`env.step()`へ渡す
  `chosen_action`には一切影響しない(`record["chosen_action"]`は
  shadow呼出より前に`Main`側で既に確定済み)。

### stateの共有/複製方法

* shadow側は`env.battle_state`(本番armが今まさに使っている
  **同一のPythonオブジェクト**)をそのまま渡す — 複製(deepcopy/clone_state)
  は行われない。安全な理由は上記の通り、`HeuristicAgent`の候補評価が
  内部で`BattleEmulator.apply_action()`(常に新しい戻り値を返し、入力を
  書き換えない設計)しか使わないため。
* **継続(ActionContinuation)レベルのshadow**は別経路
  (`make_ab_continuation_resolver()`の`shadow_resolver`引数) —
  こちらは`heuristic_agent._choose_action_continuation_live`
  (**軽量**、既にキャッシュ済みのlegal_actionsから1つ選ぶだけで
  追加restoreなし)を使う。top-levelのshadow(重量、L×T回restore)とは
  コストが大きく異なる — `call_count_summary.csv`参照。
