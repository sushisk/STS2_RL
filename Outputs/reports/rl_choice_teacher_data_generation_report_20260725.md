# RL報告: Choice教師データ生成 — 2026-07-25

Emulator `722b019`をChoiceデータ生成用baselineとして正式採用し、20 Scenarioの生成前
smokeを実施・合格の上、200 ScenarioのChoice教師データ生成を完了した。
Training開始・Choice Policy実装・lookup/schema変更・Azure使用は一切行っていない。

---

## 0. 結論

* `722b019`を正式baselineとして固定・記録した(1節)。
* 旧Suspect 10件(GUARDS 6/POWER_POTION 2/SKILL_POTION 2)は、raw origin漏洩ではなく
  `pendingChoice.choiceType`の誤ラベルが原因だったことを訂正・明記した(2節)。
* teacher2000から origin-dependent・Gambling Chip・Potion起因・discard/exhaust/
  upgrade/retrieve・複数選択・skip/confirm・1 Step内連続Choice・synthetic nested
  Choiceを含む200 Scenario manifestを構築した(3節)。
* 生成前20 Scenario smokeを2回実行し、決定論・全ゲート条件合格を確認してから
  200 Scenario本生成を実行した(6節)。
* 200 Scenario、667 Choice decisionを生成、656件(98.4%)が学習適格、11件を除外
  (理由: unknown、いずれも推測補完せず正しく除外)(7節)。
* illegal/exception/mapping mismatch/semantic mismatch = 全て0。決定論は3-way比較
  (smoke run A/B/本生成の重複20 Scenario)で0差分確認。
* **Choice Policy実装・Training開始・lookup/schema変更・Azure反映へは進まず、
  ここで停止する。**

---

## 1. Baseline固定

`Combat/policy_baseline/choice_semantics_baseline_722b019_v1_20260725.json`に記録
(既存の`choice_semantics_baseline_v1_20260724.json`/`choice_semantics_provisional_
status_v1_20260724.json`/`baseline_v1_20260724.json`は無変更のまま履歴として保持)。

| 項目 | 値 |
|---|---|
| Emulator commit | `722b019051e6f7ea368fef488abcc6451d6c9d47` |
| `Sts2Emulator.dll` SHA256 | `E3C3D26D7499E93E89F2718CCB51E18A2D66559021BBB5CDCA33980BB644C036` |
| `Sts2Imported.Stage1.dll` SHA256 | `C176109AA6C9887057C09E02245EBEDAAF476FB6E604267D36E787BD3C055900` |
| 検証タイミング | baseline記録直前・smoke実行前・本生成実行前の3回、いずれも一致(ライブDLL再ハッシュ+git HEAD再取得) |
| 採用報告書 | `Outputs/reports/rl_choice_semantics_722b019_adoption_report_20260724.md` |
| 41 Scenario再検証manifest | `Combat/evaluation/online_eval/choice_semantics_reverification_manifest.jsonl` (SHA256は同報告書参照) |
| Choice Semantics lookup | `choice_semantics.v1` / SHA256 `5e7d260f076047b6d0ee02eb79fcd57a06067be49c923621e84ec0df06df8d44` (0d16130時代から無変更) |
| origin-type-alias lookup | `choice_semantics_origin_type_aliases.v1` / SHA256 `a456f0a235c84c2ee815367593c479fb5ed8479916ad4257a55b2d9ea1a33814` (無変更) |
| テスト・比較結果 | `test_choice_semantics.py` 20/20 pass(無変更・再実行不要)、41 Scenario再検証は上記報告書の通り全採用条件合格 |

---

## 2. 監査結果の訂正

`choice_semantics_baseline_722b019_v1_20260725.json`の`audit_correction`節に明記。

* 旧監査でoriginValidationStatus=`suspected_context_leak`と分類していた10件
  (card:GUARDS x6、potion:POWER_POTION x2、potion:SKILL_POTION x2、
  Scenario `1934-19`/`7413-9`/`7551-16`)は、**raw origin漏洩ではなかった**。
* 真因: Emulatorが`pendingChoice.choiceType`を誤って`"GamblingChipDiscard"`と
  報告していた(origin自体は最初から正しかった)。RL側`CHOICE_TYPE_RULES`の
  Tier-1優先度がその誤ラベルを信頼し、本来GUARDS/POWER_POTION/SKILL_POTION
  自身の選択であるべきものにGAMBLING_CHIPのpassthrough結果を誤って適用していた。
* `722b019`でchoiceType自体が修正され、RL側ルール(無変更)は自然に正常な
  origin基準解決(Tier 2/3)へフォールバックするようになった。
* 同根の`ToolboxChooseCard`誤分類3件も同時に解消。
* 修正後、旧Suspect 10件は全てSuspect 0へ再分類(Origin-dependentへ)。
  Gambling Chip自身の開始時null origin(下記5節参照)はこの修正と無関係で、
  引き続き`originValidationStatus=missing`(漏洩ではなく既知の正常挙動)。

---

## 3. Choiceデータ生成対象(manifest構築)

`Combat/evaluation/online_eval/build_choice_teacher_data_manifest.py`(新規)。
teacher2000の`trajectories.jsonl`(2000 Scenario)を静的走査し、
`choice_semantics_lookup.v1.json`の既存entryと`choice_semantics.CHOICE_TYPE_RULES`
から読み取ったentity一覧(card/potion/power/relicカード固有ifを新規追加していない)
のreachabilityで候補プールを構築、カテゴリ別に決定論的sampling(seed=20260725)。

| 項目 | 値 |
|---|---|
| 候補プール | 1261 Scenario |
| 選定数(上限100〜300の範囲内) | **200**(teacher2000由来199 + synthetic nested Choice 1) |
| Full manifest | `Combat/evaluation/online_eval/choice_teacher_data_manifest.jsonl`<br>SHA256 `c11713c0473ad74afc9acfc3c288600a2a1ae03bb7e4189511149bfac5f7607e` |
| Smoke20 manifest | `Combat/evaluation/online_eval/choice_teacher_data_smoke20_manifest.jsonl`(Full manifestの先頭20件)<br>SHA256 `eb234b63844ecb35bf90263fd46fe06af6a3c393c6d3f5438320463a7ab406b2` |

Full manifestのカテゴリ内訳(1 Scenarioが複数カテゴリに該当し得る):

| カテゴリ | 件数 |
|---|---|
| gambling_chip | 20 |
| potion_origin | 68 |
| retrieve | 67 |
| discard | 54 |
| exhaust | 10 |
| upgrade | 15 |
| HOLOGRAM | 21 |
| NIGHTMARE | 7 |
| multi_select | 24 |
| skip_confirm | 27 |
| start_of_combat_choice_card | 27 |
| candidate_multi_choice_in_one_step(静的候補、replay時に確定) | 89 |
| action_continuation_other | 43 |
| nested_choice(synthetic) | 1 |

synthetic nested Choice ScenarioはCombat/tests/test_choice_semantics.py::
test_nested_choice_reattributes_origin_and_classificationと同一のREGENT/
DECISIONS_DECISIONS/BURNING_PACT合成Scenario(`choice_semantics_reverification_
manifest.jsonl`から再利用、teacher2000由来ではないことをmanifest上で明記)。
teacher2000全体は再生成していない。

---

## 4. 保存対象(ロギング仕様)

`Combat/evaluation/online_eval/generate_choice_teacher_data.py`(新規)。
teacher2000生成に使ったものと**同一のHeuristicAgent**
(`greedy_v1_default_weights`、`generate_heuristic_trajectories.py::
build_default_agent()`と同一)でScenarioをreplayし、下記を選択理由に一切影響
させない読み取り専用ロギングとして記録:

* battle state(`battle_state`、step適用前のengine_state全体)
* legal actions(`legal_actions`)
* teacher action(`teacher_action`、HeuristicAgent自身の選択・変更なし)
* candidate card(`candidate_card_id`、`candidate_identifiable`)
* `remaining_select_count`(pendingChoiceから)
* `resolved.operationMode` / `resolved.normalizedChoiceOperation` /
  `resolved.exceptionEntityKey`
* raw Emulator Choice context(`raw_pending_choice`、`emulator_fact`)
* semantic resolution provenance(`lookup_provenance`: lookupVersion/lookupSha256)
* trajectory/Scenario参照(`trajectory_id`/`source_run_id`/`source_combat_index`)
* terminal/truncated情報(`scenario_final_outcome`/`scenario_truncated`/
  `scenario_termination_reason`、Scenario終了確定後に全Choice行へ後付け)

ActionContinuation内で自動吸収されるChoice(HOLOGRAM/SURVIVOR/BURNING_PACT/
ARMAMENTS/NIGHTMARE/GAMBLING_CHIP/...)も、online_policy_eval.pyで確立済みの
ロギング用continuation_resolverラッパーパターンを拡張
(`make_logging_continuation_resolver_full`、実際の選択action自体もログするよう
拡張、選択ロジック自体はHeuristicAgentの`_choose_action_continuation_live`を
無改変で呼び出すのみ)し、`decision_index`(外側の実決定番号)+
`continuation_step_index`(同一決定内の連番、"1 Step内の連続Choice"検出用)で
記録。

---

## 5. 生成前Smoke(20 Scenario、2回実行して決定論確認)

| 指標 | run A | run B |
|---|---|---|
| 実行Scenario数 | 20/20 ok | 20/20 ok |
| quarantined | 0 | 0 |
| exception | 0 | 0 |
| Choice decision数 | 73 | 73 |

**決定論**: run A/B間で73行×比較フィールド8種(teacher_action/candidate_card_id/
candidate_identifiable/remaining_select_count/emulator_fact/resolved/
teacher_action_in_legal/scenario_final_outcome/scenario_truncated)を突合、
**差分0件**。

**smokeゲート条件(指示書6節)判定:**

| 条件 | 結果 |
|---|---|
| illegal/exception/mapping mismatch 0 | ✅ 0/0/0(本harnessに"mapping mismatch"概念なし — teacherは常にlegal_actionsから選択、`teacher_action_in_legal`で73/73件検証済み) |
| semantic mismatch 0 | ✅ 0(`analyze_reverification_722b019.compute_semantic_mismatch`を流用して検証) |
| Choice欠落0 | ✅ 構造的に保証(continuation_resolverラッパーは全継続ステップで無条件に呼ばれる設計、例外なし) |
| ActionContinuation Choice記録成功 | ✅ 73件中67件がaction_continuation由来(残り6件はtop_level_decision) |
| teacher actionがlegal actions内 | ✅ 73/73件 True |
| deterministic | ✅ 上記の通り0差分 |
| origin/choiceTypeが採用baselineと一致 | ✅ `choice_semantics_provenance`のlookupSha256/schemaSha256/originAliasesSha256が`choice_semantics_baseline_722b019_v1_20260725.json`と完全一致 |

72/73件が学習適格(1件はLIQUID_MEMORIESという未登録entityでunknown — 推測せず
正しく除外、バグではない)。**全条件合格**につき本生成へ進んだ。

---

## 6. 本生成(200 Scenario)

出力: `Combat/evaluation/reports/choice_teacher_data_full_20260725/`

| 指標 | 結果 |
|---|---|
| 実行Scenario数 | 200/200 ok(quarantined 0、exception 0) |
| truncated(max_decisions=60到達、非terminal) | 26/200 |
| final_outcome | victory 131 / defeat 43 / in_progress(truncated) 26 |
| 実際にChoiceが発生したScenario | 178/200(残り22件は静的候補としては該当したがreplay時にChoice未発生 — 事前に明記していた通り、replay時にのみ確定する) |

**3-way決定論クロスチェック**: 本生成の先頭20 Scenario(smoke20と同一)を
smoke run A・run Bと突合、73行×3ペア(A対B、A対Full、B対Full)全て**差分0件**。
残り180 Scenarioは同一の無改変harness/Emulatorで単発実行(個別の二重実行は
未実施 — 本engagementの既存報告と同様の範囲限定として明記)。

---

## 7. 集計

### Scenario数

200(teacher2000由来199 + synthetic 1)

### Choice decision数

**667件**

### normalized / passthrough / unknown

| operationMode | 件数 |
|---|---|
| normalized | 514 |
| passthrough | 142 |
| unknown | 11 |

### operation別件数(normalizedChoiceOperation)

| operation | 件数 |
|---|---|
| discard | 242 |
| retrieve_to_hand | 80 |
| add_generated_to_hand | 73 |
| exhaust | 43 |
| transform | 35 |
| retrieve_to_draw_pile_top | 24 |
| apply_effect_in_place | 20 |
| return_to_draw_pile_top | 15 |
| transform_to_specific_card | 5 |
| upgrade | 2 |
| select_for_power_association | 2 |
| (None — GamblingChipDiscardルール由来115 + unknown 11) | 126 |

### exception entity別件数(exceptionEntityKey、passthrough内訳)

| entity | 件数 |
|---|---|
| relic:GAMBLING_CHIP | 115 |
| card:SCULPTING_STRIKE | 5 |
| potion:TOUCH_OF_INSANITY | 5 |
| card:GUARDS | 5 |
| card:HAND_TRICK | 5 |
| card:SNAP | 3 |
| card:TRANSFIGURE | 2 |
| card:NIGHTMARE | 2 |
| **合計** | **142**(passthrough件数と一致) |

### ActionContinuation由来件数

640件(top_level_decisionは27件) — 全体の96.0%がActionContinuation経由。
teacher2000自身のtrajectories.jsonlでは一切観測できなかった種類のChoiceが
大半を占める(build_choice_scenarios_manifest.pyが最初に発見した通り)。

### origin-dependent件数

**541件**(lookupStatusが`resolved_lookup`/`resolved_emulator_fact*`/
`resolved_lookup_unknown`のいずれか — GamblingChipDiscardのchoiceType-rule
115件とunknown 11件を除いた全て)。

### Reset直後(開始時)Gambling Chip件数

**115件**、全て`originValidationStatus=missing`(漏洩0件、`valid`0件)。
`decision_index`分布: 114件が`decision_index=0`、1件が`decision_index=6`
(単一Scenario内、他の非Choice決定を経てから最初のGAMBLING_CHIP発火に到達した
ケース — 発火タイミング自体は依然として最初のターン開始起因と考えられ、
originがnullである点は他114件と同一)。**正直な訂正**: 41 Scenario再検証時の
"36件全てdecision_index=0"という所見は、その時のより小さい母集団(254件)
限定の観測であり、200 Scenario/667件という大きい母集団では100%ではなく
99.1%(114/115)である。学習適格性には影響しない(choiceType自体が意味を
一意決定するため、originの値によらず除外していない — 5節参照)。

### 学習適格/除外件数

| 区分 | 件数 |
|---|---|
| **学習適格(eligible)** | **656**(98.4%) |
| 除外(excluded) | 11(1.6%) |

### 除外理由

全11件が`operationMode=unknown`(推測補完せず正しく除外):

| origin entity | 件数 | 理由 |
|---|---|---|
| DropletOfPrecognition (DROPLET_OF_PRECOGNITION) | 3 | lookup miss(未登録entity) |
| LiquidMemories (LIQUID_MEMORIES) | 2 | lookup miss(未登録entity) |
| なし(origin無し、choiceType=Unsupported) | 6 | no_origin |

### determinism

3-way比較(5節・6節)で全73行×全ペア、差分0件。

### exception / mismatch

illegal teacher action: 0/667(`teacher_action_in_legal`全件True)。
exception: 0/200 Scenario。semantic mismatch: 0/667
(`compute_semantic_mismatch`を全667件に適用、既存の`choice_semantics_lookup.
v1.json`の`evidence`と突合)。candidate_identifiable: 667/667 True。

### 補足: synthetic nested Choice Scenarioの結果

Heuristic(teacher)側の実際の行動順では、BURNING_PACT自身のexhaust選択
(`decision_index=3`、continuation_step 0-2、3行)のみが記録され、
DECISIONS_DECISIONS自身の「どのカードを再生するか」という選択は別行として
記録されなかった。これは`choice_reverification_AFTER_722b019/choice_log.jsonl`
のheuristic armが同一trajectory_idに対して既に示していた結果と**完全一致**
しており(3行、decision_index=3、BURNING_PACT/exhaust×3)、今回のharnessが
既検証済みの挙動を正しく再現していることを確認できた。origin再帰属
(DECISIONS_DECISIONS→BURNING_PACT)そのものの実例は同ファイルのpolicy arm側に
存在する(policyの行動順の違いによりDECISIONS_DECISIONS自身のpendingChoiceが
別途表面化するため)。teacher(Heuristic)専用の本生成では自然には再現されない
という事実を、修正せず正直に記録する。

### 1 Step内の連続Choice

同一`(trajectory_id, decision_index)`に2件以上のChoice行が存在するグループ:
**74グループ**(2件: 38、3件: 13、4件: 2、5件: 2、6件: 19)。

---

## 8. 出力ファイル一覧

* `Combat/policy_baseline/choice_semantics_baseline_722b019_v1_20260725.json`(新規、baseline記録)
* `Combat/policy_baseline/choice_teacher_data_generation_v1_20260725.json`(新規、生成run記録)
* `Combat/evaluation/online_eval/build_choice_teacher_data_manifest.py`(新規)
* `Combat/evaluation/online_eval/choice_teacher_data_manifest.jsonl`(200件)
* `Combat/evaluation/online_eval/choice_teacher_data_smoke20_manifest.jsonl`(20件)
* `Combat/evaluation/online_eval/generate_choice_teacher_data.py`(新規)
* `Combat/evaluation/online_eval/filter_choice_teacher_data_eligibility.py`(新規)
* `Combat/evaluation/reports/choice_teacher_smoke20_run_a/`・`_run_b/`
* `Combat/evaluation/reports/choice_teacher_data_full_20260725/`
  (`scenarios.jsonl`、`choice_teacher_data.jsonl`、`choice_teacher_data_eligible.jsonl`、
  `choice_teacher_data_excluded.jsonl`、`eligibility_summary.json`、`summary.json`)

---

## 9. 禁止事項の遵守状況

* Trainingは開始していない。
* Choice Policyは実装していない。
* lookup/schema/`choice_semantics.py`は無変更(既存の722b019採用済みルールを
  読み取り専用で使用)。
* Emulatorは無変更。
* Azure未使用。
* 旧teacher2000全体は再生成していない(200 Scenarioのみの抽出・追加replay)。
* unknown(11件)は一切推測補完せず、除外のまま記録した。

停止する。Choice Policy実装・Training開始・lookup/schema変更・Azure反映には
進んでいない。
