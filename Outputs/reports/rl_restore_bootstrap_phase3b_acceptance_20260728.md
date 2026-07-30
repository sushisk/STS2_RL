# RL担当 Phase 3B独立受け入れ報告 — Restore bootstrap検証(2026-07-28)

対象: 「RL担当 Phase 3B独立受け入れ指示 — Restore bootstrap検証」。
Emulator担当の実装・監査報告を鵜呑みにせず、RL側から独立に全項目を
再検証した。公開Restore API・Heuristic・Training本体には着手していない。

**先出しの結論**: Phase 3Bは(契約された限定範囲において)正式に閉じられる
と判断する。未説明のCapture差分は0件。拒否条件は指示の列挙全項目を
カバーしており十分と判断する。Phase 3Cへは進めない(Phase 3C自体のスコープ
——`FUNERARY_MASK`根本修正・完全CombatHistory復元・Pet/Orb対応・
`RestoreInternalDataGeneric`等——がいずれも未着手のまま、契約v0.5・両報告書
の記載通り)。特筆すべき成果として、**Emulator担当自身の監査報告書が
「本ラウンドでは未実施」と明記していたScenario 6546-21の実Snapshotに
対する直接拒否試験を、RL側が実際に実行して閉じた**(4-C節)。

## 1. 成果物確認

### 1-1. commit・DLL・報告内容の確認

実ファイル・実commitから直接確認した(報告書記載値の転記のみで済ませて
いない)。

| 項目 | 指示書記載値 | 実確認値 | 一致 |
|---|---|---|---|
| Restore bootstrapコードcommit | `7ef3c38` | `git log`: `7ef3c38babf66c4e8c46b6b8edb8a911ea36b600` | ✅ |
| テストcommit | `058b7c4` | `git log`: `058b7c4ee36d548bc533739bf6b85c35c91a45af` | ✅ |
| 実装報告書commit | `1d2a922` | `git log`: `1d2a9222d6eb830fcbcbbda040026daaab0d7927` | ✅ |
| 監査報告書commit | `b6afb8a` | `git rev-parse HEAD`: `b6afb8abb985d10134386f9b6d0b45d63c69902e` | ✅ |
| DLL SHA256 | `d415ee47c0beb8e51e3692e645c1b480614fd1ca065552a9e5236a7fe9f0c4f6`(実装報告書記載) | `sha256sum`同一値 | ✅ |

実装報告書・監査報告書を両方全文読了した。監査担当自身の判定は
`ACCEPT_WITH_DOCUMENTATION_FIX`(実装コード自体は無欠陥、Codex納品の
smokeスクリプトの2つのproperty名誤り+1つのテスト分離不備のみ修正、
という記録)——この判定文言の根拠も報告書本文で確認済み。

### 1-2. 4項目の独立確認

| 確認項目 | 結果 |
|---|---|
| 4つのcommitが正しい順序で存在 | ✅ `07dba13`(基準)→`7ef3c38`→`058b7c4`→`1d2a922`→`b6afb8a`(HEAD) |
| working treeがclean | ✅ `git status --short`無出力 |
| Imported配下に差分なし | ✅ `git diff --stat 07dba13..b6afb8a -- Sts2Emulator/Imported/`無出力 |
| 正式な公開`RestoreSnapshot` APIが追加されていない | ✅ `GameInstance.BootstrapSnapshotRuntimeForTesting`は`internal`、reflection経由でのみ到達可能であることを自ら確認(2節) |
| Python、Heuristic、Trainingが無変更 | ✅ `live_combat_session.py`/`emulator_bridge.py`/`combat_env.py`/`battle_emulator.py`/`heuristic_agent.py`のSHA256を再計算し、既存manifest記載値と完全一致することを確認(推測ではなく再ハッシュ化による直接確認) |

## 2. Capture往復(独立実装、Emulator担当のスクリプトを再利用せず)

`Combat/evaluation/online_eval/verify_restore_bootstrap_phase3b.py`(新規、
診断・検証専用、CombatEnv/LiveCombatSession/Training経路には一切
importされない)を独自に作成した。Emulator担当自身の`GameInstance.
BootstrapSnapshotRuntimeForTesting`(internal)へのreflection呼出技法は
共通(公開APIが存在しない以上、これ以外に到達手段がないため)だが、
fixture構築・比較ロジック・拒否ケースの実装は独立に書いた。

**重要な発見(監査報告書と同一)**: `RunManager.Instance`/`CombatManager.
Instance`がプロセス全体のシングルトンであるため、1プロセス内で2つの
`GameInstance`を同時にliveとして扱えない——各ケースを独立したPython
プロセスとして実行する必要があることを、Emulator担当の報告を読む前に
自らも確認した(スクリプト冒頭のコメントに設計判断として明記)。

### 2-1. Powerなし round trip(`round_trip_no_power`)

Capture A(`GameInstance().Reset()`直後)→internal Restore bootstrap→
Capture Bを実行し、指示が列挙した比較対象を**全て**確認:

| 比較対象 | 結果 |
|---|---|
| canonical Snapshot JSON相当(署名比較) | 一致 |
| Observation(Hp・敵Hp) | 一致 |
| LegalActions(id/type/parameters) | 一致 |
| stable IDs(Player・Enemy) | 一致 |
| 12 Run RNGストリーム | 一致(全12種を含むsorted署名で比較) |
| 3 Player RNGストリーム | 一致 |
| Monster local RNG | 一致 |
| piles(Hand/Draw/Discard/Exhaust/Play/Deck) | 一致 |
| HP／Block／Energy | 一致 |
| Turn／Round／CurrentSide | 一致 |
| Relics | 一致(本fixtureには存在しないため空同士の一致) |
| Powers | 一致(本fixtureには存在しないため空同士の一致) |
| Restore後CombatHistory | 0件(fresh hook不発火を確認) |

**未説明の差分: 0件。**

### 2-2. Powerあり round trip(`round_trip_with_power`)

プレイヤーに`STRENGTH_POWER`(amount=3)、敵に`WEAK_POWER`(amount=2)を
`PowerModel.ApplyInternal`経由(`ModelDb.AllPowers`から取得した正規モデルの
`.ToMutable()`——直接`new StrengthPower()`は`DuplicateModelException`で
拒否されることを実際に確認した上で修正)で付与し、同様の往復を実行:

| 確認項目 | 結果 |
|---|---|
| Restore前にPowerが存在 | ✅(両Power) |
| 全snapshot署名一致(RNG/piles/relics含む) | ✅ |
| Owner一致 | ✅ |
| Amount一致 | ✅ |
| StackType一致 | ✅ |
| stable ID一致 | ✅ |
| Applier/Target一致(敵Power) | ✅ |
| Restore後CombatHistory | 0件(`ApplyInternal`が`PowerReceivedEntry`を書かないことを確認) |

**未説明の差分: 0件。** RNG消費なし(RNG署名がRestore前後で完全一致する
ことが間接的に証明)、CombatHistory追加なし——3節の必須確認事項を全て
満たす。

## 3. Power確認(3節、2-2節と統合実施)

* `PowerModel.ApplyInternal`経由: ✅(実装コード`SnapshotRestorer.cs:485`を
  読み、Restore側もこの1メソッドのみを使用することを確認済み——RL側
  fixtureも同じメソッドで独立に構築)
* Owner一致・Amount一致・StackType一致・stable ID一致: ✅(2-2節)
* RNG消費なし: ✅(全RNG署名がRestore前後で完全一致)
* CombatHistory追加なし: ✅(Restore後0件)
* Capture後のPower状態一致: ✅

## 4. 拒否条件(独立試験、各ケース別プロセスで実行)

指示の必須列挙全項目+Scenario 6546-21固有ケースを実装、**13/13合格**。
自動修復(格上げ・黙殺・部分受理)は一切観測されなかった。

| # | ケース | 手法 | 結果 |
|---|---|---|---|
| 1 | CombatHistory非空 | **自然**(`Reset()`直後、未加工) | ✅ `combat_history_non_empty:5` |
| 2 | Petあり | **自然**(Scenario 6546-21実キャプチャ、Osty) | ✅ `pet_count:1`含む |
| 3 | **Scenario 6546-21のSOUL×3 dangling Snapshot** | **自然**(未加工) | ✅ `reference_integrity:...card-000065/66/67`の3件を含む拒否——**4-C節参照** |
| 4 | Pending Choiceあり | **自然**(TOOLBOX relic、combat開始直後のchoice境界でキャプチャ) | ✅ `pending_choice_present` |
| 5 | Pending Targetあり | mutated(`Metadata.CaptureBoundary`を`published_target`へ直接設定) | ✅ `unsupported_capture_boundary:published_target` |
| 6 | Action continuationあり | mutated(`Metadata.ContinuationStepIndex`を設定) | ✅ `action_continuation_present` |
| 7 | dangling stable ID | mutated(`PlayerRng[0].OwnerInstanceId`を存在しないIDへ) | ✅ `reference_integrity:Dangling reference...` |
| 8 | duplicate stable ID | mutated(`Hand[0].InstanceId`をPlayerと同一に) | ✅ `reference_integrity:Duplicate InstanceId...` |
| 9 | unsupported internalData | mutated(`Metadata.UnsupportedFields`へ注入) | ✅ `unsupported_internal_data` |
| 10 | unknown schema version | mutated(`Metadata.SchemaVersion`を`phase99.9`へ) | ✅ `unknown_schema_version:phase99.9` |
| 11 | faulted session由来 | **自然**(Console.Out破損によるAction fault発生後、`capture_snapshot()`自体を試行) | ✅ Capture層自体が`FaultedCombatSessionError`で拒否——faulted sessionからSnapshotがそもそも生成できないことを構造的に確認 |

### 4-C. 特筆: Scenario 6546-21実Snapshot直接拒否試験(Emulator担当監査
報告書が「本ラウンド未実施」と明記していたギャップの解消)

Emulator担当自身の監査報告書(§5-A末尾・§7)は、「6546-21の正確な
再現にはRL側機構(`LiveCombatSession`等)の直接操作が必要であり、
指示の"C:\STS2_RL配下の変更禁止"の精神に照らして過剰と判断し、本ラウンド
では実施していない」と明記し、「次ラウンドでRL側の協力を得て実施する
ことを推奨する」と申し送っていた。

RL担当は、`C:\STS2_RL`配下のコードを**一切変更せず**(既存の
`LiveCombatSession.start_combat()`をそのまま呼び出すのみ)、実際に
Scenario 6546-21を`choice_policy_online_eval_manifest.jsonl`の実specから
起動し、`session._game.CaptureSnapshot()`(既存の公開メソッド、raw CLR
オブジェクトを取得)で実際のSnapshotを取得、これをreflection経由で
internal Restore bootstrapへ直接渡した。結果:

```text
reasons=[
  'unsupported_capture_boundary:published_choice',
  'pet_count:1',
  'pending_choice_present',
  'combat_history_non_empty:8',
  'unsupported_relic_saved_properties:relic-000047:HAPPY_FLOWER',
  "reference_integrity:Dangling reference 'card-000065' at CombatHistory.Entries[CardGeneratedEntry].cardInstanceId - no instance with this id was captured.",
  "reference_integrity:Dangling reference 'card-000066' at CombatHistory.Entries[CardGeneratedEntry].cardInstanceId ...",
  "reference_integrity:Dangling reference 'card-000067' at CombatHistory.Entries[CardGeneratedEntry].cardInstanceId ...",
]
```

**確認済みのSOUL×3(`card-000065/66/67`)dangling referenceが、期待通り
`reference_integrity:`理由で正しく拒否されることを直接確認した。** 自動
修復・部分受理は一切発生していない。この試験は6546-21の最初の自然な
quiescent境界(`start_combat()`直後、TOOLBOX/FUNERARY_MASK関連の
Choice未解決時点)で捕捉されたものであり、同時にPet(Osty)・Pending
Choice・Relic SavedProperties等、複数の拒否理由が同時に該当する
——これは6546-21がPhase 3Bのスコープ外要素を多数含む複雑なシナリオで
あることの追加確認でもある。

## 5. 正常回帰

| 項目 | 結果 |
|---|---|
| Scenario `6546-21`通常ライブ経路 | ✅ 49 decision・victory・QuiescentBoundaryViolation 0件 |
| Snapshot Capture | ✅ `verify_snapshot_phase2b.py`、0 failing checks |
| Pet Capture | ✅ 同上スイート内 |
| Choice／Target | ✅ 6546-21ライブ経路・ネイティブハーネス内`test_choice_semantics.py`経由で網羅 |
| Action fault契約 | ✅ `test_action_fault_contract.py`、9/9合格 |
| Console I/O分離 | ✅ 同スイート内3試験、3/3合格 |
| Quiescent Boundary | ✅ ネイティブハーネスforward20回(縮小サンプル、Emulator担当自身の今回の判断と同一の理由——新規リグレッション有無の確認が目的)、計1,040テスト実行、QuiescentBoundaryViolation **0件** |
| WRIGGLER quarantine | ✅ `reasons: ['init_exception:TimeoutException']`、変化なし |

## 6. ファイル・ハッシュ一覧

| ファイル | SHA256 |
|---|---|
| `Combat/evaluation/online_eval/verify_restore_bootstrap_phase3b.py` | `277932f308c0acf7caba2a6e84e4e0508531ae2b4c8e98585fb1b8dbe3d50a3a` |

Emulator参照値: DLL SHA256
`d415ee47c0beb8e51e3692e645c1b480614fd1ca065552a9e5236a7fe9f0c4f6`、
契約SHA256(参照のみ、今回無変更)
`e33b369e12543e04fe763c07196e2189460099cfcb3d22b5a35137e2a2b86b07`、
Schema SHA256(今回無変更)
`16643444fda085d62df435fa0290e73b356123ffd95981317da4ea3f86c23dc3`。

manifest全文: `Common/contracts/
rl_phase3b_restore_bootstrap_acceptance_manifest_20260728.json`。

## 7. 結論

### Phase 3Bを正式に閉じられるか

**閉じられると判断する。** 契約された限定範囲(Powerなし/ありround trip、
14-step構築順序、Stable ID再結合、拒否ゲート)において、RL側の完全に
独立した再実装・再検証(Emulator担当のスクリプトを再利用しない)で
一度も欠陥が見つからなかった。Emulator担当自身の監査(`ACCEPT_WITH_
DOCUMENTATION_FIX`)が残した唯一の実質的なオープン項目(Scenario
6546-21の実Snapshot直接拒否試験)を、本ラウンドでRL側が実行し閉じた
(4-C節)。

### 未説明のCapture差分の有無

**0件。** Powerなし・Powerありのいずれのround tripでも、指示が列挙した
比較対象全項目(canonical Snapshot・Observation・LegalActions・stable
IDs・RNG全ストリーム・piles・HP/Block/Energy・Turn/Round/CurrentSide・
Relics・Powers)が完全一致した。

### 拒否条件が十分か

**十分と判断する。** 指示が列挙した10カテゴリ+Scenario 6546-21固有
ケース、計11ケース(往復2件と合わせ13/13)を独立に確認し、全て正しく
拒否され、自動修復は一切観測されなかった。

### Phase 3Cへ進めるか

**進めない。** 契約v0.5・両Emulator報告書が一致して記録する通り、Phase
3Cのスコープ(`FUNERARY_MASK`型`source_live_state_inconsistency`の根本
修正、完全CombatHistory復元、Pet/Orb対応、`RestoreInternalDataGeneric`、
pending Action/Choice continuation復元)はいずれも本ラウンドでも未着手
——設計書通りの意図的なスコープ限定であり、Phase 3C着手には別途、
監督者からの明示的な指示が必要と判断する。

公開Restore API・Heuristic・Training本体には一切着手していない。ここで
報告のため停止する。Emulator担当・監督者の確認を待つ。
