# 設計上の潜在問題候補(7節)

結論を断定せず、コード上確認できた候補のみを列挙する。各項目に
`根拠コード` / `実害確認済み／未確認` / `影響範囲` / `調査優先度`
(最高・高・中・低)を付す。

---

### 1. fresh restoreによる副作用

* **根拠コード**: `battle_emulator.py:453`(`scenario.Relics = str_list([...id...])`)、
  `battle_emulator.py:670-673`(turnNumber常時リセットのコメント)。
* **実害**: 未確認(6546-21での因果関係は仮説段階、`normal_online_sequence.md`6節参照)。restore自体が「エンジンにとって新規combat開始と区別不能」という構造的事実は確認済み。
* **影響範囲**: 全decision(`enumerate_legal_actions`/`apply_action`いずれも`_restore()`を経由)。
* **調査優先度**: **最高**(6546-21調査の中心仮説)。

### 2. RNGの非同一性

* **根拠コード**: `build_scenario_from_state():492` `scenario.Seed = int(engine_state.get('seed', 1))` — 元シードであり、現在のRNGカーソルではない。`shuffle_rng_seed`は通常運用では`None`(with_shuffle_seed()経由のlookahead分岐専用)。
* **実害**: 未確認(本セッションでは直接の異常事例観測なし)。
* **影響範囲**: 山札が尽きてreshuffleが発生する場面、モンスター側のランダム行動選択など、pile順序以外の乱数に依存する全て。
* **調査優先度**: 中。

### 3. one-shot effectの二重適用

* **根拠コード**: `state_restore_coverage.csv`「one-shot relic／powerの消費状態」行 — Relicはidのみ送信、内部カウンタ/消費フラグを運ぶフィールドが存在しない。
* **実害**: 未確認(6546-21の仮説段階、TOOLBOX/FESTIVE_POPPERが実際にこのScenarioのrelicsに含まれることのみ確認済み)。
* **影響範囲**: start-of-combat/start-of-turn型のrelic/power全般。
* **調査優先度**: **最高**。

### 4. event hookの重複登録

* **根拠コード**: 該当するコードパスを本タスクの参照範囲内で発見できず。
* **実害**: 未確認。
* **影響範囲**: 不明。
* **調査優先度**: 低(具体的コード根拠なし — 次回Emulator側監査での確認事項として記録するに留める)。

### 5. Power／Relic内部状態の欠落

* **根拠コード**: `_power_stacks()`(`battle_emulator.py:233-246`)は`{PowerId, Amount, AssociatedCard}`のみ、`Relics`代入(453行)はidのみ。
* **実害**: 構造的欠落そのものは確認済み。個別の実害事例は未確認。
* **影響範囲**: カウンタ型power/relic全般(例: 「Nターンごとに」系)。
* **調査優先度**: 高。

### 6. command queue欠落

* **根拠コード**: `CombatScenario`/`engine_state`いずれにもcommand queue相当のフィールドが見当たらない(本タスクの参照ファイル内)。
* **実害**: 未確認 — そもそも該当概念がEmulator内部に存在するかどうか自体が本タスクの範囲では確認不能。
* **影響範囲**: 不明。
* **調査優先度**: 低〜中(次回Emulator側監査での確認事項)。

### 7. Choice context欠落

* **根拠コード**: `_pending_choice_scenario()`(`battle_emulator.py:315-345`) — `scenarioRestorable is False`または`scope=="ActionContinuation"`なら即`None`を返す。`selectedCount`はそもそも送信対象に含まれない。
* **実害**: 確認済みだが、これは**意図的な設計**(モジュールdocstring/`apply_action()`のエラーメッセージが明記する既知の制約)であり、隠れたバグではない。
* **影響範囲**: StartOfCombat-scope以外のpending choice全般。
* **調査優先度**: 低(既知・意図的)。

### 8. Python状態とlive stateの不一致

* **根拠コード**: `battle_emulator.py:670-673`(turn数はPython側`battle_state.turn`とエンジン内部`turnNumber`が別々に管理される、後者は常に1にリセットされる)。
* **実害**: 「別々に管理されている」という構造的事実は確認済み。観測可能な食い違いによる実害の唯一の候補は6546-21(未確定)。
* **影響範囲**: turn数に依存する全ロジック。
* **調査優先度**: 高。

### 9. legal action cacheの陳腐化

* **根拠コード**: `enumerate_legal_actions()`自身のコメント(`battle_emulator.py:835-838`)「we have live cases where StepResult.LegalActions after a complex card play is stale」。
* **実害**: 通常状態については**過去に確認済み、かつ現在の設計(常にfresh restore)で回避策済み**。ActionContinuation中のキャッシュ利用(`_cached_legal_actions`)について同種の陳腐化が起きないかは**未確認**。
* **影響範囲**: ActionContinuationループ中のlegal action取得。
* **調査優先度**: 中。

### 10. 状態変換コスト

* **根拠コード**: `call_count_summary.csv`(Heuristic 1decisionあたりL×T+2回のrestore)。
* **実害**: 呼出回数としては確認済み。実行時間への影響は既存のオンライン評価報告(Heuristic armがPolicy armよりわずかに遅い傾向)からも間接的に整合。
* **影響範囲**: Heuristic評価およびshadow評価を使う全ての箇所。
* **調査優先度**: 中(性能上の懸念であり、正当性の懸念ではない)。

### 11. ResetFromScenarioの呼出過多

* **根拠コード**: `legal_actions_sequence.md`の冗長呼出指摘 — `_restore()`が`ResetFromScenario`自身の戻り値(`ResetResult.LegalActions`)を捨てて、直後にもう一度`GetLegalActions()`を呼んでいる。
* **実害**: 冗長呼出そのものは確認済み。定量的な性能影響は未計測。
* **影響範囲**: 全decision(`enumerate_legal_actions`経由の全呼出)。
* **調査優先度**: 中。

### 12. deepcopy不能な.NET型

* **根拠コード**: `emulator_bridge.py:100-125`(`to_plain()`のDecimal特殊対応、コメントに「過去に`TypeError: cannot pickle 'Decimal' object`として実際に発生した」旨明記)。
* **実害**: **過去に確認済み、既に修正済み**(このengagement内で対応)。
* **影響範囲**: `clone_state()`/`with_shuffle_seed()`を使う箇所全て。
* **調査優先度**: 低(修正済み、再発監視のみ)。

### 13. static singletonと複数GameInstance

* **根拠コード**: `emulator_bridge.py:83-89`(`shared_game_instance()`)、`battle_emulator.py`モジュールdocstring「Only one GameInstance can be live per OS process (verified experimentally...)」。
* **実害**: 確認済み(過去に実験的に確認された制約として明記)。
* **影響範囲**: マルチプロセス/マルチワーカー設計全体(1 worker=1 process制約、`combat_env.py`のdocstringにも明記)。
* **調査優先度**: 中(現状は運用でカバー済みだが、将来のスケールアウト設計に直結)。

### 14. terminal判定の二重管理

* **根拠コード**: `coerce_terminal_observation()`(`battle_emulator.py:67-84`) — 「We have live saved cases where Step() returns an observation with no living enemies but IsTerminal==false」というコメント付きで、**エンジン自身の`IsTerminal`フラグを信頼せず、Python側で敵の生存状態から独自に補正する既存ロジックがある**。
* **実害**: 過去に確認済み(このロジックが存在すること自体がその証拠)。ただし**6546-21のケースはこの既存の補正ロジックの対象外**(`state_has_living_enemies()`は`SOUL_NEXUS`がhp=5で生存中なので真を返し、`is_terminal=False`のまま通過する — つまりこの既知の対策コードではキャッチされない、"生存しているのにlegal actionsが0"という**別種の**不整合であることが分かる)。
* **影響範囲**: `is_terminal`判定に依存する全ロジック。
* **調査優先度**: **最高**(6546-21と直接関連するが、既存の対策ロジックの対象パターンとは異なることが今回新たに判明した)。

### 15. ActionContinuationの二重処理

* **根拠コード**: 該当する二重処理の証跡は見当たらなかった — `apply_action()`(901-909行)のcontinuationループは単一の`while`で明確に管理されている。
* **実害**: 未確認、具体的懸念根拠は薄い。
* **影響範囲**: 該当なし。
* **調査優先度**: 低。

### 16. Policy／Heuristic間で異なる実行意味

* **根拠コード**: `call_count_summary.csv` — Policy armは1decisionあたり2 restore、Heuristic armはL×T+2 restore。同じ「1 decision」でも処理コスト・処理経路がarmにより全く異なる。
* **実害**: 構造的な非対称性として確認済み。
* **影響範囲**: Policy vs Heuristic比較を伴う全評価(速度指標等)。
* **調査優先度**: 中(既存の速度比較レポートに既に数値として現れている既知の構造)。

### 17. teacher生成とonline実行の経路差

* **根拠コード**: `state_lifecycle.md`G節 — teacher2000本体はActionContinuation Choiceを一切記録しないが、Choice教師データ生成・オンライン評価はロギングラッパーで可視化する。
* **実害**: 確認済み(`build_choice_scenarios_manifest.py`のdocstringで既に明示的に発見・報告済み)。
* **影響範囲**: Choice Policy学習データの母集団定義。
* **調査優先度**: 低(既に把握・対応済みの既知事項)。

### 18. ゲーム全体進行時にlive runを維持できない設計

* **根拠コード**: `Outputs/reports/rl_choice_policy_adoption_and_out_of_combat_survey_report_20260725.md`5節 — Combat側は`StartRun`系API(`GetMapRooms`/`ChooseRoom`/`GetRunState`等)を一切呼んでおらず、`ResetFromScenario`は常に単一combat専用の`CombatScenario`のみを渡す設計。
* **実害**: 未実装という事実として確認済み。
* **影響範囲**: 次工程(戦闘外decision対応)全体の設計。
* **調査優先度**: 高(次工程の前提となる)。

### 19. 戦闘終了後のrun state引き継ぎ不能

* **根拠コード**: 同上の報告書5節 — `GetRunState()`/`GetRunSummary()`の存在は確認されているが、Combat側から一切呼ばれていない。現在の`BattleEmulator`は戦闘終了後の状態(gold/deck変化等)をrun全体へ反映する仕組みを持たない。
* **実害**: 未実装という事実として確認済み。
* **影響範囲**: 次工程設計。
* **調査優先度**: 高。
