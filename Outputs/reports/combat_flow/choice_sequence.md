# Choice処理 — 詳細処理フロー(E節)

対象ファイル: `Combat/choice_semantics.py`(意味解決)、
`Combat/choice_policy_agent.py`(Choice Policy adapter)、
`Combat/policy_agent.py`(Heuristic fallback共通経路)、
`Combat/battle_emulator.py`(ActionContinuation自動吸収の実行部)。

## 分離すべき6種類

### 1. 外部に公開されるChoice(StartOfCombat-scope、`choice_card`/`choice_skip`/`choice_confirm`がtop-level `legal_actions`に含まれる)

`CombatEnv.get_legal_actions()`が返す`legal_actions`の`action_type`集合に
`{choice_card, choice_skip, choice_confirm}`のいずれかが含まれる状態
(`CHOICE_FALLBACK_ACTION_TYPES`、`policy_agent.py:64`)。
このケースだけが「1 real decision」として`env.step()`ループの1周に
明示的に現れる。

### 2. `env.step()`内で自動吸収されるActionContinuation Choice

`is_action_continuation_pending_choice(engine_state)`が真の間、
`BattleEmulator.apply_action()`内の`while`ループ(`battle_emulator.py:
901-909`)が**同一の`env.step()`呼出の中で**繰り返し
`continuation_resolver`を呼んで自動的に解決する。外側の`decisions`列には
**現れない**(この点はChoice教師データ生成・Choice Policyオンライン評価の
両方で`continuation_resolver`にロギング用ラッパーを差し込むことで
初めて可視化される — 元々のtrajectories.jsonl/online_policy_eval.pyの
素の経路では不可視)。

```mermaid
sequenceDiagram
    participant Loop as Harness (run_episode_ab等)
    participant Env as CombatEnv
    participant BE as BattleEmulator
    participant Resolver as continuation_resolver<br/>(make_ab_continuation_resolver / 素のHeuristic)
    participant GI as GameInstance

    Loop->>Env: step(chosen_action, continuation_resolver=resolver)
    Env->>BE: apply_action(state, action, continuation_resolver=resolver)
    BE->>BE: _restore(state) -> ResetFromScenario
    BE->>GI: step_live_action() -> Step(action_id)
    GI-->>BE: next_state
    loop is_action_continuation_pending_choice(next_state)
        BE->>BE: enumerate_legal_actions(next_state)
        Note over BE: キャッシュヒット、restoreなし
        BE->>Resolver: resolver(game, next_state, legal, deadline)
        Resolver-->>BE: continuation_action
        BE->>GI: step_live_action() -> Step(continuation_action.action_id)
        GI-->>BE: next_state (更新)
    end
    BE-->>Env: 最終next_state (継続が全て解決済み)
    Env-->>Loop: transition dict
```

### 3. `choice_card`

Top-levelで現れた場合: `choice_semantics.py::ChoiceSemanticsTable.resolve()`
で意味解決(4-tier優先順位、`choice_semantics.py`モジュールdocstring参照)
した上で、Choice Policy採用構成では
`ChoicePolicyAgent.decide()`(`choice_policy_agent.py`)が
`choice_policy_select()`を呼び、成功すればtop-1合法action、失敗すれば
Heuristic fallback。

継続scopeで現れた場合: `make_ab_continuation_resolver()`
(`choice_policy_agent.py`)が同じ`choice_policy_select()`を
micro-step単位で呼ぶ。

### 4. `choice_skip`

Top-levelで、legal_actionsに`choice_card`が**含まれない**場合
(=skip/confirmのみ): `ChoicePolicyAgent.decide()`は
Choice Policyを一切呼ばず、無条件で`PolicyAgent._heuristic_fallback()`へ
(`choice_policy_agent.py`の`"choice_skip_or_confirm_only"`分岐)。
継続scopeでは`_default_choose_action_continuation_live`/
`_choose_action_continuation_live`が`cards`が空なら
`confirm or skip or legal_actions[0]`を返す(`battle_emulator.py:818-819`、
`heuristic_agent.py:242-243`)。

### 5. `choice_confirm`

`choice_skip`と同じ経路(4と同一の分岐で一括処理)。

### 6. Choice Policy

`Training/sts2_training/choice_inference.py::ChoiceDecision`
(読み取り専用import、`choice_policy_agent.py::build_choice_decision()`
経由)。入力: `battle_state.engine_state`、`legal_actions`(choice_card
候補のみ抽出)、`operationMode`/`normalizedChoiceOperation`/
`exceptionEntityKey`/`remainingSelectCount`(全て`choice_semantics.py`の
`resolve()`出力から)。出力: ranking/top1_action_id/top1_confidence/
top2_confidence/confidence_margin/fallback_reason。

### 7. Heuristic fallback

`PolicyAgent._heuristic_fallback()`(top-level、`policy_agent.py:232-257`)
または`emulator._default_choose_action_continuation_live`/
`heuristic_agent._choose_action_continuation_live`(継続scope、
機能的に同一ロジック — `heuristic_sequence.md`参照)。
top-level fallbackは**フルHeuristic評価**(`heuristic_agent.choose_
action_with_detail()`、L×T回restore)、継続scope fallbackは**軽量**
(キャッシュ済みlegal_actionsから即決定、追加restoreなし)——
この非対称性は`known_risks.md`にも記載。

### 8. Choice Semantics logging

`choice_semantics.py::ChoiceSemanticsTable.resolve()`は
**行動選択に一切影響しない読み取り専用の意味解決層**
(モジュールdocstringの不変条件)。呼出タイミング:

* top-levelの`choice_card`/`choice_skip`/`choice_confirm`decisionでは、
  `run_episode_ab()`(または`online_policy_eval.py::run_episode()`)が
  `legal_types & CHOICE_FALLBACK_ACTION_TYPES`を検出した時点で
  `choice_table.resolve(pendingChoice)`を呼び、`record["choice_semantics"]`
  へ格納 — **decide_fn(ChoicePolicyAgent/PolicyAgent)がどちらであっても
  外側から一律で付与される**(`choice_policy_online_eval.py`参照)。
* 継続scopeでは`make_ab_continuation_resolver()`
  (`choice_policy_agent.py`)/`make_logging_continuation_resolver()`
  (`online_policy_eval.py`)が、実際の`continuation_resolver`呼出の
  **前**に`resolve()`を呼び、結果をsinkへ記録してから本来の
  resolverへ委譲する(選択そのものは変更しない)。

## Choice Policy fallback条件(`choice_policy_select()`, `choice_policy_agent.py`)

1. `operationMode == "unknown"` (Choice Semanticsが解決できなかった)
2. Choice Meaning token未登録 (`ChoiceDecision`が`fallback_reason`を返す全ケース、
   マージ後8-tokenに存在しない場合を含む)
3. `choice_card`候補が0件
4. checkpoint/推論例外(`choice_policy_select()`のtry/except)
5. top-1 action_idが候補内に見つからない(`choice_policy_select()`内で明示チェック)
6. モデル出力が非有限値(`math.isfinite`チェック、`ChoicePolicyAgent`/
   `choice_policy_select()`内)

`choice_skip`/`choice_confirm`のみのdecisionはこのリストの外側で
無条件fallback(条件判定すら行わない、5節参照)。
