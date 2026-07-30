# RL報告: 現行Combat実行系の詳細処理フロー — 監査input — 2026-07-26

コード修正を一切行わず、現在のCombat実行系(オンライン評価・teacher-data生成)
の実際の処理フローを文書化した。作業開始前に指定された全参照ファイル
(Choice Policy採用・戦闘外調査報告、no_legal_actions調査報告、最小再現
スクリプト、評価manifest、`battle_emulator.py`/`emulator_bridge.py`/
`policy_agent.py`/`choice_semantics.py`、`Combat/evaluation/online_eval/`、
722b019 baseline)を確認した上で、実装コードと照合しながら作成した。

---

## 0. 現行アーキテクチャの一文要約

Combat実行系は、**プロセスあたり1つの共有GameInstance**を
`ResetFromScenario`で毎decisionごとに**Pythonが保持するengine_state
スナップショットから丸ごと再構築**し、その都度1手(または
ActionContinuationで自動吸収される複数手)だけ進めて結果を
再びPython辞書へ持ち帰る、という**「常に作り直す」ステートレスAPI**
(`BattleEmulator`)の上に、通常Policy/Choice Policy/Heuristicの各decider、
CombatEnvという薄いstatefulラッパー、そしてオンライン評価/teacher-data生成
harnessが積み重なっている。

---

## 1. live GameInstanceの寿命

* **プロセスあたり1インスタンス**、`emulator_bridge.py::shared_game_instance()`
  が管理する単一singleton。生成は初回アクセス時の1回のみ、以後は
  **破棄・再生成されず**、`ResetFromScenario`で中身を丸ごと入れ替える形で
  再利用され続ける(プロセス終了までdisposeされない)。
* 「2つ目のGameInstanceを作ると1つ目が壊れる」ことが実験的に確認済み
  (`battle_emulator.py`モジュールdocstring)——このためマルチワーカー
  構成は「1 worker = 1 process」が必須制約(`combat_env.py`のクラス
  docstring、`Combat/evaluation/reports/emulator_hang/worker_timeout_
  policy.md`)。
* 詳細: `combat_flow/known_risks.md`項目13。

## 2. LegalActions取得方式

* **常に`_restore()`(=`build_scenario_from_state`+`ResetFromScenario`)
  経由のfresh取得が基本**——ActionContinuation待ち状態でキャッシュが
  存在する場合のみキャッシュを使う(`battle_emulator.py::enumerate_
  legal_actions()`)。
* `_restore()`は`ResetFromScenario`自身が返す`LegalActions`を使わず、
  直後に別途`GetLegalActions()`を呼ぶ——1回のlegal action取得で
  GameInstanceとの往復が実質2回発生する(冗長呼出、
  `combat_flow/known_risks.md`項目11)。
* 詳細フロー図: `combat_flow/legal_actions_sequence.md`。

## 3. ResetFromScenario利用箇所数

* **実装コード上の直接呼出箇所は2箇所のみ**
  (`battle_emulator.py:675`の`initialize()`、`battle_emulator.py:700`の
  `_restore()`)——ただし`_restore()`自体は`enumerate_legal_actions()`
  (通常状態時)と`apply_action()`(継続ローカルgameが無い場合)の
  **2つの経路から間接的に、1 decisionあたり複数回**呼ばれる。
* 過去の別インシデント("emulator_hang")の診断用に作られた
  スタンドアロンスクリプト23本にも同一パターンの直接呼出があるが、
  **現行パイプラインの一部ではない**(履歴として保存されているのみ)。
* 全一覧: `combat_flow/reset_from_scenario_call_sites.csv`。

## 4. 中間状態復元の前提

* `preflight_validate()`によるspec対state内容突合(HP/deck/relics/potions/
  powers/orbs/enemies/slot names/stars)は**エピソード開始直後の1回のみ**
  実施される。**それ以降の全ての中間restore(`_restore()`呼出)は
  一度もこの突合を受けない**——「初回の検証結果が以後の全restoreにも
  当てはまる」という**暗黙の前提**の上でパイプライン全体が組まれている。
* フィールド単位の復元可能性は一様ではない: HP/Block/Energy/Stars/
  各カード山(構成・アップグレード状態)/Potions/Orbsは完全復元、
  一方でRelic/Power内部の消費・カウンタ状態/turn数/RNGカーソル/PlayPile
  は**復元されない**(常に「idのみ」「turn=1」「元シード」「欠落」の
  いずれかへ収束する)。
* 全項目の分類: `combat_flow/state_restore_coverage.csv`。

## 5. 今回の障害フロー(Scenario 6546-21)

* 確認済み事実: `decision_index=13`、`turnNumber=1`のまま(13手とも
  End Turn未選択)、敵`SOUL_NEXUS`(hp=5、**生存中**)、`is_terminal=False`
  という状態で、Combat側の3つの独立した経路全てで legal actions が
  完全に0件——**Combat側(adapter/キャッシュ/restore呼出側)は原因から
  除外済み**、Policy/Choice Policyにも非依存(RL側の既存報告書で確認済み)。
* Scenario specには`TOOLBOX`と`FESTIVE_POPPER`が**両方とも**relicsリストに
  含まれていることを今回新たに確認した。
* 未確認の仮説チェーン(`build_scenario_from_state`→`ResetFromScenario`→
  relic関連hookの再発火→turn-start effect再発火→legal actions 0)は、
  コード上の状況証拠(relic idのみ復元・turn数常時リセット)と整合するが、
  **因果関係そのものは未検証**。
* **重要な矛盾点**: 従来の仮説例では「SOUL_NEXUS死亡」が終端に置かれていたが、
  実際に確認された状態はSOUL_NEXUS**生存中**(hp=5)——単純な
  「モンスター死亡検出漏れ」では説明できない。既存の`coerce_terminal_
  observation()`(敵全滅を検出してterminal扱いに補正する既存ロジック)の
  対象パターンとも異なることが今回判明した。
* 詳細: `combat_flow/normal_online_sequence.md`6節。

## 6. 追加で発見した設計上の懸念

`combat_flow/known_risks.md`に19項目を根拠コード付きで整理した。
特に優先度「最高」としたのは以下4件:

1. fresh restoreによる副作用(項目1)
2. one-shot effectの二重適用(項目3、TOOLBOX/FESTIVE_POPPER仮説の根幹)
3. Python状態とlive stateの不一致、特にturn数(項目8)
4. terminal判定の二重管理 — 既存の敵全滅検出ロジックが今回のケースを
   カバーしていないという新規判明事項(項目14)

次点(優先度「高」)として、次工程(戦闘外decision対応)に直結する
「ゲーム全体進行時にlive runを維持できない設計」(項目18)・
「戦闘終了後のrun state引き継ぎ不能」(項目19)も記録した。

## 7. 不明点

* `command queue`・`event hook登録状態`に相当する概念がEmulator内部に
  存在するかどうか自体、本タスクの参照範囲(RL側コード)からは確認不能。
* `ResetFromScenario`自身がEmulator内部でどのようなhook/イベントを
  発火させるか(特にrelicのstart-of-combat/start-of-turn相当ロジック)は、
  RL側コードからは完全に不可視——Emulator側ソースコードの調査が必要。
* FESTIVE_POPPER単独/TOOLBOX単独/組み合わせでの再現性の違いは未検証。
* `PowerStack`/`Relic`がEmulator内部で公開スキーマ外の隠れた状態を
  持つかどうかは未確認(`state_restore_coverage.csv`の「近似復元」
  項目群)。

## 8. 次の共同監査で優先すべき論点

1. **`ResetFromScenario`のEmulator内部実装**を確認し、mid-combat restore時に
   start-of-combat/start-of-turn相当のhookが実際に再発火するかを検証する
   (Emulator側ソースコード調査が必須、本タスクの範囲外)。
2. TOOLBOX/FESTIVE_POPPER単独・組み合わせでの分離実験
   (6546-21のspecを改変した最小Scenarioでの再現性比較)。
3. `coerce_terminal_observation()`の対象範囲を「敵全滅」以外の
   終了条件(今回のような"生存しているのにlegal actionsが0"のケース)へ
   拡張すべきかの検討。
4. Relic/Powerの内部消費・カウンタ状態を復元経路でどう扱うべきかの
   設計方針(現状は完全に非復元)。
5. 戦闘外decision対応に向けた、`StartRun`系APIと現行の
   単一combat専用`ResetFromScenario`設計との統合方針。

---

## 9. 成果物一覧

主報告書: 本ファイル
(`Outputs/reports/rl_combat_execution_flow_and_architecture_audit_input_20260726.md`)

補助成果物(`Outputs/reports/combat_flow/`):

* `normal_online_sequence.md` — 通常オンライン戦闘のシーケンス図(A節)+
  Scenario 6546-21障害フロー(6節、確認済み事実／未確認仮説を分離)
* `legal_actions_sequence.md` — `enumerate_legal_actions()`単体の詳細トレース(B節)
* `heuristic_sequence.md` — Heuristic arm(D節)+ shadow処理(F節)
* `choice_sequence.md` — Choice処理の8分類(E節)
* `state_lifecycle.md` — 状態表現の区別・ライフサイクル図・可逆性の前提・
  trajectory生成(G節)
* `reset_from_scenario_call_sites.csv` — ResetFromScenario全呼出箇所一覧(4節)
* `state_restore_coverage.csv` — 状態復元契約の現状、19項目分類(5節)
* `call_count_summary.csv` — 1decisionあたりの呼出回数概算、arm別(3節)
* `known_risks.md` — 設計上の潜在問題候補、19項目(7節)

---

## 10. 遵守事項

本タスクではRLコード・Emulatorコードの修正、live GameInstance方式や
キャッシュ方式の変更、lookup/schema変更、Choice/Policy再学習、新規データ生成、
Azure利用のいずれも行っていない。診断用の追加コードも作成・実行していない
(全て既存コードの読み取り・grep・既存ログの参照のみ)。

文書化を完了し、ここで停止する。修正フェーズへは進まない。
