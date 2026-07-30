# RL担当 Phase 3A.3最終統合報告 — Action fault契約のPython側統合(2026-07-27)

対象: 「RL担当 Phase 3A.3最終統合指示」。実施事項を全て完了した。Phase 3B・
`RestoreSnapshot`・Heuristic・Training本体には着手していない。

## 1. 基準情報確認

実ファイル・実commitから直接確認した。

| 項目 | 指示書記載値 | 実確認値 | 一致 |
|---|---|---|---|
| Importedコードcommit | `1e637c8` | `git log`: `1e637c8a7daefbdf43d3f9f4e8a49e98db2a3e2c` | ✅ |
| Emulatorコードcommit | `5d222ef` | `git log`: `5d222efa117712503d551941daa83cfa8c7365d1` | ✅ |
| 報告書commit | `5b5c98c` | `git rev-parse HEAD`: `5b5c98c8903fb5575703e46f4383ee1543fa06d7` | ✅ |
| DLL SHA256 | `f61a1533e7ba8c746d1257ccf9e68f7ea239394ec2f268f1df4aece514ad1bf0` | `sha256sum`同一値 | ✅ |

Emulator報告書(`step_action_exception_propagation_fix_20260727.md`)を
全文読了。`GameAction.cs`(Imported、`1e637c8`)の3分岐化(正常/fault/cancel)、
`GameInstance.cs`(`5d222ef`)の`ThrowIfSettledTaskFaulted`/
`EnsureSessionNotFaulted`実装、新規`ActionFaultedException.cs`/
`FaultedCombatSessionException.cs`(いずれも構造化プロパティなし、
`Message`文字列と`InnerException`のみ)を実ファイルで確認した。

## 2. Python側実装

### 2-1. Python例外型

`Combat/live_combat_session.py`に追加:

* `ActionFaultContext`(frozen dataclass): `combat_session_id`/`step_index`/
  `action_id`/`action_type`/`card_id`/`target_index`/`target_enemy_index`
  は**Python側が既に保持している情報**(このsessionの`_current_frame`と、
  呼び出し元が`step()`へ渡した`action`/target引数)から構築——C#例外
  文字列のパースに依存しない、権威的な情報源。
  `action_description`/`original_exception_type`/
  `original_exception_message`は、まずCLR例外オブジェクトの
  `InnerException`を構造的に読み(`.GetType().FullName`/`.Message`、
  文字列パースではなくオブジェクト属性アクセス)、それが得られない場合
  にのみ`ActionFaultedException.Message`への正規表現マッチへフォール
  バックする(C#側の2つの例外クラスは構造化プロパティを一切持たない
  ——実ファイルを読んで確認済み——ため、Message文字列は唯一の追加情報源
  だが、「唯一の情報源」にはしていない)。`raw_message`に元の文字列を
  そのまま保持(監査用)。
* `ActionExecutionError(RuntimeError)`: `.context`に`ActionFaultContext`、
  `.__cause__`(`raise ... from clr_exc`)で元のCLR例外連鎖を保持。
* `FaultedCombatSessionError(RuntimeError)`: `.combat_session_id`。

`Combat/emulator_bridge.py`: `ActionFaultedException`/
`FaultedCombatSessionException`を`_types`辞書へ登録(既存の
`QuiescentBoundaryViolationException`と同一パターン)。

### 2-2. `LiveCombatSession`の挙動

* `self._session_faulted`(bool)・`self.last_fault_context`を新設。
  C#側`_sessionFaulted`と同じ規則(「Reset成功時のみ解除」)を
  `start_combat()`/`resume_from()`/`_resynchronize()`(いずれも
  `ResetFromScenario`を呼ぶ)の**成功パス末尾でのみ**`False`へ。
* `_ensure_session_not_faulted()`: `step()`/新設`get_observation()`/
  新設`get_legal_actions()`/`capture_snapshot()`の冒頭で呼び出し、
  faulted中なら`FaultedCombatSessionError`を即座に送出(CLR呼び出しへ
  到達する前に拒否)。
* `step()`: `step_live_action()`への2箇所の呼び出し(本アクション・
  ActionContinuation継続ループ)双方を`except`で囲み、CLR
  `ActionFaultedException`を捕捉したら`_handle_action_fault()`で
  `ActionFaultContext`を構築・`_session_faulted = True`を設定してから
  `ActionExecutionError`を送出(先にfaulted化してから例外を投げる
  ——呼び出し元のexceptハンドラが同じsessionへ再度触れても正しく拒否
  される)。
* **意図的に変更していない**: `battle_emulator.py`の`step_live_action()`/
  `apply_action()`自体(Heuristic/beam-search/lookaheadの`legacy_
  approximate_restore`経路が共有——指示の「Heuristicは変更しない」を
  文字通り遵守)。この経路でfaultが起きた場合、生のCLR例外がそのまま
  伝播する(意図的、8節で実例を確認)。

### 2-3. episode処理

契約v0.5§6へ`engine_action_fault`分類・「現段階では学習データへ含めず
episode全体を破棄」というポリシーを記録した(コードとしての実装は
今回のスコープ外——trajectory生成コード自体は変更していない、Training/
報酬関数/Policy/Value/Heuristicは無変更)。

## 3. 必須試験結果

### 3-A. fault契約(`Combat/tests/test_action_fault_contract.py`、新規、
6件全合格)

Emulator担当自身の手法(`Console.Out`を破棄済み`StreamWriter`へ差し替え、
実際の`PlayCardAction.cs:91`の`Log.Info`呼び出しを`ObjectDisposedException`
で例外終了させる)をPython側から独立に再現した(伝聞で信頼せず、自ら
再実装・実行して確認):

| 試験 | 結果 |
|---|---|
| Step中のAction例外がPythonへ到達 | ✅ `ActionExecutionError`として捕捉 |
| 元例外情報を保持 | ✅ `original_exception_type`に`ObjectDisposedException`、`__cause__`にCLR例外連鎖 |
| StepResultを返さない | ✅ 例外送出、`BattleState`は一切返らない |
| faultしたtransitionを保存しない | ✅ (`CombatEnv.step()`の`self._state = next_state`代入順序により自動的に満たされる、コード変更不要と確認) |
| fault後のStep拒否 | ✅ `FaultedCombatSessionError` |
| fault後のObservation取得拒否 | ✅ 同上(新設`get_observation()`) |
| fault後のLegalActions取得拒否 | ✅ 同上(新設`get_legal_actions()`) |
| fault後のSnapshot取得拒否 | ✅ 同上 |
| 正常Reset後に復帰 | ✅ `start_combat()`再呼出で`_session_faulted`解除、以後の`step()`/`get_observation()`/`get_legal_actions()`/`capture_snapshot()`が正常動作 |
| QuiescentBoundaryViolationとの型的区別 | ✅ 両者間で`issubclass`関係が一切ないことを構造的に確認 |

### 3-B. 正常経路

| 試験 | 結果 |
|---|---|
| 通常のカード実行・Combat終了 | ✅(`test_normal_card_play_target_and_end_turn_never_fault`、fault化なし) |
| Choice待ち・Target待ち | ✅(Scenario 6546-21の完走に含まれる、以下) |
| Scenario `6546-21`：49 decision・victory | ✅ 49 decision、victory、QuiescentBoundaryViolation 0件、action fault 0件 |
| Snapshot／Pet Capture | ✅ `verify_snapshot_phase2b.py`、0 failing checks |
| SOUL×3の既知dangling分類 | ✅ 同上スイート内、`source_live_state_inconsistency`のまま変化なし |
| ネイティブforward／reverse | ✅ 各30回(計1,560+1,560=3,120テスト実行)、QuiescentBoundaryViolation 0件、WRIGGLER以外の失敗0件 |
| WRIGGLER既知quarantine | ✅ `reasons: ['init_exception:TimeoutException']`を再確認 |
| pytest capture各モード | **4節で詳述——重大な追加発見あり** |

## 4. 重大な追加発見: pytest下でのAction faultの自然発生とその意味

前回報告(`rl_pytest_quiescent_nondeterminism_investigation_20260727.md`)で
「pytestの出力capture機構が非決定的な`QuiescentBoundaryViolation`と相関
するが、根本原因は未特定」と報告した事象について、**今回、根本原因を
直接特定した。**

`tests/test_scenario_v2.py`+`tests/test_choice_semantics.py`+新設
`tests/test_action_fault_contract.py`を`pytest -q`(デフォルトcapture)で
複数回実行したところ、**複数回にわたり、合成的な注入なしに自然発生した
`Sts2Emulator.Api.ActionFaultedException`を観測した**:

```text
Sts2Emulator.Api.ActionFaultedException: Action execution faulted -
combatSessionId=... stepIndex=0 actionId=1 action=PlayCardAction '...'
originalExceptionType=System.IO.IOException
originalMessage=ハンドルが無効です。(mojibake表示)
...
```

スタックトレースは`Console.WriteLine`→`Godot.GD.Print`→
`ConsoleLogPrinter.Print`→`Logger.Info`→`Log.Info`→
`PlayCardAction.ExecuteAction()`——**まさにEmulator担当自身の合成注入
試験(3-A節)と全く同じ経路を、pytestの環境が実際に、自然に踏んでいた**
ことになる。

**解釈**: `pytest`のfd方式出力capture(デフォルト)が、テスト間で
OS側のファイルハンドルを操作するタイミングと、バックグラウンド
ThreadPoolスレッドの`Console.WriteLine`呼び出しが競合すると、
本物の`IOException`(「ハンドルが無効です」)が発生しうる。**これは
以前から存在していた同一のraceであり、Phase 3A.3以前は`GameAction.
Execute()`が例外を握りつぶして成功扱いにしていたため、結果的に
状態不整合となり`QuiescentBoundaryViolationException`として(誤解を
招く形で)顕在化していた。Phase 3A.3の修正により、同じraceが今度は
正直に`ActionFaultedException`として報告されるようになった**——これは
Phase 3A.3のリグレッションではなく、以前から未解決だった問題が、より
正確な形で可視化されるようになったという前進である。

**確認できたこと**:
* `pytest -s`/`--capture=no`: 今回も0件(前回投稿分と合わせ計12回、
  全てクリーン)——captureを無効化すれば発生しない。
* ネイティブハーネス(pytestを介さない): 今回も0件
  (1,560+1,560=3,120テスト実行)。
* この経路は`battle_emulator.py`の`step_live_action()`/`apply_action()`
  (Heuristic/legacy経路)を直接使う既存テスト(`test_scenario_v2.py`/
  `test_choice_semantics.py`自身)で発生しており、これらは今回
  `LiveCombatSession`を経由しないため、生のCLR例外がそのまま伝播する
  ——2-2節で述べた「意図的に変更していない」設計通りの挙動である。

**この発見の位置づけ**: 今回のPhase 3A.3統合作業そのものが原因ではなく、
既に前回報告済みの未解決事項の根本原因を明確化したものである。
`combat_state_contract.v0.5.md`§9へ記録した。`pytest`を正式な回帰手段
として使わない、という前回・前々回からの方針を継続する根拠が
より強固になった。

## 5. 既存経路への影響確認

* `LiveCombatSession`の既存メソッド(`_is_still_current`/
  `_resynchronize`のコアロジック)は無変更、fault-clear処理を1行追加した
  のみ。
* Policy/Value/Choice Policy/Heuristic/beam-search関連ファイルは一切
  参照・編集していない。
* `battle_emulator.py`/`combat_env.py`は無変更(0バイト差分、`CombatEnv.
  step()`が既存の代入順序だけでfault時のtrajectory非混入を自動的に
  満たすことを確認したのみで、コード変更は不要だった)。
* `RestoreSnapshot`本体は実装していない。
* trajectory再生成は行っていない。

## 6. ファイル・ハッシュ一覧

| ファイル | SHA256 |
|---|---|
| `Combat/live_combat_session.py` | `6c3d173251ec63bd0e0db5e8aa7d88bb309d7038a7b88a8135bddbfc0037d72f` |
| `Combat/emulator_bridge.py` | `ca05c99ecceba13bb7adf2e4fb673ecca8df825bcefed4cbba2b145736a99789` |
| `Combat/tests/test_action_fault_contract.py` | `5afd81b1f914dfd05d837b693a0bacd97fc71efc7c20a07ee4f05fc62f884ab0` |
| `Common/contracts/combat_state_contract.v0.5.md` | `839315960b73d815e460bb7f31d28c0d610e6568a6ecee3e9e29d73935a903dd` |

Emulator参照値: Importedコードcommit`1e637c8a7daefbdf43d3f9f4e8a49e98db2a3e2c`、
Emulatorコードcommit`5d222efa117712503d551941daa83cfa8c7365d1`、DLL SHA256
`f61a1533e7ba8c746d1257ccf9e68f7ea239394ec2f268f1df4aece514ad1bf0`。

manifest全文: `Common/contracts/rl_phase3a3_source_manifest_20260727.json`。

## 7. 結論

### Action faultのPython側観測結果

正しく観測できる。C#側`ActionFaultedException`が`ActionExecutionError`
として捕捉され、`ActionFaultContext`(Python側が既に保持する権威的情報+
CLR例外オブジェクトの構造的属性の両方を情報源とする)が正しく構築される
ことを、実際のfault(合成注入・自然発生の両方)で確認した。

### fault後sessionの拒否結果

`step`/`get_observation`/`get_legal_actions`/`capture_snapshot`の
4メソッド全てが`FaultedCombatSessionError`で正しく拒否されることを確認。

### Reset後の復帰結果

`start_combat()`の再呼出で`_session_faulted`が解除され、以後の全操作が
正常動作することを確認。

### trajectoryへ混入しないこと

`CombatEnv.step()`のコード変更なしに(既存の代入順序により)自動的に
満たされることを確認した。

### 全回帰結果

ネイティブハーネス(30回×2方向、3,120テスト実行)・Scenario 6546-21・
Snapshot/Pet Capture検証スイート・WRIGGLER既知quarantine、全て問題なし。
`pytest`環境下でのみ、4節で詳述した根本原因判明済みの`ActionFaultedException`
自然発生が見られる(新規リグレッションではなく、既知事象の正確な再分類)。

### Phase 3B開始可否

**不可(現時点では開始しない)。** 指示の明示的な最終行に従い、本ラウンドは
Phase 3A.3(Action fault契約のPython側統合)の実装・試験・報告のみで
完結させる。契約v0.5§9に記録した通り、`FUNERARY_MASK`由来のdangling
reference未解決(Phase 3Aゲート、契約v0.4以来継続)に加え、`pytest`環境
固有の制約(根本原因は判明したが、`pytest`自体の改善や回避策の実装は
未着手)も含め、Phase 3B着手には別途、監督者からの明示的な指示が必要。

`RestoreSnapshot`・Heuristic・Training本体には一切着手していない。ここで
報告のため停止する。Emulator担当・監督者の確認を待つ。
