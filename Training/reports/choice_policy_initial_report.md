# Choice Policy 初期学習 報告書

生成日: 2026-07-25
対象: RL担当生成の Choice教師データ (`C:\STS2_RL\Combat\evaluation\reports\choice_teacher_data_full_20260725\`, Emulator commit `722b019`, choice_semantics baseline `choice_semantics_baseline_722b019_v1_20260725`)

指示どおり、**データ監査・split作成・1 seedでの最小モデルとbaseline比較まで実行し、ここで停止する**。3 seed評価・オンライン接続・通常Policy/Valueへの変更は行っていない。

## 1. データ監査

| 区分 | 件数 | 扱い |
|---|---|---|
| RL提供 総件数 | 667 | — |
| RL eligible | 656 | — |
| RL excluded (`operation_mode_unknown`) | 11 | 削除せず保持(`reports/choice_policy_baseline/excluded_rows.jsonl`) |
| synthetic nested Scenario | 3 (1 trajectory) | train/validation/testに含めず、専用動作確認のみに使用 |
| Training側scope除外(teacher_action=`choice_confirm`) | 25 | 削除せず保持。§5指示どおりchoice_confirm/choice_skipは学習対象外のため除外 |
| **学習対象(in-scope)** | **628** (173 trajectories) | train/validation/testへ分割 |

RL側の6除外カテゴリのうち、実データで発生していたのは `operationMode=unknown` の11件のみ(`teacher actionがlegal actions外`, `candidate card欠落`, `semantic mismatch` は全656件で0件を確認済み)。`復元非決定的な行` に対応する専用フラグはソースデータになく、RL側の `determinism` 検証(73/667行を3-way diffで0差分確認)を根拠に別途フラグは立てていない。

Training側の追加除外(choice_confirm)はRLのeligibility判定とは別軸であり、`eligibility_summary.json` の除外理由には含まれない。混同しないよう `excluded_rows.jsonl` で理由を分けて記録した。

### Split

trajectory単位で80/10/10分割(seed=20260725)。同一trajectoryが複数splitへ入らないことを分割ロジックで保証(`sts2_training/choice_data.py:split_trajectories`)。

| split | trajectories | decisions |
|---|---|---|
| train | 138 | 512 |
| validation | 17 | 48 |
| test | 18 | 68 |

## 2. モデル構成

`sts2_training/model.py` に `ChoicePolicyNet` を新設。既存Policy(`checkpoints/policy_teacher2000_seed_20260724/best.pt`)から以下を再利用:

- State Encoder(`state_net`: 4560→64→64)の重みをそのままコピー
- Card Encoder(`card_embedding`: 548 cards × 32次元)の重みをそのままコピー
- candidate scoring方式(state表現とaction表現を連結してMLPでスコアリング)
- legal-action masking(`masked_logits`をそのまま再利用)
- checkpoint保存方式(dictionaries埋め込み)

新規追加: Choice Meaning Embedding(13 tokens × 8次元、`normalizedChoiceOperation`と`exceptionEntityKey`を1decisionにつき1つに合成)、`remainingSelectCount`数値入力、Choice専用candidate scoring head(`action_net`/`scorer`は新規重み)。

`originEntityType`等、指示で除外された特徴は一切モデル入力に使用していない(監査用として`raw_pending_choice`/`emulator_fact`はデータ側に保持されているが、モデルには渡していない)。

入力特徴に採用しなかった `normalizedOriginEntityType` / `originValidationStatus` / `lookupStatus` / `matchedRuleId` も同様に未使用(監査専用)。

`choice_skip`/`choice_confirm` は候補として一切スコアリングしていない(legal_actionsから`action_type==choice_card`のみを候補集合として抽出)。

## 3. 学習結果(State/Card Encoderの扱い比較)

1 seed(seed=20260725)、early stopping(patience=8, min_delta=1e-4)。

| variant | best_epoch | test top-1 | test top-3 | test top-5 | test MRR | illegal率 |
|---|---|---|---|---|---|---|
| **主案: freeze + Choice meaning あり** | 6 | 0.6029 | 0.9412 | 0.9853 | 0.7603 | 0.0 |
| freeze + Choice meaning なし | 3 | 0.6176 | 0.9265 | 0.9853 | 0.7615 | 0.0 |
| fine-tune + Choice meaning あり | 6 | 0.6471 | 0.9412 | 0.9853 | 0.7831 | 0.0 |

- **freeze vs fine-tune**: fine-tuneがtop-1で+4.4pt、MRRで+0.023上回った。ただしtest 68件のみでの1 seed結果であり、統計的に有意な差とは言えない。主案どおりfreezeを既定とするが、fine-tuneも明確な劣化はなく有力な選択肢として残る。
- **Choice meaningあり vs なし**: top-1はなし側がわずかに上(+1.5pt)、top-3はあり側が上(+1.5pt)、MRRはほぼ同値(-0.0012)。**§11の成功条件「Choice意味ありモデルが意味なしモデルと同等以上」は、指標により結果が割れており明確には満たされていない(ほぼ同等)。** データ量(train 512件)・候補数分布・1 seedという条件を踏まえると、現時点でnormalizedChoiceOperation/exceptionEntityKeyの有効性は確認も否定もできない、が正確な評価。

## 4. Baseline比較(test, primary=freeze+meaningあり基準)

| baseline | top-1 | top-3 | top-5 | MRR |
|---|---|---|---|---|
| ランダム選択(200試行平均) | 0.2802 | 0.7389 | 0.9527 | 0.5300 |
| 候補カード固定頻度順(train集計) | 0.3529 | 0.6765 | 0.9412 | 0.5566 |
| 主案モデル | 0.6029 | 0.9412 | 0.9853 | 0.7603 |

主案モデルはランダム(top-1 +32.3pt, MRR +0.230)・頻度順(top-1 +25.0pt, MRR +0.204)の双方を明確に上回る。**§11「ランダムbaselineを明確に上回る」は満たしている。**

## 5. 詳細評価(主案モデル, test)

- 候補数別: 1候補(n=3) 1.0, 2候補(n=3) 0.667, 3候補(n=15) 0.333, 4候補(n=19) 0.947, 5候補(n=20) 0.55, 6候補以上は各n<10で参考値扱い。候補数が多いほど単調に悪化するわけではなく、候補数3で特に弱い(要因はmisclassified例を参照)。
- normalizedOperation別: `discard`(n=17) 1.0, `transform`(n=17) 0.529, `return_to_draw_pile_top`(n=11) 0.273, `add_generated_to_hand`(n=7,参考値) 0.286。
- passthrough exceptionEntity別: `relic:GAMBLING_CHIP`(n=10) 0.6、他は operationMode!=passthrough に該当する行が`unknown`バケットに入っている(集計ロジック上の表示、実害なし)。
- remainingSelectCount別: 0(n=19) 0.526, 1(n=47) 0.638, 2(n=2,参考値) 0.5。
- 誤答20件(`reports/choice_policy_baseline/misclassified_test.jsonl`)を保存。battle state概要・Choice意味・legal候補・teacher選択・モデルranking・confidence・operationModeを含む。

## 6. Synthetic nested Choice 動作確認

`synthetic:nested_choice_decisions_decisions_burning_pact` (3 decisions, 候補数3/4/5) に対して推論を実行。**3件とも例外なく動作し、3件ともteacher選択と一致した。** `reports/choice_policy_baseline/synthetic_check.json`。

## 7. 成功条件の判定(§11)

| 条件 | 判定 |
|---|---|
| loader validation error 0 | ✅ (teacher_action_in_legal 656/656 True, semantic_mismatch 0件) |
| teacher action／legal action mismatch 0 | ✅ |
| illegal prediction rate 0 | ✅ (全variant・全baselineで0.0) |
| ランダムbaselineを明確に上回る | ✅ |
| Choice意味ありモデルが意味なしモデルと同等以上 | △ 指標により結果が割れており、明確な優位は確認できず(3節参照) |
| synthetic nested Choiceで推論可能 | ✅ |
| 通常Policy・Value成果物への変更なし | ✅ (`checkpoints/policy_teacher2000_seed_20260724`等は未変更・未上書き) |

6/7を満たすが、Choice meaningの有効性は本パスでは確定的な結論に至らなかった。3 seed評価への移行は、この点についてRL/意思決定者の判断を仰いでから行う(指示どおり本パスでは実施していない)。

## 8. 出力物

- `exports/choice_policy_v1/manifest.jsonl`, `split_manifest.jsonl`, `dictionaries.json`, `dataset_metadata.json`(source SHA256含む)
- `checkpoints/choice_policy_seed_20260725/best.pt`(主案モデル。dictionaries・choice_meaning_dict・provenance埋め込み済み)
- `reports/choice_policy_baseline/metrics.json`(全variant・baseline・breakdown)
- `reports/choice_policy_baseline/misclassified_validation.jsonl` / `misclassified_test.jsonl`
- `reports/choice_policy_baseline/excluded_rows.jsonl`(RL除外11件 + scope除外25件 + synthetic holdout 3件、理由付き)
- `reports/choice_policy_baseline/synthetic_check.json`
- 本報告書: `reports/choice_policy_initial_report.md`

Checkpoint provenance: `emulator_commit=722b019051e6f7ea368fef488abcc6451d6c9d47`, `choice_semantics_baseline_version=choice_semantics_baseline_722b019_v1_20260725`, lookup/alias SHA256、source Choice dataset SHA256(`12F560F42E515FB72BBB52F0497D42E3E3FB7814FD0FDB070F8076C2CC3FE5BF`)、Training commit(このディレクトリはgitリポジトリではないため `not_a_git_repository` と記録)を`checkpoint["provenance"]`に保存済み。

## 9. 次工程への申し送り

- Choice meaningあり/なしの差は本パス(1 seed, test 68件)では有意でない。3 seed評価に進む価値があるか、あるいはfine-tune方向を主案に切り替えるか、判断が必要。
- 候補数3のケースでの精度低下(0.333)は誤答例の追加分析が必要(`misclassified_test.jsonl`参照)。
- 本タスクの禁止事項(Azure不使用、通常Policy/Value不変更、originEntityId等の未使用、オンライン接続なし)はすべて遵守した。
