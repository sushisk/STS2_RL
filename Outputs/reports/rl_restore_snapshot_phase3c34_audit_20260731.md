# RL担当 Phase 3C.3-3C.4 Python統合 独立監査報告 (2026-07-31)

対象: 「RL担当 Phase 3C.3-3C.4統合再開指示」。監査対象はCodex CLI (GPT-5.5, high) による
CombatHistory全17型復元・Power内部状態Exact Restore・`RestoreSnapshotJson`入力検証強化の
Python統合セッション。

## 1. Codex使用モデル・起動コマンド

- 指定モデル: `gpt-5.5`、reasoning `high`。
- Codex CLIバージョン: `codex-cli 0.145.0`。
- 基準: RL `main` `a98b423`(tag `rl-phase3c2-python-accepted-20260730`は`1399f1a`を指す、
  `a98b423`はその1コミット後——無関係なdesign参照docのみ追加)、Emulator commit
  `982de1b`/`cb85316`/`0bb55a2`(および3C.4.1関連5commit)。
- Emulator DLL SHA256: `f79a91925c75f05bacaecdf5f614bdea01ad6c8f65f55d0e9585eff4d1074ecc`
  (Phase 3C.4.1監査報告のofficial buildハッシュと完全一致、独立再計算済み)。
- Schema/DTO仕様書/JSON例のSHA2563件も、Phase 3C.4.1監査報告記載値と完全一致することを
  独立再計算で確認済み。

## 2. 監査対象

| 対象 | 内容 |
|---|---|
| `Combat/combat_state_snapshot.py` | Codexによる変更(`KNOWN_SCHEMA_VERSIONS`/`PlayerTurnNumbers`) |
| `Combat/emulator_bridge.py` | Codexによる変更(`validate_restore_snapshot_json`追加) |
| `Combat/live_combat_session.py` | Codexによる変更(同上wrapper) |
| `Combat/tests/test_restore_snapshot_phase3c1.py` | Codexによる変更(9件新規試験+2件既存修正)、監督者が1箇所修正 |
| `Combat/evaluation/online_eval/verify_snapshot_phase2b.py` | Codexによる変更(fixture 1箇所) |
| `Common/contracts/combat_state_contract.v0.8.md` | 監督者が新規作成 |
| `Common/contracts/rl_phase3c34_history_power_integration_manifest_20260731.json` | 監督者が新規作成 |
| `Outputs/reports/rl_restore_snapshot_phase3c34_integration_20260731.md` | Codex起草+監督者が補完 |
| 本監査報告書 | 監督者が新規作成 |

## 3. スコープ逸脱の確認

- `git diff --stat`(Codex納品分): 5ファイル(632行追加、9行削除)。Emulator C#・
  `battle_emulator.py`・`heuristic_agent.py`/`beam_search.py`/`lookahead.py`・`Training/`・
  contract v0.5/v0.6/v0.7への接触なし。
- git操作(commit/branch/merge/tag)はCodexサンドボックス内で一切行われていない。
- 事前に指示書で特定した4項目のPython gap(`KNOWN_SCHEMA_VERSIONS`、
  `PlayerTurnNumbers`欠落、`validate_restore_snapshot_json`欠如、capability文字列陳腐化)
  以外に、Codexが自発的に追加した変更は無い——`verify_snapshot_phase2b.py`の1箇所修正のみ
  例外だが、これは`PlayerTurnNumbers`必須化の直接の結果として必要になった、指示の想定内の
  波及修正である。

## 4. Codexの発見・判断の評価

**特筆すべき点(2件)**: Codexは指示書自体の誤った前提を2件、実ソース確認によって
正しく検出・報告した。

1. 指示書は「公式JSON例は17種類のHistory entryを全て含む」と述べていたが、Codexが
   `combat_state_snapshot_example.v0.8.json`を直接読んだ結果、`SchemaVersion="phase3c.4"`
   でありながら`CombatHistory.Entries`が空配列であることを発見した。Codexはこれを
   黙って無視せず、明示的に報告書に記録した上で、公式例をJSON API成功経路の試験にのみ
   使用し、17型網羅試験には(Emulatorの既承認smokeテストのfixture形状を移植した)
   test専用fixtureを別途構築するという適切な対応を取った。
2. DTO仕様書§8はpublic capability rejection code vocabularyに
   `unknown_combat_history_entry_type`を含むかのように記載していたが、Codexが
   `GameInstance.cs`の`RestoreRejectionCodes`静的リストを直接確認した結果、実際には
   含まれていないことを発見した(`combat_history_non_empty`/`fresh_combat_history_written`
   のみ)。Codexはcapability vocabulary試験を実際の公開リストに合わせて調整しつつ、
   実際の拒否理由を検証する挙動試験は本来の`unknown_combat_history_entry_type:{type}`
   confirmationのまま維持するという、正確な切り分けを行った。

いずれも「指示書の誤りを鵜呑みにせず実コードで検証する」という、このプロジェクト全体で
一貫して求めてきた姿勢を体現しており、高く評価する。

## 5. Codexが正直に報告したサンドボックス制約

前ラウンドまでと同じ制約に直面: `python.exe`起動不可(Windows logon-session error)、
`py -3`launcherにインストール済みPythonなし、`pytest`がPATH上に存在しない。虚偽の
成功報告は一切なく、全ての「実行できなかった」ことを明示的に報告している。

## 6. 独立検証結果

### 6-A. 独立試験実行(初回)

```
python Combat/tests/test_restore_snapshot_phase3c1.py
```

結果: **24 passed, 4 failed**。

### 6-B. 唯一の根本原因調査

4件の失敗全てのtracebackが同一箇所を指していた:

```python
def _model_fixtures():
    ...
    ensure_loaded()
    from MegaCrit.Sts2.Core.Models import ModelDb
    ...
    for power in ModelDb.AllPowers:   # ここで例外
```

直接再現した: `ensure_loaded()`のみを呼んだ状態で`ModelDb.AllPowers`を反復すると
`TypeError: Exception has been thrown by the target of an invocation.`が発生する。
`shared_game_instance()`を先に呼び、`GameInstance`を1つ構築してから同じ反復を行うと
正常に281件のPowerを取得できることを確認した——`ModelDb`はGameInstance構築まで
populateされないという、Phase 3C.1監査で確立済みの既知パターン(「単一プロセス内の
CLR bootstrap順序」問題)の再発である。

**分類**: Codexが新規に書いたtest helper(`_model_fixtures()`)の初期化順序の
不備——実装コード(`combat_state_snapshot.py`/`emulator_bridge.py`/
`live_combat_session.py`のdiff自体)の欠陥ではなく、指示書§28相当の
「test isolationの修正はRL担当が直接行って良い」範囲に該当すると判断し、監督者が
直接修正した(Codexへの差し戻しは行わなかった)。

**修正**: `_model_fixtures()`の先頭に`shared_game_instance()`呼び出しを追加
(および`emulator_bridge`からの該当import追加)。

### 6-C. 修正後の再実行

```
python Combat/tests/test_restore_snapshot_phase3c1.py
```

結果: **28 passed, 0 failed**。CombatHistory全17型round trip、`PlayerTurnNumbers`/
`CardPlay.Resources`保存、Power分類3種(`serialize_required`/`safe_to_recompute`/
`unsupported_unknown`)全て、JSON API検証・拒否試験、Restore→Step決定性(History あり)
——全てCodexが一度も実行できなかった試験群が、初回の`_model_fixtures()`修正のみで
全て合格した。Codex納品のfixture field名(C# smokeテストから移植した
`wasEthereal`/`creatorInstanceId`/`receiverInstanceId`/`stolenStrength`等)は
一つも誤りが無かった——精読に基づく移植の正確性を裏付ける。

### 6-D. 全回帰スイート(監督者が独立実行)

| 項目 | 結果 |
|---|---|
| `pytest Combat/tests/ -q` | 88 passed, 1 failed(既知のWRIGGLER理由文字列不一致、本ラウンドと無関係) |
| `verify_snapshot_phase2b.py` | PASS、0 failing checks(このスクリプト自身の`KNOWN_SCHEMA_VERSIONS`対Schema enum一致assertionが、修正前は失敗し修正後に合格することを確認——修正が実際にgapへ対処した直接証拠) |
| `verify_live_combat_session_6546_21.py` | PASS、49 decision、victory、QuiescentBoundaryViolation 0件 |
| `test_choice_semantics.py` | 20 passed, 0 failed |
| `test_scenario_v2.py` | 31 passed, 1 failed(同上WRIGGLER) |
| `qb_repro_driver_rl.py --order forward --iterations 15` | 780 test executions、QB違反0件、WRIGGLER 15件(既知)、other 0件 |

### 6-E. Codexの2件の指摘の独立再確認

- `combat_state_snapshot_example.v0.8.json`を`json.load`で直接確認: `Metadata.
  SchemaVersion == "phase3c.4"`、`len(CombatHistory.Entries) == 0`——Codexの報告通り。
- `GameInstance.cs`の`RestoreRejectionCodes`をgrepで直接確認:
  `combat_history_non_empty`/`fresh_combat_history_written`は存在するが
  `unknown_combat_history_entry_type`(接頭辞なしの単独文字列)は存在しない——Codexの
  報告通り。

両方とも実際に確認済みの事実であり、contract v0.8 §9-D/§9-Eに将来のEmulator側
ラウンドへの申し送り事項として記録した(本ラウンドのblockerではない)。

## 7. 指摘と差し戻し履歴

本ラウンドでは**差し戻しは発生していない**。独立監査で発見された唯一の問題
(§6-B、`_model_fixtures()`のCLR bootstrap順序)は、Codexが書いたCombatHistory/Power
固有のテスト内容(fixture field名・アサーション対象)自体の欠陥ではなく、共通の
初期化ヘルパーの順序問題であり、実装コード側(4件の本体diff)には一度も修正を要さな
かった。

## 8. 残るリスク・既知の限界

- 公式JSON例が17種類のHistory entryを含んでいない件(§6-E)——将来のEmulator側ラウンド
  でexample自体の更新を推奨する旨、contract v0.8§9-Dに記録済み。
- `unknown_combat_history_entry_type`が公開rejection code vocabularyに含まれていない件
  (§6-E)——同様にcontract v0.8§9-Eに記録済み。
- Osty以外のPet種別・`EnemySnapshot.Intent`非保存など、既存の既知限界(v0.7由来)は
  継続する。

## 9. 最終判定

```text
ACCEPT
```

判定根拠:

- Codex納品のPython統合コード(4件の本体diff: schema version/PlayerTurnNumbers/JSON
  validate wrapper/regression fixture)は、独立監査(28件のテスト実行、全regression
  スイート再実行)を通じて一度も欠陥が見つからなかった。
- Codexが指示書自体の2つの誤った前提(公式JSON例のHistory網羅性、rejection code
  vocabularyの完全性)を実コード確認によって正しく検出し、架空の前提に基づくテストを
  書かなかった——推測禁止の指示に忠実な判断。
- 唯一発見された問題(`_model_fixtures()`のCLR bootstrap順序)は、実装コードとは無関係な
  test helperの初期化順序の問題であり、指示の差し戻し基準(実装欠陥のみ差し戻し対象)に
  該当しないため、監督者が直接修正することが適切と判断した。
- 全回帰スイートが既知の失敗(WRIGGLER)以外は完全に合格した。
- Emulator側3ラウンド(3C.3/3C.4/3C.4.1)と同じACCEPT基準を踏襲し、実装・テスト・契約
  文書・監査のいずれにも記載不備が残っておらず、発見した2件の指摘(§6-E)は既に本監査
  報告書とcontract v0.8に完全に文書化済みであるため、`ACCEPT_WITH_DOCUMENTATION_FIX`
  ではなく`ACCEPT`とする。

## 10. Phase 4・Heuristic・Training開始可否

**不可(現時点では開始しない)。**

指示の明示的な最終行「Phase 4、Heuristic、Trainingには進まない」に従う。merge・tag作成後、
ここで停止する。

## 11. working tree clean

監査完了時点で本監査報告書ファイル(新規追加、これからcommit)以外に変更なし
(非決定的診断ファイル2件は`git checkout --`で破棄済み)。
