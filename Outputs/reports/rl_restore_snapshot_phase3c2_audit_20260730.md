# RL担当 Phase 3C.2 Python統合 独立監査報告 (2026-07-30)

対象: 「RL担当 Phase 3C.2 Python統合指示」。監査対象はCodex CLI (GPT-5.5, high) による
Phase 3C.2 Pet Restore Python統合セッション。

## 1. Codex使用モデル・起動コマンド

- 指定モデル: `gpt-5.5`、reasoning `high`。
- Codex CLIバージョン: `codex-cli 0.145.0`。
- 起動コマンド:
  ```bash
  cat codex_prompt_phase3c2.md | codex exec \
    -m gpt-5.5 \
    -c model_reasoning_effort=high \
    -s workspace-write \
    -C "C:\STS2_RL_worktrees\phase3c2-python-pet-restore" \
    --skip-git-repo-check \
    - --json \
    > codex_run_phase3c2.jsonl 2> codex_run_phase3c2.stderr.log
  ```
- 基準: RL `main` `2ce4912` (tag `rl-phase3c1-python-accepted-20260729`)、Emulator commit
  `c385a9e`/`bb55689`/`ecabeb2`/`d3384b6`、Emulator DLL SHA256
  `ffd10ab5607f5683ce507ca849cf9e09bf671311b58f65afd8530f4d06e6daf1`。

## 2. 監査対象

| 対象 | 内容 |
|---|---|
| `Combat/tests/test_restore_snapshot_phase3c1.py` | Codexによる変更(2ファイル既存テスト更新+4件新規Pet試験) |
| `Common/contracts/combat_state_contract.v0.7.md` | 監督者が新規作成(Codexは書かない、指示通り) |
| `Common/contracts/rl_phase3c2_pet_restore_integration_manifest_20260730.json` | 監督者が新規作成 |
| `Outputs/reports/rl_restore_snapshot_phase3c2_python_integration_20260730.md` | Codex起草+監督者が§8を補完 |
| 本監査報告書 | 監督者が新規作成 |

## 3. スコープ逸脱の確認

- `git diff --stat` (Codex納品分): `Combat/tests/test_restore_snapshot_phase3c1.py`
  (137行差分)と`Outputs/reports/..._DRAFT.md`のみ。
- `Combat/emulator_bridge.py`/`Combat/live_combat_session.py`: **無変更**(Codex自身が
  「既存の汎用パスで十分」と判断、監督者が両ファイルの`git diff`が空であることを直接確認)。
- Emulator C#・`Combat/battle_emulator.py`・`heuristic_agent.py`/`beam_search.py`/
  `lookahead.py`・`Training/`・contract v0.5への接触なし。
- git操作(commit/branch/merge/tag)はCodexサンドボックス内で一切行われていない(既知の
  `.git`書き込み不可制約により、そもそも実行不可能)。

## 4. Codexの実装判断の評価

**特筆すべき点**: Codexは指示が明示的に要求した「wrapper変更が必要か検討する」という
判断過程を正確に実行し、`Player.Pets`/`CreatureSnapshot`が既存のcanonical JSON/CLR DTO
変換経路を無変更で通過することを実コード確認した上で、`emulator_bridge.py`/
`live_combat_session.py`のいずれにも変更を加えないという結論に達した。これは指示の
「重複実装を作らない」という要求に対する正しい判断であり、監督者が独立に両ファイルを
再読して同じ結論に達した(§6で詳述)。

Pet fixture構築についても、指示が提示した「実`BOUND_PHYLACTERY`召喚を第一候補とする」
方針に従い、Emulator側監査が既に`pet_roundtrip_from_real_summon`で実証済みの経路
(`LiveCombatSession.start_combat()`でrelicを付与→`_make_eligible()`でCombatHistory等を
正規化)を再利用する形でPython側fixtureを構築した——架空の合成Pet専用ヘルパーを新設せず、
既存の`_make_eligible`/`_snapshot_sig`パターンを踏襲している。

## 5. Codexが正直に報告したサンドボックス制約

前3ラウンド(Phase 3B/3C.1/Emulator側3C.2)と同じ3種の制約に直面した:

- `python.exe`起動不可(Windows logon-session error)——自身が書いた新規Pet試験を
  一度も実行できなかった。
- `pytest`がPATH上に存在しない。
- `.git`書き込み不可(そもそも指示によりcommitはCodexの責務外)。

虚偽の成功報告は一切なく、「実行できなかった」ことを明示的に報告している
(draft報告書§6「Attempted runtime tests」——全項目が「Not executed」)。

## 6. 独立検証結果

### 6-A. wrapper変更なしの判断の妥当性確認

`git diff -- Combat/emulator_bridge.py Combat/live_combat_session.py` (Codex納品分に対して):
**空**。監督者が両ファイルを直接再読し、`Player.Pets`が既存の
`_snapshot_to_json_text`→`canonical_json`→`JsonSerializer.Deserialize[ClrCombatStateSnapshot]`
経路を無変更で通過すること、`RestoreCapabilities`Pythonデータクラスに`supports_pets`
フィールドが既に(Phase 3C.1時点から)存在することを確認した。**Codexの判断は正しい。**

### 6-B. 独立試験実行(初回)

```
python Combat/tests/test_restore_snapshot_phase3c1.py
```

結果: **18 passed, 1 failed** (`test_get_restore_capabilities_hashes`)。新規Pet試験
4件(`test_pet_object_restore_round_trip`/`test_pet_json_restore_round_trip_matches_object_restore`/
`test_pet_canonical_json_round_trip`/`test_pet_restore_step_determinism_reselects_fresh_action`)は
**初回実行から全て合格**——Codex納品のテストコード自体に欠陥はなかった。

### 6-C. 唯一の失敗の根本原因調査

`test_get_restore_capabilities_hashes`の失敗箇所:

```python
assert caps.contract_sha256 == hashlib.sha256(contract_path.read_bytes()).hexdigest(), caps
```

`caps.contract_sha256` (`e33b369e...`, Emulator側の正規LFハッシュ、`main`の
`git_baseline_manifest`が記録済みの値と一致) に対し、`contract_path.read_bytes()`
(worktree内のローカルチェックアウト、`b8e5c9d1...`) が不一致——worktreeの
`combat_state_contract.v0.5.md`が**CRLF**で存在することを確認した(`core.autocrlf=true`、
`.gitattributes`の`* text=auto`との組み合わせによる、worktreeチェックアウト時の
既知の挙動)。`git status --short`はこのファイルを無変更と報告する(gitの内部比較は
smudge/clean filterで再正規化されるため)——**gitの視点では無変更、Pythonの生バイト読取り
では不一致**という、環境依存の見かけ上の差異であることを確認した。

この問題はPhase 3C.1時点でも潜在していたはずだが、当時は`GetRestoreCapabilities()`が
`AppContext.BaseDirectory`空文字列バグ(契約v0.6§9-B)で例外を送出し、このassertion行に
到達すること自体がなかった——今回Emulator側がこのバグを修正したことで、初めてこの
assertionまで処理が進み、潜在していた別の問題が顕在化した、という経緯を確認した。

**分類**: 実装コード(Codexの`test_restore_snapshot_phase3c1.py`diff自体)の欠陥ではなく、
テストのハッシュ計算がチェックアウト環境(CRLF/LF)に依存してしまっていたという
test-fixture/環境頑健性の問題——指示§28相当の「test isolationの修正はRL担当が直接行って
良い」範囲に該当すると判断し、監督者が直接修正した(Codexへの差し戻しは行わなかった)。

**修正**: ローカル読み取りバイト列を`\r\n`→`\n`正規化してからハッシュ計算する
(Emulatorが報告する正規LF形式との比較を、チェックアウト環境非依存にする)。
`combat_state_contract.v0.7.md`§11「Artifact Hash Resolution Rules」に恒久ルールとして
記録した。

### 6-D. 修正後の再実行

```
python Combat/tests/test_restore_snapshot_phase3c1.py
```

結果: **19 passed, 0 failed**。

### 6-E. 全回帰スイート(監督者が独立実行)

| 項目 | 結果 |
|---|---|
| `pytest Combat/tests/ -q` | 79 passed, 1 failed(既知のWRIGGLER理由文字列不一致、Phase 3C.2と無関係) |
| `verify_live_combat_session_6546_21.py` | PASS、49 decision、victory、QuiescentBoundaryViolation 0件 |
| `verify_snapshot_phase2b.py` | PASS、0 failing checks |
| `test_choice_semantics.py` | 20 passed, 0 failed |
| `test_scenario_v2.py` | 31 passed, 1 failed(同上WRIGGLER) |
| `qb_repro_driver_rl.py --order forward --iterations 15` | 780 test executions、QB違反0件、WRIGGLER 15件(既知)、other 0件 |

### 6-F. Pet固有の契約確認

- Pet stable ID(`InstanceId`)がRestore前後で完全一致することを
  `test_pet_object_restore_round_trip`/`test_pet_json_restore_round_trip_matches_object_restore`
  で確認。
- Pet `CombatId`が実`BOUND_PHYLACTERY`召喚経路で再現することを同上試験で確認
  (Emulator側監査の`pet_roundtrip_from_real_summon`と同じ結論をPython公開API経由でも
  独立に再現)。
- Pet HP/MaxHp/Block/Owner/Powerが完全一致することを`_assert_restored_pet_matches`/
  `_pet_sig`ヘルパーで確認。
- Petあり canonical JSON round tripが(既存の`Intent`/`CombatSessionId`除外を除いて)
  完全一致することを`test_pet_canonical_json_round_trip`で確認。
- Petあり Restore→Step決定性を`test_pet_restore_step_determinism_reselects_fresh_action`
  で確認(Pet状態を含む署名比較)。
- Petあり CombatHistory非空Snapshotが引き続き拒否されること、`pet_count`が拒否理由に
  含まれなくなったことの両方を`test_rejection_categories_via_public_python_api`で確認。
- Scenario `6546-21`(実Osty Pet含む)が引き続き`reference_integrity`理由で拒否される
  ことを`test_real_6546_21_rejected_via_public_api`(無変更)で確認。

## 7. 指摘と差し戻し履歴

本ラウンドでは**差し戻しは発生していない**。独立監査で発見された唯一の問題
(§6-C、`test_get_restore_capabilities_hashes`のCRLF/LF依存)は実装コード
(Codexが書いたテスト本体のPetロジック)の欠陥ではなく、無関係な既存テストヘルパーの
環境依存性——Codex納品のPet固有テスト4件は初回実行から一度も修正を要さなかった。

## 8. 残るリスク・既知の限界

- `Byrdpip`/`PaelsLegion`等、Osty以外のPet種別は実コード確認のみで、実際のCapture→Restore
  往復試験は未実施(Emulator側監査と同じ既知の限界、`combat_state_contract.v0.7.md`§10-C
  に記録)。
- `EnemySnapshot.Intent`非保存の限界(v0.6§9-A由来)は継続する既知の制約——Pet対応とは
  無関係。
- CRLF/LFアーティファクトハッシュの問題(§6-C)は今回修正したが、将来同種の
  ハッシュ比較コードを追加する際は`combat_state_contract.v0.7.md`§11のルールに
  従うことを推奨する。

## 9. 最終判定

```text
ACCEPT
```

判定根拠:

- Codex納品のPet統合コード(テストロジック本体)は、独立監査(19件のテスト実行、全regression
  スイート再実行)を通じて一度も欠陥が見つからなかった。
- Codexの「wrapper変更不要」という自発的判断を、監督者が独立にファイル差分を確認して
  正当性を検証した。
- 唯一発見された問題(CRLF/LFハッシュ比較)は、Codexが書いたPet固有のロジックとは無関係な、
  既存の(Phase 3C.1由来の)テストヘルパーの環境依存性の問題であり、「実装コードの欠陥」
  ではなく「test-fixtureの環境頑健性」に分類されるため、指示の差し戻し基準
  (実装欠陥のみ差し戻し対象)に該当しない——監督者が直接修正することが適切と判断した。
- 全回帰スイートが既知の失敗(WRIGGLER)以外は完全に合格した。
- `ACCEPT_WITH_DOCUMENTATION_FIX`ではなく`ACCEPT`とした理由: Emulator側Phase 3C.2監査
  (`d3384b6`)と同じ基準を踏襲し、実装・テスト・契約文書・監査のいずれにも記載不備が
  残っておらず、修正した問題(§6-C)は既にこの監査報告書自体とcontract v0.7に完全に
  文書化済みであるため。

## 10. Phase 3C.3・Heuristic・Training開始可否

**不可(現時点では開始しない)。**

指示の明示的な最終行「Phase 3C.3、Heuristic、Trainingには進まず停止すること」に従う。
merge・tag作成後、ここで停止する。

## 11. working tree clean

監査完了時点で本監査報告書ファイル(新規追加、これからcommit)以外に変更なし
(非決定的診断ファイル2件は`git checkout --`で破棄済み)。
