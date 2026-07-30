# Choice Meaning 有効性分析 報告書

生成日: 2026-07-25
前提: 初期Choice Policy学習(`checkpoints/choice_policy_seed_20260725/best.pt`)は合格済み。本分析はオフライン分析＋同一seed/split/frozen encoderでの小規模ablationのみ。3-seed評価・オンライン接続・追加学習(通常Policy/Value含む)は行っていない。

再現性確認: no-meaningモデルを同一seed(20260725)で再学習し、承認済み報告書の数値(test top-1: meaning 0.6029 / no-meaning 0.6176)と完全一致することを確認した(`reports/choice_meaning_analysis/sanity_check.json`)。

## 1. §2 モデル間比較(test 68件)

| 分類 | 件数 |
|---|---|
| both_correct | 38 |
| both_wrong | 23 |
| no_meaning_only_correct | 4 |
| meaning_only_correct | 3 |

ほぼ互角(meaningが3件だけ独自に正解、no-meaningが4件だけ独自に正解)。代表例(`reports/choice_meaning_analysis/section2_pairwise_comparison.json`):

- **meaning_only_correct**: `relic:GAMBLING_CHIP`(候補3, teacher=DARKNESS)、`discard`(候補5, teacher=ADRENALINE)、`add_generated_to_hand`(候補3, teacher=UNDEATH) — いずれもno-meaningが僅差(confidence 0.48-0.53)で2位に落とした例。
- **no_meaning_only_correct**: `transform`が2件、`relic:GAMBLING_CHIP`、`return_to_draw_pile_top`が各1件 — `transform`はmeaningモデルが誤った候補(PHOTON_CUT/WOUND)を選好する傾向が見える。

## 2. §3 Meaning token別分析(13 token)

全13 tokenのうち、in-scope 628件に実際に出現するのは12 token(`__UNKNOWN__`は0件、想定どおり)。

| token | train/val/test | scenario数 | 候補数平均 | test top-1(meaning) | test top-1(no-meaning) | 差 |
|---|---|---|---|---|---|---|
| discard | 203/18/17 | 57 | 4.92 | 1.000 | 0.941 | +0.059 |
| relic:GAMBLING_CHIP | 76/10/10 | 19 | 2.99 | 0.600 | 0.600 | 0.000 |
| retrieve_to_hand | 71/5/3 | 35 | 7.92 | 0.667 | 0.667 | 0.000(参考値, test n=3) |
| add_generated_to_hand | 62/4/7 | 67 | 3.08 | 0.286 | 0.143 | +0.143(参考値, test n=7) |
| transform | 18/0/17 | 10 | 6.29 | 0.529 | 0.647 | -0.118 |
| exhaust | 35/4/1 | 16 | 3.67 | 0.0 | 0.0 | 0.000(**参考値, test n=1のためノイズ**) |
| retrieve_to_draw_pile_top | 17/5/2 | 14 | 7.04 | 1.0 | 1.0 | 0.000(参考値, test n=2) |
| return_to_draw_pile_top | 4/0/11 | 6 | 4.80 | 0.273 | 0.364 | -0.091 |
| apply_effect_in_place | 18/2/0 | 17 | 2.8 | — | — | test 0件 |
| select_for_power_association | 2/0/0 | 2 | 4.0 | — | — | test 0件 |
| transform_to_specific_card | 4/0/0 | 1 | 2.5 | — | — | test 0件、単一trajectory |
| upgrade | 2/0/0 | 1 | 3.0 | — | — | test 0件 |

観察:
- splitがtrajectory単位のランダム分割のため、token別の分布が偏っている(`transform`と`return_to_draw_pile_top`はvalidationに0件、`apply_effect_in_place`等はtestに0件)。early stoppingがこれらのtokenの挙動を一切見ずに行われている点に注意。
- teacher選択カードは概ね多様(1トークン内で同一カードが繰り返し選ばれる集中は見られない。唯一の例外は`transform_to_specific_card`のDAZED 100%だが、単一scenario・n=4のため一般化できない)。
- candidate集合の重複率は低い(discard 15%、GAMBLING_CHIP 18%、他は概ね3-13%)。→ 同一候補集合の暗記では説明できない。

## 3. §4 情報重複の確認

| 確認項目 | 結果 |
|---|---|
| operationごとに候補カード集合がほぼ固定 | **否**。各operationのtop候補集合シェアは3-13%程度で、集合自体は多様([`candidate_set_almost_fixed_per_operation`](../reports/choice_meaning_analysis/section4_redundancy_checks.json)参照) |
| 特定カードが常に教師選択 | ほぼ無し。80%以上を1カードが占めるのは`transform_to_specific_card`(DAZED, n=4)のみ |
| **candidate card IDだけでoperationが推測可能か** | **強く可能**。628件中584通りの候補集合(candidate label set)のうち582通り(99.66%)が単一operationにしか出現せず、decision数で重み付けした一致率は**99.68%**。つまり「どのカードが候補に挙がっているか」を見るだけで、ほぼ常にoperationが一意に決まる |
| battle state内の手札／山札位置からの漏洩 | 直接検証はしていないが、上記の候補集合の高い純度で説明としては十分。カードの出処(山札/手札等)自体がoperationとほぼ1対1対応するため、候補カード集合を見たCard Encoderだけで実質的にmeaning相当の情報を再構成できていると考えられる |
| 同一Scenario/近似状態がsplitをまたぐ | **なし**。scenario_hash・trajectory_idともに複数splitへの出現は0件(`confirmed_no_leakage: true`) |
| ActionContinuation由来データの重複 | 601/628件がaction_continuation由来。114 trajectoryが複数decisionを持つが(複数枚選択の逐次decision化による想定内の挙動)、split はtrajectory単位のため split をまたぐ重複は構造的に発生しない |

**結論**: Meaning特徴(`normalizedChoiceOperation`/`exceptionEntityKey`)が明示的に効かなかった主因は、**候補カードの構成自体がoperationとほぼ1対1で対応しており、Card Encoderがすでにその情報を暗黙的に学習できる**ため。Meaning tokenは理論的には正しい情報だが、Card Encoder側の情報と99.7%重複しており、限られたデータ(train 512件)ではモデルが重複入力から追加の恩恵を引き出せていない。

## 4. §5 最小カテゴリ案

実件数・挙動差を根拠に、以下の統合を提案する(13→8 token、`__UNKNOWN__`除く):

| 統合後 | 統合元 | 根拠 |
|---|---|---|
| `retrieve` | retrieve_to_hand(79) + retrieve_to_draw_pile_top(24) | 候補数平均が近い(7.92 / 7.04、他operationより明確に高い)、meaning-no_meaning差が両方0で挙動が類似 |
| `other_normalized_rare` | apply_effect_in_place(20) + select_for_power_association(2) + upgrade(2) + transform_to_specific_card(4) | 個別には件数が少なすぎ(2-20件、ほぼtest 0件)、単独カテゴリとして埋め込みを学習させるには小さすぎる |
| `discard`(238) | 統合なし | 最大カテゴリ、単独で十分な件数、他operationと明確に異なる挙動 |
| `exhaust`(40) | 統合なし | 件数は中程度で他operationと候補数平均が異なる(3.67)。test n=1は今回の分割の偶然であり、token自体を統合する根拠にはならない |
| `transform`(35) | 統合なし | 候補数平均6.29と高く、meaning-no_meaning差が-0.118と明確な差があり他operationと異なる挙動 |
| `add_generated_to_hand`(73) | 統合なし | 件数十分、他operationと異なる挙動(+0.143差) |
| `return_to_draw_pile_top`(15) | `retrieve`へ統合しない | 候補数平均(4.8)がretrieve_*(7-8)と明確に異なり、meaning-no_meaning差の符号も逆(-0.091 vs 0.000)。名称は似るが挙動が異なるため統合しない |
| `relic:GAMBLING_CHIP`(96) | 統合なし(指示どおり) | 指示にある通り、通常discard等と評価基準が異なるため安易に統合しない。件数も十分(96件)で単独カテゴリとして妥当 |

## 5. §6 Ablation結果(test, 同一seed=20260725・同一split・同一frozen encoder)

| variant | top-1 | top-3 | top-5 | MRR | illegal率 |
|---|---|---|---|---|---|
| meaning なし | 0.6176 | — | — | — | 0.0 |
| meaning 13-token(現行, 承認済み) | 0.6029 | 0.9412 | 0.9853 | 0.7603 | 0.0 |
| **meaning 統合8-token** | **0.6618** | **0.9559** | **1.0000** | **0.8002** | 0.0 |

統合8-tokenモデルは、13-tokenモデル・meaningなしモデルの**両方を全指標で上回った**(13-token比: top-1 +5.9pt, MRR +0.040 / no-meaning比: top-1 +4.4pt)。これは§2の「meaningあり/なしがほぼ同等」という結果とは異なり、明確で一貫した改善である。

## 6. 総合判断

### 推奨: **B. Meaning tokenを統合する**

根拠:
1. 13 tokenは細かすぎる — 12実token中、test件数10件以上は discard・GAMBLING_CHIPの2つのみ。半数近くがtest 0-3件で評価不能に近い。
2. §4の分析により、細かいtoken分割の情報はCard Encoder側とほぼ完全に重複(候補集合の99.7%が単一operationに対応)しており、少ないデータでは細かい分割の恩恵を学習しきれていない。
3. データ駆動で統合したところ(§5)、8 tokenへの統合により3variant中最良の性能(全指標で他2案を上回る)を達成した。
4. GAMBLING_CHIPのような評価基準が異なる特殊ケースは統合せず個別に保持しており、意味を壊す統合ではない。

Aを推奨しない理由: 「特定operationで明確な改善」が13-token単位では確認できず(discardの+0.059のみが有意サイズ、他は参考値かマイナス)、データ増加を待つより先に統合で明確な改善が既に得られている。
Cを推奨しない理由: 統合形のmeaning特徴はno-meaningを明確に上回っており、meaning情報自体を落とす根拠はない。ただし、本ケースのとおりCを選ぶことがあったとしても、Choice Semantics側のログ・教師データからmeaning情報を削除すべきではない(将来の再検証用に保持)— 本分析でもRL側データは一切変更していない。

## 7. 次工程への申し送り

- 統合8-token案での再学習・3-seed評価に進む価値があると考えられるが、本パスの指示により3-seed評価は開始していない。
- token別の分布がsplitに偏りがある点(§2)は、3-seed評価に進む際にsplit seedを複数試すことで緩和できる可能性がある。
- 出力物: `reports/choice_meaning_analysis/{records,section2_pairwise_comparison,section3_per_token_analysis,section4_redundancy_checks,section5_6_merge_and_ablation,sanity_check}.json`
