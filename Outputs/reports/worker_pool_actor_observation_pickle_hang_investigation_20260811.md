# worker pool/holder-lease テストのハング・失敗 調査報告(2026-08-11)

対象: `C:\STS2_RL` `Run/tests/test_worker_pool_process_separation.py`(全9件中3件が
ハングまたは失敗)、`Run/tests/test_fault_injection_additional.py`の
`test_episode_close_with_holder_lease_still_active_shuts_down_cleanly`(ハング)。
本書は調査のみであり、修正コードは実装していない。

## 1. 結論(先出し)

**根本原因は1つに特定できた。** `run_emulator_bridge.py`の`to_plain()`ヘルパーが、
`TransitionOutcome.FinalEnemies`(`ActorObservation[]`)の各要素を実際には変換していない
(配列自体はPythonの`list`になるが、中身の各要素は生のpythonnet CLRオブジェクトのまま)。
これが`multiprocessing.Queue`を跨ぐ場面(worker pool経由でのみ発生)でpickle化に失敗し、
結果メッセージがworker側で静かに失われ、Controller側が`request_timeout_s`(既定120秒)
まるごと待ってから`_queue.Empty`で失敗する。同一プロセス内でしか使わない他のテスト
(`test_whole_run_connectivity.py`等)やRLの通常利用ではpickle化自体が発生しないため、
このバグは一切露見しなかった。

今回問題になった4件のテストは、**すべて同一の呼び出しパターン
(`pool.explore(ExploreRequest(seed=18, ..., min_rooms=10, max_steps=1500, ...))`)
から`_discover_all`経由で呼ばれており、seed=18でのその探索過程が(決定的に)途中で
戦闘敗北を経由するため、全4件が同一原因で失敗する。** 個別に4つのバグがあるわけではない。

## 2. 再現・特定手順

### 2.1 pytest経由では「ハング」にしか見えなかった

`pytest Run/tests/test_worker_pool_process_separation.py`をタイムアウト付きで実行すると、
90秒/120秒/300秒のいずれの上限でも該当テストの完了を確認できず、CPU使用時間も進捗ゼロ
(8秒間隔の2回サンプリングで完全一致)だったため、当初は真の無限ハングを疑った。

### 2.2 `python`直接実行で実際の例外が確認できた

このテストファイル自身のdocstringが「Run: `python test_worker_pool_process_separation.py`」
と明記している通り、pytest経由ではなく素の`python`で実行したところ(`-u`で出力バッファ無効化)、
以下の例外がworker側の`multiprocessing.Queue`内部スレッドから出力されることを確認した:

```
File "...\multiprocessing\queues.py", line 264, in _feed
    obj = _ForkingPickler.dumps(obj)
File "...\multiprocessing\reduction.py", line 51, in dumps
    cls(buf, protocol).dump(obj)
TypeError: cannot pickle 'ActorObservation' object
```

これはQueueの内部フィーダースレッド(デーモンスレッド)内で発生しており、worker側の
メインループはこれを検知せず、送るはずだったメッセージが黙って失われる。

### 2.3 Controller側の実際の挙動(タイムアウト経由の`queue.Empty`)

ラッパー無しで`python -c`から直接`test_holder_and_sibling_pids_differ_for_all_six_choice_types()`
を呼び出したところ(タイムアウト無し)、約120秒後に以下の通り明確な例外で失敗することを確認した
(=真の無限ハングではなく、`request_timeout_s`(既定`120.0`、[worker_pool.py:738](../../Run/worker_pool.py#L738))
分の待機後に失敗する、という意味での「実質ハング」):

```
File "...\worker_pool.py", line 909, in explore
    received_id, kind, payload = self._result_queue.get(timeout=self.request_timeout_s)
File "...\multiprocessing\queues.py", line 114, in get
    raise Empty
_queue.Empty
```

これで「pytestでは真の無限ハングに見えていたが、実際は120秒タイムアウト後に例外で
落ちている」という挙動が説明できた(pytestの実行全体を包む短いタイムアウトが、
個々のテスト内部の120秒待機より先に切れていたため、こちらからは進捗ゼロの無限ハングに
見えていた)。

### 2.4 根本原因の直接確認(pickle失敗の再現)

`run_emulator_bridge.py`の変換関数を1つずつ確認した結果、`transition_outcome_to_dict()`
([run_emulator_bridge.py:127-146](../../Run/run_emulator_bridge.py#L127-L146))が
`"final_enemies": to_plain(transition.FinalEnemies)`という変換を行っているが、
`TransitionOutcome.FinalEnemies`の実体は`ActorObservation[]`
([Sts2Emulator/Dto/TransitionOutcome.cs:20](../../../STS2_Emulator/Sts2Emulator/Dto/TransitionOutcome.cs#L20)、
Emulator側)である。

`to_plain()`([run_emulator_bridge.py:80-91](../../Run/run_emulator_bridge.py#L80-L91))は
`None`/プリミティブ/`System.Decimal`/`Keys`+`Values`を持つ辞書風オブジェクト/`__iter__`を
持つ反復可能オブジェクトのみ変換対象とし、**それ以外(スカラーなCLRオブジェクト)は
無変換のまま`return value`する**。`ActorObservation`(`Id`/`Name`/`Hp`/`MaxHp`/`Block`/
`Energy`/`Intent`というスカラープロパティのみを持つ)はこのいずれにも該当しない。
そのため`to_plain(ActorObservation[])`は「外側の配列はPythonの`list`に変換されるが、
各要素は生のCLRオブジェクトのまま」という中途半端な結果になる。

自作の直接検証スクリプトで確定させた:

* 1HP即死シナリオ(`ResetFromScenario`でプレイヤーHP1、敵CALCIFIED_CULTIST HP999)で
  意図的に敗北を発生させ、`transition_outcome_to_dict(step_result.Transition)`の
  `final_enemies`を確認したところ、`len(final_enemies) == 1`かつ
  `type(final_enemies[0]) == Sts2Emulator.Dto.ActorObservation`(生のCLRオブジェクト)、
  `pickle.dumps(final_enemies)`が実際に`TypeError: cannot pickle 'ActorObservation' object`
  で失敗することを確認した。
* 一方、**勝利**時は`final_enemies`が空配列(`[]`)になる(全滅した敵はこの配列に
  含まれない模様)ため、pickle化に失敗する要素そのものが存在せず、バグが顕在化しない。
  これが「同一プロセス内で使う限り誰も気づかなかった」理由の一部でもある(空配列なら
  pickle化も問題なく成功するため、勝利で終わる戦闘しか通らない経路ではこのバグを踏まない)。

### 2.5 4件すべてが同一経路を通ることの確認

該当4テストのコードを確認した結果、いずれも冒頭で同一の呼び出しを行っている:

```python
found = _discover_all(pool, seed=18)  # → pool.explore(ExploreRequest(seed=18, ...,
                                       #    min_rooms=10, max_steps=1500, ...))
```

* `test_holder_and_sibling_pids_differ_for_all_six_choice_types`
* `test_worker_generation_increments_on_respawn_and_old_leases_invalidated`
* `test_in_worker_fault_does_not_kill_the_process`
* `test_episode_close_with_holder_lease_still_active_shuts_down_cleanly`
  (`test_fault_injection_additional.py`、`target_room_types`は異なるが同じ`explore`呼び出し)

`ExploreRequest(min_rooms=10, max_steps=1500, ...)`は`room_progression_driver.drive_rooms`
経由で`pick_default_action`という決定論的だが強くはないfillerポリシーで最大1500 stepまで
複数戦闘を進行させる。seed=18ではこの探索過程で(決定的に)少なくとも1回、戦闘が
「勝利以外の形」(敗北、または生存する敵が残った状態での結果)で終わり、
`FinalEnemies`が非空になるタイミングを踏むと推測される(直接の統計は取っていないが、
本調査の直接検証(2.4節)で1回の敗北がこの状態を確実に再現することは確認済み)。

## 3. 影響範囲の整理

* **影響を受ける**: `worker_pool.py`経由(`ExploreRequest`/`ChoiceWorkItem`のいずれの
  dispatchも)で、途中に「敵が生き残った状態で戦闘が終了する」ケースを1回でも踏む全ての
  呼び出し。`explore()`はデフォルトの`min_rooms=10, max_steps=1500`という大きな探索
  予算を持つため、実質的にほぼ確実にこの経路を踏む。
* **影響を受けない**: `test_whole_run_connectivity.py`、`room_progression_driver.py`
  単体実行、本調査より前に追加した`test_ascension_level_effects.py`など、同一プロセス内で
  `WholeRunSession`を直接使う経路。pickle化そのものが発生しないため、生のCLRオブジェクトが
  混ざっていても実害がない。
* `test_treasure_room_stable_gap.py::test_second_treasure_visit_reinitializes`の失敗
  (別途確認済み)は本調査の対象外の、無関係な既存の失敗と思われる(未深掘り)。

## 4. 修正方針(未実装、案のみ)

`run_emulator_bridge.py`の`to_plain()`自体を「スカラーなpythonnetオブジェクトでも、
既知のDTO型なら専用の変換関数を呼ぶ」ように拡張するか、より narrow に
`transition_outcome_to_dict()`内で`final_enemies`を`to_plain()`ではなく
`[actor_observation_to_dict(e) for e in transition.FinalEnemies]`のように明示変換する
(`ActorObservation`→dict変換関数は`observation_to_dict()`が`Player`/`Enemies`型フィールドを
使っていないため現状存在しない — 新設が必要)、のいずれかが妥当と思われる。後者の方が
影響範囲が小さく、`to_plain()`という汎用ヘルパーの意味論(コンテナのみ再帰変換)を
変えずに済むため望ましいと考える。

Emulator側(C#)の変更は不要 — `TransitionOutcome.FinalEnemies`が`ActorObservation[]`型で
あること自体は正当な設計であり、問題はPython側の変換漏れのみ。

## 5. 停止

原因の特定と修正方針の提示までを行った。修正実装はsupervisor承認待ちとしてここで停止する。
