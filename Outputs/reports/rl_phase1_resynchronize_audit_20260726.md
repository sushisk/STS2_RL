# RL担当報告: Phase 1残存`resynchronize`監査 — 2026-07-26

対象: 「RL担当 作業指示 Phase 1残存resynchronize監査」。20 episode中発生した
8件の`resynchronize`について、ライブ実行の正確性への影響を確定する。
コード修正は行っていない(4節参照 — 監査中の計測は関数のmonkey-patchのみで
実施し、恒久ファイルは一切変更していない)。

---

## 0. 結論

**8件の`resynchronize`は全て、ライブ実行の正確性に影響しない
(`live_correctness_risk`は0件)。** 原因は全件、Heuristic候補評価
(`legacy_approximate_restore`、Phase 1で意図的に変更対象外とした経路)が
共有GameInstanceを一時的に上書きしたことの検知・訂正であり、
`resynchronize`後の状態は`battle_state`が保持する正しい値と完全一致する
ことを実測で確認した。8件とも同一条件で2回再現し、発生箇所・原因・
影響は完全に決定論的。**ライブ経路自体に中間restoreは残っていない
(`resynchronize`はライブ経路の一部ではなく、legacy経路からの干渉に対する
防御機構)。Phase 1追加修正は不要。Phase 2へ進める状態にある。**

---

## 1. `resynchronize`呼出箇所の全数調査

`grep -rn "resynchronize" Combat/*.py Combat/**/*.py`の結果:

* **定義**: `Combat/live_combat_session.py:159` (`LiveCombatSession.
  _resynchronize()`)。
* **呼出箇所**: **`Combat/live_combat_session.py:203`の1箇所のみ**
  (`LiveCombatSession.step()`内、`if not self._is_still_current():
  self._resynchronize(battle_state)`)。

**用途別一覧(全1種類)**: `step()`が実際にactionをcommitする直前、
`_is_still_current()`(`GameInstance.GetObservation()`による読み取り専用
チェック、restoreなし)が現在のライブ`(combatSessionId, stepIndex)`と
このセッションが最後に保持していたframeの不一致を検知した場合にのみ
呼ばれる。他の用途(初期化・診断・冗長呼出)にこの関数が使われている
箇所は存在しない。

---

## 2. 発生ごとの詳細記録

10 Scenario×2 arm(20 episode、shadow評価有効・`choice_policy_online_
eval.py --stage c`と同一条件)を2回実行し、**両回とも同一の8件**が発生した
(3節「再現性」参照)。以下は1回目の実行の全8件(scenario ID・arm以外は
2回目も同一パターン)。

| # | Scenario ID | arm | DecisionFrame(期待、`battle_state`側) | drift先(実際のlive状態) |
|---|---|---|---|---|
| 1 | `302-13` | choice_policy | stepIndex=0 | stepIndex=1(別combatSessionId) |
| 2 | `302-13` | heuristic_choice | stepIndex=0 | stepIndex=1(別combatSessionId) |
| 3 | `2156-12` | choice_policy | stepIndex=0 | stepIndex=1(別combatSessionId) |
| 4 | `2156-12` | heuristic_choice | stepIndex=0 | stepIndex=1(別combatSessionId) |
| 5 | `1814-7` | choice_policy | stepIndex=0 | stepIndex=1(別combatSessionId) |
| 6 | `1814-7` | heuristic_choice | stepIndex=0 | stepIndex=1(別combatSessionId) |
| 7 | `4407-14` | choice_policy | stepIndex=0 | stepIndex=1(別combatSessionId) |
| 8 | `4407-14` | heuristic_choice | stepIndex=0 | stepIndex=1(別combatSessionId) |

**全8件が`decision_index=0`(episodeの最初の実decision)で発生**。該当する
4 Scenarioは全て`choice_policy_online_eval_manifest.jsonl`上で
`start_of_combat_choice_card`カテゴリを持つ(=episode開始直後の最初の
decisionが必ずtop-level choice_card decisionになる) — この4 Scenarioの
「最初のdecisionがChoice」という構造的特徴が、8件全ての発生条件と
完全に一致することを確認した。

### 代表例の全項目記録(#1: `302-13` / choice_policy arm)

* **Scenario ID**: `302-13`
* **arm種別**: **live**(コミット対象の本番arm、choice_policy)。
  ※`resynchronize`自体は「live経路自身の中間restore」ではなく、
  「live経路がlegacy(Heuristic候補評価)経路からの干渉を検知して
  補正する処理」——分類は5節参照。
* **DecisionFrame**: 期待値`{combatSessionId: "ecf91fd8...", stepIndex: 0}`。
* **呼出元関数とファイル・行番号**(`traceback.extract_stack()`で実測):
  ```text
  live_combat_session.py:203 in step            <- _resynchronize()の直接呼出元
  combat_env.py:179 in step                       <- CombatEnv.step()
  choice_policy_online_eval.py:226 in run_episode_ab
  choice_policy_online_eval.py:290 in run_scenario_ab
  ```
* **発生理由**(干渉源、`interference_source_sample`で実測): 直前に
  以下が3回(候補評価ループ内で複数candidate分)実行されていた:
  ```text
  battle_emulator.py:922 in apply_action
  heuristic_agent.py:132 in choose_action_with_detail
  ```
  = `ChoicePolicyAgent`のChoice fallback、または`choose_action_with_detail()`
  自身の候補スコアリングが、共有GameInstanceを`apply_action()`(=`_restore()`
  経由の`ResetFromScenario`)で複数回上書きしていたことが原因。
* **`ResetFromScenario`実行の有無**: **あり**(`_resynchronize()`内で1回)。
* **restore入力が初期Scenarioか中間Observationか**: **中間Observation**
  (`battle_state.engine_state`、`build_scenario_from_state()`経由 —
  `_resynchronize()`のコード自体がこの経路のみを使い、初期Scenario spec
  (`build_scenario_from_spec()`)を使うことは構造的にあり得ない
  — `start_combat()`のみがspec入力を使う)。
* **restore前後の比較**:

  | | HP | maxHp | enemies | turnNumber | stepIndex |
  |---|---|---|---|---|---|
  | drift(直前のlive実態) | 59 | 85 | EXOSKELETON×4、全て29 HP・生存 | 1 | 1 |
  | `battle_state`本来の値(restore目標) | 59 | 85 | EXOSKELETON×4、全て29 HP・生存 | 1 | 0 |
  | restore後のlive実態 | 59 | 85 | EXOSKELETON×4、全て29 HP・生存 | 1 | 0 |

  この例ではHP/enemies自体はdrift前後で変化していない(Heuristic候補評価が
  たまたま戦闘に影響しないchoice_card候補を評価していたため)——
  **重要なのは`stepIndex`/`combatSessionId`の不一致が正しく検知され、
  restore後の状態が`battle_state`の値と完全一致したこと**。
* **その後の戦闘結果**: 該当episodeは正常に最後まで進行(illegal/exception
  なし、`choice_policy_online_eval_stage_c_phase1_livesession`の
  30 Scenario本試験と同条件下で確認済み)。
* **同一条件での再現性**: **完全に再現**(2回の独立実行で同一Scenario・
  同一arm・同一decision_index(0)・同一干渉源で発生 — 3節参照)。

他7件も`interference_source_sample`のcaller_framesが同一パターン
(`heuristic_agent.py::choose_action_with_detail` → `battle_emulator.py::
apply_action`)であることを確認した(`resynchronize_audit_raw.json`に
全件の生データを保存)。

---

## 3. 再現性

同一10 Scenario×2 armを2回連続実行した結果:

| 実行回 | resync件数 | 発生Scenario | 発生decision_index |
|---|---|---|---|
| 1回目 | 8 | `302-13`/`2156-12`/`1814-7`/`4407-14`(各×2 arm) | 全て0 |
| 2回目 | 8 | `302-13`/`2156-12`/`1814-7`/`4407-14`(各×2 arm) | 全て0 |

**完全一致**(件数・Scenario・arm・decision_index全て同一)。
`combatSessionId`の実際の値自体はGUIDのため実行毎に異なるが
(`ResetFromScenario`が毎回新規発行するため想定通り)、**発生パターン・
原因・影響は完全に決定論的**。

---

## 4. 診断コードの使用について

`Combat/evaluation/online_eval/audit_resynchronize_events.py`(新規)を
作成し、以下の関数を**実行時にのみ**monkey-patch(=Pythonの関数オブジェクト
差し替え、ソースファイル自体は無変更)して計測した。

* `BattleEmulator._restore`(呼出のcaller_frames・engine_state記録用)
* `LiveCombatSession._is_still_current`(drift検知時のlive frame/state記録用)
* `LiveCombatSession._resynchronize`(発生時の全項目記録用)
* `choice_policy_online_eval.run_episode_ab`(scenario_id/arm文脈の記録用)

**全てのpatchは`run_audit()`関数内の`try/finally`で確実に元へ戻している**
(`finally`節で4関数とも元の実装へ復元 — 実行完了後、これらのクラス/
モジュールの属性は監査前と完全に同一)。**既存ファイル
(`live_combat_session.py`/`battle_emulator.py`/`choice_policy_online_
eval.py`)は一切編集していない** — diskへの変更はゼロ、revertは
「プロセス終了と共に自動的に完了」(monkey-patchはプロセスローカルな
実行時状態であり、ファイルI/Oを一切伴わない)。

新規追加した`audit_resynchronize_events.py`自体は、本engagementの既存の
調査スクリプト群(`investigate_no_legal_actions_6546_21.py`等)と同じ
位置づけの読み取り専用診断ツールとして保持する(削除しない — 再監査時に
再利用可能)。

---

## 5. 判定分類

8件全てに以下の分類を適用する。

```text
legacy_approximate_restore
```

**理由**: `resynchronize`イベント自体は「ライブ経路に残った中間restore」
ではなく、「legacy(Heuristic候補評価/Choice fallback、Phase 1契約6節で
明示的に変更対象外とされた`legacy_approximate_restore`経路)が共有
GameInstanceへ及ぼした干渉を、ライブ経路が検知して補正する防御機構」
である。干渉源(`interference_source_sample`)は全件`heuristic_agent.py::
choose_action_with_detail`→`battle_emulator.py::apply_action`——
これは`combat_state_contract.v0.3.md`§6が明示的に「Phase 1のスコープ外、
Phase 3まで`legacy_approximate_restore`として扱う」と定めた経路そのもの。

**`live_correctness_risk`には該当しない**——restore後の状態
(`live_state_after_resync`)が`battle_state`本来の値
(`restored_state_summary_battle_state_own_data`)と全件で完全一致
することを実測で確認しており、ライブ経路が誤った状態でactionを
commitするリスクは実際には発生していない。

他4分類の該当有無:

* `initialization_only`: `resynchronize`自体は該当しない
  (これは`start_combat()`/`resume_from()`が担う別カテゴリ — 1 episodeに
  つき無条件で1回発生する、既存のPhase 1報告書で記録済み)。
* `diagnostic_only`: 該当なし(本番コードパス、監査用の計測コードとは別)。
* `unnecessary_redundancy`: 該当なし——8件全てが真の
  `(combatSessionId, stepIndex)`不一致(=実際に共有GameInstanceが
  他所から上書きされていた)を検知した真陽性であり、
  `_is_still_current()`の誤検知(偽陽性)は0件だった。

---

## 6. RL Phase 1 Source Manifest

`C:\STS2_RL\Common\contracts\rl_phase1_source_manifest_20260726.json`
(新規)に、Phase 1対象ソース一式のファイルパス・SHA256・サイズ・
DLL SHA256・Emulator commit・契約SHA256・生成日時を記録した。
`C:\STS2_RL`はgitリポジトリではないため`rlCommit`は
`not_a_git_repository`と明記している。

---

## 7. 結論(必須明記事項)

* **live経路に中間restoreが残っているか**: **残っていない。**
  `LiveCombatSession.step()`自身がactionをcommitする経路
  (`step_live_action()`呼出以降)は無条件のrestoreを一切行わない。
  唯一存在する`_resynchronize()`は、ライブ経路の外側(Heuristic候補評価/
  Choice fallback、`legacy_approximate_restore`)からの干渉を検知した
  場合にのみ発火する**防御的補正**であり、「ライブ経路自身が持つ
  冗長restore」ではない。8件全ての実測で、この補正は正確に機能し
  (restore後の状態が`battle_state`の値と完全一致)、干渉源も全件
  Phase 1契約が明示的にスコープ外とした経路(`legacy_approximate_
  restore`)に限定されることを確認した。
* **Phase 1追加修正が必要か**: **不要。** 8件の発生は全て
  `legacy_approximate_restore`分類であり、`live_correctness_risk`は
  0件。再現性も完全(2回実行で同一パターン)。`resynchronize`機構自体が
  「設計通り正しく機能している」ことの実証であり、修正すべき欠陥は
  発見されなかった。
* **Phase 2へ進めるか**: **進められる状態にある。** 本監査により
  Phase 1のライブ経路実装に是正が必要な問題は見つからなかった。
  ただし進行の可否は監督者・Emulator担当との合意によって最終決定される
  べきであり、本報告はその判断material の提供に留める。

---

## 8. 出力ファイル一覧

* `Combat/evaluation/online_eval/audit_resynchronize_events.py`(新規、
  読み取り専用監査ツール、既存ファイル無変更)
* `Combat/evaluation/online_eval/resynchronize_audit_raw.json`(新規、
  全8件×2回実行分の生データ)
* `Common/contracts/rl_phase1_source_manifest_20260726.json`(新規)
* 本報告書

---

Phase 2へは進まず、ここで停止する。監督者・Emulator担当の確認を待つ。
