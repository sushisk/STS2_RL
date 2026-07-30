# RL報告: Choice Policy採用固定・完走阻害ケース確認・次工程準備 — 2026-07-25

---

## 0. 結論

* Choice Policyを戦闘用オンラインadapterの限定正式構成として採用・固定した(1節)。
* `max_decisions=60`到達3件は`max_decisions=150`で**全て正常完走**を確認した(2節)。
* `no_legal_actions`(Scenario `6546-21`)は最小再現に成功、
  **Combat側(adapter/キャッシュ/restore)は原因から除外**、Policy／Choice Policy
  にも依存しないことを確認、`emulator_legal_action_bug`(第一候補)/
  `normal_terminal_detection_issue`(次点候補)として分類、RL側では修正せず
  最小再現を提出した(3節)。
* 最終確認: 3条件(150再実行での正常完走・no_legal_actionsの非Choice-Policy性・
  illegal/exception/mismatch=0の維持)を全て満たし、**Choice Policy評価を終了**
  した(4節)。
* 戦闘外10 decisionカテゴリについてEmulator/Modの現状調査を行い、優先順位案を
  報告する(5節)。**実装・データ生成には進んでいない。**

---

## 1. 採用構成の固定

`Combat/policy_baseline/choice_policy_adoption_v1_20260725.json`(新規)に記録
(既存の`choice_semantics_baseline_722b019_v1_20260725.json`・
`choice_teacher_data_generation_v1_20260725.json`・
`choice_policy_online_eval_provenance_v1_20260725.json`はいずれも無変更、
参照のみ)。

| 項目 | 値 |
|---|---|
| 通常行動 | 通常Policy(`Combat/policy_agent.py`、無変更) |
| `choice_card` | 8-token Choice Policy(`Combat/choice_policy_agent.py`) |
| Choice Policy推論不能時 | Heuristic fallback |
| `choice_skip`／`choice_confirm` | 常にHeuristic fallback |
| Value | log-only |
| AnyEnemy | first-alive |
| Choice Policy checkpoint | `choice_policy_8token_best/best.pt`、SHA256 `f5299e4abf8a30a0400cba2e5094777276b84f9c3a70d7051b0ec886c457f29f` |
| Emulator commit | `722b019051e6f7ea368fef488abcc6451d6c9d47` |
| 限定オンライン評価報告書 | `Outputs/reports/rl_choice_policy_online_eval_report_20260725.md` |
| 使用率 | 90.8%(108/119、Stage C) |
| 勝率比 | 95.5%(Choice Policy／Heuristic Choice) |
| 平均推論時間 | 6.6ms |

---

## 2. max_decisions確認

`Combat/evaluation/online_eval/choice_policy_max_decisions_recheck.py`(新規、
既存の`choice_policy_online_eval.py`は無変更、既存関数を呼び出すのみ)。
`max_decisions=150`、両arm同一設定、shadow比較なし、他のコード／checkpoint／
lookup／manifestは無変更で、Stage Cで`max_decisions=60`に到達した3件のみ再実行。

| Scenario | Choice Policy arm | Heuristic Choice arm |
|---|---|---|
| `7376-7` | victory、113 decisions、HP 31、例外なし | victory、113 decisions、HP 31、例外なし(両arm完全一致) |
| `3340-17` | defeat、111 decisions、HP 0、例外なし | defeat、109 decisions、HP 0、例外なし |
| `4535-4` | victory、96 decisions、HP 35、例外なし | victory、96 decisions、HP 35、例外なし(両arm完全一致) |

**3/3が`max_decisions=150`で正常完走**(non-terminal truncationなし)。
illegal action・新しい例外は**0件**。停止理由(`termination_reason`)は全て
`None`(=正常終了、`is_terminal=True`到達)。

出力: `Combat/evaluation/reports/choice_policy_max_decisions_recheck_150/`

---

## 3. no_legal_actions調査

詳細: `Outputs/reports/rl_no_legal_actions_investigation_6546-21_20260725.md`
(新規)、再現スクリプト: `Combat/evaluation/online_eval/
investigate_no_legal_actions_6546_21.py`(新規)。

**要旨**: Scenario `6546-21`、decision_index=13、`turnNumber=1`
(まだ最初のターン中)、敵`SOUL_NEXUS`(hp=5、生存中)、プレイヤーhp=68/78、
`pendingChoice=null`、`is_terminal=False`という状態で、`GetLegalActions()`が
**End Turnすら含めて0件**を返す。

* CombatEnvの通常経路・BattleEmulatorの直接呼び出し・完全新規restore+生
  `GetLegalActions()`の3通り全てで同一の0件 → **Combat側のadapter/キャッシュ/
  restoreは原因から除外**。
* Stage Cの元記録で、Choice Policyを一切使わないHeuristic Choice armも
  **独立に同一Scenario・同一decision数(13)**で到達済み →
  **Policy／Choice Policyに依存しない**ことを確認。
* 分類: **`emulator_legal_action_bug`(第一候補)** /
  `normal_terminal_detection_issue`(次点候補)。`combat_adapter_progress_bug`・
  `scenario_restore_gap`・`data_issue`は上記の切り分けにより除外。
* Emulator修正の要否判断はEmulator担当に委ね、**RL側では修正せず最小再現
  スクリプトと本報告を提出するのみ**とした。

---

## 4. 採用条件の最終確認

| 確認事項 | 結果 |
|---|---|
| decision上限150にした3件が正常完走 | ✅ 3/3(2節) |
| no_legal_actionsがChoice Policy固有でない | ✅ Combat側/Policy/Choice Policyいずれにも非依存を確認(3節) |
| Choice Policy導入後もillegal／exception／mapping mismatch 0 | ✅ 全段階・全再実行を通じて0 |

**Choice Policy評価はここで終了する。** `choice_policy_adoption_v1_20260725.json`
の`status`を`ADOPTED`に更新した。

---

## 5. 次工程準備: 戦闘外decision一覧と優先順位案(報告のみ、実装なし)

`C:\STS2_Emulator`のソース調査により、`GameInstance.StartRun()`経由で
戦闘外decisionの多くが**既にAPIとして存在する**ことを確認した
(`C:\STS2_RL\docs\RL_HANDOFF.md` §1.1が「最終的にはラン全体方策も学習するが
現在は戦闘AIを最優先」と明記している通り、これまで意図的に未着手だった領域)。
Combat側(`C:\STS2_RL`)は現時点で`StartRun`系APIを一切呼び出しておらず、
`GetMapRooms`/`ChooseRoom`/`GetRunState`/`RestSiteRoom`/`MerchantRoom`等への
参照はゼロ(唯一の既存痕跡は`Common/schemas/legal_action_schema.json`が
`choice_reward_card`等のaction_type文字列を仕様として先行記載している点のみ)。

ゲーム進行順に整理し、各項目についてEmulator/Mod現状取得可否・legal action
列挙可否・Heuristic教師作成可否・進行阻害優先度を評価する。

| # | Decision(進行順) | Emulator/Mod現状 | legal actions列挙 | Heuristic教師 | 進行阻害優先度 |
|---|---|---|---|---|---|
| 6 | **イベント選択(Neow含む)** | ✅既存(`BuildEventLegalActions`/`AnswerEventChoice`、Neowも同一機構) | ✅可能(`choice_event_option`) | △中(選択肢が事象ごとに異質、個別価値付けが必要) | **最高** — StartRun直後のNeowが**全runの最初の一手**、未対応だとrun開始直後に停止 |
| 5 | **マップ経路** | ✅既存(`GetMapRooms`/`ChooseRoom`) | ✅可能(小規模な到達可能ノード配列) | ○中〜高(ノード種別・HP状態ベースの浅い特徴量で構築可) | **最高** — 各部屋遷移の度に必須、未対応だと最初の部屋を出た時点で停止 |
| 1 | 戦闘報酬カード選択 | ✅既存(`BuildRewardLegalActions`/`AnswerRewardChoice`) | ✅可能(`choice_reward_card`) | ◎高(既存の戦闘内`choice_card`と同型、Choice Policy資産を流用しやすい) | 高 — 通常戦闘の度に発生、頻度最大 |
| 2 | カード報酬skip | ✅既存(#1と同一経路、`choice_reward_skip`) | ✅可能 | ◎高(#1と一体) | 高(#1と同時) |
| 9 | カード削除／強化 | ✅既存(rest siteの`SmithRestSiteOption`、shopの`choice_shop_remove_card`。いずれも既存の戦闘内`choice_card`と**同一の汎用mid-effect choice機構**を再利用) | ✅可能 | ◎高(`choice_policy_agent.py`／`choice_semantics.py`の既存資産をほぼそのまま転用可能) | 中〜高 — rest site/shop訪問時のみだが、既存Choice Policy投資の再利用効率が最も高い |
| 8 | 休憩所(Heal/Smith等) | ✅既存(`BuildRestLegalActions`/`AnswerRestChoice`) | ✅可能(`choice_rest_option`) | ◎高(Heal対Smithは単純な二択相当、頻度も高い) | 中 — rest site訪問時のみ |
| 7 | ショップ | ✅既存(`BuildShopLegalActions`/`AnswerShopChoice`) | ✅可能(`choice_shop_buy_*`/`choice_shop_remove_card`/`choice_shop_leave`) | ○中(gold予算内での複数品目トレードオフ、`potion_value_table.py`同様の価値表が必要) | 中 — shop訪問時のみ、金額制約付きで意思決定がやや複雑 |
| 10 | ボス報酬 | **△既存の枠組みに分解される** — 「1体3relicから選択」という専用機構は本ゲームに存在しない。ボス戦闘報酬自体は#1(カード報酬)と同一経路、relic選択に相当する体験は#6のAncient/Neow系event機構が担う | ✅可能(#1・#6経由) | ◎高(新規実装不要、#1・#6が対応済みなら実質対応済み) | 低 — 独立カテゴリとしての追加作業は不要、#1・#6の対応で自動的にカバー |
| 4 | Relic報酬 | **意思決定が存在しない** — Elite戦のみ1件自動付与、Monster/Boss戦では付与自体なし。「複数から選ぶ」機構は本ゲームに実装されていない | N/A(選択肢が常に0か1) | N/A | 最低 — 構築すべき意思決定が存在しない |
| 3 | Potion取得／破棄 | **❌未公開** — belt満杯時は`PotionReward.OnSelect`が黙って取得失敗させるのみで、破棄/交換を促す選択肢が一切公開されていない(`DiscardPotionGameAction`は内部的に存在するが、外部公開されたaction機構には未接続) | ❌不可(現状legal actionとして存在しない) | ❌不可(選択肢がないため教師化不能) | 低(現状は進行を止めない — 黙って喪失するだけ) — ただしRL側では着手不可、**Emulator側の新規API追加が前提** |

### 優先順位案(まとめ)

1. **最優先**: マップ経路(5)・イベント選択(6、Neow含む) —
   この2つが揃わない限り、戦闘外のあらゆる後続decisionにも到達できず、
   run全体が実質進行不能になる基盤部分。
2. **次点**: 戦闘報酬カード選択+skip(1・2)・カード削除／強化(9) —
   頻度が高く、かつ既存のChoice Policy／choice_semantics資産をそのまま
   転用できるため、追加エンジニアリングコストが低い。
3. **中位**: 休憩所(8)・ショップ(7) — 訪問頻度は限定的だが、意思決定として
   十分に単純(休憩所)〜中程度の複雑さ(ショップの予算配分)。
4. **低位・追加作業不要**: ボス報酬(10、#1・#6でカバー済み)・
   Relic報酬(4、意思決定自体が存在しない)。
5. **保留(RL側では未着手)**: Potion取得／破棄(3) — 現状Emulatorに
   意思決定として公開されておらず、**Emulator側の新規API追加が先行条件**。
   現状は黙って喪失するのみで進行は止めないため、緊急度は低い。

**実装・データ生成にはまだ進まず、本報告のみで停止する。**

---

## 6. 出力ファイル一覧

* `Combat/policy_baseline/choice_policy_adoption_v1_20260725.json`(新規、更新済み)
* `Combat/evaluation/online_eval/choice_policy_max_decisions_recheck.py`(新規)
* `Combat/evaluation/online_eval/choice_policy_max_decisions_recheck_manifest.jsonl`(新規)
* `Combat/evaluation/reports/choice_policy_max_decisions_recheck_150/`(新規)
* `Combat/evaluation/online_eval/investigate_no_legal_actions_6546_21.py`(新規)
* `Outputs/reports/rl_no_legal_actions_investigation_6546-21_20260725.md`(新規)
* 本報告書

---

## 7. 禁止事項の遵守状況

* Emulatorは調査のみ、編集していない(no_legal_actions問題もRL側では修正せず)。
* 3節で「Emulator修正が必要と判断した場合もRL側で修正せず最小再現を提出する」
  方針を遵守。
* 5節は調査・優先順位案の報告のみ、実装・データ生成には着手していない。
* 既存成果物(baseline/provenance/report群)は一切上書きしていない。

停止する。
