# Whole Run god mode がデフォルトONだった件の調査・修正報告(2026-08-11)

対象: `C:\STS2_RL` `Run/`。ユーザーからの指摘「全体runのdefaultがgod modeになっているように
思う」を受けた調査、および最小修正。

## 1. 結論(先出し)

**RL側のPythonコード3箇所が、`GameInstance.EnableGodModeForTesting()`(プレイヤーの
Strength/Buffer/Regenを999999999に設定=実質無敵)を無条件でデフォルトONにしていた。**
Emulator(C#)側に自動有効化は無く、原因は完全にRL側にある。

| 箇所 | 内容 | 導入commit |
|---|---|---|
| `choice_branch_runner.py`の`new_session()` | このモジュールの標準セッション生成関数。呼ぶたびに無条件で有効化 | `a03532f`(2026-08-03) |
| `room_progression_driver.py`の`drive_rooms()` | `already_started=False`なら無条件で有効化。無効化する引数が無い | `a03532f`(2026-08-03) |
| `worker_pool.py`の`_WorkerRuntime.__init__` | Worker Pool内の**全Worker**(Main Run Worker・Branch Worker全部)が起動時に無条件で有効化 | `e342f70`(2026-08-03) |

両commitのメッセージにgod modeをデフォルトONにする理由の記載は無い。「接続テスト用の
使い捨てscaffolding」として作られた際の利便性の名残りと見られ、その後`worker_pool.py`が
実際のBranch探索基盤に格上げされた際もこのデフォルトが引き継がれてしまったと考えられる。

`WholeRunSession.enable_god_mode_for_testing()`自体(thin wrapper)、および
`Run/tests/`配下でテストが自分自身のsessionに対して明示的にこれを呼んでいる箇所
(`test_execution_mode.py`/`test_external_control_decision_types.py`/
`test_fault_injection_additional.py`)は、指示により対象外(意図的な明示利用のため)。

## 2. 修正内容

上記3箇所から無条件呼び出しを削除し、god modeはデフォルトOFF・明示opt-inのみに変更した。

* `new_session()`: 呼び出しを削除するのみ(呼び出し元が必要なら自分で
  `session.enable_god_mode_for_testing()`を呼ぶ)。
* `drive_rooms()`: 同上。
* `worker_pool.py`の`_WorkerRuntime.__init__`: 同上。Worker Poolを使う全テストが
  god mode無しでも通ることを確認済み(§3)。

## 3. 副作用の調査・修正

上記3箇所の削除後、`Run/tests`全体で新たに6件が失敗した
(`test_treasure_room_stable_gap.py`の5件、`test_whole_run_connectivity.py`の1件)。
いずれも「god modeが無いと、決め打ちの簡易操作方針(`pick_default_action`という、
現実的な強さを持たない旧来のfillerポリシー)ではナビゲーション中に戦闘で敗北し、
本来テストしたい対象(TreasureRoomの仕様、10部屋到達可能性)に到達する前に探索が
止まってしまう」という同一原因だった。

これらはいずれも「戦闘の強さ」ではなく「特定の部屋種別に確実に到達できるか」を
検証するテストであり、god modeは目的達成のための手段として正当。指示の
「テストで明示的にgod modeをしている場合はそれをスキップしてほしい」という方針に
倣い、これらのテスト(または、テストが依存するライブラリ関数)にも同様に**明示的な
opt-in**を追加した:

* `choice_branch_runner.search_for_room_type()`に`god_mode: bool = False`引数を追加。
  `GameInstance.SaveState`/`LoadState`がこのフラグをsnapshotに永続化するため
  (`_godModeEnabled`→`GodModeEnabled`)、探索の起点セッションで一度有効にすれば、
  そこから派生する全てのprobe/downstreamセッションが`load_state()`経由で自動的に
  同じ状態を引き継ぐ(各呼び出し箇所で個別に再設定する必要はない)。
* `test_treasure_room_stable_gap.py`の`_find_treasure_room()`: 上記に`god_mode=True`を
  明示指定。
* `test_whole_run_connectivity.py`の`test_room_progression_driver_reaches_at_least_ten_rooms_seed18`:
  `new_session()`直後に`session.enable_god_mode_for_testing()`を明示追加(こちらは
  10部屋への機械的到達可能性そのものを検証する接続テストであるため)。

`worker_pool.py`(Branch Worker/Main Run Worker)由来のテスト(`test_worker_pool_process_separation.py`
全9件、`test_fault_injection_additional.py`の該当テスト)は、god mode削除後も**修正無しで
全てpassし続けた**ため、追加のopt-inは不要だった(seed=18・`min_rooms=10, max_steps=1500`の
探索予算内では、naive fillerポリシーでも自然に生き残っている模様)。

## 4. 検証結果

`Run/tests`全体:

* 修正前(god modeデフォルト削除前の元の状態): `37 passed, 1 failed`
  (失敗は`test_treasure_room_stable_gap.py::test_second_treasure_visit_reinitializes`、
  本件と無関係の既存failure — 別途確認済み)
* god modeデフォルト削除直後(opt-in追加前): `32 passed, 6 failed`(新規failure6件、
  全て§3の原因)
* opt-in追加後(最終): `37 passed, 1 failed` — **元のベースラインと完全一致**。
  god mode関連のfailureは全て解消し、新規のregressionは無い。

## 5. 影響範囲

* Emulator(C#)側の変更は無し。`EnableGodModeForTesting()`自体はテスト専用APIとして
  正当な設計であり(既定でOFF、`docs`にも明記)、今回の問題はRL側の呼び出し方のみ。
* `worker_pool.py`経由の実際のBranch探索(本番相当の経路)は、これでプレイヤーが
  無敵ではなくなった — 今後この経路で生成される学習データ/探索結果のHP・生死に
  関する情報が、初めて意味を持つようになる。
