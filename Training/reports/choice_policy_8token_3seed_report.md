# 8-token Choice Policy 3-seed評価 報告書

生成日: 2026-07-25
構成: 既存State/Card Encoder(freeze) + 8-token統合Choice Meaning(`reports/choice_meaning_analysis_report.md`のmerge map採用) + remainingSelectCount + Choice専用scoring head。13-token版(`checkpoints/choice_policy_seed_20260725/best.pt`)・meaningなし版は上書きしていない。origin/zone等の追加特徴は使用していない。

merge mapはversioned artifact化: `exports/choice_policy_v1/merge_map.v1.json`(version `choice_meaning_merge_map.v1`, SHA256 `D6BBB9178550A6A2097E30946D056C91516D0BC6B673F426C07A4355BBB6D2EA`)。__UNKNOWN__該当行は0件(`ChoiceDecisionDataset`で防御的に除外実装済み、実際の除外件数も0を確認)。

split(trajectory単位, split_seed=20260725)は3 seedとも完全に同一。既実施のseed 20260725は正式採用の8-token構成で再学習し、13-token時点(合格版)とは別物のcheckpointとして保存した(重み共有元の`state_net`/`card_embedding`5パラメータは全seedで正しくコピーされていることを確認済み)。

## 1. Seed別結果

| seed | best_epoch | val loss | val top-1 | val MRR | test loss | test top-1 | test top-3 | test top-5 | test MRR | illegal率 |
|---|---|---|---|---|---|---|---|---|---|---|
| 20260725 | 7 | 0.9138 | 0.750 | 0.8576 | 1.0064 | 0.6618 | 0.9559 | 1.0000 | 0.8002 | 0.0 |
| 20260726 | 3 | 1.0645 | 0.542 | 0.7396 | 1.1458 | 0.5294 | 0.8971 | 0.9853 | 0.7072 | 0.0 |
| 20260727 | 4 | 1.0998 | 0.583 | 0.7495 | 1.0750 | 0.6324 | 0.9412 | 0.9853 | 0.7701 | 0.0 |

train lossはseed 20260725で0.535、20260726で0.973、20260727で0.901(early stoppingのタイミング差による: 20260725はpatience内で7エポックまで改善し続けたのに対し、他2 seedは3-4エポックで頭打ち)。

candidate count別精度(seed 20260725, test): 1件(n=3)1.0, 2件(n=3)1.0, 3件(n=15)0.333, 4件(n=19)0.895, 5件(n=20)0.75, 6件以上は各n≤3で参考値。

operation category別精度(seed 20260725, test): discard(n=17) 1.0, transform(n=17) 0.588, relic:GAMBLING_CHIP(n=10) 0.7, return_to_draw_pile_top(n=11) 0.364, add_generated_to_hand(n=7,参考値) 0.286, retrieve(n=5,参考値) 0.8, exhaust(n=1,参考値) 1.0。

## 2. 平均・標準偏差(test)

| 指標 | 平均 | 標準偏差 |
|---|---|---|
| top-1 | 0.6079 | 0.0567 |
| top-3 | 0.9314 | 0.0250 |
| top-5 | 0.9902 | 0.0069 |
| MRR | 0.7592 | 0.0387 |
| illegal率 | 0.0 | 0.0 |

## 3. 安定性確認(§4)

| 確認項目 | 結果 |
|---|---|
| top-1がseed間で大きく崩れない | 範囲0.529-0.662(幅13.2pt、標準偏差0.057)。中程度のばらつきがあり、tiny test set(n=68)としては想定内だが「大きく崩れない」を厳密に満たすかはグレー |
| illegal率が全seedで0 | ✅ 3 seedとも0.0 |
| random/frequency baselineを平均で明確に上回る | ✅ random top-1 0.280, frequency top-1 0.353 に対し平均0.608(いずれの単一seedも両baselineを上回る) |
| Meaningなしbaselineを平均で上回る | △ Meaningなし(1 seed, test top-1=0.6176)に対し8-token平均0.6079で**わずかに下回る**(-1.0pt)。ただし8-token側の標準偏差(0.057)の範囲内であり、「有意に劣る」とは言えない |
| synthetic nested Choiceが全seedで実行可能 | ✅ 3 seedとも3decisionsとも例外なく実行(`synthetic_ok_all_seeds: true`) |
| 特定カテゴリで全seed共通の0% collapse | ✅ なし(n≥10のカテゴリで3 seed共通0%は皆無。`collapsed_categories_ge10n_all_seeds_zero: []`) |

13-token版(1 seed, test MRR=0.7603)との比較: 8-token平均MRR=0.7592で**ほぼ同値**(-0.0011、標準偏差0.039の範囲内)。「上回る」は達成していないが「同等」は成立している。

## 4. 最良checkpoint候補

選定基準(validation MRR → validation top-1 → illegal率、**testではなくvalidationで選定**):

| seed | val MRR | val top-1 | val illegal率 |
|---|---|---|---|
| **20260725(選定)** | **0.8576** | **0.750** | 0.0 |
| 20260726 | 0.7396 | 0.542 | 0.0 |
| 20260727 | 0.7495 | 0.583 | 0.0 |

seed 20260725が全validation指標で明確に最良のため選定。保存先: `checkpoints/choice_policy_8token_best/best.pt`。

Provenance埋め込み内容:
- `emulator_commit`: 722b019051e6f7ea368fef488abcc6451d6c9d47
- `choice_semantics_baseline_version`: choice_semantics_baseline_722b019_v1_20260725
- `choice_semantics_lookup_sha256` / `choice_semantics_origin_alias_sha256`: baseline fileから取得済み
- `source_choice_dataset_sha256`: choice_teacher_data.jsonlのSHA256
- `merge_map_version`: choice_meaning_merge_map.v1 / `merge_map_sha256`: merge_map.v1.jsonのSHA256
- `split_manifest_sha256`: exports/choice_policy_v1/split_manifest.jsonlのSHA256
- `seed`: 20260725
- `training_commit`: not_a_git_repository(Trainingディレクトリはgit管理外のため)
- `model config`: state_dim/card_vocab/choice_meaning_vocab(9)/embedding次元/hidden_dim等

## 5. 推論interface確認(§6)

`sts2_training/choice_inference.py`に`ChoiceDecision`を実装(`run_choice_inference_demo.py`でオフライン検証、Combat adapterへは未接続)。

- 入力: battle_state, legal_actions(choice_card以外は内部で除外), operationMode, normalizedChoiceOperation/exceptionEntityKey, remainingSelectCount
- 出力: legal候補のranking, top-1 action_id, top-1/top-2 confidence, confidence margin, fallback_reason
- 検証結果: test split 8件の実データで正常動作(teacher一致率は個別事例のため参考程度)。synthetic nested Choice 3件も例外なく動作。
- fallback発火確認: `operationMode=unknown` → `fallback_reason=operation_mode_unknown`、candidate無し(choice_confirmのみ) → `fallback_reason=no_choice_card_candidates`。いずれも正しく検出。
- 推論レイテンシ: 平均5.85ms/decision(CPU, batch無し, 8件計測)。

## 6. §7 採用条件の判定

| 条件 | 判定 |
|---|---|
| 3 seedすべてillegal rate 0 | ✅ |
| 平均top-1がrandom／frequency baselineを明確に上回る | ✅ (+32.8pt / +25.5pt) |
| 平均top-1がMeaningなしモデルと同等以上 | △ わずかに下回る(-1.0pt、標準偏差0.057の範囲内で「同等」とは言えるが「以上」ではない) |
| 平均MRRが13-token版を上回る、または同等で分散が小さい | △ ほぼ同値(-0.0011)。「上回る」ではないが「同等」は成立。分散(std=0.039)自体は「小さい」と断定できるほどではない |
| synthetic nested Choiceが全seedで正常 | ✅ |
| loader／mapping error 0 | ✅ |

6条件中4条件を明確に満たし、2条件("以上"/"上回る"の厳密な意味では)未達だが、いずれも「同等」の範囲。§7に明記された救済規定「Meaningなしとの差が小さくても、8-token版が安定しており入力コストが軽微なら、Meaning付き構成を採用してよい」に該当すると判断する: 差は1pt前後でノイズ範囲内、illegal率0・synthetic正常・category collapseなしという安定性は満たしている。

## 7. オンライン評価へ進む可否

**限定オンライン評価への候補として進めることを推奨する。**

根拠: illegal/loader/mapping errorが0、baseline比で明確な優位、synthetic動作確認済み、Meaningなし・13-token版との差はいずれも小さくノイズ範囲内で「同等」水準は満たす。seed間のtop-1ばらつき(std=0.057)はtest 68件という少データゆえの限界であり、限定オンライン評価はこのばらつきを実環境で検証する好機でもある。

留保事項: 
- 3 seed中2 seedがMeaningなしと13-token版のいずれよりもやや劣る結果(20260726, 20260727)であり、選定されたseed 20260725固有の強さが偶然の可能性を否定できない。限定評価の結果次第では追加seedでの再検証が望ましい。
- candidate数3件の区分(discard中心)で精度0.333と低く、実運用時の注意点として引き継ぐ。

## 8. 出力物

- `exports/choice_policy_v1/merge_map.v1.json`(versioned merge map)
- `checkpoints/choice_policy_8token_best/best.pt`(選定checkpoint、provenance埋め込み済み)
- `reports/choice_policy_8token_3seed/metrics.json`(seed別・集計・baseline・stability全データ)
- `sts2_training/choice_inference.py`, `run_choice_inference_demo.py`

オンラインadapterへの接続、RL側変更、追加データ生成は行っていない。本報告をもって停止する。
