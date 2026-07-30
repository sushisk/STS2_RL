# RL報告: no_legal_actions調査 — Scenario 6546-21 — 2026-07-25

## 0. 結論

* 最小再現に成功。`decision_index=13`、`turnNumber=1`(まだ最初のターン中)、
  敵`SOUL_NEXUS`(hp=5、`isAlive=true`、intent=`SOUL_BURN_MOVE`)、
  プレイヤーhp=68/78、block=166、energy=3、手札10枚、`pendingChoice=null`、
  `is_terminal=False`という**非終了状態にもかかわらずlegal actionsが完全に0件**
  になる。
* Combat側のキャッシュ／adapterロジックを完全に迂回した「新規restore→
  生のGetLegalActions()」でも同じく0件 — **Combat側の不具合(adapter/
  キャッシュ/CombatEnv)ではない**ことを確認。
* Policy／Choice Policyに依存せず再現する: Stage Cの元データで
  Heuristic Choice arm(Choice Policy不使用)も**同一Scenarioで同一
  decision数(13)・同一終了理由**で独立に到達している。
* **分類: `emulator_legal_action_bug`(第一候補) / `normal_terminal_detection_
  issue`(次点候補)** — 詳細は3節。RL側では修正せず、最小再現のみ提出する。

---

## 1. 再現手順

`Combat/evaluation/online_eval/investigate_no_legal_actions_6546_21.py`(新規、
読み取り専用の調査スクリプト)。

* Scenario: `choice_policy_online_eval_manifest.jsonl`内の`trajectory_id=
  "6546-21"`(character: NECROBINDER)。
* `preflight_validate`は`status=ok`(状態不整合や初期化例外なし)。
* Choice Policy採用構成(4節記載のadapter)で13 decisionを実行 —
  Stage Cの記録と完全に同一のaction系列を再現(`DRAMATIC_ENTRANCE`→
  `DELAY`→`PULL_AGGRO`→`GRAVE_WARDEN`×2→`DEFEND_NECROBINDER`→
  `DRAMATIC_ENTRANCE`→`LETHALITY`→`VEILPIERCER`→`DEFEND_NECROBINDER`→
  `HIGH_FIVE`→`DEFEND_NECROBINDER`→`BLOCK_POTION`)。
* decision_index=13で`env.get_legal_actions()`が空リストを返す一方、
  `env.battle_state.is_terminal`は`False`。

---

## 2. 失敗時点の状態

```json
{
  "hp": 68, "maxHp": 78, "block": 166, "energy": 3,
  "turnNumber": 1, "combatRoundNumber": 1, "stepIndex": 13,
  "pendingChoice": null,
  "hand_count": 10, "drawPile_count": 12, "discardPile_count": 9,
  "exhaustPile_count": 1, "playPile_count": 0,
  "enemies": [{"id": "SOUL_NEXUS", "index": 0, "hp": 5, "isAlive": true, "intent": "SOUL_BURN_MOVE"}]
}
```

`battle_state.outcome`: `"in_progress"`。
`is_action_continuation_pending_choice()`: `False`(ActionContinuation待ちでもない)。

---

## 3. 確認事項(指示書3節の各項目)

| 確認事項 | 結果 |
|---|---|
| 戦闘が実際には終了済みだったか | **No** — `is_terminal=False`、敵`SOUL_NEXUS`は`isAlive=true`・hp=5(0ではない)、プレイヤーhp=68。エンジン側の終了判定は「未終了」を報告している |
| pending Choiceが残っていないか | 残っていない(`pendingChoice=null`、continuation待ちでもない) |
| legal action生成漏れか | **可能性が高い** — 手札10枚・energy=3・敵1体生存という通常状態にもかかわらず、最低限保証されるはずの「End Turn」すら返らない |
| auto continuation処理後の状態か | 直前のBLOCK_POTION使用はcontinuationを伴わない単純なpotion使用で、continuation処理後の異常ではない |
| Scenario restore不足か | **除外** — `preflight_validate`は`status=ok`、かつ13手を実際に進めた後の失敗であり、初期restore由来ではない。新規`_restore()`+生`GetLegalActions()`でも同じ0件のため、キャッシュ／restore経路の問題でもない |
| Policy／Choice Policyに依存せず再現するか | **Yes** — Stage Cの元記録で、Choice Policy未使用のHeuristic Choice armも同一Scenarioで**同一decision数(13)・同一termination_reason**に到達済み(独立実行、Choice Policy adapterを一切経由しない経路) |

### 迂回確認(Combat側要因の切り分け)

1. `CombatEnv.get_legal_actions()`(通常経路、キャッシュあり) → 0件
2. `BattleEmulator.enumerate_legal_actions(battle_state)`(CombatEnvを介さない直接呼び出し) → 0件
3. `emulator._restore(battle_state)` + 生の`game.GetLegalActions()`(完全に新規のGameInstance再構築、キャッシュ完全迂回) → **0件**

3つとも同一結果 — Combat側のどの層(CombatEnv／BattleEmulatorラッパー／
キャッシュ)にも原因を帰属できない。**ライブGameInstance自体がこの状態で
legal actionを一切生成していない。**

---

## 4. 分類

```text
第一候補: emulator_legal_action_bug
次点候補: normal_terminal_detection_issue
```

**根拠**: 敵`SOUL_NEXUS`(hp=5、intent=`SOUL_BURN_MOVE`)というID/intent名から、
何らかの特殊機構(HPが一定以下になると特殊な状態遷移をする等)を持つ
非標準的なモンスターである可能性が高い。以下のいずれかと推測される。

* **emulator_legal_action_bug**: この特殊状態でGetLegalActions()が本来
  生成すべき行動(最低でもEnd Turn)を生成しないバグ。
* **normal_terminal_detection_issue**: エンジン内部では実質的に戦闘終了/
  状態遷移中と扱われているが、外部公開用の`IsTerminal`フラグが未更新の
  ままlegal actionの生成だけが止まっている内部不整合。

いずれもEmulator内部(`SOUL_NEXUS`固有ロジックの可能性)の問題であり、
`combat_adapter_progress_bug`・`scenario_restore_gap`・`data_issue`は
3節の迂回確認により除外した。両候補の切り分けにはEmulator側のソースコード
調査が必要なため、**RL側では修正せず、本報告と最小再現スクリプトの提出に
留める**。

---

## 5. 提出物

* `Combat/evaluation/online_eval/investigate_no_legal_actions_6546_21.py`
  (最小再現スクリプト、読み取り専用、単独実行可能)
* 本報告書

---

## 6. Choice Policy採用への影響

Policy／Choice Policyのいずれにも依存せず再現すること(3節)を確認したため、
**Choice Policy固有の欠陥ではない**。既存のChoice Policy採用条件(illegal／
exception／mapping mismatch=0、勝率比90%以上等)には影響しない。
