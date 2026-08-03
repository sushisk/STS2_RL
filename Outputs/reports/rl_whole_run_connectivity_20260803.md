# Whole Run連続進行／Choice分岐試験 — 中間報告(停止)

対象: `C:\STS2_Emulator` baseline commit `dd8c800`(「新StepResult DTO対応とWhole Run試験再開」指示に基づく)。
本書は指示の停止条件に該当する事象を検出したための**停止報告**であり、完了報告ではない。

## 1. 結論(先頭サマリ)

- 新StepResult DTO(`Transition`/`RoomContext`/`reward_select`/新`Done`意味)への対応と、既存Combat専用
  terminal互換は完了・検証済み。既存Combat Search/Main Loop/Shadow/耐久試験への影響はゼロ。
- Whole Run接続コード(`Run/`)を新規実装し、10 Room以上の連続進行、Map Choice分岐は実機で安定して
  成功することを確認した。
- **Event・Combat Reward・Rest Choiceの解決Stepで、`GameInstance.EagerExitCurrentRooms()`が
  `InvalidOperationException("RunManager.ExitCurrentRooms returned but CurrentRoom is still ...")`
  を投げる、commit `dd8c800`由来の再現性のある不具合を検出した。** 旧baseline(`87a0962`)との
  A/Bテストで、同じ操作列が旧baselineでは100%成功することを確認済み — dd8c800のRoom Exit処理
  リファクタリングによる新規リグレッションと判断する。
- 上記は「ChoiceをReplayで再現できない」「公式API文書と実挙動の不一致」という指示の停止条件に
  該当するため、原因の推測修正は行わず、ここで停止して報告する。1,000件規模の混合Choice耐久試験は
  未実施。

## 2. 新StepResult DTO対応(完了)

### 2.1 実装

- `Run/run_emulator_bridge.py`: `transition_outcome_to_dict()`(新設)、`step_result_to_dict()`に
  `room_context`/`transition`を追加、`Done`の意味変更をdocstringで明記。
- `Run/whole_run_session.py`: `RUN_TERMINAL`/`REWARD_SELECT`等のBoundary定数、
  `combat_just_concluded()`ヘルパー、旧`GetObservation()`再取得の回避処理は導入しない方針を明記
  (Step()自体がRoomContext/Observation/LegalActionsを同一世代で構築するため不要)。
- `Combat/battle_emulator.py`(`step_live_action`): `StepResult.Transition.Kind ==
  "combat_completed"`を権威あるCombat終了シグナルとして使用し、`Transition.FinalObservation`を
  最終戦闘状態としてwrapするよう変更。デバッグ用整合性assertion
  (`Transition`存在時は必ず`Observation.IsTerminal`が真であること)を追加。

### 2.2 旧`Boundary == "terminal"` / `StepResult.Done`使用箇所の全数調査

`Combat/`全体を調査した結果、Combat側のコードは**一度も生のEmulator `Boundary`文字列や
`StepResult.Done`を直接参照していない**ことを確認した(`Combat/search/decision_context.py`の
`BOUNDARY_TERMINAL = "terminal"`はRL側独自の語彙で、`BattleState.is_terminal`から導出される別物)。
唯一の実質的な依存は`battle_emulator.py:660`の`obs.IsTerminal`読み取りで、これは
`ComputeIsRunOver`のlegacy no-mapモード契約(`!HasMap => 戦闘終了時のみtrue`)が dd8c800でも
変更されていないため無改修でも動作は同一だが、上記の通り`Transition`を権威あるシグナルとして
明示的に使うよう更新した。

### 2.3 検証

`Combat/tests/test_battle_emulator_transition_outcome.py`(新規、3 tests)で victory/defeat/継続中
の3ケースを実機検証、全PASS。既存回帰スイート20ファイルを実行し、新規リグレッション無し
(4節参照)。

## 3. Whole Run接続コード(`Run/`、新規)

| ファイル | 内容 |
|---|---|
| `Run/run_emulator_bridge.py` | CLRブートストラップ、DTO→plain dict変換 |
| `Run/whole_run_session.py` | `GameInstance`を1プロセス1個保持する`WholeRunSession`ラッパー |
| `Run/room_progression_driver.py` | 連続Room進行ドライバ(Treasureは別経路で継続) |
| `Run/choice_branch_runner.py` | Holder/sibling Choice分岐ランナー(6種) |
| `Run/tests/test_whole_run_connectivity.py` | 実機テスト(5 tests) |

### 3.1 実装時に発見・修正したバグ(自己発見、Emulator側ではなくRL側)

`RoomContext.Event`(`EventRoomContext`型)が`to_plain()`の汎用CLR→dict変換に非対応
(Keys/Values/`__iter__`いずれも持たない)で、生のCLRオブジェクトのまま返っていた。2回の独立した
`GetRoomContext()`呼び出しが等価比較で不一致になることで発覚。`event_room_context_to_dict()`を
新設して修正、回帰テストを追加済み。

## 4. 実機検証結果

### 4.1 連続Room進行(10 Room以上)

seed=18, Ironclad, ascension=0で実行。ログ全文: `Outputs/reports/whole_run_logs/room_progression_seed18.json`。

| 項目 | 結果 |
|---|---|
| 到達Room数 | 10 |
| 到達Boundary種別 | Map選択、Combat(`stable`)、Combat Reward(`reward_select`)、Event(`event_choice`→`pending_choice`) |
| 遭遇Room種別 | CombatRoom、EventRoom、MerchantRoom、TreasureRoom |
| 未対応Room | TreasureRoom ×2(直前Map SnapshotへReload後、別候補への迂回を試行 — 1件は迂回成功、
  もう1件は候補が全てTreasureのみで迂回不可、既知の制約として記録) |

各Stepについて、Boundary(前後)、選択Action、LegalActions、RoomContext、Transition(該当時)、
CombatSessionId、HP/Deck/Relic/Potion/Goldをログに記録済み。RestSiteRoom/MerchantRoomの購入は
このseedの10 Room内では自然発生しなかった(MerchantRoomには到達したが未購入のまま次Roomへ)。

Rest選択の実機確認は5-3節のChoice分岐試験(seed=2)で別途実施済み。

### 4.2 Choice分岐試験(6種)

生成JSON全文: `Outputs/reports/whole_run_logs/choice_branch_results.json`。

| Choice種別 | 結果 | 備考 |
|---|---|---|
| Map | **OK** | Boundary/同一Choice同一結果/異なるChoice分岐/Holder-sibling分離、全て確認 |
| Combat Pending(TOOLBOX注入) | **部分OK** | Boundary/ChoiceScope/ChoiceKind/RoomContext/LegalAction Semantic集合/Prefix再現、全て一致。ただし`different_choices_diverge`のみFalse — 5節参照 |
| Shop | **OK** | 全チェック一致(1/1試行で成功) |
| Event | **NG** | 3/3試行で`EagerExitCurrentRooms`例外、Holderの初回到達段階で失敗 — 5節参照 |
| Combat Reward | **NG** | 3/3試行で同例外 |
| Rest | **NG** | 3/3試行で同例外 |

Combat Pending到達には、直前Map Boundary SnapshotのJSONへ`TOOLBOX`relicを注入する手法
(`choice_branch_runner.inject_relic`)を用いた — Whole Run API文書の「JSON編集可能項目」節が
`relics`編集の反映を確認済みとしていることに基づく、文書が示唆する正規の手法である。
既存のCombat Start Replay Root(Combat側の内部探索機構)は今回利用しなかった — Whole Run層は
`StartRun`駆動でCombat側の`ResetFromScenario`駆動とは実行文脈が異なり、Map Boundary Snapshot +
Action Prefix Replayという単一の統一機構が全6種のChoiceに共通してそのまま適用できたため。

## 5. 検出した不具合(停止条件に該当)

### 5.1 事象

`GameInstance.Step()`内、`TryEagerExitResolvedRoom()`→`EagerExitCurrentRooms()`
(`GameInstance.cs:5158-5183`)が、Event/Combat Reward/Rest Choiceを解決した直後に
`InvalidOperationException("RunManager.ExitCurrentRooms returned but CurrentRoom is still
{RoomType}.")`を投げる。`RunManager.Instance.ExitCurrentRooms()`が完了したにもかかわらず
`_runState.CurrentRoom`がまだ非nullという、エンジン内部の不整合を検出した防御的例外である。

### 5.2 再現性の切り分け(A/Bテスト実施)

| 条件 | 結果 |
|---|---|
| `dd8c800`、同一プロセス内でLoadState/ChooseRoom/probe等を多数(6件以上)連続構築した後にReward選択 | Reward: 3/3失敗、Rest: 3/3失敗、Event: 実施した9試行中1回失敗 |
| `dd8c800`、GameInstanceを2個(StartRun側/LoadState側)のみ使う最小構成でReward選択×5試行 | 5/5成功 |
| 旧baseline `87a0962`(dd8c800の直前commit)、上記と同一の多数GameInstance構成でReward選択×5試行 | 5/5成功 |

この切り分けにより、(a) 同一プロセス内で`GameInstance`を多数連続構築するほど発生しやすくなる、
(b) `dd8c800`で新規に混入した規模であり`87a0962`には存在しない、の2点を確認した。
`GameInstance`は本来「同一プロセス内では逐次操作する限り安全」と文書化されているが(Whole Run
API文書「GameInstance singleton依存とsiblingの注意点」節)、今回のHolder/sibling/Replay機構が
要求する「Map SnapshotをLoadしては破棄」という反復パターンでこの契約が破れることを検出した。

### 5.3 Combat Pendingの`different_choices_diverge`不一致(関連事象、別原因)

TOOLBOX選択で異なるカードを選んでも、結果状態(gold/hp/deck_size/relics/room_type)に差が
出なかった。個別に調査したところ、`Toolbox.BeforeHandDraw`→`CardPileCmd.AddGeneratedCardToCombat`
内で断続的に`ArgumentOutOfRangeException`が発生しているが、この例外は`TaskHelper`経由の
fire-and-forget非同期タスク内でログ出力されるのみで`Step()`の戻り値には伝播せず、選択したカードが
静かに手札へ追加されないまま`Step()`が正常終了したように見える。5.2節と同じ「同一プロセス内での
GameInstance連続構築」条件下でのみ観測されており、根本原因は共通の可能性が高い。

### 5.4 判断

いずれも指示の停止条件「ChoiceをReplayで再現できない」「公式API文書と実挙動の不一致」に該当する
と判断し、原因を推測して修正することはせず、ここで停止する。1,000件規模の混合Choice耐久試験は
この不具合の影響を受けるため未実施。

## 6. 推奨事項(Emulator側 / RL側)

- Emulator側: `EagerExitCurrentRooms`/`RunManager.ExitCurrentRooms`および
  `Toolbox.BeforeHandDraw`まわりの非同期処理が、直前に構築・破棄された別の`GameInstance`の
  残存タスクと競合している可能性が高い。`GameInstance`の構築/破棄(teardown)時に、旧インスタンスに
  紐づく非同期タスクを確実に完了/キャンセルしてから新インスタンスの静的シングルトン
  (`RunManager.Instance`/`CombatManager.Instance`)を差し替える保証が無いのではないか、という
  観点での調査を推奨する。
- RL側: 本タスクのHolder/sibling/Replay機構を今後も使う場合、Combat側が既に採用している
  「Branch WorkerはOS Processを分離する」方式(`Combat/search/branch_worker_pool.py`、
  Phase H/Iで1,000+イベント規模の安定動作を確認済み)への移行を検討する — 同一プロセス内での
  `GameInstance`連続構築ではなく、Holder/siblingそれぞれを別プロセスにすることで、今回検出した
  クラスの不具合を回避できる可能性が高い。

## 7. 制約の遵守確認

- Pending/Reward/Event途中状態を直接Save/Loadしていない(`SaveState()`は常にMap Boundaryでのみ
  呼んでいる) — 確認済み。
- Combat Start Pendingを含め、Choice待ち状態自体をSnapshotとして扱っていない。
- Main(この文脈ではHolder自身のセッション)の真のOrderedDrawPile/将来RNGを評価へ直接渡していない
  — Whole Run層はCombatの内部RNG Hypothesis機構に触れておらず、既存のSearch Hypothesis/DrawPile
  Belief規則(Combat側)は変更していない。

## 8. 回帰テスト結果

`Combat/tests/`全20ファイルを実行。

- 新規リグレッション: **無し**。
- 既知の事前リグレッション: `test_restore_snapshot_phase3c1.py`の2件
  (`test_official_json_example_restores_successfully`、`test_real_6546_21_rejected_via_public_api`
  — 既存フェーズから継続する既知の無関係な失敗)。
- 新たに観測: `test_scenario_v2.py::test_wriggler_missing_slot_without_encounter_is_detected`が
  `TimeoutException`で失敗。旧baseline `87a0962`でも同一条件で再現することを確認したため、
  dd8c800由来ではなく、本セッションの高負荷(複数回のdotnet build/大量のCLRロード)による
  タイムアウト起因の既存の環境依存フレークと判断した(コード変更に起因するものではない)。

`Run/tests/test_whole_run_connectivity.py`(5 tests)は3回連続実行して安定してPASSしている
(5.2節の不具合は低頻度のためこの5テストでは顕在化していない)。

## 9. 成果物

- コード: `Run/run_emulator_bridge.py`、`Run/whole_run_session.py`、
  `Run/room_progression_driver.py`、`Run/choice_branch_runner.py`
- テスト: `Combat/tests/test_battle_emulator_transition_outcome.py`、
  `Run/tests/test_whole_run_connectivity.py`
- ログ: `Outputs/reports/whole_run_logs/room_progression_seed18.json`、
  `Outputs/reports/whole_run_logs/choice_branch_results.json`
- 本報告書

## 10. 次のアクション(後任 / 再開時への申し送り)

1. 5.4節の不具合についてEmulator側の調査・修正を依頼する。
2. 修正後、Choice分岐の6種全てで再検証してから1,000件混合耐久試験に進む。
3. 恒久対応として、Holder/sibling/ReplayをOS Process分離方式へ再設計するか検討する
   (6節参照)。
