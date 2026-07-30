# RL担当 Phase 3A.4最終統合報告 — Console I/O分離のPython側確認(2026-07-28)

対象: 「RL担当 Phase 3A.4最終統合指示 — Console I/O分離のPython側確認」。
確認事項を全て独立に再検証した。Phase 3B・`RestoreSnapshot`・Heuristic・
Training本体には着手していない。

## 1. 基準情報確認

実ファイル・実commitから直接確認した。

| 項目 | 指示書記載値 | 実確認値 | 一致 |
|---|---|---|---|
| Emulatorコードcommit | `e9fb60b` | `git log`: `e9fb60b0c70382be78c23af7f7d4a32a407d537f` | ✅ |
| 追加修正commit | `50cf52a` | `git log`: `50cf52a19af84eba6e18229198ed847e878b26ac` | ✅ |
| 報告書commit | `39e0f00` | `git rev-parse HEAD`: `39e0f0085b9a257a6fc90180043b4b2f1d54d00d` | ✅ |
| DLL SHA256 | `9b10549f92202b750f866d4592d76b9d8b515bbab9eac73d51998c7d05a5e629` | `sha256sum`同一値 | ✅ |

Emulator報告書(`console_io_isolation_phase3a4_20260727.md`)を全文読了。
`SafeConsoleTextWriter`/`SafeConsoleOutput`の実装方式(`Console.Out`/
`Console.Error`を`GameInstance.EnsureTestMode()`で1回だけラップ、
`IOException`/`ObjectDisposedException`/`UnauthorizedAccessException`の
3型のみを`_inner`のWrite/WriteLine/Flush呼び出しから捕捉)、
`UnauthorizedAccessException`追加の経緯(Emulator担当自身の§8必須回帰で
実測に基づき発見、推測追加ではない)を確認した。

## 2. Console障害の確認(pytest capture方式別、独立再検証)

指示通りの試行回数で、**Emulatorの報告を鵜呑みにせず、独自ドライバ
(`Combat/evaluation/online_eval/pytest_capture_mode_repro.py`、新規)を
用いて完全に独立した`pytest`サブプロセスをそれぞれ起動**して再検証した
(1プロセス内でループする方式ではなく、指定条件・反復回数ぶんの真に
独立したプロセスを毎回起動——`--capture`フラグの効果を正しく反映させる
ため)。

| 条件 | 試行回数 | QuiescentBoundaryViolation | ActionExecutionError(IOException/ObjectDisposedException/UnauthorizedAccessException起因) | ActionExecutionError(その他原因) | その他予期しない失敗 | 所要時間 |
|---|---|---|---|---|---|---|
| デフォルトcapture | 100回 | **0/100** | **0** | 0 | 0 | 2,291秒 |
| `--capture=fd` | 100回 | **0/100** | **0** | 0 | 0 | 2,299秒 |
| `--capture=sys` | 50回 | **0/50** | **0** | 0 | 0 | 1,252秒 |
| `-s` | 50回 | **0/50** | **0** | 0 | 0 | 1,239秒 |

**合計300回、全条件で`ActionExecutionError(System.IO.*) = 0`・
`QuiescentBoundaryViolation = 0`を確認した。** Emulator担当自身の実測
(100/100/50/50、同じく全件0)と完全に一致する。前回(Phase 3A.3)報告で
RL担当が独自に発見した自然発生`ActionFaultedException`(`System.IO.
IOException`/後に判明した`System.UnauthorizedAccessException`起因)は、
今回のConsole I/O分離導入により完全に消失したことを独立に確認した。

## 3. 本物のAction faultの確認(合成例外注入)

### 3-A. 既存(Phase 3A.3)手法の再確認——全体`Console.Out`置換

`Combat/tests/test_action_fault_contract.py`の既存6試験(`Console.Out`
全体を破棄済み`StreamWriter`へ差し替える手法、`SafeConsoleTextWriter`
自体をバイパスする)を無変更のまま再実行し、**6/6合格**——SafeConsole
導入後も、ラッパーを経由しない本物の未処理例外は引き続き正しく
`ActionExecutionError`としてfaultすることを確認した。

### 3-B. 新規(Phase 3A.4)手法——`SafeConsoleTextWriter._inner`への
reflectionタンパー

Emulator担当自身のLayer B手法(§7-B、`_inner`フィールドのみをreflection
経由で差し替え、ラッパー自体はインストールされたまま)を独立に再現する
3試験を新規追加、**全件合格**:

| 試験 | 手法 | 結果 |
|---|---|---|
| `test_console_io_isolation_does_not_fault_on_disposed_inner_writer` | `_inner`を破棄済み`StreamWriter`へ差し替え | fault**しない**、正常な`StepResult`が返る |
| `test_console_io_isolation_does_not_fault_on_broken_pipe_inner_writer` | `_inner`を破損`AnonymousPipeServerStream`(読み取り側破棄後の書き込み)へ差し替え、本物のネイティブ`IOException`を発生させる(Pythonサブクラス経由の`TargetInvocationException`ラップという既知の試験手法上の罠——Emulator報告書§7-Bで指摘済み——を回避) | fault**しない** |
| `test_console_io_isolation_still_faults_on_unrelated_inner_writer_exception`(control) | `_inner`を固定容量`MemoryStream`(オーバーフロー時`NotSupportedException`)へ差し替え | **fault する**(`ActionExecutionError`、`original_exception_type`に`NotSupportedException`を確認)——安全網が無関係な例外まで隠していないことを確認 |

**`UnauthorizedAccessException`単体の合成再現は行っていない**(正直な
申告): Pythonから移植性のある形でこの型をネイティブに発生させる手段が
見当たらず、Pythonサブクラス`TextWriter`経由では上記と同じ
`TargetInvocationException`ラップの罠に陥る。この型についてはEmulator
担当自身も実測(§8必須回帰)で発見した経緯があり、本ラウンドでは2節の
pytest capture方式別再検証(300回、全件0)を実質的な確認手段とした
——合成注入ではなく、実際の発生条件を再現する形での確認である。

### 3-C. fault契約の健全性(session拒否・Reset後復帰)

既存9試験(3-A・3-Bの計9件)全てにおいて、fault後`_session_faulted`が
`True`になること、`step`/`get_observation`/`get_legal_actions`/
`capture_snapshot`が拒否されること、`start_combat()`再呼出で復帰する
ことを引き続き確認済み(Phase 3A.3から無変更のアサーション)。

## 4. 正常回帰

| 試験 | 結果 |
|---|---|
| Scenario `6546-21`：49 decision・victory | ✅ QuiescentBoundaryViolation 0件 |
| Snapshot／Pet Capture | ✅ `verify_snapshot_phase2b.py`、0 failing checks |
| Choice／Target | ✅ ネイティブハーネス内`test_choice_semantics.py`経由で網羅(以下) |
| ネイティブforward／reverse | ✅ 各30回(計1,560+1,560=3,120テスト実行)、QuiescentBoundaryViolation 0件、WRIGGLER以外の失敗0件 |
| SOUL×3の既知分類 | ✅ `verify_snapshot_phase2b.py`内、`source_live_state_inconsistency`のまま変化なし |
| WRIGGLER quarantine | ✅ `reasons: ['init_exception:TimeoutException']`を再確認、分離導入前後で変化なし |
| trajectoryへfault transitionを保存しない | ✅ Phase 3A.3から無変更(`CombatEnv.step()`の代入順序により自動的に満たされる、コード変更不要) |

## 5. Python側コード変更の有無

**`Combat/live_combat_session.py`・`Combat/emulator_bridge.py`は今回
無変更。** Console I/O分離はEmulator側のみで完結し、Python側からは透過的
——`Console.Out`/`Console.Error`自体の内部実装が変わっただけで、Python
から見えるCLR API(`Step`/`GetObservation`/`GetLegalActions`/
`CaptureSnapshot`/例外型)は一切変わっていないため、新規のPython型・
ラッパーは不要と判断した(推測ではなく、2-3節の独立試験で実際に確認)。

## 6. 固定作業

* `combat_state_contract.v0.5.md`: 新設§13(Console I/O分離)を追加。
  Console出力失敗がゲームActionの成否に影響しないことの保証、無害化対象
  3例外型(`IOException`/`ObjectDisposedException`/
  `UnauthorizedAccessException`)、`UnauthorizedAccessException`追加の
  実測的経緯、RL独自の300回再検証結果を記録。§9の該当項目を「根本原因
  判明」から「解決済み」へ更新。最終契約SHA256:
  `e33b369e12543e04fe763c07196e2189460099cfcb3d22b5a35137e2a2b86b07`。
* `rl_phase3a3_source_manifest_20260727.json`: 既存ファイルを直接更新
  (新規ファイル作成ではなく、指示通り同一ファイルへ`phase3a4Addendum`
  セクションを追加)。契約SHA256を最終値へ更新、Phase 3A.4対象ファイル
  ハッシュ・テストコマンド・テスト結果を記録。

## 7. ファイル・ハッシュ一覧(今回変更分)

| ファイル | SHA256 |
|---|---|
| `Combat/tests/test_action_fault_contract.py` | `65639215b2956508ed1a89d7097b204b58b7c4b9ca74bce1438729cfb34055e1` |
| `Combat/evaluation/online_eval/pytest_capture_mode_repro.py` | `4319aadb00a841416e5f9e340335487f3ffb7786ba4194d52ef3f2e3ae76f6a8` |
| `Common/contracts/combat_state_contract.v0.5.md` | `e33b369e12543e04fe763c07196e2189460099cfcb3d22b5a35137e2a2b86b07` |

Emulator参照値: コードcommit`e9fb60b0c70382be78c23af7f7d4a32a407d537f`+
`50cf52a19af84eba6e18229198ed847e878b26ac`、DLL SHA256
`9b10549f92202b750f866d4592d76b9d8b515bbab9eac73d51998c7d05a5e629`。

manifest全文: `Common/contracts/rl_phase3a3_source_manifest_20260727.json`
(`phase3a4Addendum`セクション)。

## 8. 結論

### DLL SHA256一致

一致確認済み(1節)。

### pytest capture各モードの結果

デフォルト100回・`--capture=fd`100回・`--capture=sys`50回・`-s`50回、
計300回の完全に独立したサブプロセス実行全てで、
`QuiescentBoundaryViolation`・`ActionExecutionError`(IOException系)
ともに0件を独立に確認(2節)。

### 自然発生Console faultの消失

確認した。Phase 3A.3で発見した自然発生`ActionFaultedException`
(`System.IO.IOException`/`System.UnauthorizedAccessException`起因)は、
今回の300回再検証で1件も再現しなかった。

### 合成Action faultの伝播維持

確認した。既存の全体`Console.Out`置換手法(6/6)に加え、新規の
`SafeConsoleTextWriter._inner`reflectionタンパー手法(3/3、無害化対象
2型+無関係例外のcontrolケース)で、SafeConsole導入後も本物のAction fault
契約(fault送出・元例外保持・StepResult非返却・session faulted化・
fault後API拒否・Reset後復帰)が健全に維持されていることを確認した。

### Phase 3B開始可否

**不可(現時点では開始しない)。** 指示の明示的な最終行に従い、本ラウンド
はConsole I/O分離のPython側確認のみで完結させる。契約v0.5§9/§14に
記録の通り、`FUNERARY_MASK`由来dangling reference未解決(Phase 3Aゲート、
継続中)が依然として残っており、Phase 3B着手には別途、監督者からの
明示的な指示が必要。

`RestoreSnapshot`・Heuristic・Training本体には一切着手していない。ここで
報告のため停止する。Emulator担当・監督者の確認を待つ。
