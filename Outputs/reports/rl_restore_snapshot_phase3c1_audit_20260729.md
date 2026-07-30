# RL担当 Phase 3C.1独立監査報告 — Python Restore統合(2026-07-29)

対象: 「RL担当 Git導入およびPhase 3C.1 Codex運用指示書」Part C。Codex CLI
(GPT-5.5、reasoning=high)によるPhase 3C.1 Python Restore統合セッションの
独立監査。

## 1. 判定

```text
ACCEPT_WITH_DOCUMENTATION_FIX
```

理由は3節で詳述するが、要約すると: Codexが実装したPythonコード自体
(`live_combat_session.py`/`emulator_bridge.py`)には、独立監査を通じて
一度も欠陥が見つからなかった。修正を要したのは(a) Codex自身が一度も
実行できなかった新規テストファイルの2件の軽微な不備(bootstrap呼び出し
漏れ・比較アサーションの過剰厳格化)、(b) 本ラウンドの独立試験で新たに
発見した、Emulator側C#コードに起因する2件の事実(Codexの実装対象外)、
(c) 本ラウンド自身のGit導入(Part A)自体に起因する無関係な`.gitignore`
バグ、のみ。`ACCEPT`ではなく`ACCEPT_WITH_DOCUMENTATION_FIX`とした理由は、
Codexが一度もPythonを実行できずテスト結果を一切主張できなかった事実
(誠実な申告そのものは高く評価する)と、(b)の2件の発見を記録に残すため。

## 2. Codex実行情報

* 起動コマンド:
  ```bash
  cat codex_prompt.md | codex exec \
    -m gpt-5.5 \
    -c model_reasoning_effort=high \
    -s workspace-write \
    -C "C:\STS2_RL_worktrees\phase3c1-python-restore" \
    --skip-git-repo-check \
    - --json \
    > codex_run_2.jsonl 2> codex_run_2.stderr.log
  ```
* Codex CLIバージョン: `codex-cli 0.145.0`(Codex自身の自己申告と一致)。
* モデル/reasoning: `gpt-5.5`/`high`を指定して起動(指示通り)。Codex自身は
  セッション内からモデルID・reasoning設定を確認する手段がなく、「取得
  不可」と正直に報告——これまでのEmulator側Codexラウンドと同一の既知の
  制約。
* **初回起動の失敗と原因**: 初回起動時、監督担当(現RL担当)自身の
  セットアップミスにより`codex_prompt.md`をworktree内部へ直接配置して
  しまい(stdin経由で渡す設計だったため本来worktree内に置く必要は
  なかった)、Codex自身の指示書が要求する「working treeがcleanであること」
  という前提条件にCodex自身が抵触することを正しく検知し、**実装を一切
  行わずpreflightのみで正しく停止した**。監督担当がプロンプトファイルを
  worktree外へ移動し、再起動して解決した——Codexの判断・実装能力の
  問題ではなく、監督担当自身のセットアップ不備だったことを明記する。
* 2回目の起動でCodexは9資料+実C#/Pythonソースを指定順序で読了し、
  実装前report(branch/HEAD/working tree状態/DLL hash/読了資料/変更予定
  ファイル/再利用対象/禁止対象/試験予定)を出力した後、実装を行った。
* **Python起動不可**: `python.exe`実行時、既知のWindowsログオンセッション
  エラー(過去のEmulator側Codexラウンドと同一)によりCodexは一切の
  Pythonテストを実行できなかった。Codexはこれを正直に記録し、成功を
  偽らず、代わりにgit差分・静的読解による自己点検のみを行った。

## 3. 監査項目

### 3-A. commit差分・スコープ逸脱

* Codexが変更したファイルは`Combat/live_combat_session.py`・
  `Combat/emulator_bridge.py`(既存ファイル変更)、`Combat/tests/
  test_restore_snapshot_phase3c1.py`・`Common/contracts/
  combat_state_contract.v0.6.md`・`Common/contracts/
  rl_phase3c1_python_restore_integration_manifest_20260729.json`・
  `Outputs/reports/..._DRAFT.md`(新規)のみ——指示書の想定ファイル一覧と
  完全一致。
* `Combat/battle_emulator.py`(legacy `step_live_action`/`apply_action`)・
  `Combat/heuristic_agent.py`・`Combat/beam_search.py`・
  `Combat/lookahead.py`・`Training/`・trajectory生成コード・Emulator C#
  コード・`combat_state_contract.v0.5.md`——いずれも無変更(`git diff`
  で確認)。
* git commit・branch操作・merge・tagは一切行っていない(指示通り)。

### 3-B. 再利用方針の遵守

`emulator_bridge.py`の追加コードを読んだ結果、既存の`_types`登録
パターン・`to_plain()`・`legal_actions_to_list()`を素直に再利用している
ことを確認した。新規`snapshot_to_clr()`は既存`combat_state_snapshot.
canonical_json()`を経由してPython DTOをJSON化し、それを`System.Text.Json.
JsonSerializer.Deserialize`でCLR DTOへ変換する設計——第二のパーサー・
canonicalizerを新設していないことを確認した。

`live_combat_session.py`の追加コードは、既存の`_session_faulted`/
`_ensure_session_not_faulted`/`_wrap()`パターンをそのまま拡張しており、
別建てのfault追跡機構やsession counterを新設していないことを確認した。
`ActionFaultContext`と対になる`SnapshotRestoreRejectedContext`/
`SnapshotRestoreFailedContext`は、後者がC#側の構造化property
(`RestorePhase`/`CombatSessionId`等)を直接読み取る設計になっており
(3-D節で実行時に確認)、正規表現フォールバックへの安易な依存を避けている
——指示書§1の注記通りの設計判断。

### 3-C. fault契約の健全性

`SnapshotRestoreRejectedError`が`_session_faulted`を変更しないこと、
`SnapshotRestoreFailedError`が`_handle_restore_failed()`経由で
`_session_faulted = True`を設定することをコードレビューで確認し、独立
試験(4節、`test_rejected_restore_preserves_session_and_step_still_works`・
`test_post_teardown_failure_faults_and_all_recovery_paths_clear`)で実行時
にも直接確認した。post-teardown failure後、`start_combat()`・
`resume_from()`・`restore_snapshot()`の3経路全てで`_session_faulted`が
解除されることも実行時に確認済み。

### 3-D. Codex自身が一度も実行できなかったテストの独立実行(最重要)

Codexの成果物はこのラウンドで初めて実際に実行された。実行の結果、
**15件中2件が失敗**した。原因を特定した結果:

1. **テストfixtureの単純な不備(2件、監査担当が直接修正)**:
   `_eligible_snapshot()`と`test_rejection_categories_via_public_python_api()`
   が、他のテストケースが先に`ensure_loaded()`(pythonnet/CoreCLR
   bootstrap)を済ませていることに暗黙に依存しており、単独プロセスで
   先頭から実行されると`ModuleNotFoundError: No module named
   'Sts2Emulator'`で失敗していた。`ensure_loaded()`呼び出しを追加して
   修正——実装コード自体の欠陥ではない。
2. **`test_canonical_json_round_trip`の比較が過度に厳格だった(監査担当が
   直接修正)**: 修正後、この試験は**2つの独立した、正当な理由による
   差分**を検出した(次項参照)——「バグ」ではなく「試験の期待値が
   間違っていた」ケース。

### 3-E. 本ラウンドで新たに発見した2件の事実(Emulator側C#起因、Codexの
実装対象外)

独立試験の過程で、**これまでどちらの担当のどのラウンドでも一度も文書化
されていなかった2つの事実**を発見した:

1. **`EnemySnapshot.Intent`はRestoreを跨いで保持されない**:
   `SnapshotRestorer.cs`を実際に検索し、`Intent`・`RollMove`への言及が
   0件であることを確認した。IntentはRollMove()が計算する派生状態であり、
   RollMove()はRestoreが呼んではならない禁止済みfresh-startフックの
   1つ(Phase 3B設計)——したがってRestore後のIntentは常に
   `{"intentTypes":[],"stateId":"UNSET_MOVE"}`になる、という**構造上
   避けられない、正当な制約**であることを確認した。過去のいかなる
   round trip検証(Emulator自身のsmoke/監査試験、RL自身のPhase 3B
   `verify_restore_bootstrap_phase3b.py`)も、狭いフィールド署名比較を
   使っており、Intentを一度も比較していなかった——今回の
   `test_canonical_json_round_trip`(完全なcanonical JSON比較)が初めて
   検出した。契約v0.6§9-Aに記録し、試験側は理由を明記した上でIntentを
   比較対象から除外した(検証を弱めたのではなく、真の保証内容に
   合わせて修正した)。
2. **`GetRestoreCapabilities()`が本プロジェクトの実行環境で例外を投げる
   (要Emulator担当エスカレーション)**: 実行すると
   `System.ArgumentException: The path is empty`で失敗することを確認
   した。原因を`GameInstance.cs`の`FindFileUpwards`/`CandidateRoots()`
   まで追跡し、`AppContext.BaseDirectory`をこのプロジェクトの
   pythonnet/CoreCLRホスティング下で直接確認したところ**空文字列**
   であることを実証した(`str(System.AppContext.BaseDirectory)`が
   `''`を返す)——`new DirectoryInfo("")`が`ArgumentException`を投げ、
   本来意図されていた`?? KnownSnapshotSchemaSha256`フォールバックへ
   到達できていない。**これはEmulator側C#コードの実行環境依存バグ
   であり、Codexの実装対象(Python側)の欠陥ではない**。Python側からの
   回避策(`Environment.CurrentDirectory`を呼出前に書き換える等)は、
   Emulator内の他の相対パス解決処理へ及ぼす未監査の副作用リスクを
   理由に、監査担当の判断で見送った。契約v0.6§9-Bに記録し、
   `test_get_restore_capabilities_hashes`を「失敗する既知の
   カナリア」として意図的に残した。**Emulator担当への正式なエスカレー
   ションが必要。**

いずれもCodexが実装した`live_combat_session.py`/`emulator_bridge.py`
自体の欠陥ではなく、Codexへの差し戻しは行っていない(差し戻しても
Emulator側C#コードは変更できないため意味がない)。

### 3-F. 無関係な発見: Part A自身の`.gitignore`バグ(Phase 3C.1範囲外、
`main`へ直接修正)

回帰試験の過程で`verify_live_combat_session_6546_21.py`が
`ModuleNotFoundError: No module named 'combat_env'`で失敗することを
発見した。原因は本ラウンドPart A(Git導入)自身の`.gitignore`——Python
仮想環境除外用の`env/`パターンが、無関係のソースディレクトリ
`Combat/env/`(`combat_env.py`を含む)にも一致し、初回baseline commitから
サイレントに除外されていた。`main`へ直接commit
(`b597ef1`、Phase 3C.1 branchとは別)して修正し、featureブランチへも
同期した。Phase 3C.1のスコープには含まれないが、この監査の過程で発見
したため記録する。

## 4. 独立再実行結果

| 項目 | 結果 |
|---|---|
| `test_restore_snapshot_phase3c1.py`(新規、15ケース) | **14/15合格**(唯一の失敗は3-E節2の確認済みEmulator側バグ) |
| Scenario `6546-21`ライブ回帰 | 49 decision、victory、QuiescentBoundaryViolation 0件 |
| Snapshot/Pet Capture(`verify_snapshot_phase2b.py`) | 0 failing checks |
| Choice/Target(`test_choice_semantics.py`) | 20/20合格 |
| ネイティブ`test_scenario_v2.py` | 31/32合格(既知WRIGGLER 1件のみ) |
| pytest(`Combat/tests`、参考情報) | 74/76合格(WRIGGLER既知1件+3-E節2の既知1件のみ、新規pytest固有の非決定性は本回では非再現) |
| ネイティブハーネスforward(15回、縮小サンプル) | 780テスト実行、QuiescentBoundaryViolation 0件、他失敗0件(既知WRIGGLER 15件除く) |
| WRIGGLER quarantine | `reasons: ['init_exception:TimeoutException']`、変化なし |

## 5. Restore決定性・session契約

`test_restore_step_determinism_reselects_fresh_action`で、同一snapshotを
2回独立にRestoreし、それぞれ再取得した`LegalActions`から同じ論理action
(カードID一致)を選び直す方式で、結果状態が完全一致することを確認した
——古い整数action idの使い回しは一切発生していない。

`test_post_teardown_failure_faults_and_all_recovery_paths_clear`で、
`RestoreFailureInjectionForTesting`(Phase 3B由来の既存internal static
フック)経由でteardown後failureを注入し、`SnapshotRestoreFailedError`が
`RestorePhase`/`CombatSessionId`等の構造化propertyを正しく保持すること、
`_session_faulted`が正しく設定・3経路全てで解除されることを確認した。

## 6. 既知pytest失敗の同定

指示書§8が要求した「pytest node ID・完全なfailure message・既知
baseline・再現証拠」を確認した: `test_scenario_v2.py::test_wriggler_
missing_slot_without_encounter_is_detected`、reason文字列
`init_exception:TimeoutException`——Emulator監査報告書
(`restore_snapshot_phase3c1_audit_20260728.md`§5-D)記載の既知baseline
と完全一致。今回の独立実行(pytest 1回・ネイティブ複数回)でこれ以外の
新規failure identityは確認されなかった(3-E節2の`test_get_restore_
capabilities_hashes`は新規追加テストの既知failureであり、既存baseline
の範囲外——別項目として3-Eで明記済み)。

## 7. Contract v0.6 / manifest / 変更禁止領域

* `combat_state_contract.v0.6.md` SHA256:
  `edad19d8f555ad6d425e2dcfa7642da22f42d2c539f969da0dd5cba3753fa874`
  (監査担当が§9追加後の最終値)。
* `rl_phase3c1_python_restore_integration_manifest_20260729.json`:
  Codex draftを監査担当が実測値で全面更新(全コマンド・全結果を実行し
  記録)。
* 変更禁止領域のSHA256不変を確認: `combat_state_contract.v0.5.md`
  (`e33b369e...`)・`Combat/heuristic_agent.py`・`Combat/beam_search.py`・
  `Combat/lookahead.py`・`Training/`配下は本ラウンドで一切変更されて
  いない(git diffで確認)。
* working tree clean(4-way split commit後、`git status --short`無出力)。

## 8. Phase 3C.1を閉じられるか

**Python統合としては閉じられると判断する**——ただし2件の既知制約
(3-E節)を伴う条件付き。実装コード自体(`live_combat_session.py`/
`emulator_bridge.py`)は独立監査で一度も欠陥が見つからなかった。

**`get_restore_capabilities()`は現状、本プロジェクトの実行環境では
機能しない**(3-E節2)——これはPython統合の完成度そのものというより、
Emulator側の環境依存バグに起因する。次ラウンドでEmulator担当へ
正式にエスカレーションすることを強く推奨する。

## 9. mainへの統合可否

判定`ACCEPT_WITH_DOCUMENTATION_FIX`(実装に影響しない文書修正のみ)に
該当するため、指示書§30の条件を満たす——mainへの統合を推奨する。

Phase 3C.2・Heuristic接続・Training統合には一切着手していない。ここで
監査を完了し、mainへのmerge・最終回帰・tag付けへ進む。
