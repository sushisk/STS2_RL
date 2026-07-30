# RL担当 Phase 2B実装報告 — Snapshot契約固定・Restore準備(2026-07-26)

対象: 「RL・Emulator共同作業指示 — Phase 2B Snapshot契約固定・Restore準備」
「RL担当 Phase 2B指示」「RL担当 Phase 2B追加指示」。実施事項全項目を完了し、
ここで報告のため停止する。`RestoreSnapshot`本体・`SnapshotBranchEvaluator`・
Heuristic/beam-search変更・Snapshotからのaction実行・trajectory再生成の
いずれにも着手していない。

## 1. 基準情報確認

Emulator担当成果物を実ファイル・実commitから直接確認した(値の転記のみで
済ませていない)。

| 項目 | 指示書記載値 | 実確認値 | 一致 |
|---|---|---|---|
| Emulatorコードcommit | `6aa903e` | `git log`確認: `6aa903ec8f656055e8c698e933412bff38115eba` | ✅ |
| Emulator報告書commit | `326919e` | `git rev-parse HEAD`: `326919eba5634efa22528b8c262c8c4fdf660677` | ✅ |
| DLL SHA256 | `e40e7e3d812e73ac032f49cbd9095846a62dc82fc74431c37aa042f702e0bb53` | `sha256sum Sts2Emulator.Cli/bin/Debug/net8.0/Sts2Emulator.dll`: 同一値 | ✅ |
| 正式Schema SHA256 | `ab3f1c721fe70ca9a334cd114fb8d3224ed67df532a9a9e64c0e6b4f0bf0f158` | `sha256sum docs/schemas/combat_state_snapshot.schema.json`: 同一値 | ✅ |
| Power 35クラス分類表パス | 報告書自体(4節) | `docs/reports/combat_state_snapshot_phase2b_emulator_report_20260726.md` §4読了、分類 17/16/0/2 を確認 | ✅ |

省略されたhashは使用していない(全て64文字のフルSHA256を上表・本報告・
manifest全てに記載)。

## 2. RL担当実装事項

### 2-1. Python型と正式JSON Schemaの完全一致検証

`Combat/combat_state_snapshot.py`を更新:

* `KNOWN_SCHEMA_VERSIONS`に`"phase2b.1"`を追加(`"phase2a.1"`も既存分として
  維持 — 加算的バージョンのため両方有効)。
* `PowerSnapshot`に`InternalData: dict | None`・
  `InternalDataSerializerVersion: str | None`を追加(Phase 2Bで追加された
  フィールドのみ、既存フィールドは無変更)。
* **新規発見・修正**: `PlayerRngSnapshot`/`MonsterRngSnapshot`が正式Schemaで
  独立した`$defs`型として定義されているのに対し、Python側はPhase 2Aで
  素のdict(`{"OwnerInstanceId":..., "Purposes":...}`)のまま実装されていた
  ——これは「Python型とSchemaの完全一致」の要件を満たしていなかったため、
  今回2つの独立dataclassとして正式に追加し、`RngSnapshotSet.from_dict()`を
  更新した(既存の`Rng.PlayerRng`/`Rng.MonsterRng`を参照する呼び出し元は
  本ファイル内の新規コードのみだったため、破壊的変更の影響範囲なし——
  `Grep`で確認済み)。

**検証**(`verify_snapshot_phase2b.py` §A): 16個の`$defs`型全てについて、
Pythonデータクラスのフィールド集合とSchemaの`properties`集合をreflectionで
突合。**16/16一致、drift 0件**(schemaVersion/completeness/captureBoundaryの
enum値集合の一致も含む)。

### 2-2. 未知フィールド／欠落フィールド／型不一致の拒否テスト

既存の寛容なloader(`from_dict`、Phase 2Aで確立した「未知フィールドは記録の
みで拒否しない」設計)は**意図的に変更していない**——`LiveCombatSession.
capture_snapshot()`が使い続ける唯一の経路であり、Emulator側の将来の
加算的フィールド追加がRL側のCapture処理を壊さないための設計判断だった
ため。

その代わり、**正式Schemaに対する厳格な適合性検証**を別ユーティリティとして
追加(`validate_against_formal_schema()`、`jsonschema`ライブラリの
`Draft202012Validator`を利用、`docs/schemas/combat_state_snapshot.schema.json`
を直接読み込む)。診断・テスト専用——通常のCapture経路からは一切呼ばれない。

`verify_snapshot_phase2b.py` §Bで以下を確認(全件PASS):

* 正規のCapture結果(未変更)は厳格Schema検証を通過する。
* 未知トップレベルフィールド追加 → 厳格Schemaは拒否、寛容loaderは
  `unknown_fields`へ記録(拒否しない)——両者の意図的な違いを両方確認。
* 必須フィールド欠落(`TurnNumber`削除) → 両方とも拒否。
* 型不一致(`TurnNumber`に非数値文字列) → 両方とも拒否。
* 型不一致だが数値様文字列(`"7"`) → 厳格Schemaは拒否(`type: integer`
  厳守)、寛容loaderは`int()`により暗黙変換(既存の、意図された挙動)——
  この非対称性自体を明示的にテストし文書化した。
* 不正な`StableInstanceId`形式(`^[a-z]+-[0-9]{6,}$`に違反) → 厳格Schemaは
  `pattern`違反として拒否。

### 2-3. Snapshot JSONのcanonical serialization固定

`canonical_json()`を追加(`combat_state_snapshot.py`) — 再帰的キーソート・
余分な空白排除の固定シリアライズ。`Metadata.SnapshotId`/
`Metadata.CapturedAtUtc`(同一状態への複数回Captureで意図的に異なる値)は
比較対象から除外する既定動作。

**検証**(`verify_snapshot_phase2b.py` §C・Case 5): 同一quiescent状態を
`Step()`を挟まず連続2回Captureし、`canonical_json()`が完全に同一の
バイト列になることを確認。SnapshotIdは(想定通り)異なることも確認——
「同一データは同一ハッシュ、識別子だけが変わる」という要求を満たす。

### 2-4. stable instance IDの重複・参照切れ検証

`validate_snapshot_references()`(Python側、Emulator側
`SnapshotReferenceValidator.cs`とは独立実装)を追加。パース済み
`CombatStateSnapshot`のみから動作するため、live GameInstanceなしで
アーカイブ済みJSONに対しても実行可能。

* 全`InstanceId`定義箇所(Player本体/カード山6種/Relics/Powers/
  AssociatedCard/Potions/Orbs/Enemies各Powers)を収集し、重複割当を検出。
* `CombatHistory.Entries[].ActorInstanceId`・`.Fields`内の全
  `*InstanceId`/`*InstanceIds`(再帰的にDictionary/配列を走査、Emulator側の
  走査規約と同一)・Powers自身の`ApplierInstanceId`/`TargetInstanceId`・
  `Rng.PlayerRng`/`Rng.MonsterRng`の`OwnerInstanceId`を参照側として収集し、
  未定義IDへの参照(dangling)を検出。
* 各danglingは`source_live_state_inconsistency`(`CombatHistory`エントリの
  `EntryType`が`CardDrawnEntry`の場合)または`capture_bug`(それ以外、
  保守的既定値)に分類。

**合成テスト**(`verify_snapshot_phase2b.py` §D): 実データを複製・改変し、
(a) Hand[0]のInstanceIdをPlayer自身のInstanceIdへ書き換えて重複を注入 →
検出確認、(b) 存在しないInstanceIdを参照するCombatHistoryエントリを注入 →
検出確認・`capture_bug`分類確認。

**実データ相互検証**(`verify_snapshot_phase2b.py` §Emulator cross-check):
Scenario `302-13`で同一Capture結果に対し、Emulator自身の
`GameInstance.ValidateSnapshotReferences()`(CLR経由で直接呼出)とPython側
`validate_snapshot_references()`を実行し比較——**両者とも5件のdangling
reference、0件のduplicateで完全一致**。

### 2-5. `complete`をPython側で格上げしないことの再確認 + Restore入力適格性の新設

`completeness_is_complete()`は変更していない(Emulatorの判定を検証なしで
そのまま読むのみ)。

今回の指示(「dangling referenceが1件でも存在する場合completeness !=
completeとし、Phase 3 Restore入力として使用可能とは判定しない」)を満たす
ため、`restore_input_eligibility()`を**新設**——これは`Metadata.
Completeness`フィールド自体を書き換える・格上げするものではなく、
「Emulatorの`complete`判定」AND「Python側参照整合性チェックが無傷」の
**両方**を要求する、別建ての追加ゲートである。Emulatorが`complete`と
判定したSnapshotでも、dangling referenceが1件でもあれば
`restore_input_eligible=False`となる——格上げ禁止原則を破らずに指示の
意図を満たす設計とした。

### 2-6. RL source manifestの更新

`Common/contracts/rl_phase2b_source_manifest_20260726.json`を新規作成
(Phase 1 manifestを上書きせず別ファイルとして追加、`priorManifestPath`で
相互参照)。Phase 2B対象ファイル(`combat_state_snapshot.py`・
`live_combat_session.py`・`capture_snapshot_diagnostic.py`・
`verify_snapshot_phase2a.py`・`verify_snapshot_phase2b.py`・
`combat_state_contract.v0.4.md`)全件の完全SHA256・サイズを記録。
`live_combat_session.py`のhashがPhase 1 manifest記載値と完全一致することを
確認済み(Phase 2A/2Bを通じて無変更であることの直接証拠)。

### 2-7. 契約正本`combat_state_contract.v0.4.md`の共同更新

`Common/contracts/combat_state_contract.v0.4.md`を新規作成(v0.3は
supersededとして保持、削除していない)。Emulator側Phase 2A/2B報告書の
内容(Schema定義・Power分類・Stable ID再結合設計・Phase 3順序とリスク)を
そのまま引用し、RL側の実装確認結果(2-1〜2-6節)を追記した。**§9-F・§13に
今回RLが発見した新規事項(3節参照)を明記**——Emulator担当・監督者の
確認待ちとして明示的にフラグを立てた。

## 3. 重大な追加発見: dangling referenceパターンがCardDrawnEntry以外にも存在

指示書は「今回の既知ケースは...`source_live_state_inconsistency`として
扱う」とし、既知パターンを`CardDrawnEntry`(自然ドロー)に限定していた。
Scenario `6546-21`で5つの比較ケースの1つとして検証したところ、
**Emulator自身の`ValidateSnapshotReferences`も同一の5件を検出した
(§2-4で相互確認済み)一方、その5件の`EntryType`は`CardDrawnEntry`では
なく、`PowerReceivedEntry`(2件)・`CardGeneratedEntry`(3件)だった**:

```text
CombatHistory.Entries[1].ActorInstanceId       creature-000063  PowerReceivedEntry
CombatHistory.Entries[1].Fields.powerInstanceId power-000064    PowerReceivedEntry
CombatHistory.Entries[4].Fields.powerInstanceId power-000062    PowerReceivedEntry (applier=creature-000001, 実在)
CombatHistory.Entries[5].Fields.cardInstanceId  card-000065     CardGeneratedEntry (creator=player-000000, 実在)
CombatHistory.Entries[6].Fields.cardInstanceId  card-000066     CardGeneratedEntry (creator=player-000000, 実在)
CombatHistory.Entries[7].Fields.cardInstanceId  card-000067     CardGeneratedEntry (creator=player-000000, 実在)
```

Scenario `6546-21`のrelicリストを確認したところ、**`FESTIVE_POPPER`**
(戦闘開始時に仲間を召喚しPowerを付与するrelic)と**`TOOLBOX`**(戦闘開始時に
ランダムなSkillカード3枚を手札へ生成するrelic)の両方が含まれていた——
これは前回の「現行Combat実行系の詳細処理フロー作成」タスクで挙げた
TOOLBOX/FESTIVE_POPPER仮説と一致する。

**根拠に基づく評価(推測ではなく確認)**: `applierInstanceId`/
`creatorInstanceId`はいずれも実在する生存インスタンス(`creature-000001`
=プレイヤー自身のCreature、`player-000000`=プレイヤー自身)であり、
参照先のみが不在——これは既知の`CardDrawnEntry`パターン(「
`ResetFromScenario`が実際のフックを一度実行し、その後scenario指定の
盤面で上書きし、生成されたインスタンスがCombatHistoryに参照だけ残る」)と
**構造的に同一のメカニズム**である可能性が高い。FESTIVE_POPPERの召喚
Creature/PowerもTOOLBOXの生成カードも、scenarioが指定する最終盤面には
含まれないため上書きで破棄される。

**この場で結論とせず、判定分類コードは保守的既定(`capture_bug`)のまま
変更していない**——「推測で分類しない」という本タスクの原則に従い、
Emulator担当による最終確認(このメカニズムが`CardDrawnEntry`と同一の
`ResetFromScenario`設計に起因することの確認、または実際には異なる原因で
あることの反証)を待つべき項目として`combat_state_contract.v0.4.md`
§9-F・§13に明記した。**Phase 2B Emulator報告書§6の「Scenario 6546-21含む
...上記の既知パターンのみであることを確認済み」という記述は、
`CardDrawnEntry`以外のentry型については実質的に未検証だったことになる
——次回の共同作業で優先的に扱うべき事項として提起する。**

## 4. 5つの比較検証ケース(`verify_snapshot_phase2b.py` §F)

| ケース | completeness | dangling件数 | dangling原因 | restore_input_eligible |
|---|---|---|---|---|
| 1. `Reset()`(scenario未指定) | complete | **0** | — | **True** |
| 2. `ResetFromScenario()`(302-13) | complete | 5 | source_live_state_inconsistency(全件`CardDrawnEntry`) | False |
| 3. Scenario `6546-21` | complete | 5 | **capture_bug**(3節参照、`PowerReceivedEntry`/`CardGeneratedEntry`) | False |
| 4. natural draw確認(302-13) | complete | 5 | source_live_state_inconsistency | False(`CardDrawnEntry` 5件を確認、danglingは全て既知分類) |
| 5. 連続2回Capture(302-13) | 1回目・2回目で完全一致(SnapshotId/CapturedAtUtc以外) | 同一 | 同一 | 同一 |

Case 1(`Reset()`)がdangling 0件であることは、Emulator報告書§6の
「原因は`ResetFromScenario`特有の挙動」という結論をRL側から独立に再現
確認したことになる。Case 5は完全性・dangling・stable ID・canonical JSON
ハッシュの全てが2回のCaptureで一致することを確認し、canonical
serializationの安定性(2-3節)を実データでも裏付けた。

## 5. 既存経路への影響確認

* `LiveCombatSession`の既存メソッド(`start_combat`/`resume_from`/`step`/
  `_is_still_current`/`_resynchronize`)は**無変更**(`capture_snapshot()`も
  Phase 2Aから無変更 — hash一致で確認済み、§2-6)。
* Policy/Value/Choice Policy/Heuristic/beam-search関連ファイルは一切参照・
  編集していない。
* Snapshotからのaction実行は実装していない(`validate_against_formal_
  schema()`/`validate_snapshot_references()`/`restore_input_eligibility()`は
  全て読み取り専用の検証関数)。
* `RestoreSnapshot`本体は実装していない(v0.4契約§9-Gは設計記述のみ)。
* trajectory再生成は行っていない。

**Phase 1回帰再実行**: `pytest tests/test_scenario_v2.py
tests/test_choice_semantics.py -q` → **52/52 合格**(無変更)。

## 6. ファイル・ハッシュ一覧

| ファイル | SHA256 | 備考 |
|---|---|---|
| `Combat/combat_state_snapshot.py` | `5631f0c6224f61f1a1e7f826b5e754a1528309c9f3f45521281a4f82cc98adbc` | Phase 2B: schema version追加、PowerSnapshot拡張、PlayerRngSnapshot/MonsterRngSnapshot新設、canonical_json/validate_snapshot_references/restore_input_eligibility/validate_against_formal_schema追加 |
| `Combat/live_combat_session.py` | `056e1e9e976dfcfa7c93d3734d020a088078f7f835dd152b70b87e57f5daecb8` | Phase 2Aから無変更(hash一致で確認) |
| `Combat/evaluation/online_eval/capture_snapshot_diagnostic.py` | `9ddcaef4bf542f467fc8e929f0173461f9e001efd94302a16019a952d71e9d95` | Phase 2Aから無変更 |
| `Combat/evaluation/online_eval/verify_snapshot_phase2a.py` | `6e5e724309f153d0f76ac6ad288283e32cecfb878d07d5abf0171240fc7f59b1` | Phase 2Aから無変更、再実行のみ |
| `Combat/evaluation/online_eval/verify_snapshot_phase2b.py` | `a30b207ca30c332d871569a0fd94235cee54b7ee8a195d58b82e46473256c72e` | 新規(本フェーズの検証スイート) |
| `Common/contracts/combat_state_contract.v0.4.md` | `b46530309e79b6a52427d144b32dc08510539a90b9cf76504dec182dfdd119c8` | 新規(契約正本、v0.3を supersede) |
| `Common/contracts/rl_phase2b_source_manifest_20260726.json` | (本ファイル自身) | 新規 |

Emulator参照値(前掲1節で実確認済み): コードcommit
`6aa903ec8f656055e8c698e933412bff38115eba`、報告書commit
`326919eba5634efa22528b8c262c8c4fdf660677`、DLL SHA256
`e40e7e3d812e73ac032f49cbd9095846a62dc82fc74431c37aa042f702e0bb53`、
Schema SHA256
`ab3f1c721fe70ca9a334cd114fb8d3224ed67df532a9a9e64c0e6b4f0bf0f158`。

## 7. Phase 3移行条件チェックリスト

| 条件 | 状態 |
|---|---|
| Python型と正式Schemaが完全一致 | ✅ 16/16(2-1節) |
| `unsupported_unknown` 2クラスの扱いが契約へ明記済み | ✅ v0.4契約§9-D・§13 |
| dangling referenceを確実に検出可能 | ✅ 検出は可能(2-4節)。**ただし分類の完全性は未確定**——3節の新規発見(`PowerReceivedEntry`/`CardGeneratedEntry`パターン)がEmulator未確認のまま |
| 問題のあるSnapshotを`complete`として扱わない | ✅ `restore_input_eligibility()`(2-5節) |
| `combat_state_contract.v0.4.md`に既知制約を反映 | ✅ §13 |
| Phase 1回帰が全件合格 | ✅ 52/52 |

**上記のうち「dangling referenceを確実に検出可能」は技術的には満たしている
(検出自体は100%機能する)が、3節の新規発見により「検出された
dangling referenceの原因分類が本当に網羅的か」という点でPhase 3移行前に
Emulator担当の確認が必要と考える。** これは移行のブロッカーではなく、
次回共同作業の最優先確認事項として明示的に申し送る。

## 8. 結論

Phase 2B実施事項を全て完了した。Snapshot契約(Schema・完全性判定・Power
分類・Stable ID Restore設計)を固定し、RL側で独立に検証・相互確認した。
既存のLive実行経路・Policy・Choice Policy・Heuristic・trajectory生成には
一切触れていない。`RestoreSnapshot`本体・`SnapshotBranchEvaluator`には
着手していない。

3節で報告した新規発見(dangling referenceパターンがCardDrawnEntry以外にも
存在する具体的証拠)は、Phase 3設計判断に影響しうる重要事項として
Emulator担当・監督者の確認を要請する。

Phase 3(`RestoreSnapshot`本体実装)へは進まず、ここで停止する。
Emulator担当・監督者の確認、特に3節の新規発見への対応方針の判断を待つ。
