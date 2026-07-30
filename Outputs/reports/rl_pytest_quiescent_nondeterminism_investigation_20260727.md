# RL担当 pytest実行時非決定性 調査報告(2026-07-27)

対象: 「RL担当 pytest実行時非決定性 調査指示」。Phase 3Aの実装とは独立して
調査した。Emulatorの本番コード(`C:\STS2_Emulator`)は一切変更していない
(参照・実行のみ)。診断用に一時的に追加した`Combat/tests/conftest.py`は
調査完了後に削除し、通常実行(1 failed=WRIGGLERのみ)へ復帰することを
確認済み。

## 結論(先出し)

* **原因分類**: `production_runtime_race`。ただし「pytestなら必ず起きる」
  「ネイティブなら絶対起きない」という単純な二値ではなく、**production
  コード側に実在する低確率のタイミングレースが、pytestの出力capture機構
  (デフォルトのfd-based capture)によって発現確率が引き上げられている**、
  という構造だと判断する。
* **最小再現条件**: `pytest`のデフォルト出力capture(fd方式)を有効にした
  状態で、52テストの累積実行量(単発の小規模subsetだけでは再現しない)。
  `-s`/`--capture=no`でcaptureを無効化すると、テスト対象・実行順序・
  DLL・累積実行量を完全に揃えても**再現しなかった**(0/8)。
* **production runtimeへの影響**: あり得る。根本原因はテストツール側の
  アーティファクトではなく、`ae56293`修正後もなお存在する production
  コード側の残存レース(詳細は4節)である可能性が高い——ただし今回は
  Emulator本番コードの変更・深掘りデバッグは行っていないため、
  確定的な内部メカニズムの証明はできていない。
* **正式回帰手段としてpytestを使用可能か**: **不可**(現状のデフォルト
  設定では)。`-s`/`--capture=no`を常に併用すれば実用上信頼できる可能性が
  高いが、それでもネイティブハーネスを正本とすべきという前回の結論を
  維持する。
* **テスト側だけで修正可能か**: 部分的に可能(`-s`/`--capture=no`の強制、
  または`pytest`をやめてネイティブハーネスへ統一)——ただしこれは
  「観測されなくする」だけであり、productionコード側のレース自体を
  修正するものではない。
* **Emulator側修正が必要か**: レースの完全な解消には必要になる可能性が
  高いが、今回はその判断・実装のいずれも行っていない(スコープ外)。

## 1. 固定した基準情報

| 項目 | 値 |
|---|---|
| Pythonバージョン | 3.12.7 |
| pythonnetバージョン | 3.1.0 |
| pytestバージョン | 9.1.1 |
| jsonschemaバージョン(参考、Phase 2B関連) | 4.26.0 |
| 登録済みpytest plugin | 全て`_pytest.*`組み込みplugin(`mark`/`main`/`runner`/`fixtures`/`python`/`terminal`/`debugging`/`unittest`/`capture`/`skipping`/`legacypath`/`tmpdir`/`monkeypatch`/`recwarn`/`pastebin`/`assertion`/`junitxml`/`doctest`/`cacheprovider`/`setuponly`/`setupplan`/`stepwise`/`unraisableexception`/`threadexception`/`warnings`/`logging`/`reports`/`faulthandler`/`subtests`/`terminalprogress`)。**サードパーティplugin無し** |
| 実際のcollection順 | ネイティブ`main()`(`list(globals().items())`宣言順)と**完全一致**(52件、順序含め1件も相違なし——比較データは本報告書の元となった調査ログに記録済み) |
| Emulator commit | `ae56293a88ddd56b643aa8107bae402e948d7e87` (`git log`で確認) |
| DLL SHA256 | `7afe01f20cf982e23d046d57fe23f057339c5466a9a1930f7eddb7edc601a392`(`sha256sum`で確認、指示書記載値と一致) |
| CLR Assembly load回数 | **常に1**(診断conftest経由、`AppDomain.CurrentDomain.GetAssemblies()`で`Sts2Emulator`という名前のAssemblyをカウント——52テスト×2(開始/終了)=104イベント全てで1、複数回loadされている形跡なし) |
| テストモジュールのimport回数 | `battle_emulator`/`emulator_bridge`いずれも**単一のmodule id**のまま(`sys.modules`経由、104イベント通して不変)——別名での二重importなし |
| 各テスト開始・終了時のactive combat session | `GameInstance.GetObservation()`の`CombatSessionId`/`StepIndex`を都度記録。テストごとに`CombatSessionId`が変化すること(=各テストが自分のBattleEmulator/Reset()経由で新しいsessionを張ること)を確認、異常な使い回しは検出せず |
| Python側global singleton | `emulator_bridge._shared_instance`のid()は、最初の未初期化時(`id(None)`相当)を除き**一貫して単一の値**——正しく単一instanceとして再利用されている(設計通り、異常ではない) |

## 2. 比較条件の実行結果

| # | 条件 | 試行回数 | QuiescentBoundaryViolation発生率 |
|---|---|---|---|
| 1 | ネイティブ`main()`(両ファイル、100回×forward/reverse、前回報告時点の結果を踏襲) | 100+100(10,400テスト実行) | **0/10,400** |
| 2 | 独自ネイティブハーネス(`qb_repro_driver_rl.py`) | 同上(#1と同一実装) | 同上 |
| 3 | `pytest -q`(デフォルト) | 12回(単発) + 追加8回 = 20回 | **12/20(60%)** |
| 4 | `pytest -s` | 5回 | **0/5** |
| 5 | `pytest --capture=no` | 3回 | **0/3** |
| 6 | `pytest -p no:cacheprovider`(capture既定のまま) | 5回 | **3/5** |
| 7 | 主要plugin無効化構成(`-p no:cacheprovider -p no:legacypath -p no:doctest -p no:junitxml -p no:stepwise -p no:setupplan -p no:setuponly -p no:subtests -p no:pastebin`、captureは既定のまま) | 5回 | **4/5** |
| 8 | 失敗したテストだけを同じ順序で直接`pytest`実行(前段の約30件を実行せず) | 3回 | **0/3** |

**#4・#5(capture無効化)のみが0%——それ以外の条件(#3・#6・#7、いずれも
captureはデフォルトのまま)は全て非0%。** `--assert=plain`(前回セッション
で確認済み、assertion書き換え無効化)も非0%のままだった。以上より、
**出力capture機構(fd方式)の有無が唯一の一貫した分岐変数**であると判断した。

**#8が特に重要**: 失敗した8テストだけを、同じ収集順序・同じ`pytest`
(captureデフォルト有効)で単独実行すると、**3回とも1件も再現しなかった**。
これは「これらのテスト自体に固有の欠陥がある」のではなく、**前段の約30件
分の累積実行(=多数回のReset()/Step()サイクル)が無いと、pytest環境下でも
この現象は実用上観測されない**ことを示す——低確率事象が試行回数の蓄積で
可視化される、という典型的なレースコンディションの挙動と整合する。

## 3. 仮説ごとの判定

| 仮説 | 判定 | 根拠 |
|---|---|---|
| pytestのcollection順がネイティブ実行と異なる | **否定** | 1節、52件完全一致を確認 |
| 同じモジュールが異なる名前で複数回importされている | **否定** | 1節、`battle_emulator`/`emulator_bridge`とも単一module id |
| fixture／hookがテスト間cleanup前に次テストを開始している | **否定(可能性は排除できないが、直接証拠なし)** | 両テストファイルはfixtureを使わない素朴な関数(`pytest`はテスト関数を直接収集・実行するのみ)。`pytest_runtest_logstart`/`logfinish`のタイミングで観測した限り、テスト間の境界そのものは正常(CombatSessionIdがテストごとに更新される、1節)。ただし「バックグラウンドTaskの完了を跨いだcleanup」自体はPython側から観測できないため、完全な否定ではない |
| pytestの出力captureがスレッドスケジューリングへ影響する | **支持(本調査の主結論)** | 2節、`-s`/`--capture=no`のみ0%、他は全て非0% |
| CLRまたはDLLが複数コンテキストへloadされている | **否定** | 1節、Assembly count常に1 |
| 例外を起こしたbackground Taskが次テストまで残っている | **部分的に否定** | 2節#8の通り、WRIGGLER(位置27)より前の単独テストでもQB違反が発生した実例を確認済み(前回投稿ログ)——WRIGGLER特有の残存Taskが原因の全てではない。ただし「境界侵害を起こしたテスト自身の裏でまだ完了していないTask」が次の1〜数テストに影響する、という一般化された形では否定しきれない(4節) |
| Python側global singleton参照が再利用されている | **該当するが異常ではない** | 1節の通り、単一instanceとして正しく再利用される設計そのままであり、これ自体はバグの証拠ではない |

## 4. 例外発生時の記録(観測できた範囲)

指示にある詳細項目(`CurrentlyRunningAction`／Pending Choice／Target／
ready action／queue内容／CombatManagerとRunManagerのinstance識別／
`TurnStarted`／`CombatEnded`／Choice signalの発火履歴)のうち、**Python側
から直接観測できたのは例外メッセージ自体に含まれる`CurrentlyRunningAction`
の文字列表現のみ**である。`TurnStarted`/`CombatEnded`等のC# event、
`CombatManager.Instance`/`RunManager.Instance`のinstance識別子、
ActionQueueSetの中身は、現在`emulator_bridge.py`が公開している型
(`GameInstance`/`QuiescentBoundaryViolationException`のみ)経由では
取得できない——これらを取得するにはEmulator側に新しい診断用の公開APIを
追加する必要があり、「Emulatorの本番コードは変更しない」という今回の
制約の範囲内では実施できなかった。**これは限界として正直に記録する。**

代わりに、例外メッセージ自体(全件で共通の形式)を記録した:

```text
[Step] Quiescent Decision Boundary violated: GameAction 'PlayCardAction
card: CARD.<X> (<catalog index>) index: 10 targetid: <...>' is still
executing with no published PendingChoice/PendingTargetSelection to
explain it.
```

「index: 10」は前回報告時点の推測通り、アクションIDや手札位置ではなく、
`PlayCardAction`自身の`ToString()`内部表現の一部(カードカタログ内
インデックス)であることをEmulator側調査報告書(§7)で確認済み——本調査を
通じてこの理解を覆す新情報はなかった。

## 5. Emulator側投資報告書との整合性

`quiescent_boundary_nondeterminism_investigation_20260726.md`の
ネイティブハーネスによる結果(forward/reverse各60回・計約6,000件超、
違反0件)と、本調査のネイティブハーネス確認(forward/reverse各100回・
計10,400件、違反0件)は**完全に整合する**——Emulator担当の結論
(「修正はネイティブ実行方式において確実にraceを閉じている」)を追試・
再確認できた。

本調査で新たに追加した情報は、**「pytestという特定の実行方式においてのみ、
この修正後もなお非決定的に再現する」という事実、およびそれが出力capture
機構と強く相関するという特定**である。Emulator側投資報告書はpytestを
検証手段として使っていない(両ファイル自身のdocstring通り、pytestは
このプロジェクトの正式なテスト手段として導入されたことがない)ため、
この発見はEmulator側の結論と矛盾するものではなく、**pytestという
RL担当が独自に持ち込んだ検証手段固有の追加知見**として位置づける。

## 6. 総合判断

### 原因分類

**`production_runtime_race`。** 収集順序・モジュール二重import・CLR
複数load・singleton再利用のいずれにも異常がないことを確認した(3節)。
唯一一貫して再現・非再現を分けたのは出力capture機構の有無であり、
これはPythonのテストハーネス側の「順序」や「状態」の問題ではなく、
**capture機構がスレッドスケジューリングに何らかの形で影響し、
production側に実在する(おそらく`ae56293`修正でも完全には閉じきれて
いない)低確率のタイミングウィンドウの発現確率を引き上げている**、
という解釈が最も証拠と整合する。「`test_harness_state_or_order_issue`」
という分類は、少なくとも本調査で明示的にテストした各仮説(順序・
二重import・複数load・fixtureタイミング)のいずれによっても支持されず、
棄却する。

### production runtimeへの影響

**あり得る。** capture機構によって発現確率が引き上げられているとしても、
根本にあるのはproduction側の実際のタイミング特性であり、実運用環境
(pytestを介さない、RLの本番学習・推論パイプライン)でも、スケジューリング
条件次第では同種の違反が理論上発生し得ることを否定できない。ただし
ネイティブハーネスでの10,400件・違反0件という実績は、**実際の発現確率が
非常に低い**ことも同時に示しており、「危険だから直ちに使用停止すべき」
というレベルの深刻度ではないと判断する。

### 正式回帰手段としてpytestを使用可能か

**現状のデフォルト設定では不可。** `-s`/`--capture=no`を常時併用すれば
実用上信頼できる可能性が高いが(0/8)、試行数が少なく確証はまだ弱い。
前回報告の提言(ネイティブハーネスを正本とする)を維持する。

### テスト側だけで修正可能か

**「隠す」ことは可能、「直す」ことはできない。** `-s`/`--capture=no`の
強制、またはpytestからネイティブハーネスへの全面移行により、この現象を
観測されなくすることは可能。しかしこれはproduction側のレースそのものを
解消するものではなく、単に「このテスト実行環境ではこの経路を通らない」
という回避策に留まる。

### Emulator側修正が必要か

**レースを完全に閉じるためには必要になる可能性が高いと考えるが、今回は
判断・実装のいずれも行っていない。** 現時点の情報(4節で明記した通り、
`TurnStarted`/`CombatEnded`/Choice signal発火履歴やActionQueueSet内部を
Python側から直接観測できていない)だけでは、修正の要否を最終確定する
根拠として不十分——Emulator担当による、本番コード内部(特に`ae56293`の
`_pendingChoice != null`早期returnパスが、選択肢公開後もなお継続する
背景Taskの残処理を待たない設計になっている点)への追加調査を推奨するに
留める。

## 7. 遵守事項

* `C:\STS2_Emulator`配下のコードは一切変更していない(読み取り・
  `git log`/`sha256sum`確認のみ)。
* 診断用に追加した`Combat/tests/conftest.py`は調査完了後に削除し、
  削除後の動作(`1 failed`=WRIGGLERのみ、通常通り)を再確認した。
* Phase 3A・`RestoreSnapshot`・Heuristic・beam-search関連ファイルには
  一切触れていない。
* 本調査で使用した一時ログファイル(`qb_repro_forward_100.log`/
  `qb_repro_reverse_100.log`/`pytest_diagnostic_trace.jsonl`)は削除済み。
  `qb_repro_driver_rl.py`(ネイティブハーネス再現ドライバ)は今後も再利用
  可能な成果物として保持している。

原因確定(Emulator側追加調査による最終確認)まで、`pytest`結果を正式な
Phase受け入れ判定に使用しない。ネイティブハーネスを正本とする、という
前回報告の方針を継続する。ここで報告のため停止する。
