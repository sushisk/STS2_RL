# RL担当レビュー報告 — Combat完成形フロー(Worker Pool方式)の実現可能性確認 (2026-07-31)

対象: 「RL担当レビュー依頼 — Combat完成形フローの実現可能性確認」。**本ラウンドはレビューと
文書化のみ。runtimeコードは一切変更していない**(`git status --short`で無変更を確認済み、
本報告書ファイル追加のみ)。

## 0. 調査branch・HEAD・参照資料

- 調査branch: `main`
- 調査開始時点HEAD: `d3c0b38`(直前ラウンドの戦闘RNG調査報告commit)
- 参照したCombat図: `C:\STS2_Mermaid\mermaid_combat_target_worker_pool.mermaid`(レビュー対象)
- 参照した関連図(接続確認用、変更なし): `C:\STS2_Mermaid\mermaid_combat.mermaid`
  (現行実装準拠版)、`C:\STS2_Mermaid\mermaid_wholerun.mermaid`、
  `C:\STS2_Mermaid\mermaid_eventroom.mermaid`
- 使用DLL SHA256(調査開始時点、`C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\
  Sts2Emulator.dll`): `c0ffd64365cfaba9659bc5bf710470fcd637eb4d45ecd2dc71552b286018e40f`
  (前回の戦闘RNG調査報告と同一——Emulator側は本ラウンド中に更新されていない)
- 前提として、直前ラウンドの調査結果(`rl_combat_rng_flow_investigation_20260731.md`)と
  本セッション内の「複数GameInstance調査」(未ファイル化、チャット内回答)を踏まえている。

## 1. Main Stream

### 1-A. 探索中のMain GameInstance不変性

**現行実装では不可能、target設計では実現可能。**

現行実装(`mermaid_combat.mermaid`が示す通り)は`shared_game_instance()`という単一
process内singleton(`emulator_bridge.py:100-106`)を使い、Beam Search/Lookaheadの各候補も
`BattleEmulator.apply_action()`経由で**同じ**Instanceを`ResetFromScenario`で上書きする
(`battle_emulator.py:881-941`)——Mainはこの間、確実に変更される。

Target設計(`ENTRY_RUN`→`MAIN_STREAM`と`WORKER_POOL`が別subgraph、`RULE_PROCESS`:
「1 process = 1 GameInstance」)がこれを解決できる理由は、単なる設計上の分離ではなく、
実コード根拠がある: `GameInstance.Instance`(`GameInstance.cs:247`)、`CombatManager.Instance`
(`CombatManager.cs:90`)、`RunManager.Instance`(`RunManager.cs:78`)はいずれもprocess全体で
共有されるstatic singletonであり、`GameInstance`のコンストラクタ(`GameInstance.cs:236-246`)は
既存の`Instance`が非nullかどうかを一切チェックせず無条件に上書きする——**同一process内では
複数のGameInstanceを安全に共存させること自体が構造的に不可能**(本セッション内の別調査で
確認済み)。裏を返せば、**OSプロセスを分ければ各processのstatic状態は完全に独立するため、
Main用processとBranch Worker用processを物理的に分離すれば、探索がMainに一切触れない設計は
成立する**。`SnapshotRestoreFailedException.cs`の doc comment
(`"The previous session cannot be rolled back because the imported runtime is
process-singleton state."`)も同じ制約を裏付けている。

### 1-B. 学習用Combat再現を専用processへ割り当てる構成

**適切。** 現行の`generate_heuristic_trajectories.py`は既に「1回の実行 = 1 Pythonプロセス
= 1 GameInstance」という構成で動いており(`preflight_validate()`→
`CombatEnv.adopt_state()`→`LiveCombatSession.resume_from()`)、target設計の
`ENTRY_REPLAY`(専用Replay Root Process割り当て)はこの既存パターンをそのまま踏襲している。
`REPLAY_VALIDATE`(`ValidateRestoreSnapshotJson`)→`REPLAY_RESTORE`
(`RestoreSnapshotJson`)という順序も、Phase 3C.4.1で確立済みの「JSON検証→deserialize→
preflight→(Restoreの場合のみ)teardown」という順序(`combat_state_snapshot_dto.v0.8.md`
§8.1)と一致している。

### 1-C. Event戦闘sampleを専用processで実行する構成とEvent図との接続

**設計としては`mermaid_eventroom.mermaid`のCOMBAT_SAMPLING(「複数のランダムRNGで戦闘を
評価」)と整合するが、両者とも現状は未実装。** 前回の調査
(`rl_combat_rng_flow_investigation_20260731.md`§2-5)で確認した通り、`Combat/`配下に
Event選択肢評価用のsample戦闘を実行するコード経路は現状**存在しない**——この事実は本ラウンドの
調査でも変わっていない。`ENTRY_EVENT`(`SAMPLE_ALLOCATE`)は、Event図のCOMBAT_SAMPLINGが
将来実装される際の受け皿として設計上は矛盾しないが、**「接続可能かどうか」の検証は現時点では
両側とも実コードが無いため、設計レベルの整合性確認に留まる**。

なお、`SAMPLE_CONTEXT --> MAIN_READY`という接続は、「Main」が特定の1個のグローバル
Instanceではなく、**「この戦闘進行を担当する正本」という相対的な役割**であることを意味する
——実Runの戦闘・再現用戦闘・Event sample戦闘はそれぞれ独立したprocessで独立した「Main」を
持ち、必要なら各自が`SEARCH_COORDINATOR`/`WORKER_POOL`を利用できる、という設計は一貫している。

### 1-D. Main不変性確認用signature

図が提案する`CombatSessionId・StepIndex・DecisionFrame・LegalActions・Snapshot hash`は
いずれも取得可能である:

- `CombatSessionId`/`StepIndex`は`GameObservation`の実フィールド
  (`GameObservation.cs:13,21`)——現行の`_is_still_current()`
  (`live_combat_session.py:607-615`)が既に安価な確認手段としてこの2つだけを使っている。
- `DecisionFrame`はC#側のDTO型ではなく、RL Python側の概念(`battle_emulator.py`の
  `DecisionFrame`dataclass、`combat_session_id`+`step_index`+`continuation_step_index`)
  ——`CombatSessionId`/`StepIndex`から導出可能。
- `LegalActions`は`GetLegalActions()`の結果をそのまま比較材料にできる。
- `Snapshot hash`は`CaptureSnapshot()`→canonical JSON→sha256という、この関与全体で
  ずっと使われてきた`_snapshot_sig`系パターンで取得可能。

**性能上の助言**: `CombatSessionId`/`StepIndex`比較は`GetObservation()`1回だけで済む
安価な確認であるのに対し、Snapshot hash比較は`CaptureSnapshot()`(CombatHistory全体を
含む)を要するため相対的に高価——探索batchごとに毎回Snapshot hashまで取るのではなく、
`CombatSessionId`/`StepIndex`を主たる軽量チェックとし、Snapshot hashは定期的な
診断/監査用途に限定することを推奨する(詳細は§9性能リスク参照)。

## 2. Snapshot API

図中の利用方法は、いずれも現行APIと一致することを確認した(`GameInstance.cs`を直接
grepして確認、行番号は本ラウンド時点):

| 図中の呼称 | 現行の正式名称 | 確認 |
|---|---|---|
| `CaptureSnapshot` | `GameInstance.CaptureSnapshot()`(`GameInstance.cs:3000`) | 一致 |
| Snapshot DTOのcanonical JSON化 | RL側`combat_state_snapshot.canonical_json()` | 一致(RL側関数、Emulator APIではない) |
| `ValidateRestoreSnapshotJson` | `GameInstance.ValidateRestoreSnapshotJson(string)`(`GameInstance.cs:585`) | 一致 |
| `RestoreSnapshotJson` | `GameInstance.RestoreSnapshotJson(string)`(`GameInstance.cs:565`) | 一致 |
| Restore後の`Observation` | `RestoreResult.Observation`(`GameObservation`型) | 一致 |
| `DecisionFrame` | C# APIには存在しない概念——RL Python側dataclass(`battle_emulator.py`) | **要注記**、§1-D参照 |
| `LegalActions` | `RestoreResult.LegalActions`(`LegalAction[]`型)/`GameInstance.GetLegalActions()` | 一致 |
| `Step` | `GameInstance.Step(int actionId)`(`GameInstance.cs:2828`) | 一致 |

`RestoreResult`(`Sts2Emulator/Dto/RestoreResult.cs`)は`Seed/SeedText/CharacterId/
Ascension/Observation/LegalActions/Metadata/RestoreCompleteness/RestoredFromSnapshotId`
を持つ——`Observation`と`LegalActions`はここから直接取得できる。

**修正提案**: 図中で「DecisionFrame」をC#側APIの戻り値であるかのように暗黙に扱っている
箇所があれば、「`GameObservation.CombatSessionId`/`StepIndex`からRL側で構築する概念」
であることを明示した方が、実装時の誤解を防げる(Worker側でC#ネイティブ実装する場合、
`DecisionFrame`という型を新設するかどうかも設計判断が必要になる——§8参照)。

## 3. C# Branch Worker

### 3-A. C#常駐processとして実装可能か

**可能。既存の前例あり。** `Sts2Emulator.Cli`(`Sts2Emulator.Cli/Program.cs`、946行)は
pythonnetを一切経由せず、pure C#で`new GameInstance()`を直接構築し、`Console.ReadLine()`
ループでコマンドを受け付ける常駐processとして既に動作している(`RunInteractiveSession`、
`Program.cs:256-`)。ただし既存モードは**人間向けの対話的テキストコマンド**であり、
JSON-RPC的なWorkItemプロトコルではない——新規に「非対話・JSON入出力」モードを追加する
作業が必要(§8参照)。pythonnet/CoreCLRホスティング特有の問題
(`AppContext.BaseDirectory`が空文字列になる件、contract v0.6§9-Bで既に解決済みではあるが)
は、そもそもpure C# nativeプロセスでは**発生しようがない**——pythonnet越しにCoreCLRを
ロードする現行のPython側と異なり、Workerは通常の.NET実行ファイルとしてそのまま起動する
ため、この種のホスティング起因の問題を構造的に回避できる。

### 3-B. processごとにGameInstanceを1個だけ保持できるか

**可能。** §1-Aで確認した通り、複数GameInstanceの共存自体が不可能なので、「1 process =
1 GameInstance」は望む・望まないに関わらず必然の制約であり、Worker側で強制する必要すら
ない(2個目を作ろうとした場合は`Instance`が上書きされるだけで、例外にはならない点は
実装上の注意点として残る——Workerプロセス自身が誤って2個目の`GameInstance`を作らない
よう、単一のstatic/1回限りの構築を徹底するコーディング規約が必要)。

### 3-C. CoreCLR、ModelDb、GameInstanceの初期化を起動時の1回に限定できるか

**可能、かつ既に自然にそうなっている。** `GameInstance`のコンストラクタは`EnsureTestMode()`
(`GameInstance.cs:5130-5149`)を呼び、この関数はstatic bool `_testModeInitialized`
(`GameInstance.cs:154`)でガードされている——`ModelDbBootstrap.EnsureInitialized()`/
`ModManager.Initialize()`/`SafeConsoleOutput.Install()`はprocess全体で1回しか走らない。
さらにpure C# nativeプロセスの場合、pythonnet越しのCoreCLRロード手順自体が不要
(コンパイル済み.NET実行ファイルとして直接起動するため)——Python+pythonnet構成より
起動オーバーヘッドが小さくなる可能性が高い。

### 3-D. 複数要求のたびに同じGameInstanceへSnapshot Restoreできるか

**可能。既に確立済みの前提。** `RestoreSnapshot`/`RestoreSnapshotJson`は「同一の
既存GameInstanceに対して繰り返し呼ぶ」ことを前提に設計されている
(`issues_new_combat_session=true`——毎回新しいcombatSessionIdを発行、
`preserves_stable_ids=true`)。この関与全体を通じて、複数回の独立したRestoreを同一
Instance上で繰り返すテスト(`test_restore_step_determinism_reselects_fresh_action`、
`test_pet_restore_step_determinism_reselects_fresh_action`、
`test_restore_step_determinism_with_non_empty_combat_history`)が既に実行・合格しており、
「Workerが多数のWorkItemを生涯にわたって処理し続ける」という使い方は既存の検証範囲と
整合する。

### 3-E. Restore失敗後もWorkerを安全に再利用できるか

失敗の種類によって回答が異なるため、3段階に分けて報告する:

1. **入力検証拒否**(`ValidateRestoreSnapshotJson`/`RestoreSnapshotJson`の
   teardown前拒否、`RejectionCodes`)——**安全にそのまま再利用可能**。契約上、
   現在のcombat session/runtimeは一切変更されない(`combat_state_contract.v0.8.md`§3)。
2. **teardown後の構築失敗**(`SnapshotRestoreFailedException`、`_sessionFaulted=true`)
   ——**processを再起動せずに再利用可能**、ただし次に成功する`RestoreSnapshot`/
   `RestoreSnapshotJson`/`Reset*`を1回発行するまでfaultは解除されない
   (`combat_state_contract.v0.8.md`§4/§5、既存の`test_post_teardown_failure_faults_
   and_all_recovery_paths_clear`で確認済みの契約)。つまりWorker側は「次のWorkItemの
   Restoreが成功すれば自動的に復帰する」——明示的なfault-clear処理は不要。
3. **真のprocess crash/hang**(未処理例外・デッドロック等)——これは1・2とは別の問題で、
   在来のfault契約の範囲外。processそのものの生死監視・再起動が必要(§7で詳述)。

**図への確認**: `mermaid_combat_target_worker_pool.mermaid`の`WORKER_DONE --> RECEIVE_TASK`
ループはこの1・2のケースを正しく表現している(`RETURN_REJECT`/`RETURN_FAULT`いずれも
`WORKER_DONE`を経て次のタスク受付へ戻る、processを再起動しない)——3のケースは別途
`WORKER_MANAGEMENT`subgraphが担当しており、責務の分離は適切。

## 4. IPC境界

図が列挙した全データはJSON化可能であり、CLRオブジェクトを直接渡す必要はない
——ただし一部は**新規に定義が必要**な形式である。

| データ | JSON化可否 | 備考 |
|---|---|---|
| Root Snapshot JSON | 可能・既存API | `CaptureSnapshotJson()`/`RestoreSnapshotJson()`がそのまま使える |
| Semantic LogicalActionSequence Prefix | 可能だが**未整備** | 現状「論理action識別」は`test_restore_snapshot_phase3c1.py`内の`_first_logical_action`/`_find_logical_action`ヘルパー(`(action_type, card_id, target_type)`タプル)にのみ存在するad hocなテストコードであり、正式なスキーマとしてEmulator/RL間で合意されたものではない。**新規に定義が必要**(§8参照)。 |
| 探索設定(depth/width/branch上限等) | 可能 | 純粋なRL側パラメータ、Emulator APIと無関係 |
| 明示的な探索seed | 可能だが**設計上の空白がある** | 下記4-A参照 |
| 候補Observation | 可能・既存API | `GameObservation`は`System.Text.Json`で標準的にシリアライズ可能 |
| Child Snapshot JSON | 可能・既存API | `CaptureSnapshotJson()` |
| Terminal結果 | 可能・既存API | `Observation.IsTerminal`/`Outcome` |
| rejection／fault | 可能だが**新規シリアライズ実装が必要** | 下記4-B参照 |
| 診断情報 | 可能 | 形式はRL側で新規定義(既存の各種audit/verifyスクリプトの診断dict パターンを踏襲可能) |

### 4-A. 明示的な探索seedと厳密Restoreの整合性(設計上の空白)

`RestoreSnapshotJson`は捕捉時点の`RunRng`/`PlayerRng`/`MonsterRng`を**厳密に**復元する
——復元後の状態に対して「明示的な探索seed」を与えて意図的に分岐させる(例:
reshuffle結果の仮説サンプリング)ためのAPIは現状**存在しない**。現行のlegacy機構
(`ShuffleRngSeed`、`CombatScenario`経由)はこれとは別物で、`RestoreSnapshotJson`の
厳密復元とは独立した仕組みである。「Restoreで厳密再現した状態から、特定の1ストリームだけ
意図的に再seedして分岐評価する」というユースケースをtarget設計が想定しているなら、
Emulator側に新規APIが必要になる可能性がある——**この点は本レビューでは判断せず、
最終監督者への確認事項として報告する**(§13参照)。

### 4-B. rejection/faultのJSON化

`SnapshotRestoreRejectedException`(`Sts2Emulator/Api/SnapshotRestoreRejectedException.cs`)
は`Reasons: string[]`/`UnsupportedFieldPaths: string[]`を、
`SnapshotRestoreFailedException`(`.../SnapshotRestoreFailedException.cs`)は
`RestorePhase/CombatSessionId/SchemaVersion/ContractVersion/SnapshotId/
OriginalExceptionType/OriginalExceptionMessage`を持つ——いずれも単純な文字列/nullable
文字列のみで構成されており、JSON化自体は技術的に容易。ただし現状これらはC#側で
「投げられる例外」としてのみ存在し、**IPC応答用のJSON envelopeへ変換するコードは
存在しない**——Worker側でこれらの例外をcatchしてフィールドをJSON化する薄いラッパーの
新規実装が必要(既存フィールド構成をそのまま転記するだけなので、実装難度は低い)。

### 4-C. IPC方式の候補(今回は確定しない、指示通り)

| 方式 | 長所 | 短所 |
|---|---|---|
| stdin/stdout改行区切りJSON(常駐子process) | この関与全体で既に前例多数(`Sts2Emulator.Cli`の対話ループ、`codex exec --json`) | 1 processにつき1リクエストずつの逐次処理が基本、多重化するには独自にrequest idを付与する設計が必要。大きなJSON(CombatHistory込みSnapshot)がOSパイプのバッファ境界で分断されないよう、改行区切りに加えて長さprefix等のフレーミングも要検討 |
| named pipe / localソケット | 非同期・多重化がしやすい | 実装コストが高い、Windows/Linux差異への配慮が必要 |
| 軽量HTTP/gRPC | 標準的なツールで可観測性を得やすい | 常駐サービス化の運用コストが増える、1呼び出しあたりのlatencyがstdin/stdoutより大きくなりがち |

## 5. PythonとC#の責任分担

**大枠として妥当。** CLRオブジェクトを一切process境界外に出さない(RULE_IPC)という
制約と、「状態を変更する操作は全てC# Worker内で完結させる」という設計は一貫しており、
Python側は純粋なスコアリング・探索戦略(Beam幅/深さ管理、Heuristic/Model評価)に
専念できる——これはPython側で既に確立している評価ロジック(`state_evaluator.py`等)や、
将来的なモデル推論(PyTorch等)をPython側に残すという自然な分担でもある。

ただし2点、性能・実装コスト上の懸念を報告する:

### 5-A. Semantic Actionのframe-local action_id解決をC# Worker内で行う設計

現状この解決ロジック(`(action_type, card_id, target_type)`による論理一致)は
**RL Python側のテストコードにのみ存在**し(`_find_logical_action`、
`test_restore_snapshot_phase3c1.py`)、正式なスキーマや契約として文書化されたことは
一度もない。C# Worker側でこれを実装するには、(a) 「Semantic Action」の正式なJSON
スキーマをEmulator/RL間で合意し、(b) それをC#側で`LegalAction[]`と照合するresolverを
**新規実装**する必要がある——これは「移植」ではなく実質的に新規設計・新規実装である。

### 5-B. Batch同期(`BATCH_DONE`/`WAIT_RESULT`)の性能特性

現在の図は「batch内の全WorkItemが完了するまで待ってから一括評価する」という同期barrier
方式になっている。実装がシンプルになる利点はあるが、1個のWorkItem評価コストが
突出して重い場合(例: `generate_heuristic_trajectories.py`のdocstringが既に指摘している
「add-spawningエリート×大きいデッキで1decisionが8分超かかった」という実例)、
batch内の他Workerが完了を待つだけの遊休時間が発生しうる——ストラグラー問題。
初期実装としては許容範囲だが、深いBeam Search/大きいwidthで顕在化しうる点は
性能リスクとして記録する(§9参照)。

## 6. Pending処理

- **Root SnapshotとSequence PrefixからPendingを再生成できるか**——可能。厳密RNG復元
  (`RestoreSnapshotJson`)+論理識別によるprefix再実行という組み合わせは、既存の
  `test_restore_step_determinism_reselects_fresh_action`系テストが検証している
  「2回の独立Restore→同じ論理Action→同じ結果」という性質の直接の一般化であり、
  「stale action_idを再利用しない」という既存ルールを守る限り決定論的に成立する。
- **Workerが最新LegalActionsから候補を列挙できるか**——可能。`GetLegalActions()`が
  そのまま使える。
- **Pending候補を1操作ずつ遅延展開できるか**——可能。図の`ENUM_PENDING → RETURN_PENDING`
  (1操作延長したPrefixを持つ新WorkItemを返す)という設計は、`C:\STS2_RL\mermaid3.txt`
  (以前ユーザーから共有されたBeam Search下書き図)が既に同じ発想を示しており、設計として
  一貫している。
- **staleなaction_idをprocess境界で保持しない設計になっているか**——なっている。境界を
  越えるのは「Semantic LogicalActionSequence Prefix」(論理識別子ベース)のみであり、
  生の`action_id`整数をprocess境界越しに保持・再利用する設計にはなっていない——これは
  Phase 3C.1以来確立している「Restore後は毎回最新LegalActionsから論理的に選び直す」
  という既存ルールとも一致する。
- **複数段のPendingを同じ方式で扱えるか**——構造上は扱える(WorkItemの再帰的生成で
  自然に多段化する)。ただし**範囲に関する重要な確認事項がある**——§13参照。

### 6-A. 確認が必要な設計判断: 「Pending」の範囲

本セッション内の別調査(継続入力/ActionContinuationの詳細調査)で確認した通り、
現行実装ではPendingChoiceの**約96%がActionContinuationスコープ**であり
(`choice_policy_agent.py`のdocstringが根拠とする census値)、これらは現在
`LiveCombatSession.step()`内部の1ループ(`live_combat_session.py:701-717`、
最大50回)として**Step呼び出し1回の中で自動解決**されており、上位の意思決定ループには
一切露出しない。

Target設計の`BRANCH_BOUNDARY -->|Pending| ENUM_PENDING`が、この96%を占める
ActionContinuationスコープのPendingまで**個別に**Beam Search対象として展開する
つもりなのか、それとも現行同様「Worker内部でStepのcontinuationループとして
自動解決し、上位のWorkItem展開対象は真の`published_choice`/`published_target`
境界(まれ)に限定する」つもりなのかは、**図だけからは判別できない**。

前者(全Pendingを個別WorkItem化)を選ぶと、既に指摘されている「1 decisionの候補数が
手札サイズ×敵数のオーダーで膨れ上がる」という既存の性能問題に、選択肢の分岐までが
乗算されることになり、探索コストが劇的に増大するリスクがある。後者(Worker内部で
現行同様に自動解決し、真の上位Pending境界だけをWorkItem化)であれば、現行の性能特性を
大きく変えずに済む。

**この判断は方針の差であり、本レビューでは決定せず、最終監督者への確認事項として
報告する**(§13参照)。

## 7. Worker障害

| 障害種別 | 実装可能性 | 根拠 |
|---|---|---|
| task timeout | 可能 | Worker呼び出しをRL側でtimeout付きで待つのは通常のプロセス間呼び出しパターン、新規実装だが技術的障壁はない |
| process crash | 検知可能 | OSプロセスの終了コード/例外終了はRL側(親process)から監視できる標準的な仕組み |
| Worker再起動 | 可能 | §3で確認した通り、Worker起動シーケンス(CoreCLR/ModelDb/GameInstance初期化)は1回限りの決定的な手順であり、再現性のある再起動が可能 |
| WorkItem再試行 | 可能 | WorkItem自体がRoot Snapshot JSON+Prefixという不変な入力を持つため、別のWorkerへ再割当てするだけで再試行できる(状態を持ち越す必要がない) |
| Branch rejection | 可能 | §3-Eの通り、入力検証拒否は既存契約で安全に扱える |
| 全Worker失敗時の停止条件 | 要設計 | 技術的には「一定回数再試行後にsearchをFaultとして打ち切り、Directにfallbackするか完全に停止するか」という**方針**の問題——後述 |

**受入条件(Main StreamがWorker障害によって破壊されないこと)は、§1-Aで確認した
process分離が正しく実装される限り、構造的に満たされる**——MainとWorkerが別processで
あり続ける限り、Worker側のいかなる障害もMainのGameInstanceに物理的に影響しようがない。
ただし、これは「実装がRULE_MAIN(探索中にMain GameInstanceをRestore・Reset・Stepしない)
を一度も破らないこと」に依存する——例えば緊急fallbackとして「Workerが全滅したら
Mainの共有Instanceを一時的にbranch評価に流用する」といった近道を実装してしまうと、
現行の問題を再導入することになる。この点は実装時の設計原則として明記すべきである。

## 8. 現行実装との差分

| 現行の仕組み | 分類 | 備考 |
|---|---|---|
| `BattleEmulator.apply_action` | 部分廃止対象 | Beam Search/Lookaheadの候補評価用途としては廃止対象。Directや(§11で提案する)軽量Heuristicをin-process維持する場合は、その用途に限り存続しうる |
| `_restore`(`apply_action`の内部ヘルパー) | `apply_action`と同じ運命 | 1:1で連動 |
| `_resynchronize`/`_is_still_current` | 構造的に不要化 | Main専用processが探索から一切触れられなくなるため、「外部干渉の検出・復帰」という前提そのものが消える。ただし「不変性を検証する」という精神は`MAIN_SIGNATURE`/`VERIFY_MAIN`として図中に引き継がれている(§1-D) |
| 共有GameInstance上での候補評価 | 廃止対象 | Worker Poolの各Worker専用GameInstanceに置き換え |
| legacy `Seed`/`ShuffleRngSeed`近似復元 | 用途により異なる | 探索枝の近似復元としては廃止対象(`RestoreSnapshotJson`の厳密復元に置換)。**ただしエピソード開始時のScenario初期Seed設定(`build_scenario_from_spec`)は別用途であり、そのまま再利用可能**——最初の状態にはそもそも「復元元のRestore」が存在しないため |
| 現行Heuristic候補評価(`HeuristicAgent.choose_action_with_detail`) | 移植が必要 | スコアリングロジック自体はPython Evaluator(`SCORE_BATCH`)へ概ね転用できるが、候補生成・実行ループ(現行は`apply_action`直呼び出し)はWorkItem/Worker Poolプロトコルへ書き換える必要がある |
| 現行Beam Search/Lookahead展開(`beam_search.py`/`lookahead.py`) | 移植が必要 | Beam幅・深さ管理等の探索戦略ロジックは概念として再利用できるが、状態展開の実装はWorkItemベースへの全面書き換えが必要 |

### 8-A. Emulator側の追加実装が必要か

**必要。** `CaptureSnapshotJson`/`RestoreSnapshotJson`/`ValidateRestoreSnapshotJson`/
`GetObservation`/`GetLegalActions`/`Step`という個別APIは全て既存のまま使えるが、
以下は現状どこにも存在せず、新規実装が要る:

1. 非対話・JSON入出力の常駐Workerホストプログラム(`Sts2Emulator.Cli`の新規モード、
   または新規project)。
2. 「Semantic Action」の正式JSONスキーマと、それを`LegalAction[]`と照合するC#側
   resolver(§5-A)。
3. `SnapshotRestoreRejectedException`/`SnapshotRestoreFailedException`の構造化
   フィールドをIPC応答JSONへ変換する薄いシリアライザ(§4-B、実装難度は低い)。
4. (§4-Aの判断次第で)Restore後に特定RNGストリームだけ明示的に再seedする新規API。

### 8-B. RL側の追加実装

- WorkItem/Search Coordinatorの実装一式(`beam_search.py`/`lookahead.py`の後継、
  §8の「移植が必要」項目)。
- Worker Pool管理(起動・healthcheck・timeout・再起動・再試行ポリシー)。
- IPCクライアント側実装(§4-Cのいずれかの方式)。
- Semantic LogicalActionSequenceのPython側データ構造・シリアライズ(現行のad hoc
  ヘルパーを正式なモジュールへ格上げ)。
- Main不変性signatureの比較ロジック(§1-D)。

## 9. 主な性能リスク

1. **IPCラウンドトリップのオーバーヘッド**: 現行はin-process(同一OSプロセス内、
   関数呼び出しレベル)での`apply_action`呼び出しだが、target設計は候補ごとに
   別processとのIPCラウンドトリップを要する。個々の`ResetFromScenario`/`Step`自体の
   処理コストが既に大きい(この関与全体を通じ、既存smoke/回帰スイートの実行時間から
   推測される数百ms〜秒オーダー)ため、IPCオーバーヘッド自体はおそらく相対的に小さいと
   見込まれるが、**実測による検証が必要**——特にDirectや浅いHeuristic評価のように
   1decisionあたりの呼び出し回数が多い(手札×敵数)ケースで、IPCの累積コストが
   無視できない可能性がある。
2. **Batch同期のストラグラー問題**(§5-B)。
3. **Worker数倍のメモリ消費**: 各Workerが独立にCoreCLR/Emulator DLL/ModelDbキャッシュを
   保持するため、Worker pool sizeに比例してメモリを消費する。
4. **Snapshot hash比較の頻度**(§1-D)——毎batch実施すると探索全体のオーバーヘッドになる。

## 10. 主な決定性リスク

1. **明示的探索seedと厳密Restoreの未整理な組み合わせ**(§4-A)——「厳密復元した状態から
   意図的に分岐させる」ためのAPIが現状ないため、実装時に独自の(未監査の)RNG操作を
   持ち込んでしまうと、この関与全体が慎重に守ってきた「決定論的Restore」という不変条件を
   壊しかねない。
2. **Worker再試行時の再現性**: 同じWorkItemを別Workerへ再割当てする際、再試行が
   本当に決定論的な同一結果を返すかは、Worker起動シーケンス自体に非決定的要素
   (OSエントロピー由来のRNG初期化等、`LookaheadSearcher`のデフォルト`random.Random()`が
   既に持つ既知の懸念と同種)が紛れ込んでいないことに依存する——明示的seed受け渡しの
   徹底が必要。
3. **§6-Aの判断次第でのRNGストリーム消費の違い**: ActionContinuationをWorker内部で
   自動解決するか、個別WorkItemとして展開するかで、消費されるRNGストリームの経路が
   変わりうる——事前に明確化しておくべき。

## 11. より良い設計の提案(監督者確認事項)

指示に「より良い設計があればそれを主張してください」とあるため、レビューの過程で
気づいた1点を提案として記録する:

**Direct実行および浅いHeuristic評価は、当面Worker Pool化せず現行のin-process機構
(または現行のまま)を維持することを提案する。** 理由: 現行実装(`mermaid_combat.mermaid`)
では、Direct実行はRNG操作すら発生しない極めて軽量な経路であり、Worker Pool化による
IPCオーバーヘッドを負うメリットが薄い。浅いHeuristic(1段先読みのみ)についても、
候補数が手札×敵数のオーダーで多く、IPCラウンドトリップの累積コストが顕在化しやすい
——§9-1の性能リスクが最も先鋭化する箇所である。Beam Search(複数段の探索)にのみ
Worker Poolを適用し、Direct/浅いHeuristicは現行同様Main側で完結させる、という段階的な
移行の方が、リスクを抑えつつ設計の恩恵(Main不変性)を得られると考える。ただし、
これは実測データなしの推測に基づく提案であり、**最終的な採否は監督者の判断を仰ぐ**。

## 12. 最小実装単位・推奨する実装順

指示は「Worker Pool、IPC、Heuristic移行、Beam Search移行の実装にはまだ進まない」と
明記しているため、以下は**将来の実装着手時の参考として記録するのみ**であり、本ラウンドで
着手するものではない。

推奨する最小実装単位・順序(判断が分かれる可能性のある部分は§13で確認):

1. Semantic Action JSONスキーマの合意(Emulator/RL間、§5-A/§8-A-2)。
2. C# Branch Worker最小実装(1個のWorkItem: Restore→単純なStableへの1手評価→
   Child Snapshot返却のみ、Pending/エラー処理は後回し)。
3. Worker起動・IPC疎通の最小確認(1 Worker、1 WorkItem、往復のみ)。
4. Rejection/Fault処理の追加(§3-E/§4-B)。
5. Pending列挙・prefix延長WorkItem生成の追加(§6-Aの判断確定後)。
6. Worker Pool化(複数Worker、DISPATCH/COLLECT機構)。
7. Worker障害管理(timeout/crash検知/再起動/再試行、§7)。
8. Python Search Coordinator(Heuristic評価から着手、Beam Searchは最後)。
9. Main不変性signature比較の組み込み(§1-D)。

## 13. 最終監督者への確認事項(方針判断が必要)

1. **§6-A**: 「Pending」としてWorkItem展開するのは、真の上位決定境界(`published_choice`/
   `published_target`等)に限定するか、ActionContinuationスコープ(全Choice決定の
   約96%)まで含めるか。
2. **§4-A**: 厳密Restore後に特定RNGストリームだけ意図的に再seedするAPIが必要か
   ——必要であればEmulator側への新規API依頼が要る。
3. **§11**: Direct/浅いHeuristicをWorker Pool化の対象に含めるか、当面現行機構を
   維持するか。

## 14. 実装開始可能か

**Semantic Action JSONスキーマの合意と§13の3点の方針確定を条件に、実装着手は
可能と判断する。** 技術的な障壁(同一process内での複数GameInstance不可)は
プロセス分離という設計そのもので解消されており、既存API(`RestoreSnapshotJson`等)は
そのまま利用できる。Emulator側の新規実装(§8-A)はいずれも既存パターンの延長線上に
あり、技術的に困難な要素は見当たらない。ただし指示の停止条件(Worker Pool、IPC、
Heuristic移行、Beam Search移行への未着手)に従い、本ラウンドではここで停止する。

## 15. runtimeコード無変更・working tree clean

本ラウンドはレビューと文書化のみ——`Combat/`配下・Emulator側C#ソースのいずれも
変更していない。`git status --short`は本報告書ファイル(新規追加、これからcommit)以外
無変更であることを確認済み。
