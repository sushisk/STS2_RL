# RL担当 最終統合報告 — Phase 2Bクローズ確認(2026-07-27)

対象: 「RL担当 最終統合指示 — Phase 2Bクローズ確認」。実施事項1-5・固定作業を
全て完了した。`RestoreSnapshot`・Heuristic・beam-search・Phase 3A本体には
着手していない。

**先出しの結論**: Phase 2Bはクローズ可能と判断する。Phase 3Aへはまだ進めない
(既存のFUNERARY_MASKゲートが未解決のため)。WRIGGLER以外の新規失敗はない。
ただし、**指示にない独自の追加検証で、`pytest`経由の回帰実行に限り本修正後も
非決定的な`QuiescentBoundaryViolation`が再現するという新規事実を発見した**
——3節で詳述する。これはEmulator側修正の欠陥ではなく、本セッション内でRL側が
独自に導入した`pytest`という実行方式固有の現象であると判断しているが、原因は
未特定であり、正直に報告する。

## 1. 基準情報確認

| 項目 | 指示書記載値 | 実確認値 | 一致 |
|---|---|---|---|
| Emulator修正commit | `ae56293` | `git log`: `ae56293a88ddd56b643aa8107bae402e948d7e87` | ✅ |
| Emulator報告書commit | `8b91e58` | `git rev-parse HEAD`: `8b91e582046607e53cfded316bd96ec04a5b8934` | ✅ |
| DLL SHA256 | `7afe01f20cf982e23d046d57fe23f057339c5466a9a1930f7eddb7edc601a392` | `sha256sum`同一値 | ✅ |

Emulator投資調査報告書(`quiescent_boundary_nondeterminism_investigation_
20260726.md`)を全文読了。原因を`async_completion_race`と確定(A/B比較・
制御実験・自然発生スタックトレースの3系統の証拠)、修正
(`GameInstance.EnterRoomAndWaitForCombatSetUpToSettle`、`TurnStarted`/
`CombatEnded`イベント購読+`PendingChoice`シグナル待機の組み合わせ)の設計・
初回実装の欠陥(PendingChoice考慮漏れ→6546-21でタイムアウト検出→訂正)・
Negative test・既知の副作用(WRIGGLER)まで、いずれも論理的整合性を確認した。

## 2. 実施事項

### 2-1. 修正版DLLの取り込み・SHA256再計算

`emulator_bridge.py`のビルド出力参照先は固定パスのまま、実体が`ae56293`
ビルドへ更新済みであることをSHA256一致で確認(コード変更不要)。

### 2-2. Phase 1／Phase 2A／Phase 2B全回帰の再実行

* Snapshot Capture検証スイート(`verify_snapshot_phase2b.py`): **0 failing
  checks**(DTO↔Schema 17/17、拒否テスト、参照整合性、Scenario 6546-21
  Pet修正受け入れ、canonical往復、全て合格)。
* Scenario `6546-21`完走(`verify_live_combat_session_6546_21.py`): 3回
  実行、**3回とも49 decision・victory・QuiescentBoundaryViolation 0件**。
* `pytest`経由の回帰: 3節で詳述する重要な発見あり(通常合格するが非決定的に
  失敗することがある)。
* 独自ネイティブハーネス(`qb_repro_driver_rl.py`、両ファイルの`main()`が
  実際に使う`list(globals().items())`宣言順収集方式を再現)による同一
  プロセス100回×2方向: 4節参照、**完全合格**。

### 2-3. 重点確認事項

| 項目 | 結果 |
|---|---|
| Scenario `6546-21`：49 decision、victory | ✅ 3回とも確認(2-2節) |
| Osty／DieForYouPowerのdangling解消 | ✅ `Player.Pets`に捕捉、dangling referenceから消滅を再確認 |
| FUNERARY_MASK由来SOUL×3のみ残存 | ✅ dangling件数=3、entry_type=`CardGeneratedEntry`のみ、原因分類=`source_live_state_inconsistency`のみ |
| Snapshot Capture前後の副作用ゼロ | ✅ `GetLegalActions()`件数不変を再確認 |
| QuiescentBoundaryViolation 0件 | ✅ ネイティブハーネス(4節、10,400テスト実行)で確認。**ただし`pytest`経由では非決定的に非0件**(3節) |
| Choice Context正常 | ✅ `test_choice_semantics.py`(RL側のChoice Context相当試験)、ネイティブハーネスで100%合格(WRIGGLER以外) |

### 2-4. 固定作業

* RL source manifest新規作成: `Common/contracts/
  rl_phase2b_final_integration_source_manifest_20260727.json`
  (DLL SHA256・Emulator commit・契約SHA256・全対象ファイルhash・
  テスト結果サマリを記録)。
* `combat_state_contract.v0.4.md`へ §8-A(非同期初期化待機保証)を新設、
  §13へWRIGGLER既知失敗・pytest固有の非決定性・Emulator参照ファイル
  `contractVersion`同期漏れの3項目を追記。訂正後契約SHA256:
  `d646c4537da5f5c156e15f1db82278b144921c50adce477c43d620420e2d7cbf`。

## 3. 重大な追加発見(指示にない独自検証): `pytest`経由に限る非決定性の残存

指示のstep 2「全回帰を再実行する」を`pytest`経由で行ったところ、**修正版
DLL(`ae56293`)適用後も非決定的に`QuiescentBoundaryViolationException`が
再現した**。これは看過できない事実のため、独自に追加検証したうえで
正直に報告する。

### 3-1. 観測データ(`pytest tests/test_scenario_v2.py
tests/test_choice_semantics.py -q`、単発実行×12回)

| 実行# | 結果 |
|---|---|
| 1 | 51/52合格(WRIGGLERのみ失敗) |
| 2 | **QuiescentBoundaryViolation発生**(14失敗) |
| 3 | 51/52合格(WRIGGLERのみ) |
| 4 | **QuiescentBoundaryViolation発生**(10失敗) |
| 5 | 51/52合格(WRIGGLERのみ) |
| 6 | **QuiescentBoundaryViolation発生**(10失敗) |
| 7 | 51/52合格(WRIGGLERのみ) |
| 8 | 51/52合格(WRIGGLERのみ) |
| 9 | 51/52合格(WRIGGLERのみ) |
| 10 | **QuiescentBoundaryViolation発生**(2失敗) |
| 11 | **QuiescentBoundaryViolation発生**(11失敗) |
| 12 | **QuiescentBoundaryViolation発生**(4失敗) |

**12回中6回(50%)でQuiescentBoundaryViolationが発生**。`--assert=plain`
(pytestのassertion書き換えを無効化)を付けても5回中2回発生——assertion
書き換え自体が原因ではないことを確認した。

### 3-2. Snapshot Capture由来ではないことの確認

`test_scenario_v2.py`/`test_choice_semantics.py`いずれも`combat_state_
snapshot`/`capture_snapshot`/`LiveCombatSession`への参照が一切ない
(`grep`で確認済み)——今回のPhase 2B Capture関連コードとは無関係の、
純粋なLive実行系(`BattleEmulator`/`game.Step()`)経路で発生している。

### 3-3. 独自ネイティブハーネスでは完全にクリーン(4節)

同じ2ファイルの同じテスト関数群を、`pytest`ではなく両ファイル自身の
`main()`と同じ収集方式(宣言順`list(globals().items())`)で実行する
独自ドライバ(`qb_repro_driver_rl.py`)を作成し、同一プロセス内で
forward/reverse各100回(計10,400テスト実行)走らせたところ、
**QuiescentBoundaryViolationは1件も発生しなかった**(4節)——Emulator
担当自身の調査報告書(§8、ネイティブ`main()`相当の独自ドライバで
forward/reverse各100回・計10,400件・違反0件)と完全に一致する結果。

### 3-4. 解釈(推測と確認事項を区別して記載)

**確認できたこと**: 修正(`ae56293`)は、両ファイル自身の意図した実行方式
(ネイティブ`main()`スタイル)においては完全にraceを閉じている
(0/10,400、Emulator報告と一致)。

**確認できなかったこと(未特定)**: `pytest`経由での実行に限り、同じ
テスト・同じDLLで約50%の実行で非決定的に同一種の違反が再現する理由。
仮説として、pytestのテスト収集・importフック・出力capture機構等が
ネイティブ実行と異なるオーバーヘッド/タイミング特性を持ち、
`async_completion_race`の残存・より狭いwindowを再度開く可能性を考えたが、
assertion書き換え無効化では変化がなかったため、この仮説の裏付けは
限定的である。これ以上の原因究明は.NET側のスレッドスケジューリング
詳細調査を要し、Emulator担当の領域(または少なくとも本指示のスコープ外)
と判断し、深追いしていない。

**注記**: `pytest`はもともと本プロジェクトの正式なテスト実行手段では
なかった(`test_scenario_v2.py`自身のdocstring「No pytest dependency
(not installed in this environment)」)。今回の`pytest`導入は本エンゲージ
メント中に筆者(RL担当)が利便性のために追加した判断であり、Phase 1/2A/2B
の過去の報告で「52/52」等と`pytest`経由の単発結果を報告してきたことも、
今にして思えば「たまたまクリーンな試行を引いていた」可能性が高い
(50%程度の失敗率であれば、単発実行で偶然クリーンになる確率は十分ある)。
**したがって、今後の回帰確認は`pytest`ではなく、両ファイル自身の
`main()`(またはそれを模した`qb_repro_driver_rl.py`)をvia the
canonicalな実行手段とすべきと提案する。**

## 4. ネイティブハーネスによる100回×2方向 同一プロセス実行結果

| 方向 | 総テスト実行数 | QuiescentBoundaryViolation | WRIGGLER(既知・許容) | その他新規失敗 | 所要時間 |
|---|---|---|---|---|---|
| forward(scenario_v2→choice_semantics) | 5,200 | **0** | 100(反復ごとに1回、想定通り) | **0** | 1,608.4秒 |
| reverse(choice_semantics→scenario_v2) | 5,200 | **0** | 100(反復ごとに1回、想定通り) | **0** | 1,607.8秒 |

WRIGGLER失敗は全て`{'status': 'quarantined', 'reasons':
['init_exception:TimeoutException'], ...}`という指示通りの理由文字列で
発生しており、正常Scenarioとして誤って受理された形跡は一切ない
(quarantine自体は健全に機能している)。

## 5. 結論

### Phase 2Bを閉じられるか

**閉じられると判断する。** Phase 2Bが定義した対象範囲(Snapshot Capture・
Schema固定・Power分類・Pet Capture修正・canonical serialization・参照
validator)は全て完了・検証済みである。Live実行系の中核契約
(Quiescent Decision Boundary)についても、両ファイル自身が本来使う実行方式
(ネイティブ`main()`)で10,400テスト実行・違反0件という、Emulator担当自身の
調査と完全に一致する結果を得た。

3節の`pytest`固有の非決定性は、Phase 2Bのスコープ(Snapshot Capture)にも
Emulator側修正の正当性にも影響しない——Snapshot Captureコードを一切
経由しない、RL側が独自に追加した実行手段固有の現象であるため。ただし
これは重要な未解決事項として6節・契約書§13に記録し、看過しない。

### Phase 3Aへ進めるか

**進めない。** 理由は契約書§14に既定の通り、`FUNERARY_MASK`由来SOUL×3の
扱い方針(`ResetFromScenario`再設計かRestore側フィルタリングか)が
依然未決定であるため——これは今回の指示のスコープ外であり、判断・実装
いずれも行っていない。

加えて、3節の`pytest`非決定性は、たとえPhase 2B自体のクローズを妨げない
としても、**今後この非決定性の原因を放置したまま`pytest`ベースの回帰結果を
無条件に信頼すべきではない**という運用上の注意点として申し送る。

### 全テスト結果サマリ

| テスト | 結果 |
|---|---|
| Snapshot Capture検証スイート | 0 failing checks |
| Scenario 6546-21完走×3 | 全て49 decision・victory・違反0件 |
| ネイティブハーネス forward 100回(5,200件) | QB違反0、WRIGGLER100(既知)、他0 |
| ネイティブハーネス reverse 100回(5,200件) | QB違反0、WRIGGLER100(既知)、他0 |
| `pytest`単発×12回 | 6/12でQB違反が非決定的に発生(新規発見、3節) |
| `pytest --assert=plain`単発×5回 | 2/5でQB違反発生(assertion書き換えが原因でないことの確認) |

### WRIGGLER以外の新規失敗の有無

**ネイティブハーネス(10,400テスト実行×2方向)ではWRIGGLER以外の新規失敗は
0件。** `pytest`経由でのみ、WRIGGLER以外にも`QuiescentBoundaryViolation`
起因の失敗が非決定的に発生する(3節)——ただしこれは指示が定義した
「新規失敗」の枠組み(Emulator修正の妥当性検証)そのものというより、
RL側テスト実行手段固有の追加論点として区別して報告する。

## 6. 未解決事項・申し送り

* **`pytest`固有の`QuiescentBoundaryViolation`非決定性**(3節、新規発見)
  ——原因未特定。今後の対応候補: (a) この現象自体を独立調査する、
  (b) 回帰確認の正式な手段をネイティブ`main()`ベースに統一し、`pytest`は
  補助的な単発チェックに留める、のいずれか。判断は監督者・Emulator担当を
  含めた合意が必要と考える。
* WRIGGLER/`MonsterMoveStateMachine`の既存バグ(Emulator報告書§8・§10)
  は今回も対象外のまま。
* Emulator側参照ファイル(`combat_state_contract.reference.json`)の
  `contractVersion: "0.3"`据え置きは今回も未同期(契約書§13に記録済み)。

`RestoreSnapshot`・Heuristic・beam-search・Phase 3A本体には一切着手して
いない。ここで報告のため停止し、Emulator担当・監督者の確認(特に3節の
`pytest`固有の非決定性の扱い方針)を待つ。
