# RL担当 Phase 2B最終再統合報告 — Pet Capture修正取り込み(2026-07-26)

対象: 「RL担当 Phase 2B最終再統合指示」。実施事項1-6・Scenario 6546-21確認・
回帰試験・固定作業を全て完了した。作業中に**回帰試験で新規の非決定的な
Quiescent Decision Boundary違反を発見した**(4節)——これは今回のPet
Capture修正のスコープ外の事象であり、`RestoreSnapshot`・Heuristic・
beam-search・Importedゲームロジックのいずれにも着手せずここで報告のため
停止する。

## 1. 基準情報確認

全て実ファイル・実commitから直接確認した(値の転記のみで済ませていない)。

| 項目 | 指示書記載値 | 実確認値 | 一致 |
|---|---|---|---|
| Emulatorコードcommit | `f2343b1` | `git log`: `f2343b13a8cee25a5730018bab3000166f333ba9` | ✅ |
| 調査報告書commit | `b9109bf` | `git show`: `b9109bf52193442b97299ebb5dba0abb409966e7` | ✅ |
| 修正報告書commit | `44c01d7` | `git rev-parse HEAD`: `44c01d7f9e58073e5b2925ac65d5b31e8db082f9` | ✅ |
| DLL SHA256 | `790af6e390c6bbab5b92e78c8cc6cd6498bdc95fbb1df462b0e91ed82a873201` | `sha256sum`同一値 | ✅ |
| Schema SHA256 | `16643444fda085d62df435fa0290e73b356123ffd95981317da4ea3f86c23dc3` | `sha256sum`同一値 | ✅ |
| 契約SHA256 | `ddb632358cb02ba4755b4e064e4838882fb77e172974bd9953c5e817df2aec88` | `sha256sum`同一値(Emulator担当が既に訂正済み、RL側は未編集で確認のみ) | ✅ |

## 2. 実施事項

### 2-1. 修正版DLLとSchemaの取り込み

`emulator_bridge.py`のビルド出力参照先(`Sts2Emulator.Cli/bin/Debug/net8.0/`)
は既存の固定パスのままで、実体が`f2343b1`ビルドへ更新済みであることを
DLL SHA256一致で確認した(コード変更不要)。

### 2-2. `PlayerSnapshot.Pets`の追加

`Combat/combat_state_snapshot.py`に新規`CreatureSnapshot`データクラスを
追加(`InstanceId`/`Kind`/`OwnerInstanceId`/`CombatId`/`MonsterId`/`Name`/
`Hp`/`MaxHp`/`Block`/`IsAlive`/`Powers`/`Intent`/`StateLog`)、
`PlayerSnapshot.Pets: list[CreatureSnapshot]`として追加した。

### 2-3. `CreatureSnapshot.Kind`と`OwnerInstanceId`の完全保持

`from_dict()`で両フィールドを他フィールドと同様に完全パース——`Kind`は
文字列としてそのまま保持(Schema側`enum: ["player","enemy","pet"]`の
検証は厳格Schema側`validate_against_formal_schema()`が担当、寛容loaderは
値を検証なしで保持する既存方針を踏襲)、`OwnerInstanceId`はoptional
(現状`pet`のみ産出、常に非null)。

### 2-4. stable ID／Pet Power所有者参照validatorの更新

`_collect_known_instance_ids()`に`Player.Pets[].InstanceId`・
`Player.Pets[].Powers[].InstanceId`(+`AssociatedCard`)を追加。
`validate_snapshot_references()`の直接参照チェックに
`Player.Pets[].OwnerInstanceId`・`Player.Pets[].Powers[].
ApplierInstanceId`/`TargetInstanceId`を追加した。

**分類ルールの更新(根拠あり、推測ではない)**: Emulator担当の調査報告書
(`b9109bf`)がSOUL×3の`CardGeneratedEntry`を`source_live_state_
inconsistency`と直接ソース確認込みで確定させたため、
`_CONFIRMED_SOURCE_LIVE_STATE_INCONSISTENCY_ENTRY_TYPES`を
`{"CardDrawnEntry"}`から`{"CardDrawnEntry", "CardGeneratedEntry"}`へ
拡張した(前回報告時点では`capture_bug`保守的既定のままにしていた項目)。
`PowerReceivedEntry`は今回のOsty修正で該当ケースが解消されたため、
一般には未確認のまま保守的`capture_bug`既定を維持している——「relicリスト
の相関だけでは分類根拠にならない」という契約書の一般化知見をコードにも
反映した。

### 2-5. DTOと正式Schemaの完全一致確認

`verify_snapshot_phase2b.py`の§A対象リストへ`CreatureSnapshot`を追加。
**17/17一致、drift 0件**(Phase 2B時点の16から`CreatureSnapshot`分1件増加)。

### 2-6. canonical JSONの往復一致確認

Scenario `6546-21`(Petsを含む唯一のテストシナリオ)で新規検証:

* 連続2回Capture → `canonical_json()`完全一致(Pets込み)。
* `from_dict(asdict(...))`往復 → Pets件数・InstanceId列全て完全一致
  (stable IDが往復で保持されることを確認)。

## 3. Scenario 6546-21確認

`case_6546_21_pet_capture_fix_check()`(新規)で全項目確認、**全件PASS**:

| 確認項目 | 結果 |
|---|---|
| Osty(`creature-000063`/`power-000064`)の解消 | ✅ `Player.Pets`に1件(`Kind="pet"`)として捕捉され、dangling referenceから消滅 |
| `DieForYouPower`の解消 | ✅ Osty配下`Powers`に`DIE_FOR_YOU_POWER`として保持、`OwnerInstanceId`がOsty自身のInstanceIdと一致 |
| SOUL×3の残存 | ✅ `card-000065/66/67`、`CardGeneratedEntry`として3件残存を確認 |
| 残存3件の原因分類 | ✅ 全件`source_live_state_inconsistency`(2-4節の分類ルール拡張により) |
| completeness | `"complete"`のまま(Capture前後・修正前後で不変) — **注記**: `Metadata.Completeness`はEmulator自身のフィールド完全性判定であり、CombatHistoryの参照整合性とは別軸の概念であることを本セッションで明確化した(5節参照)。RL側は独立して格上げも格下げもしていない |
| Capture前後のライブ挙動 | ✅ `capture_snapshot()`呼出前後で`GetLegalActions()`件数不変を確認(副作用ゼロ) |

## 4. 重大な追加発見: 新DLLで非決定的なQuiescent Decision Boundary違反を観測

**Phase 1回帰(`test_scenario_v2.py` + `test_choice_semantics.py`)を新DLL
(`f2343b1`)で再実行したところ、以前(旧DLL `6aa903e`)で確認していた
安定した52/52合格が再現しなかった。** 単純に「52/52」と報告することは
事実に反するため、詳細を以下に記録する。

### 4-1. 観測データ

| # | 実行方法 | 結果 |
|---|---|---|
| 1 | `test_scenario_v2.py`単独 | 32/32 合格(複数回確認) |
| 2 | `test_choice_semantics.py`単独 | 20/20 合格(複数回確認) |
| 3 | 両ファイル同一プロセスで連続実行(計5回) | 13失敗→9失敗→6失敗→**52/52合格**→1失敗 (5回中3回で失敗発生、run-level失敗率約60%) |
| 4 | Scenario `6546-21` 49-decision replay単独(3回) | 3回とも49決定・victory・0違反 |

* **単独実行では100%安定**(`test_scenario_v2.py`のみ、`test_choice_
  semantics.py`のみ、いずれも複数回確認して全件合格)。
* **2ファイルを同一プロセスで連続実行した場合のみ**、非決定的に失敗が
  発生する(failした場合も毎回具体的な失敗テスト・失敗数が異なる——
  典型的な非決定性の兆候)。
* 失敗は**常に`test_choice_semantics.py`側**(`test_scenario_v2.py`側の
  32ケースが先に全て走った後)で発生し、`test_scenario_v2.py`自体が
  失敗したことは一度もない。
* エラーは一貫して`QuiescentBoundaryViolationException`——`PlayCardAction`
  (`action index: 10`、複数の異なるカードで発生: GUARDS/HEADBUTT/
  ARMAMENTS/HOLOGRAM/SURVIVOR等)が「まだ実行中で、それを説明する
  PendingChoice/PendingTargetSelectionが無い」という、Phase 1の中核
  契約(`AssertQuiescentDecisionBoundary`)そのものへの違反。

### 4-2. Snapshot Capture由来ではないことの確認

`grep`で`test_scenario_v2.py`/`test_choice_semantics.py`両ファイルを
検索し、`combat_state_snapshot`/`capture_snapshot`/`LiveCombatSession`への
参照が**一切ないこと**を確認した——両ファイルは`BattleEmulator`/
`emu._restore`/`game.Step()`を直接使う既存の(Phase 1以前からある)テスト
であり、今回のPet Capture修正やSnapshot関連コードとは無関係の経路である。
**この現象はCapture側の変更が原因ではなく、純粋にEmulatorのLive実行系
(DLL本体)側で発生している。**

### 4-3. 原因特定できなかった理由(正直な申告)

新DLL(`f2343b1`)導入前の旧DLL(`6aa903e`)でこの組み合わせ実行を
複数回試して比較する、いわゆるクリーンなA/Bテストを行いたかったが、
以下の理由で実施しなかった:

* 旧DLLのバイナリを別途保存していなかった(ビルド出力ディレクトリは
  `dotnet build`のたびに上書きされる)。
* 旧commitでの再ビルドはEmulator側のgit作業ツリー操作
  (`git checkout`+`dotnet build`)を伴い、RL担当のスコープを超える
  ("Emulator担当"の領域へ踏み込む行為であり、以前「担当が違う」との
  訂正を受けた経緯を踏まえ、今回は実施しなかった)。

したがって、**今回発見した非決定性が`f2343b1`(Pet Capture修正)による
新規リグレッションなのか、既存のテストハーネス設計(1プロセス内で単一の
共有`GameInstance`を多数のシナリオへ順次再利用する構造)に元々内在していた
潜在的な不安定性が今回たまたま顕在化しただけなのかは、確定できていない。**
以前の単発実行(本Phase 2B作業開始直前、旧DLL使用時)で52/52合格を
確認していたが、これは1回の試行に過ぎず、非決定的事象の不在を証明する
ものではない。

## 5. `complete`セマンティクスの明確化(今回の副次的成果)

Scenario 6546-21確認作業を通じて、`Metadata.Completeness`と
danglingリファレンスの参照整合性は**別軸の概念**であることが明確になった:

* `Completeness`: Emulator自身によるフィールド/データ捕捉の完全性判定
  (全てのPower/Relic/RNG値等が捕捉されたか)。
* 参照整合性(dangling reference): `CombatHistory`が指すIDが実際に
  Snapshot内に存在するか——別の懸念軸。

3件のdangling referenceが残るScenario 6546-21でも`Completeness`が
`"complete"`のままであることは、Emulator側の設計として一貫している
(全フィールドは実際に捕捉されているため)。Restore入力としての適格性は
`restore_input_eligibility()`(両軸を統合した独自ゲート、Phase 2B既存実装
から無変更)が正しく`False`を返すことで担保されている——`combat_state_
contract.v0.4.md`への追加訂正は不要と判断した(既存記述で正確)。

## 6. ファイル・ハッシュ一覧

| ファイル | SHA256 | 備考 |
|---|---|---|
| `Combat/combat_state_snapshot.py` | `6a8be66bb710c098921950cb3c2ee237f622f695fd09b5613de88fb1e9669711` | `CreatureSnapshot`/`Pets`追加、分類ルール拡張 |
| `Combat/live_combat_session.py` | `056e1e9e976dfcfa7c93d3734d020a088078f7f835dd152b70b87e57f5daecb8` | 無変更(Phase 2A/2Bから継続) |
| `Combat/evaluation/online_eval/verify_snapshot_phase2b.py` | `c9bc2346098a0789a04559e70bc2014d2d54b13fba4930060aaae6dd4fe3ffee` | Pet-fix受け入れ検証追加 |
| `Common/contracts/combat_state_contract.v0.4.md` | `ddb632358cb02ba4755b4e064e4838882fb77e172974bd9953c5e817df2aec88` | Emulator担当により訂正済み、RL側未編集(確認のみ) |
| `Common/contracts/rl_phase2b_pet_capture_fix_source_manifest_20260726.json` | (本ファイル自身) | 新規、4節の新規発見も記録 |

## 7. 結論

### Phase 2Bを閉じられるか

**条件付き。** 指示された実施事項(Pet Capture取り込み・Schema/Power分類/
canonical serialization/参照validator確認)は全て完了し、Scenario 6546-21の
既知`capture_bug`(Osty/DieForYouPower)は解消を確認した。**しかし4節の
新規発見(新DLLでの非決定的Quiescent Decision Boundary違反)は、Pet
Capture修正そのものとは無関係でありながら、Phase 1の中核契約に関わる
未解決事項であるため、これを解決せずにPhase 2Bを無条件に「閉じられる」
とは判断できない。** Pet Capture修正自体の受け入れは完了と報告するが、
4節の事象は別途Emulator担当による調査(可能であれば`test_scenario_v2.py`
単独→`test_choice_semantics.py`単独→連続実行、の順での再現性確認、
および該当コミット前後でのbisection)を要請する。

### Snapshot Capture側の既知`capture_bug`が残っていないか

**Pet Capture修正のスコープでは残っていない。** Osty/DieForYouPowerは
解消済み(3節)。SOUL×3は`capture_bug`ではなく確認済みの
`source_live_state_inconsistency`(Phase 3A対象、修正見送りは契約書
既定路線通り)。ただし4節の新規事象はCapture側ではなくLive実行側
(Step())の問題であり、「capture_bug」という分類対象そのものの外側にある
——別カテゴリの新規オープン項目として扱うべきと考える。

### Phase 3Aへ進めるか

**進めない。** 理由は2点:

1. 契約書§14既定のPhase 3Aゲート(`FUNERARY_MASK`由来SOUL×3の扱い方針
   決定——`ResetFromScenario`再設計かRestore側フィルタリングか)は
   今回も未決定のまま(指示のスコープ外)。
2. **4節の新規発見が未解決。** Phase 3A(CombatHistory復元関連)は
   Quiescent Decision Boundaryが安定して信頼できることを前提とする
   ため、この非決定性の原因が判明・解消されるまでは、Phase 3A着手の
   前提条件を満たしていないと判断する。

`RestoreSnapshot`本体・Heuristic・beam-search・Importedゲームロジックには
一切着手していない。ここで報告のため停止し、Emulator担当・監督者の判断
(特に4節の非決定性調査の要否・実施方法)を待つ。
