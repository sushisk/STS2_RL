# RL報告: Choice Semantics統合・検証 Stage B (50 Scenario) — 2026-07-24

前段: `Outputs/reports/rl_choice_semantics_stage_a_report_20260724.md`(20 Scenario)

## 0. 結論

Stage Bを完走し、指示通り**lookup/schema/コードは無変更のまま停止**する。

* illegal / exception / mapping mismatch: **全て0**
* **統合前後の行動一致・決定論性: 完全一致**(50 Scenario×2arm、275 choice decisionいずれも)
* **semantic mismatch: 0件**(151件をtable evidenceと突合)
* GamblingChipDiscard: 実操作を**discard**と実証確認。加えて**新規の疑わしい挙動を発見**
  (2節) — 2ターン目以降の再発火時にorigin属性が直前に使用したPotionへ**漏洩**している。
* Potion起因choiceのorigin命名: 11種類全Potionで**例外なく**PascalCaseパターンに一致。
* 2つの判断事項(6節)に対する材料を提示。**ruleの追加・修正は行っていない**。

---

## 1. 実行条件(指示通り)

* コード・schema・lookup変更なし(実行前後で無変更を確認)
* local Emulator `0d16130`使用
* Choice実行は既存Heuristic継続(`continuation_resolver`ラッパーは選択ロジックそのまま呼び出すのみ)
* manifest: `choice_scenarios_stage_b_manifest.jsonl`(Stage Aの20件を含む入れ子、50件)
* 決定論性確認のため同一manifestを再実行(4節)

---

## 2. GamblingChipDiscard: 実操作の確定 + 新規発見

### 2.1 実操作(empirical probe、`probe_gambling_chip_and_potions.py`)

重複のない3枚の手札(BASH/ANGER/TWIN_STRIKE)でGAMBLING_CHIP保持シナリオを構築し、
BASH・TWIN_STRIKEを選択、ANGERは未選択のまま確認:

```text
選択前: hand=[BASH, ANGER, TWIN_STRIKE]  draw=[IRON_WAVE, SHRUG_IT_OFF]  discard=[]
選択後: hand=[ANGER, IRON_WAVE, SHRUG_IT_OFF]  draw=[]  discard=[BASH, TWIN_STRIKE]
```

**選択したカードが正確にdiscard_pileへ移動し、未選択のANGERは手札に残った。** その後
選択枚数分(2枚)をdraw pileから引いている(副次効果)。実際の操作は明確に
**discard**(`source_zone=hand`, `destination_zone=discard_pile`)であり、
`__GENERIC_DISCARD__`と同じ意味的操作。決定論性も確認済み(同一seedで
`pendingChoice`完全一致)。

### 2.2 新規発見: origin属性の漏洩疑い(Emulator側の挙動、要確認)

Stage B全体でGamblingChipDiscardは40回出現。うち36回は`decision_index=0`
(戦闘開始直後)で`originEntityType/Id`とも正しく`None`(2節冒頭の通り、
GAMBLING_CHIPはPlayerChoiceContextを介さないため元々originは解決不能)。

**しかし残り4回は`decision_index`が0以外(4, 11, 12)で発生し、いずれも
`originEntityType`/`originEntityId`が直前に無関係な操作で使用されたPotionの値
(`PowerPotion`/`POWER_POTION`)になっていた**:

```text
7551-16/policy: decision 0 (GamblingChipDiscard, origin=None)
              → decision 4 (GamblingChipDiscard, origin=PowerPotion/POWER_POTION) ← 疑わしい
              → decision 20 (Unsupported, origin=card/TRUE_GRIT)
```

同型のパターンが`ToolboxChooseCard`(TOOLBOX relic起因)でも2件確認された
(origin=`ColorlessPotion`/`COLORLESS_POTION`が漏洩)。

**解釈**: GAMBLING_CHIPは戦闘開始時だけでなく**戦闘中に複数回再発火する**ようで
(このEmulator版の仕様として妥当かは未確認)、再発火時にPlayerChoiceContextの
`LastInvolvedModel`が正しくクリアされず、**その戦闘で直近に使用されたPotionの
コンテキストを誤って引き継いでいる**疑いがある。Emulator自身のテストスイート
(`smoke_choice_context.py::test_context_does_not_leak_after_reset`)はGameInstance
Reset間・インスタンス間のcontext非リークは確認しているが、**同一戦闘内で異なる
choiceType間のcontext非リークは検証範囲外**であり、今回のケースはそのギャップに
該当する可能性が高い。

**対応**: 本ロールでは修正・推測補完せず、再現データ(上記trajectory_id・
decision_index)とともに事実のみ記録。Emulator担当への確認依頼候補として報告する。

---

## 3. Potion起源命名パターン: 11種で確認、例外なし

Stage A(5種)+ 追加probe(6種、`probe_gambling_chip_and_potions.py`)で計11種の
Potionを確認、**全件が例外なくPascalCase命名パターンに一致**:

| potion_id | 実際のoriginEntityType |
|---|---|
| COLORLESS_POTION | `ColorlessPotion` |
| SKILL_POTION | `SkillPotion` |
| POWER_POTION | `PowerPotion` |
| ATTACK_POTION | `AttackPotion` |
| TOUCH_OF_INSANITY | `TouchOfInsanity` |
| ASHWATER | `Ashwater` |

(残り5種はStage Aで確認済み。`DROPLET_OF_PRECOGNITION`/`LIQUID_MEMORIES`は
今回のprobe構成ではpendingChoiceが発火せず未確認——targetType/スロット設定の
probe側の問題であり、Potion自体の挙動不明を意味しない。)

`originEntityType`は毎回「`origin_entity_id`からアンダースコアを除去し
PascalCaseにしたもの」と完全に一致しており、機械的で予測可能な変換規則である
ことが11/11で実証された。cardの`originEntityType`は一貫して`"card"`固定文字列
であるのに対し、potionのみC#クラス名がそのまま露出している——**関連レリック
(TOOLBOX)でも同様の疑いがある**ことが2.2節の漏洩ケースから伺える
(`ColorlessPotion`という文字列が漏洩元として観測された)。

---

## 4. 決定論性・統合前後の行動一致(実測)

* **決定論性**: 同一manifestを再実行し、275件のchoice-log entryを`(trajectory_id,
  arm, source)`単位で突合、**全件一致(0 mismatch)**。
* **統合前後の行動一致**: `--choice-semantics`ありなしで50 Scenario×2armを実行し、
  最終outcome・最終HP・decision数・**選択行動列そのもの**を比較、**全件一致
  (0 mismatch)**。ロギング追加が行動選択に一切影響しないことをStage Bでも再確認。

---

## 5. 指標一覧

### 5.1 全体

| 指標 | 値 |
|---|---|
| Choice Scenario数(choiceが発生したscenario) | 48/50 |
| Choice decision数 | **275** |
| illegal action | 0 |
| exception | 0 |
| action mapping mismatch | 0 |
| semantic mismatch | 0(151件中) |
| 統合前後の行動一致率 | 100%(50 Scenario×2arm) |
| 最終状態一致率 | 100%(outcome/HP/decision数) |
| 決定論性 | 100%(275/275 entry) |
| 正常完走率 | Policy 96%(48/50)、Heuristic 100% — 差分1件は`no_legal_actions_while_non_terminal`
  (Policy armのみ、`6546-21`、choice semanticsと無関係の既知カテゴリの再発、
  RL_HANDOFF既知事項) |
| 勝率 | Policy 39/50(78%)、Heuristic 42/50(84%) |

### 5.2 通常公開Choice vs ActionContinuation由来

| 種別 | 件数 | 割合 |
|---|---|---|
| 通常の外部公開Choice(topレベルdecision) | 12 | 4.4% |
| **ActionContinuation内で自動処理** | **263** | **95.6%** |

Stage Aと同傾向(Stage A: 128/134=95.5%)——ActionContinuation経由が引き続き
Choice全体の大半を占める。

### 5.3 Scenario単位のChoice数

48 Scenarioに分布(平均5.73件/Scenario、最小1件、最大18件)。

### 5.4 1 Stepで発生した連続Choice数

`(trajectory_id, arm, decision_index)`単位でグループ化:

| 連続数 | 発生回数(step数) |
|---|---|
| 1 | 185 |
| 2 | 12 |
| 4 | 2 |
| 5 | 8 |
| **6(最大)** | 1 |

複数枚選択系(DREDGE/NEOWS_FURY等のmulti-select)で1 Step内に最大6件のchoice_card
継続が連鎖することを確認。

### 5.5 origin取得率・operation既知率・normalized/passthrough/unknown率

| 指標 | 値 |
|---|---|
| pendingChoice取得率 | 100%(275/275) |
| origin取得率 | 86.9%(239/275) |
| operation既知率(Emulator側) | 39.3%(108/275) |
| destination既知率 | Emulator側`destinationZone`が非nullの件数ベースで別途`choice_log.jsonl`から算出可能——discard/exhaust系(operation既知かつdestination判明)が中心、upgrade等destination概念自体がない操作を含むため単純な「既知率」は解釈に注意 |
| **normalized率** | 68.7%(189/275) |
| **passthrough率** | 5.5%(15/275) |
| **unknown率** | 25.8%(71/275) |
| ambiguous match率 | 0% |
| lookup miss率 | 12.7%(35/275) |

### 5.6 解決方法別内訳(5分類)

| 分類 | 件数 | 割合 |
|---|---|---|
| emulator_fact(Tier1、Emulator concretely reports) | 108 | 39.3% |
| prompt_confirmed | **0** | 0% |
| hardcoded_entity_rule(Tier2、lookup、passthroughを除く) | 81 | 29.5% |
| passthrough | 15 | 5.5% |
| unknown | 71 | 25.8% |

`prompt_confirmed`が0件なのは想定通り: この値を持つのは`__GENERIC_*__`
プレースホルダ行(`origin_entity_type=null`)のみで、実entityへの直接lookup key
としては一致しない設計のため(1節の分類仕様通り、Emulatorが具体的operationを
返す場合はTier1=`emulator_fact`として扱われ、tableの`semantic_source`が
`prompt_confirmed`のGENERIC行はそもそも実queryのkeyになり得ない)。バグではなく
設計上の帰結。

---

## 6. 未解決2系統の詳細報告

### 6.1 GamblingChipDiscard

| 項目 | 内容 |
|---|---|
| 発生Scenario数 | 複数(40 decision中、`decision_index=0`のもの含む) |
| decision数 | 40(no_origin 36 + miss 4) |
| originEntityType/Id | `decision_index=0`: 常に`None`/`None`。それ以外(4件): 直前に使用された
  Potionのクラス名が漏洩(2.2節) |
| choiceType | `GamblingChipDiscard`固定 |
| sourceZone/destinationZone | `sourceZone="hand"`(一部`None`)、`destinationZone`は常に`None`
  (Emulatorはこの操作の宛先を報告しない) |
| 実際に行われた操作 | **discard**(2.1節で実証、選択カード→discard_pile、その後同数draw) |
| 同一文脈での決定論性 | 決定論的(同一seedで`pendingChoice`完全一致、2.1節) |
| 独立したnormalized operationが必要か | 実操作自体は既存の`discard`(`NORMALIZABLE_OPERATIONS`
  収録済み)で表現可能。ただしorigin漏洩(2.2節)が実際にEmulator側の挙動として
  意図的か不具合かが未確認のため、lookup行追加(`origin_entity_type=null`の
  特殊entryとして`GamblingChipDiscard`choiceType自体をkeyにするか等)の要否は
  2.2節の確認結果を待つべきと判断する |

### 6.2 Potion起因Choice

| 項目 | 内容 |
|---|---|
| 実際に返るoriginEntityType | 11/11がPascalCase(3節の表) |
| originEntityId | 対応するSCREAMING_SNAKE_CASE id(table収録値と完全一致) |
| Choiceの意味 | 各Potion固有(add_generated_to_hand/exhaust/apply_effect_in_place等、
  tableに個別収録済み・全て`hardcoded_entity_rule`) |
| 発生数 | Stage B: `choiceType=Unsupported`のmiss 29件中、6種のpotionが該当
  (COLORLESS_POTION 10、TOUCH_OF_INSANITY 6、ASHWATER 5、ATTACK_POTION 4、
  SKILL_POTION 2、POWER_POTION 2) |
| クラス名の種類 | 6種類確認(`ColorlessPotion`/`SkillPotion`/`PowerPotion`/`AttackPotion`/
  `TouchOfInsanity`/`Ashwater`)、Stage A+追加probeで11種類累計確認、**全て
  例外なくPascalCase規則に一致** |
| 同じ種類のPotionで一貫しているか | **一貫している**(同一potion_idは常に同一
  originEntityTypeを返す、決定論的) |
| `originEntityType = potion`へのPython側正規化だけで解決可能か | **可能と判断できる
  根拠は揃っている**——11/11で例外のない機械的パターン(アンダースコア除去+
  PascalCase)であり、`choice_semantics_lookup.v1.json`収録の全potion entryの
  `origin_entity_id`から同じ変換で逆引き可能。ただし**本ロールでは実装しない**
  (指示通り)。判断はChoice Semantics/RL全体の方針決定に委ねる。 |

---

## 7. 禁止事項の遵守状況

* コード・schema・lookupは実行前後で無変更(diffなし)。
* Azure未使用。
* Choice実行は既存Heuristicのまま(4節で行動一致を実測確認)。
* GamblingChipDiscard・Potion起因choiceとも、**推測での修正・正規化ルール追加は
  一切行っていない**——probeスクリプト(`probe_gambling_chip_and_potions.py`)は
  既存ファイルを一切変更せず、新規の一時観測用スクリプトとして追加したのみ。

---

## 8. 次の判断(ユーザー判断待ち、本ロールでは決定しない)

1. **GamblingChipDiscardの扱い**: 通常operationとして正規化(discard)するか、
   passthroughとするか。実操作はdiscardと確定(2.1節)。ただし2.2節のorigin
   漏洩疑いをEmulator側へ確認してから判断する方が安全と考えられる
   (漏洩が実際にEmulatorのバグであれば、修正後にoriginEntityType自体が
   変わる可能性があるため)。
2. **PotionのoriginEntityType正規化**: Python側(`choice_semantics.py`)で
   PascalCase→`"potion"`への変換を追加するか。11/11で一貫した機械的パターンが
   確認されており技術的には解決可能(6.2節)。

いずれもStage B中には実装していない。ご判断をお待ちする。
