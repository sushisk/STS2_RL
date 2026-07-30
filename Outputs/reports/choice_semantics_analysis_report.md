# Choice Semantics・評価分析 レポート

作成日: 2026-07-24
担当: Choice Semantics・評価分析(Emulator非起動・調査/仕様作成のみ)
関連成果物: `Common/schemas/choice_semantics_schema.json`(スキーマ)、
`Common/schemas/choice_semantics_lookup.v1.json`(実データ、57行)

前段の初回報告(チャット内)からの更新点: 「要目視確認」49件を全件ソースまで読み、hardcode
lookupテーブルを確定させた。

---

## 1. Choice Semantics JSON Schema案

`Common/schemas/choice_semantics_schema.json` に確定版を保存済み(draft-07、`Common/schemas`
既存4ファイルと同じ形式)。要点:

- キーは `(origin_entity_type, origin_entity_id)`。
- `semantic_confidence`: `confirmed`(ソースコード直読) / `prompt_confirmed`(Emulatorの正規prompt
  で既に確定) / `inferred`(命名からの推測、未読) / `unknown`。
- `semantic_source`(ご指定の4区分): `emulator_fact` / `prompt_confirmed` / `hardcoded_entity_rule`
  / `unknown`。
- `evidence` オブジェクトにEmulatorが実際に返す生値(`emulator_choice_operation` /
  `emulator_destination_zone` / `emulator_origin_resolvable` / `card_select_cmd_method` /
  `prompt_is_canonical`)を保持し、RL側の正規化値と分離。
- `combat_scope`: `combat` / `non_combat_deck` / `either` — 戦闘外Deck系choiceを戦闘Policyの
  優先対象と混ぜないためのご指示に対応。

`normalized_choice_operation` の確定enum(19種、実例1件ずつ):

| 値 | 実例 |
|---|---|
| discard | SURVIVOR |
| exhaust | BURNING_PACT(canonical prompt)/ PURITY・ASHWATER(prompt非canonical) |
| upgrade | ARMAMENTS |
| transform | (deck-level, rest site等) |
| transform_to_specific_card | GUARDS(MINION_SACRIFICEに固定変換) |
| enchant | (deck-level) |
| remove | (deck-level, CookRestSiteOption等) |
| retrieve_to_hand | HOLOGRAM, DREDGE, GRAVEBLAST, SECRET_TECHNIQUE等 |
| retrieve_to_hand_free | LIQUID_MEMORIES |
| retrieve_to_draw_pile_top | HEADBUTT, COSMIC_INDIFFERENCE |
| return_to_draw_pile_top | GLIMMER, PHOTON_CUT, THINKING_AHEAD |
| clone_to_hand | DUAL_WIELD, HEIRLOOM_HAMMER |
| add_generated_to_hand | ABUNDANCE, DISCOVERY, QUASAR, SPLASH, 各種Potion |
| add_generated_to_deck | HEFTY_TABLET, LEAD_PAPERWEIGHT, SEA_GLASS(非戦闘) |
| select_to_replay | DECISIONS_DECISIONS |
| select_for_power_association | NIGHTMARE(→NIGHTMARE_POWERのassociated_card) |
| apply_effect_in_place | SNAP, SCULPTING_STRIKE, HAND_TRICK, TRANSFIGURE, TOUCH_OF_INSANITY |
| bundle_select | SCROLL_BOXES(3枚束を選ぶ、個別カード選択と構造が異なる) |
| enemy_forced_deck_addition | KNOWLEDGE_DEMON(敵move起因、唯一のplayer非起因choice) |

---

## 2. choice発生entity一覧(棚卸し)

`CardSelectCmd.From*` 呼び出しをEmulator側decompiled sourceから機械抽出: **113ファイル・122箇所**。

| カテゴリ | 箇所数 | 戦闘Policy優先対象か |
|---|---|---|
| card | 43 | ○ |
| relic | 28 | 一部(pickup時は×、Toolbox/ChoicesParadoxのみ○) |
| power | 5 | ○ |
| potion | 9 | ○ |
| monster | 1 | ○(唯一の敵起因choice) |
| event | 31 | ×(戦闘外Deck系、別区分) |
| rest_site | 2 | ×(戦闘外Deck系、別区分) |
| (multiplayer sync infra、実体なし) | 2 | 対象外 |

解決状況の内訳(122箇所):
- **method名のみで`choiceOperation`確定**: 61件(`FromHandForDiscard`/`FromHandForUpgrade`/
  `FromDeckForUpgrade`/`FromDeckForRemoval`/`FromDeckForTransformation`/`FromDeckForEnchantment`)
- **近傍の正規prompt(6種)で確定**: 12件
- **要ソース精読**(汎用entry point、正規prompt不使用): **49件 — 全件読了・分類済み**

読了49件の内訳(詳細は`choice_semantics_lookup.v1.json`):
- 28 card、8 potion系(実質8、potion category内では9件中1件は既にmethod名解決)、2 power、
  8 relic、1 monster、2 event(代表例のみ精読、残り29 eventは同型パターンへの帰着を確認)

origin(`originEntityType`/`originEntityId`)が**構造的にnull**になるのは、`FromDeckGeneric`/
`FromDeckForUpgrade`/`FromDeckForTransformation`/`FromDeckForEnchantment`/`FromDeckForRemoval`系統
(**50/122、41%**、`PlayerChoiceContext`引数が存在しないメソッド群)。ただしこれらは`combat_scope=
non_combat_deck`(Deck構築系)がほとんどで、戦闘Policyの学習対象からは元々除外される。

**戦闘中に発生しうるchoice**(`combat_scope=combat`)は実質: card 43 + power 5 + potion 9 +
monster 1 + relic 2(TOOLBOX, CHOICES_PARADOX、いずれも試合開始時限定)= **60箇所**。残りの relic
26箇所とevent 31・rest_site 2は非戦闘Deck系。

---

## 3. hardcode(RL側lookup)候補一覧

`choice_semantics_lookup.v1.json` の `semantic_source: "hardcoded_entity_rule"` 行(44件)が
候補全体。特に重要な発見:

- **PURITY / ASHWATER**: 実際の操作は`exhaust`だが、prompt keyがカード/ポーション固有の
  `SelectionScreenPrompt`(共有`ExhaustSelectionPrompt`ではない)のため、Emulatorは
  `choiceOperation: "unknown"`のまま。**method名やEmulator返り値だけでは判定できず、ソース精読が
  必須だった実例**。
- **NIGHTMARE**: 選択したカードは移動せず、`NIGHTMARE_POWER`の`associatedCard`になる。
  `Combat/data/README.md`が既に明記している「`player_powers[].associated_card`保存必須、欠落時は
  `missing_associated_card`でquarantine」という既存の制約と直結する発見。
- **KNOWLEDGE_DEMON**(唯一の非card/relic/power起因choice): 新規`BlockingPlayerChoiceContext`を
  都度生成するため、`FromChooseACardScreen`系なのにoriginが恒久的にnull。
- **SCROLL_BOXES**: 個別カードでなく3枚1組の「束」を選ぶ、構造的に異なるUI。既存の
  `choice_card`(1カード=1候補)の枠組みに素直に収まらない。

---

## 4. unresolved / unknown 一覧

- **origin恒久的にnull(50/122、method設計上の制約)**: `FromDeckGeneric`系。Emulator側の
  `PlayerChoiceContext`拡張なしには解決不可(Emulator担当への依頼候補、§10参照)。
- **KNOWLEDGE_DEMON**: 上記の通りorigin不可。加えて選択後の効果は選ばれたカード自身の
  `IChoosable.OnChosen()`に委譲されており、汎用的な観測では効果を予測できない。
- **SCROLL_BOXES**: 束選択という別UIパターン。現行`choice_card`の1カード粒度では表現しきれない。
  重要度は低い(Ancient relic、出現頻度が低い)が、対応する場合は仕様の再設計が要る。
- **MASSIVE_SCROLL / TUTOR**: マルチプレイヤー専用(`IsAllowed`/`MultiplayerConstraint`で制限)。
  現行の単人プレイRLパイプラインには出現しないため優先度最低(`semantic_confidence: inferred`のまま)。
- **同名カード/Powerの複数同時存在時のinstance識別**: `originEntityInstanceId`は存在せず
  (`AbstractModel`にGuid等の永続instance idがない)、Emulator側も意図的に非対応と明記。

---

## 5. Policy confidence分析仕様

前提: `sts2_training/inference.py`の`PolicyDecision()`は現在
`{selected_action_index, confidence, ranked_action_indices, recommend_heuristic_fallback,
provenance}` を返す。`confidence`はtop-1のsoftmax確率のみ。**top-2以降のスコアは現状取得不可
(§7で追加フィールドを依頼)。**

### 5.1 confidence帯別の勝率・Heuristic一致率

- `complete_test`エクスポート(175 trajectories / 4,200 decisions)に対し、各decisionで
  `PolicyDecision()`を実行して`confidence`を取得。
- ビン分割: `[0, 0.5), [0.5, 0.7), [0.7, 0.85), [0.85, 0.95), [0.95, 1.0]`(5分位、初期案。
  実データのconfidence分布を見て等頻度ビンに調整可)。
- 各ビンについて:
  - **勝率**: そのビンに属するdecisionが起きたtrajectoryの`final_outcome`(victory/defeat)の
    比率(1 decisionが複数ビンにまたがるtrajectoryに属する場合はtrajectory単位で重複カウントしうる
    点に注意 — 「trajectory中のmin confidence decisionのビン」等、集計粒度を明記して実装すること)。
  - **Heuristic一致率**: `selected_action_index`が`teacher_action`(＝Heuristicの選択)と一致する
    decisionの比率。同一state・legal_actionsに対するPolicy vs Heuristicの直接比較であり、200件
    評価の主目的そのもの。

### 5.2 top1とtop2のscore差(要追加フィールド)

- `confidence_margin = P(top1) - P(top2)` を計算するには、Training側に`PolicyDecision()`の
  返り値へ最低限 `top2_confidence` または `probabilities`(全legal_action分のsoftmax配列)の
  追加が必要(§7)。
- 追加後: `confidence_margin`帯別の勝率・一致率(5.1と同じビン分析をmarginでも実施)、および
  `confidence`(top-1絶対値)と`confidence_margin`(相対差)の相関を確認 — 「絶対値は高いが僅差」
  ケース(例: legal_actionsが2つしかない=System End Turn固定の状況)と「絶対値もmarginも高い」
  ケースを区別する。

### 5.3 low-confidence局面のaction typeとの関係

- confidence下位ビン(例: 下位10%)のdecisionを`action_type`(`system`/`card`/`potion`/
  `choice_card`)別に集計。既存の`teacher2000_initial_report.md`で action_type別 top-1 accuracy が
  `card 0.735 < potion 0.782 < system 0.926`(test)と判明済みなので、**低confidenceは`card`
  action(候補数が多く、選択の分岐が本質的に多いアクション種別)に偏る、という仮説を確認する分析**。

### 5.4 low-confidenceと敗北の関係

- trajectory単位で「低confidence decisionの発生回数/割合」と`final_outcome`の関連をロジスティック
  回帰または層別集計で確認。「低confidence局面が多いtrajectoryほど敗北しやすいか」を検証し、
  confidence-based fallback(低confidence時はHeuristicに切り替える)導入の根拠データとする。

### 5.5 target ambiguityとの関係

- 2軸で定義する:
  - (a) **legal_actions側の曖昧さ**: 同一decisionで`target_type=AnyEnemy`かつ生存敵2体以上
    (`choice_target`が発生しうる局面)。
  - (b) **Heuristic側の曖昧さ**: `candidate_actions`(Data Contract既存フィールド)内で
    上位2件の`score`差が小さい局面(Heuristic自身が僅差で選んでいるケース)。
  (a)(b)それぞれについてPolicyの`confidence`分布を比較し、「対象が複数ある状況ほどPolicyの
  confidenceも下がるか」を検証する。(b)は既存データのみで実施可能(追加フィールド不要)。

### 5.6 encounter別の傾向

- `trajectory_meta`/`scenario_manifest`には明示的な`encounter_id`が無いため、各trajectoryの
  decision_index=0の`observation.state.enemies[].id`の組み合わせ(ソートして正規化した文字列、
  例: `"CALCIFIED_CULTIST+CALCIFIED_CULTIST"`)を代理キーとして使う。
- encounter代理キー別に、勝率・平均confidence・Heuristic一致率を集計し、特定の敵構成でPolicyの
  精度が落ちていないか確認する。

---

## 6. Value分析仕様

前提: `ValueDetermination()`は`{win_probability, expected_final_hp_fraction,
expected_final_hp, expected_remaining_decisions, provenance}`を返す(state入力のみ、行動非依存)。

### 6.1 win probabilityのcalibration

- `win_probability`を10分位ビンに分割し、各ビンの平均予測値 vs 実際の勝率(reliability diagram)。
- Brier score(`mean((win_probability - actual_outcome)^2)`)とExpected Calibration Error (ECE)
  を算出。`teacher2000_initial_report.md`記載のtest win accuracy(0.872、victory 0.944 / defeat
  0.636)から、defeat側の予測精度が低いことは既知 — calibrationでも同様の非対称性が出るか確認。

### 6.2 勝利・敗北別の予測分布

- 各decisionの`win_probability`を、そのdecisionが属するtrajectoryの実際の`final_outcome`で層別し
  ヒストグラム化。理想は victory側が1.0近傍・defeat側が0.0近傍に分離すること。decision_indexの
  早い段階(試合序盤)では両者が重なりやすいはずなので、§6.4のdecision_index分解と組み合わせて見る。

### 6.3 final HP予測誤差

- 既知のtest MAE(0.174, fraction of maxHp)を、decision_indexの相対位置(0-25% / 25-50% /
  50-75% / 75-100%、combat進行度で正規化)ごとに分解。終盤ほど誤差が縮小するのが自然な期待値。

### 6.4 remaining decisions予測誤差

- 同様にdecision_index相対位置で分解。既知のtest MAE 7.23の内訳を確認。

### 6.5 PolicyだけでLoss・HeuristicだけでLossのScenarioでのValue

**この項目はRL担当が現在実行中の200-scenario比較評価(Policy rollout vs Heuristic rollout、
同一scenario)の生データが前提**であり、現時点では未実施。必要なデータ形式をここで規定する:

- 200-scenarioの各scenario_idについて `{policy_outcome, heuristic_outcome}` のペア。
- 4象限に分類: 両方victory / 両方defeat / **Policyのみdefeat** / **Heuristicのみdefeat**。
- 後者2象限それぞれについて、Policy側rolloutの各decisionでの`win_probability`推移
  (decision_indexごとの折れ線)を比較する。「PolicyがHeuristicなら勝てた試合で負けた」場合、
  Valueが早い段階から低い予測を出していたか(=Valueは正しく危険を検知していたがPolicyの行動選択が
  それを活かせなかった)、それとも直前まで高い予測のまま急落したか(=予測自体が外れた)を区別する。

### 6.6 分岐点でValueが良い行動を識別できた可能性

- **実施可能な範囲を明確化**: 現行`ValueDetermination()`は実際に選ばれた行動の後の`next_state`
  (ログ済み)のみ評価可能で、選ばれなかった他候補の仮想next_stateは**Emulatorを起動しない限り
  生成できない**(本ロール禁止事項に抵触)。従って本項目は次の2段階で設計する:
  1. **今回実施可能**: 各decisionについて、実際に選ばれた行動適用後の`next_state`に対する
     `win_probability`が、適用前の`state`に対する`win_probability`より改善したか悪化したかを
     時系列で追い、「Policyの一手ごとにValueの評価がどう動いたか」を可視化する(Heuristic選択との
     比較も同様に可能、`teacher_action`後の状態は同じログに含まれる)。
  2. **将来の拡張(Emulator起動が必要、本ロールの範囲外)**: 候補行動ごとの仮想next_stateを実際に
     生成し、Value net でスコアリングして最良候補とPolicy選択の一致率を見る「1手先読み」分析。
     RL担当が別途Emulatorを使って実施する前提の設計として仕様のみここに残す。

---

## 7. Data Contractへの追加フィールド案

| 優先度 | フィールド | 対象 | 理由 |
|---|---|---|---|
| 高 | `PolicyDecision()`返り値に `top2_confidence` または `probabilities`(全candidate分のsoftmax配列) | `sts2_training/inference.py` | §5.2のtop1/top2差分析に必須。現状top-1のみ露出。 |
| 中 | `training_decision.schema.json`の`teacher_action`/`legal_actions`に、choice系decision向けの`choice_context`(`origin_entity_type`/`origin_entity_id`/`source_zone`/`normalized_choice_operation`/`semantic_confidence`) | Data Contract | 現状`choice_card`はEmulatorのchoice contextがRL側に渡されておらず、Policy学習から機械的除外・推論時は構造的fallbackのみ。`Common/schemas/choice_semantics_lookup.v1.json`を接続すれば、`choice_card`をPolicy学習対象に含める判断ができるようになる(本レポートは接続の可否を判断するものではなく、接続可能なデータを用意するところまで)。 |
| 低 | `trajectory_meta`または`scenario_manifest`への明示的`encounter_id` | Data Contract / Combat側export | §5.6のencounter別分析を、decision_index=0のenemies推定に頼らず直接キーで行えるようにする。 |

---

## 8. RL担当が次に実装すべき事項

1. `complete_test`(175 trajectories)に対する§5.1〜5.6の集計スクリプトの実装(§5.2以外は現行
   データのみで着手可能)。
2. `Common/schemas/choice_semantics_lookup.v1.json`を`choice_card`除外ロジックの代わりに使うか
   どうかの判断(実装するかは本レポートの範囲外 — 判断材料の提供のみ)。
3. §6.5のための、200-scenario比較評価の出力を`{scenario_id, policy_outcome, heuristic_outcome,
   policy_decision_trace}`形式で保存するようcurrent評価スクリプトを確認・調整。
4. confidence-based fallback導入可否の意思決定(§5.1・5.4の結果待ち)。

## 9. Training担当へ依頼すべき事項

1. **`PolicyDecision()`に`top2_confidence`または全candidate分`probabilities`を追加**(§5.2・§7、
   最優先)。
2. `ValueDetermination()`が任意の`observation`を受け取れる現行仕様を維持したまま、§6.6-1のために
   「適用前state」「適用後next_state」の両方を1回の呼び出しで評価できるバッチAPIがあると効率的
   (必須ではない、あれば助かる程度)。

## 10. Emulator担当へ追加依頼が必要な事項

1. **`FromDeckGeneric`/`FromDeckForUpgrade`/`FromDeckForTransformation`/`FromDeckForEnchantment`/
   `FromDeckForRemoval`系(50/122箇所)へのorigin付与** — これらは`PlayerChoiceContext`引数が
   メソッド自体に存在しないため、シグネチャ変更が要る大きめの変更。優先度は中(戦闘中choiceの
   大半は既にorigin取得可能なため)。
2. `KNOWLEDGE_DEMON`が`BlockingPlayerChoiceContext`を都度生成している点 — 意図的な設計か確認
   したい(敵の手番でプレイヤーが選ぶcurse choiceのため、ambient contextを使わない設計判断かも
   しれない)。origin付与の価値は低い(常にKNOWLEDGE_DEMON自身と分かっているため)が、念のため。
3. 優先度低: `SCROLL_BOXES`の束選択(bundle_select)を`choice_card`の枠組みでどう表現するか
   (現状は個別カードのobjectとして4件の候補が返るのか、束単位で返るのか未確認 — 実際に
   `GetLegalActions()`を叩いて確認する必要があるが、本ロールはEmulator起動禁止のため確認できず、
   Emulator担当側での確認を依頼)。

---

## 付録: 集計に使うデータソース

- `C:\STS2_RL\Training\exports\teacher2000_20260723_dataset_export_v1\complete_{train,validation,test}.jsonl`
- `C:\STS2_RL\Training\checkpoints\policy_teacher2000_seed_20260724\best.pt`
- `C:\STS2_RL\Training\checkpoints\value_teacher2000_seed_20260724\best.pt`
- `C:\STS2_RL\Training\sts2_training\inference.py`(`PolicyDecision`/`ValueDetermination`)
- `Common/schemas/choice_semantics_schema.json` / `choice_semantics_lookup.v1.json`(本レポート成果物)
