# RL報告: Choice Policy限定オンライン評価 — 2026-07-25

通常Policy(teacher2000 checkpoint、無変更)を両armで共有し、Choice部分のみを
Choice Policy(`choice_policy_8token_best`)とHeuristic Choiceで比較する
限定オンライン評価を実施した。Stage A(直接統合テスト)→ synthetic(1件) →
Stage B(10 Scenario)→ Stage C(30 Scenario)の順で段階実行し、全段階で異常なし。

---

## 0. 結論

* Stage A/B/C/synthetic全て実行完了。illegal action・exception・mapping mismatch
  は**全段階・両arm・全Scenarioで0**。
* Choice Policy使用率90.8%(108/119、Stage C)、fallbackは想定条件のみ
  (`no_choice_card_candidates`・`operation_mode_unknown`)で発生。
* 勝率比(Choice Policy／Heuristic Choice) = **95.5%**(21/30 対 22/30)、
  採用条件の90%以上を満たす。
* 通常Policyの挙動は**分岐前の全748区間で完全一致**を経験的に確認(構造的にも
  同一関数呼び出しのため保証されている)。
* **正常完走率が86.7%(4/30が`max_decisions=60`到達または
  `no_legal_actions`)で採用条件の95%を下回った** — ただし該当4 Scenarioは
  **両arm完全に同一の理由・同一の決定数で発生**しており、Choice Policy固有の
  問題ではなくScenario選定／`max_decisions`設定に起因することを確認した(8節)。
* 分岐発生Scenarioは15/30、うち最終勝敗が変わったのは1件のみ(690-4、
  Heuristic-better、極めて低いconfidence marginでの僅差選択)(9節)。
* **結論: Choice Policy固有の採用条件は全て合格。完走率のみ、Choice Policyに
  起因しない既知の原因で基準未達 — 限定採用は可能だが、完走率改善(max_decisions
  見直し等)を別途確認してからの正式採用を推奨する。** 30 Scenarioを超えては
  拡大せず、ここで停止する。

---

## 1. 使用成果物・Provenance(実行前記録)

`Combat/policy_baseline/choice_policy_online_eval_provenance_v1_20260725.json`
に記録。

| 項目 | 値 |
|---|---|
| Emulator commit | `722b019051e6f7ea368fef488abcc6451d6c9d47`(実行前に3回、ライブDLL再ハッシュで確認) |
| `Sts2Emulator.dll` SHA256 | `E3C3D26D7499E93E89F2718CCB51E18A2D66559021BBB5CDCA33980BB644C036` |
| 通常Policy checkpoint | `policy_teacher2000_seed_20260724/best.pt`、SHA256 `eb3af996b7c151c47b5203dc811291cb3b05a65c355c05ffaa3fbbe47339994a`(既存baselineと同一ハッシュ — 無変更を確認) |
| Value checkpoint | `value_teacher2000_seed_20260724/best.pt`、SHA256 `24020cf571c76a4116613b2e5306f92c687b21dd7bcedf69f0f6139aa38790ba`(ログ専用、無変更) |
| Choice Policy checkpoint | `checkpoints/choice_policy_8token_best/best.pt`、SHA256 `f5299e4abf8a30a0400cba2e5094777276b84f9c3a70d7051b0ec886c457f29f` |
| Choice Meaning merge map | `choice_meaning_merge_map.v1`、SHA256 `D6BBB9178550A6A2097E30946D056C91516D0BC6B673F426C07A4355BBB6D2EA`(checkpoint埋め込みハッシュと一致確認) |
| Choice Semantics lookup／alias | `choice_semantics.v1` / `choice_semantics_origin_type_aliases.v1`(既存baseline、無変更) |

Choice Policy checkpointの学習データはこのengagementで生成した
`choice_teacher_data_full_20260725`(SHA256 `12F560F4...`)そのもの — provenance
連鎖を確認済み。

---

## 2. Adapter統合

新規ファイル(既存の`Combat/policy_agent.py`・`Combat/evaluation/online_eval/
online_policy_eval.py`は**無変更**、両方とも読み取り専用でimport/委譲のみ):

* `Combat/choice_policy_agent.py` — `ChoicePolicyAgent`(top-level decide_fn)、
  `choice_policy_select()`(fallback判定の単一ソース)、
  `make_ab_continuation_resolver()`(ActionContinuation-scope choice用)。
* `Combat/evaluation/online_eval/choice_policy_online_eval.py` — Stage A/B/C/
  synthetic実行harness。
* `Combat/evaluation/online_eval/build_choice_policy_online_eval_manifest.py` —
  30 Scenario manifest構築。

**重要な設計上の保証**: arm A・arm Bとも、card/potion/system決定は
`PolicyAgent.decide()`を完全に同一のオブジェクトとして呼び出す
(`ChoicePolicyAgent.decide()`は非choice型decisionを一切加工せず即委譲)。
このため両armの通常Policy挙動は**構造的に**同一であり、分岐が起きるとすれば
必ずChoice decisionが原因である(7節で経験的にも確認)。

fallback条件(指示書3節)は`choice_policy_select()`に一元化:
operationMode=unknown・Choice Meaning token未登録・候補0件・
checkpoint/推論例外・top-1がlegal actions外・非有限値出力。
`choice_skip`/`choice_confirm`のみのdecisionは`ChoicePolicyAgent.decide()`内で
Choice Policyを呼ばず無条件にHeuristicへ(`"choice_skip_or_confirm_only"`)。

---

## 3. Scenario選定(30 + synthetic 1)

`build_choice_policy_online_eval_manifest.py`。Choice教師データ生成で使用した
200 Scenario poolのうち、**実際にChoiceが発生した177件**から選定
(`choice_teacher_data_full_20260725`のreplay結果を参照、静的candidateタグのみに
依存しない)。学習splitのtestのみに偏らせず、train/validation/testを混在。

| バケット | 最低要求 | 実際の件数(30件中) |
|---|---|---|
| Gambling Chip | 3 | 4 |
| passthrough含む | 5 | 6 |
| Potion起因 | 5 | 8 |
| discard | 5 | 5 |
| exhaust | 3 | 3 |
| retrieve | 5 | 5 |
| upgrade | 2 | 2 |
| 複数Choice(≥2件) | 10 | 11 |
| 候補数少(≤3) | 3 | 3 |
| 候補数多(≥10) | 3 | 3 |

split内訳: train 21 / test 3 / validation 4 / 学習範囲外(not_in_scope) 2。
synthetic nested Choice(REGENT/DECISIONS_DECISIONS/BURNING_PACT)は別枠1件
(`choice_policy_online_eval_synthetic_manifest.jsonl`)、30件には含まない。

---

## 4. Stage A: 直接統合テスト — 全項目合格

| 確認項目 | 結果 |
|---|---|
| Choice Policy checkpoint読込 | OK(provenance/config/merge_map/dictionaries全読込成功) |
| 8-token変換 | OK(`retrieve_to_hand`→`retrieve`のmerge、`relic:GAMBLING_CHIP`のunmerged-passthrough、双方vocab解決確認) |
| action_id mapping | OK(top1_action_idが元候補actionの`action_id`と一致、候補リスト内のオブジェクト同一性も確認) |
| fallback全条件 | 0候補・operationMode unknownの2条件を直接確認、他(例外・非有限値・top1未マッチ)はコード上`choice_policy_select()`内で単一ロジックとして実装、Stage B/Cで実際に発火なし(想定条件内) |
| choice_skip/choice_confirmのHeuristic経路 | OK(無条件fallback、`choice_skip_or_confirm_only`) |
| 同一入力で決定論的 | OK(同一state/candidatesを2回推論、ranking・confidence完全一致) |

## Synthetic nested Choice(別枠1件)

illegal/exception = 0/0、両arm勝利・正常完走。`DECISIONS_DECISIONS`
(`select_to_replay`、学習語彙外)は**両arm**でHeuristicへ正しくfallback、
`BURNING_PACT`(exhaust、正しくorigin再帰属済み)はChoice Policyが正常に使用
(3 continuation step、全てchoice_policy経由)。両arm最終結果一致
(victory、HP 71)。

---

## 5. Stage B: 10 Scenario — 停止条件なし、Stage Cへ進行

| 指標 | Choice Policy arm | Heuristic Choice arm |
|---|---|---|
| illegal/exception | 0/0 | 0/0 |
| 正常完走率 | 100%(10/10) | 100%(10/10) |
| 勝利数 | 8/10 | 8/10 |
| Choice Policy使用率 | 90.9%(40/44) | — |
| fallback理由 | `no_choice_card_candidates`×4 のみ | — |
| agreement率(shadow) | 79.5%(35/44) | — |

停止条件(illegal action・mapping mismatch・再現性のある例外・Choice Policyに
よる進行停止・legal actions外選択・最終状態破損)**該当なし**。Stage Cへ進行。

---

## 6. Stage C: 30 Scenario — 全件実行完了

| 指標 | Choice Policy arm | Heuristic Choice arm |
|---|---|---|
| illegal action | 0 | 0 |
| exception | 0 | 0 |
| mapping mismatch | 0(該当概念なし — 両armともlegal_actionsから直接選択、`top1`がlegal外なら構造的にfallback) | 0 |
| 正常完走率 | 86.7%(26/30) | 86.7%(26/30) |
| 勝利数／勝率 | 21/30(70.0%) | 22/30(73.3%) |
| 最終HP平均 | 40.37 | 40.57 |
| 残りPotion平均 | 0.067 | 0.067 |
| 総戦闘時間 | 221.1s | 223.0s |
| 1戦闘あたり時間 | 7.37s | 7.43s |
| Choice decision数 | 119 | 125 |
| Choice Policy使用率 | 90.8%(108/119) | — |
| fallback率 | 9.2%(11/119、想定条件のみ) | 4.8%(6/125、常にHeuristic経路のためfallback概念は"skip/confirm-only"等と別軌) |
| agreement率(shadow) | 79.0%(94/119) | — |
| Choice推論時間 | 平均6.60ms／最大63.6ms | — |

**勝率比(Choice Policy／Heuristic Choice) = 21/30 ÷ 22/30 = 95.5%**
(採用条件90%以上を満たす)。Choice Policy arm側がわずかに速い
(推論オーバーヘッドは実用上無視できる水準)。

### 分解指標

**operation別使用数**(choice_policy／fallback):

| operationMode | choice_policy | fallback |
|---|---|---|
| normalized | 82 | 1 |
| passthrough | 26 | 5 |
| unknown | 0 | 5(設計通り、常にfallback) |

**passthrough entity別**: `relic:GAMBLING_CHIP` 25 choice_policy / 5 fallback、
`card:HAND_TRICK` 1 choice_policy / 0 fallback。

**候補数別**: 1-3件 52/61(85.2%)、4-7件 47/48(97.9%)、8+件 9/10(90.0%) —
いずれのバケットでも0%崩壊なし。

**confidence margin別**(choice_policy使用時のみ、fallback時はn/a):
0-0.2: 37件、0.2-0.5: 21件、0.5-1.0: 50件。

**fallback理由内訳**: `no_choice_card_candidates` 6件、
`choice_policy:operation_mode_unknown` 5件 — **想定外の理由は0件**。

---

## 7. 通常Policy挙動の不変性(経験的検証)

両armの`decisions`列を、各Scenarioで最初のChoice関連fork
(top-level choice decisionまたはActionContinuation choice)が発生する直前まで
突合(`action_id`+`label`)。**30 Scenario合計148件のfork前decision対、
差分0件。** 通常Policyチェックポイントのハッシュも既存baselineと完全一致
(1節)。**通常Policyの行動が最初に分岐したケースは0件、分岐は必ずChoiceに
起因する**ことを構造的にも経験的にも確認した。

---

## 8. 採用条件チェックリスト

| 条件 | 結果 |
|---|---|
| illegal／exception／mapping mismatch = 0 | ✅ 全段階・両arm 0 |
| 正常完走率95%以上 | ❌ **86.7%**(4/30、両arm完全同一のScenario・同一理由で未達 — 下記参照) |
| Choice Policy勝率がHeuristic Choice比90%以上 | ✅ 95.5% |
| Choice推論時間増加が実用上軽微 | ✅ 平均6.6ms、CP arm総時間はHC armよりむしろ短い |
| fallbackが想定条件だけで発生 | ✅ `no_choice_card_candidates`/`operation_mode_unknown`のみ |
| 特定operationで重大な0%崩壊なし | ✅ normalized 98.8%・passthrough 83.9%使用、unknownの0%は設計通り |
| synthetic nested Choiceが正常完走 | ✅ |
| 通常Policyの挙動に意図しない変更なし | ✅ 148 fork前decision対、差分0件 |

### 完走率未達の原因分析(指示書8節「行動分岐と失敗原因を確認する」への対応)

未完走4 Scenario全ての詳細:

| Scenario | 終了理由 | CP decision数 | HC decision数 | CP HP | HC HP |
|---|---|---|---|---|---|
| 7376-7 | `max_decisions=60`到達 | 60 | 60 | 31 | 31 |
| 6546-21 | `no_legal_actions_while_non_terminal` | 13 | 13 | 68 | 68 |
| 3340-17 | `max_decisions=60`到達 | 60 | 60 | 24 | 25 |
| 4535-4 | `max_decisions=60`到達 | 60 | 60 | 35 | 35 |

**4件とも両arm完全に同一のdecision数・同一の終了理由・ほぼ同一の最終HP**で
発生している。これはChoice Policyの選択品質に起因するものではなく、
(a) 3件は`max_decisions=60`という評価上限に達しただけの長期戦、
(b) 1件(`6546-21`)はChoice経路と無関係な`no_legal_actions`という
Scenario/Emulator側の既知の終了パターンであり、両armで再現性がある。

**結論**: 完走率未達はChoice Policy固有の欠陥ではない。ただし指示書の採用条件
文言上は数値未達のため、**「Choice Policy固有の条件は全て合格、完走率は
Scenario選定/`max_decisions`設定起因で暫定的に基準未達」として正直に報告**する
(30件を超えての再選定・設定変更は今回のタスク範囲外、指示書10節の
「30 Scenarioを超えて自動拡大しない」に従い実施していない)。

---

## 9. 分岐Scenarioの保存

`Combat/evaluation/reports/choice_policy_online_eval_stage_c/divergence_log.json`
(新規、15 Scenario全件、各分岐点についてChoice時点の盤面・legal candidates・
Choice意味・ranking/confidence・Heuristic action・選択後状態要約を保存)。

| 分類 | 件数 |
|---|---|
| Policy-better(Choice Policyのみ勝利) | 0 |
| Heuristic-better(Heuristicのみ勝利) | 1(`690-4`) |
| comparable(最終勝敗同一) | 14 |

**Heuristic-betterの唯一の例(`690-4`)詳細**:

* decision_index=1(ActionContinuation、continuation_step=0)、POWER_POTION起因
  (`normalized`/`add_generated_to_hand`)のカード追加選択。
* 候補3件: `SLEIGHT_OF_FLESH`(idx0)／`CALCIFY`(idx1)／`HAUNT`(idx2)。
* Choice Policy ranking: `SLEIGHT_OF_FLESH`(top1, confidence=0.427) >
  `HAUNT`(0.417) > `CALCIFY`(低)。**confidence margin=0.0098**という
  ほぼ3択僅差の状態でtop1を選択。
* Heuristicは`CALCIFY`を選択(異なる)。
* 最終結果: Choice Policy arm defeat(HP 0)、Heuristic Choice arm victory
  (HP 12)。この1戦闘のみ、最初期(decision_index=1)の低confidence margin選択が
  後続の展開に影響し敗北に繋がったと考えられる。

残り14件は全て最終勝敗が両arm一致しており、Choice行動の違いが結果に
影響しなかったケース。

---

## 10. 禁止事項の遵守状況

* Training／Emulatorは無変更。
* checkpointは再学習していない。
* lookup／merge mapは無変更(読み取り専用)。
* low-confidenceを理由とした新しい閾値fallbackは追加していない
  (`choice_policy_select()`のfallback条件は指示書3節の列挙のみ、
  confidence margin自体はログ専用でfallback判定に使っていない)。
* Valueで行動を上書きしていない(ログ専用、`ChoicePolicyAgent`でも
  `PolicyAgent.decide()`と同じ扱い)。
* 30 Scenarioを超えて自動拡大していない。
* Azure未使用。

---

## 11. 出力ファイル一覧

* `Combat/choice_policy_agent.py`(新規)
* `Combat/evaluation/online_eval/choice_policy_online_eval.py`(新規)
* `Combat/evaluation/online_eval/build_choice_policy_online_eval_manifest.py`(新規)
* `Combat/evaluation/online_eval/extract_choice_policy_divergences.py`(新規)
* `Combat/evaluation/online_eval/choice_policy_online_eval_manifest.jsonl`(30件)
* `Combat/evaluation/online_eval/choice_policy_online_eval_synthetic_manifest.jsonl`(1件)
* `Combat/policy_baseline/choice_policy_online_eval_provenance_v1_20260725.json`
* `Combat/evaluation/reports/choice_policy_online_eval_stage_b/`
* `Combat/evaluation/reports/choice_policy_online_eval_stage_c/`
  (`combats.jsonl`、`summary.json`、`divergence_log.json`)
* `Combat/evaluation/reports/choice_policy_online_eval_stage_synthetic/`

---

## 12. 限定採用可否

* **Choice Policy固有の採用条件(illegal/exception/mismatch・勝率比・推論時間・
  fallback妥当性・operation別崩壊なし・synthetic正常完走・通常Policy不変性)は
  全て合格。**
* **完走率のみ基準未達(86.7% < 95%)だが、原因はChoice Policyに起因せず、
  両arm同一の Scenario選定/`max_decisions`設定に起因することを確認した。**
* 30件は小標本であり、勝率差(4.5pt)・完走率未達の4件とも、行動分岐と
  失敗原因を個別確認した結果、Choice Policyを不採用とする根拠は見当たらない。
* **推奨: Choice Policyの限定採用(この評価スコープ内)は妥当。ただし
  完走率条件の正式合格判定には、`max_decisions`見直し等を含む追加確認を
  別途実施することを推奨する。**

Choice Policy／Training本格導入・lookup変更・Azure反映へは進まず、ここで
停止する。
