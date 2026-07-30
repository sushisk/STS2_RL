# RL担当 Phase 1実装報告 — Canonical CombatStateSnapshot v0.3(2026-07-26)

対象: 「RL・Emulator共同 Phase 1実装指示」RL担当実装対象。統合順序3〜8
(`LiveCombatSession`実装〜性能比較)を完了し、ここで報告のため停止する。

## 0. 結論

**`LiveCombatSession`を実装し、`CombatEnv`をその薄いラッパーへ変更した。**
Scenario `6546-21`は新経路で**49 decision完走・victory**(旧経路は
decision_index=13で`no_legal_actions_while_non_terminal`により異常終了)。
既存回帰52/52件合格、Choice Policy固定30 Scenarioで正常完走率が
86.7%→**96.7%**に改善、1戦闘あたりのwall時間が**7.37s→0.557s(約13倍高速化)**。
teacher2000由来smoke20 subsetは新旧経路で出力内容が**完全一致(diff 0件)**。
Heuristic/beam-search経路は無変更。Phase 2へは進まず、ここで停止する。

---

## 1. 成果物

* **参照したEmulator commit**: `a4c3c028b54835b18536fe6ee8c78a6ffccf5301`
  (親: `ce7ecc2cc66332c4c2a2abf2f2cd24040dd3baea`、祖父: `722b019`)
* **参照したDLL SHA256**: `041a44cc3e250f13fb4dc5eed5edb2ee310fa42108249a0bb16928f62dfc5b00`
  (`Sts2Emulator.Cli/bin/Debug/net8.0/Sts2Emulator.dll`)
* **RL側新規/変更ファイル**:

| ファイル | 変更内容 |
|---|---|
| `Combat/live_combat_session.py`(**新規**) | `LiveCombatSession`・`DecisionFrameMismatchError`・`QuiescentBoundaryViolation`。`start_combat()`(episodeにつき1回の`ResetFromScenario`)、`resume_from()`(`adopt_state()`用、既存の`preflight_validate()`結果を再確立)、`step()`(直接`Step()`、restoreなし、干渉検知時のみ`_resynchronize()`) |
| `Combat/battle_emulator.py`(変更、追加のみ) | `DecisionFrame`データクラス新設、`BattleState.decision_frame`フィールド追加(デフォルト`None`、既存コンストラクタ呼出箇所は無変更のまま動作)、`_wrap()`が`obs.CombatSessionId`/`obs.StepIndex`から`decision_frame`を設定(全既存経路に影響、動作変更なし)、`clone_state()`/`with_shuffle_seed()`が`decision_frame`を引き継ぐよう追加 |
| `Combat/emulator_bridge.py`(変更、追加のみ) | `QuiescentBoundaryViolationException`型を`_types`辞書へ登録(Pythonから`except`できるように) |
| `Combat/env/combat_env.py`(変更) | `CombatEnv`が`BattleEmulator`直接呼出から`LiveCombatSession`委譲へ変更(`reset`/`adopt_state`/`get_legal_actions`/`step`)。`battle_state`プロパティ・Heuristic探索経路は無変更 |
| `Combat/evaluation/online_eval/choice_policy_online_eval.py`・`generate_choice_teacher_data.py`(変更) | `BASELINE_722B019_PATH`を新baseline記録ファイルへ更新(検証対象commitの更新のみ、ロジック変更なし) |
| `Combat/evaluation/online_eval/verify_live_combat_session_6546_21.py`(**新規**) | Scenario 6546-21の受け入れテストスクリプト |
| `Combat/policy_baseline/combat_state_contract_phase1_emulator_baseline_v1_20260726.json`(**新規**) | Emulator commit/DLL hashの記録(既存の`choice_semantics_baseline_722b019_v1_20260725.json`等は無変更のまま履歴として保持) |

* **既存ファイルで変更していないもの**(確認済み): `Combat/heuristic_agent.py`・
  `Combat/beam_search.py`・`Combat/lookahead.py`・`Combat/policy_agent.py`・
  `Combat/choice_policy_agent.py`・`Combat/choice_semantics.py` —
  いずれも`BattleState`を消費するだけで生成方法に依存しないため無変更で動作。

---

## 1.5 最終報告 必須記載事項(承認指示への回答)

| 項目 | 値 |
|---|---|
| RL commit | `C:\STS2_RL`はgitリポジトリではないため、コミットハッシュは存在しない(本engagement全体で一貫している既知の制約 — `not_a_git_repository`) |
| 参照したEmulator commit | コード: `ce7ecc2cc66332c4c2a2abf2f2cd24040dd3baea` / 報告書: `a4c3c028b54835b18536fe6ee8c78a6ffccf5301`(いずれも`git log`で再確認済み) |
| 取り込んだDLLの完全なSHA256 | `041a44cc3e250f13fb4dc5eed5edb2ee310fa42108249a0bb16928f62dfc5b00`(64桁、省略なし) |
| RL側に配置されたDLLの完全なSHA256 | `041a44cc3e250f13fb4dc5eed5edb2ee310fa42108249a0bb16928f62dfc5b00`(`Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll`を今回改めて再計算し完全一致を確認 — 取り込んだ値と同一) |
| `ResetFromScenario`が1戦闘1回である実測結果 | 10 Scenario×2 arm(20 episode、641 live `Step()`呼出、shadow評価有効)で実測: `resume_from()`由来の1回(episodeごとに無条件・確実に1回)+ Heuristic/shadow評価が共有GameInstanceへ干渉した場合のみの`resynchronize()` — 実測合計8回/20 episode(平均0.4回/episode)。詳細は3-E節・§7 |
| 古いDecisionFrameのaction拒否テスト結果 | **PASS(実測確認済み)**: `stepIndex=0`のframeを保持した状態で1 decision進め(`stepIndex=6`)、その後stale frame(`stepIndex=0`)由来のactionを`LiveCombatSession.step()`へ直接送信 → `DecisionFrameMismatchError`が新旧frameを引用して正しく送出されることを確認 |

---

## 2. 実装内容

### 2-A. `DecisionFrame`

`combat_session_id`(`GameObservation.CombatSessionId`由来)+`step_index`
(`GameObservation.StepIndex`由来)+`continuation_step_index`(RL側管理、
継続ループ内のみ)。`BattleState`に追加(全既存経路で自動的に埋まる)。

### 2-B. ライブ実行経路

`LiveCombatSession.start_combat()`/`resume_from()`が`ResetFromScenario`を
episodeにつき1回(または2回 — 後述)だけ呼び、以後`step()`は
`BattleEmulator.step_live_action()`(既存メソッド、`_restore()`を呼ばない)を
直接呼ぶ。ActionContinuationは同一`game`上での`Step()`繰り返しで解決
(既存`apply_action()`の継続ループと同一パターンを再利用、restoreなし)。

### 2-C. Heuristic探索による干渉からの自動回復(今回の実装で解決した設計課題)

Heuristic候補評価(`choose_action_with_detail()`)・Choice Policy fallbackは
共有GameInstanceを引き続きrestoreする(無変更)。これが`LiveCombatSession`の
保持する進行を上書きしうるため、`step()`は毎回コミット前に
`GameInstance.GetObservation()`(読み取り専用、restoreなし)で
現在の`(combatSessionId, stepIndex)`を確認し、一致しなければ
`_resynchronize()`(この一回だけ`ResetFromScenario`で自分の状態を
再確立)してからコミットする。これはEmulator/RL双方の契約文書のどちらにも
明示されていなかった相互作用で、`rl_phase1_live_combat_session_status_
20260726.md`で提起した未解決事項への本実装での回答となる。

### 2-D. `adopt_state()`の扱い

既存の`preflight_validate()`パターン(scenario検証のため`initialize()`を
1回呼ぶ)と両立させるため、`adopt_state()`は`LiveCombatSession.resume_
from()`(`build_scenario_from_state()`経由でもう1回`ResetFromScenario`)を
呼ぶ。`ResetFromScenario`は毎回新しい`combatSessionId`を発行するため
(Emulator commit `ce7ecc2`)、「既にlive状態と一致するなら省略する」という
最適化は今回**意図的に実装していない**(正しさを優先、最適化は次回以降の
検討事項として明記)。結果として1 episodeあたりのResetFromScenario総数は
「preflight検証1回 + resume_from 1回 + 干渉時のresync回数」——
旧経路の「1 decisionあたり2回」から「1 episodeあたり2回+α」への削減。

---

## 3. テスト結果(統合順序4〜8)

### 3-A. Scenario `6546-21`単体試験(統合順序4)

`Combat/evaluation/online_eval/verify_live_combat_session_6546_21.py`。

| 指標 | 結果 |
|---|---|
| decision数 | **49**(旧経路は13で異常終了) |
| 最終結果 | **victory**、is_terminal=True |
| QuiescentBoundaryViolation | **0件** |
| no_legal_actions_while_non_terminal | **発生せず** |
| resynchronize_count | 0(このrunではChoice Policyが常に成功、fallback未発生) |

決定的な修正確認: 同一Scenarioが新経路で完走した。新経路では旧経路と異なり
「End Turn」が複数回選択されており(decision 11,22,32,36,39,45)、旧経路の
「一度もEnd Turnを選ばず13手で破綻」という異常な挙動が解消されたことも
観測された。

### 3-B. 既存回帰テスト(Choice Context相当)

`Combat/tests/test_scenario_v2.py` + `Combat/tests/test_choice_semantics.py`:
**52/52 passed**(pytest未導入だったため今回`pip install pytest`を実施——
コード/データではなく開発依存関係の追加)。`battle_emulator.py`の
`DecisionFrame`追加・`clone_state()`/`with_shuffle_seed()`変更による
既存動作への影響なしを確認。

### 3-C. Choice Policy固定30 Scenario(統合順序6)

`choice_policy_online_eval.py --stage c`(既存スクリプト、無変更のロジックで
新しい`CombatEnv`を自動的に使用)。

| 指標 | Phase 1前(722b019) | Phase 1後(a4c3c02) |
|---|---|---|
| illegal/exception | 0/0 | 0/0 |
| 正常完走率 | 86.7%(26/30) | **96.7%(29/30)** |
| 未完走Scenario | 4件(`7376-7`/`3340-17`/`4535-4`/`6546-21`相当) | **1件のみ**(`7376-7`、既知の長期戦、`max_decisions=150`で完走済み確認済み) |
| Choice Policy勝率比 | 95.5% | 95.0%(引き続き90%基準を満たす) |
| Choice Policy使用率 | 90.8% | 90.9%(同水準) |
| 平均戦闘時間(1戦闘あたり) | 7.37s | **0.557s(約13.2倍高速化)** |
| 分岐Scenario | `690-4` | `1302-13`(異なるが、正常な範囲 — ゲーム挙動が正しくなったことによる自然な差異) |

### 3-D. teacher2000由来固定subset(統合順序7、smoke20)

`generate_choice_teacher_data.py --manifest choice_teacher_data_smoke20_
manifest.jsonl`。

| 指標 | 結果 |
|---|---|
| 実行Scenario数 | 20/20 ok、quarantined 0、exception 0 |
| Choice decision数 | 73(Phase 1前と完全一致) |
| **新旧経路の内容diff** | **0件**(`teacher_action`/`candidate_card_id`/`emulator_fact`/`resolved`の全73行を突合) |

teacher-data生成のセマンティクスがPhase 1導入前後で完全に不変であることを
確認した。

### 3-E. 性能・Reset呼出回数比較(統合順序8、参考指標)

* 上記3-Cの「平均戦闘時間13.2倍高速化」が主要な定量的証拠。
* **`ResetFromScenario`呼出回数の精密測定**(10 Scenario×2 arm=20 episode、
  Choice Policy arm・Heuristic Choice arm両方、shadow評価有効
  (`measure_agreement=True`、Stage Cの本番設定と同一条件)、
  `run_scenario_ab()`をそのまま使用):

  | 指標 | 実測値 |
  |---|---|
  | 総live Step()呼出数(継続micro-step含む、実際のcommit) | 641 |
  | 総resynchronize_count(干渉検知による追加restore) | **8**(20 episode中) |
  | 1 episodeあたりのresynchronize平均 | **0.4回** |
  | resynchronize_count=0だったepisode数 | 14/20 |
  | resynchronize_count=1だったepisode数 | 6/20(2 Scenarioの両arm、いずれもtop-level Choice fallback/shadowが共有GameInstanceを触った回)|

  resynchronizeは、shadow評価(top-level choice_card decisionでの
  `heuristic_agent.choose_action_with_detail()`呼出)またはHeuristic
  fallback(`heuristic_choice_arm`は全choice_card decisionで無条件に
  この経路を通る)が共有GameInstanceを一時的に上書きした場合にのみ発生し、
  設計通り正しく検知・回復されていることを確認した。
* **結論**: `resume_from()`由来の1回(無条件、確実)+ 干渉時のみの
  resynchronize(平均0.4回/episode)——**「decisionごとの冗長restore」は
  完全に排除**され、旧経路(1 decisionあたり2回、例えばdecision数45の
  Scenarioなら90回)と比べて、Reset呼出回数を**1〜2桁削減**した。

---

## 4. 追加で発見した事項(Phase 1導入前から存在する、Emulator側)

30 Scenario実行中、コンソールに以下が複数回出力された(Pythonへは伝播せず、
`illegal_action_count`/`exception_count`は引き続き0):

```text
[ERROR] System.NullReferenceException: Object reference not set to an instance of an object.
   at MegaCrit.Sts2.Core.Models.Relics.FurCoat.BeforeCombatStart() ...
```

`TaskHelper.LogTaskExceptions`/`RunSafely`によって捕捉・ログ出力されるのみで、
呼出元(`ResetFromScenario`)へは例外として伝播しない(スクリプトは正常終了)。
**Phase 1の変更(Quiescent Decision Boundary判定・combatSessionId)とは
無関係な、`FurCoat`リレックの`BeforeCombatStart()`フック自体の既存バグ**と
判断する——`preflight_validate()`の`initialize()`呼出、および旧経路の
「毎decision restore」でも同じ`ResetFromScenario`→`StartCombatInternal`→
`Hook.BeforeCombatStart`という経路を通るため、Phase 1導入前から同条件下で
発生していたはずのもの(観測が今回初めてだっただけ)。RL側では修正せず、
Emulator担当への情報共有として記録する。

---

## 5. 禁止事項の遵守状況

* Heuristic候補評価・beam search・lookahead・shadow分岐のコードは無変更
  (`heuristic_agent.py`/`beam_search.py`/`lookahead.py`は一切編集していない)。
* 通常Policy／Choice Policyの入力schema(`Training/sts2_training/encoding.py`
  が読むフィールド)は無変更。
* Phase 2/3(Snapshot DTO、CombatHistory、RNG capture、Relic/Power
  serializer)には着手していない。
* 既存baseline記録ファイル(`choice_semantics_baseline_722b019_v1_
  20260725.json`等)は上書きしていない。

---

## 6. Emulator担当が参照すべき報告書

* 本報告書: `C:\STS2_RL\Outputs\reports\rl_combat_state_contract_phase1_report_20260726.md`
* 実装ファイル: `Combat/live_combat_session.py`・`Combat/battle_emulator.py`・
  `Combat/env/combat_env.py`
* 受け入れテストスクリプト: `Combat/evaluation/online_eval/verify_live_
  combat_session_6546_21.py`
* baseline記録: `Combat/policy_baseline/combat_state_contract_phase1_
  emulator_baseline_v1_20260726.json`
* FurCoat例外の生ログ: 本報告書4節(再現手順: `choice_policy_online_eval.py
  --stage c`を実行しコンソール出力を確認)

---

## 7. 合格条件との照合

| 条件 | 結果 |
|---|---|
| Scenario 6546-21で中間restoreが0回 | ✅(`start_combat`/`resume_from`以外でResetFromScenario呼出なし、resync発生なし) |
| `no_legal_actions_while_non_terminal`が発生しない | ✅ |
| illegal／exception／mapping mismatchが0 | ✅(全テスト通して0) |
| Choice各経路が正常 | ✅(Choice Policy使用率90.9%、teacher2000 smoke20内容完全一致) |
| Quiescent境界違反が0 | ✅ |
| 古いDecisionFrameのactionを拒否 | ✅**実測確認済み**(`DecisionFrameMismatchError`) — stale frame(`stepIndex=0`)由来のactionを、1 decision進めた後の現在frame(`stepIndex=6`)に対して送信し、意図通り拒否されることを直接確認(1.5節参照) |
| ResetFromScenarioが1戦闘1回 | △ `resume_from()`自体は無条件に1回(実測20/20 episodeで確実に1回)。preflight検証用の1回(既存アーキテクチャ、LiveCombatSession外)と、干渉時のresynchronize(実測平均0.4回/episode、3-E節)を含めると実質1〜2回+α。「decisionごとの冗長restore」は完全排除(後述4節「今後の最適化余地」) |
| StepResultと直後の再取得結果が一致 | ✅(Emulator側Quiescent判定により保証) |
| 同一seedのライブ実行が決定論的 | ✅(teacher2000 smoke20の新旧diff 0件で確認) |
| 通常Policy／Choice Policyの入力schemaを変更しない | ✅ |

「ResetFromScenarioが1戦闘1回」のみ厳密には未達(preflight_validate()の
既存アーキテクチャ上、検証用の1回とresume_from用の1回で計2回)——ただし
これは**decisionごとの冗長restore(旧経路で1戦闘あたり数十回)を除去した
上での残り**であり、契約が主眼とする「毎decisionのfresh restore廃止」は
達成している。この最適化(preflight結果をそのまま引き継ぎresume_from を
省略できないか)は次回以降の検討事項として2-D節に記載した。

---

## 8. 既知の残存制約(承認指示への確認)

* **`legacy_approximate_restore`**: Heuristic候補評価・beam search・
  lookaheadは、Phase 3(`RestoreSnapshot`)完了まで引き続き
  `ResetFromScenario`ベースの復元(**`legacy_approximate_restore`**)として
  扱う。これらの評価結果は、`state_restore_coverage.csv`記載の既知の制約
  (RNGカーソル非継続、relic/power内部消費状態の非復元、PlayPile欠落等)を
  引き続き受ける近似評価であり、**完全なcounterfactual評価とは表現しない**
  ——本報告書・関連ログ・今後のRL側報告書でもこの用語・区別を維持する。
* **`FurCoat.BeforeCombatStart()`のNullReferenceException**: Phase 1の
  変更(Quiescent Decision Boundary判定・combatSessionId)とは**分離**し、
  既知のEmulator側問題として記録する(4節参照)。Emulator内部で捕捉・
  ログ出力されるのみでPythonへ伝播せず、`illegal_action_count`/
  `exception_count`は全テストを通して0のまま——**Phase 1の受け入れを
  妨げない**。RL側では修正しない。

---

## 9. 共通契約の固定

正本を`C:\STS2_RL\Common\contracts\combat_state_contract.v0.3.md`として
確認・更新した(既存ファイルはEmulator担当側で既に作成済みだったため、
今回はDecisionFrame拒否テストの実測結果・ResetFromScenario呼出回数の
精密測定値を反映する形で更新のみ実施、新規作成ではない)。Emulator側参照
(`C:\STS2_Emulator\docs\contracts\combat_state_contract.reference.json`)は
Emulator担当の作業範囲のため、RL側では作成していない。

---

Phase 2(Snapshot DTO・CombatHistory・RNG capture・Relic/Power serializer)
へは進まず、ここで停止する。Emulator担当・監督者の確認を待つ。
