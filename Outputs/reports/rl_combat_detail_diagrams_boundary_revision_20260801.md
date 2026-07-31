# RL担当 作業報告 — Combat詳細図の境界・Instance再利用方針の修正 (2026-08-01)

対象: `C:\STS2_Mermaid\`配下のCombat詳細図7点のうち5点(指示された必須対象)＋関連する用語不整合1点を修正。

RL HEAD(この報告のcommit直前): `df086bb857a7c3925b32e6da92ef83298c3f5a91`

**本ラウンドはMermaid図の修正と報告のみ。ランタイムコードの変更は一切行っていない。**

## 修正の背景(指示より)

Restore・Sequence Prefix再生の再構築コストが探索時間の主要なボトルネックであるという実測知見に基づき、
「すでに目的の状態へ到達しているGameInstanceが存在する場合は正確性を損なわない範囲でそのlive stateを再利用する」
という方針を、前回図の「Body Worker事前確保」モデルから「State-Holding Worker」モデルへ改めた。
あわせて、Pending状態を独立したStable相当の状態として扱わない(Capture/Restore対象外)ことを
より明確に図へ反映し、ActionContinuationも1 Stepごとに制御をSearch Coordinatorへ返す設計へ修正した。

## 主要な概念変更

### 1. 「Body Worker事前確保」→「State-Holding Worker」

前回図では`BODY_ASSIGN`ノードが「ラウンド開始時に確保済みのBody Workerへ割当」という、
Search Coordinator側が能動的にWorkerを予約する設計だった。これを撤回し、次のモデルへ置き換えた。

> Worker AでCardをStep → Pending Choiceが発生 → Worker AのGameInstanceがそのPending状態を保持
> → 最上位ChoiceをWorker AでそのままStep → その他の兄弟ChoiceだけをSub Branchとして別Workerへ送る

State-Holding Workerは「事前に確保されるもの」ではなく、「直前にStepを実行し、結果としてある
Decision Contextへ到達したWorker」として事後的に成立する。この再利用は、その分岐地点から
次の評価境界(Stable／Terminal)までに限定される点は従来の「ラウンド限定」という設計意図を
維持しているが、単位を「ラウンド」という大きな括りから「個々のDecision Context」へ改めたことで、
Beam幅>1で複数のDecision Contextが並行して生存する場合でも、それぞれが独立にState-Holding Workerを
持てる設計になった(以前は暗黙に「Bodyは1系列のみ」という読み方もできたため、この点を明確化した)。

### 2. Decision Contextの正式定義

指示された構成要素をそのまま図の共通語彙として導入した(`mermaid_combat_snapshot_replay_detail.mermaid`)。

```
Decision Context
- Stable Root Snapshot
- Stable Rootから現在地点までのSequence Prefix
- 現在のBoundary
- 現在のObservation
- 現在のLegalActions
- State-Holding Worker（存在する場合のみ）
```

Stable地点: `Root Snapshot = 現在地点のStable Snapshot` / `Sequence Prefix = 空`
Pending地点: `Root Snapshot = 直前のStable Snapshot` / `Sequence Prefix = Stableから現在Pendingまでの実行済みAction列`

この定義を全5図で共通して参照する形に揃えた。「ラウンド」という語は5図から削除し
(`mermaid_combat_fault_worker_detail.mermaid`の関連箇所も含め)、代わりに「Decision Context」
「評価境界(Stable／Terminal)」を単位として記述した。

### 3. Pending再生成処理の削除

前回図にあった`REGEN_PENDING`(独立した「Pending候補列挙・再生成」ノード)を削除した。
指示の通り、Pendingは「Stable Root SnapshotからのSequence Prefix再生の自然な結果」として
到達するものであり、Prefix最後のActionをStepした結果がそのままPendingであることを
`mermaid_combat_snapshot_replay_detail.mermaid`の`VERIFY_BOUNDARY`ノードで明示した
(想定Boundary・Choice種別との一致検証のみを行い、独立生成は行わない)。

### 4. ActionContinuationの1 Step単位化

前回図は「次の評価境界まで進行」という粒度でBranch Worker内の処理をブラックボックス化していたが、
今回は「1 Step実行 → 即座にBoundary/Observation/LegalActionsを返す」という粒度に修正した
(`mermaid_combat_branch_scheduler_detail.mermaid`の`HOLDER_STEP`/`BOOTSTRAP_STEP`/`SUB_RESTORE`の
いずれも1 Stepのみを行い、`RETURN_CONTROL`で即座に分岐判定へ戻る)。Pendingであれば
`CONTEXT_UPDATE`を経て`mermaid_combat_candidate_pipeline_detail.mermaid`のCLASSIFYへ毎回再突入する。
これによりActionContinuation内の個々のChoiceも、他のPrimary Action/Targetと完全に同じ
分類・事前評価・枝刈りパイプラインへ露出される。

### 5. Main側の連続実行ループ

`mermaid_combat_main_loop_detail.mermaid`の`EXEC_LOOP`を、採用Sequenceを同一live Main Instance上で
1手ずつ「再解決→Step→Boundary確認→(Sequence残りがあれば継続)」というループとして再構成した。
Sequence途中でPendingになること自体を正常な途中状態として扱い、破棄・再同期を行うのは
指示にある4条件(再解決不能／Boundary不一致／予定外のTerminal・Fault／Main状態が探索前提と不一致。
Sequence完了時点でなおPendingが残る場合もBoundary不一致の一種としてここに含めた)に限定した。
`mermaid_combat_commit_detail.mermaid`側もこのEXEC_LOOPを参照する形に簡素化し、
Commit図内でSequence実行の詳細を重複記述しないようにした。

### 6. RNG仮説比較: 標準比較(共通仮説集合)と独立集合の区別

`mermaid_combat_rng_hypothesis_detail.mermaid`に、指示にあった
「Action A × H1,H2,H3 / Action B × H1,H2,H3 / Action C × H1,H2,H3」という共通仮説集合による
グリッド比較を標準経路(`STANDARD_SET`→`GRID`)として明示し、独立集合による追加評価
(`INDEPENDENT_SET`)は標準比較を置き換えない任意の頑健性確認として別経路(`COMPARE_IND`)に分離した。
方式B(Restore前にSnapshot JSONへ組み込む)は維持し、Restore後の再シードは行わない旨を再確認した。

## その他の変更

- `mermaid_combat_commit_detail.mermaid`に`WORKER_STILL_ALIVE`分岐を追加し、選択したDecision Context
  のState-Holding Workerが評価境界を跨いでも生存しており、かつそのlive状態がChild Snapshotと一致する
  場合は、次のDecision ContextについてもRestoreを行わずそのWorkerを再利用できることを明示した。
  これは指示に直接の記述はないが、「既に目的の状態へ到達しているGameInstanceがあれば再利用する」という
  背景方針の自然な帰結として追加した設計注記であり、義務ではなく可能な最適化として記載した
  (§未解決事項に判断保留として記載)。
- `mermaid_combat_candidate_pipeline_detail.mermaid`の「Body候補／Sub候補」という表現を
  「継続候補(State-Holding Workerが存在すればそのままStep)／Sub Branch候補」に修正し、
  他図との用語を統一した。
- `mermaid_combat_fault_worker_detail.mermaid`の「ラウンド」表現(3箇所)を
  「同一Decision Context系列」「この評価境界」へ置換した(必須対象外だが用語不整合のため修正)。

## 未解決事項・判断保留

1. **評価境界を跨いだState-Holding Worker再利用(`WORKER_STILL_ALIVE`)の要否**: 今回`mermaid_combat_commit_detail.mermaid`
   に追加したが、これは背景方針からの類推であり指示に明示された要件ではない。実装時に
   「そもそも評価境界を跨いでWorkerを生存させ続ける運用が現実的か(Worker Poolのサイズ制約等)」を
   別途判断する必要がある。
2. **Decision Context系列内のContinuation深さ上限の具体値**: 前回報告から継続する未決事項。今回の
   用語修正(ラウンド→Decision Context系列)に伴い、上限の単位も「Decision Context系列内の連鎖選択回数」
   に読み替えたが、具体的な数値は未決定のまま。
3. **カスケードコスト対応方針**: 前回同様、現状(常にStable Root Snapshotから再生)を維持する設計を
   図示している。指示の通りPending Snapshot等への設計変更は行わず、実装後の計測結果に基づいて
   追加最適化を判断する方針を維持した。
4. **必須受け入れテストの具体的な比較項目**: `mermaid_combat_branch_scheduler_detail.mermaid`の
   `NOTE_DETERMINISM`に、Boundary・Observation・LegalActions・RNG状態・Child Snapshotの
   契約上重要な項目の一致を必須受け入れテストとして明記した。「契約上重要な項目」の具体的な
   フィールド一覧(既存のRestore契約v0.8のどの項目に対応するか)は未確定であり、実装設計時に
   決定が必要。

## 停止

指示の通り、上記6図の修正とこの報告のみを行い、ランタイムコードの変更は行っていない。
`git status`でSTS2_RLリポジトリの作業ツリーはこの報告ファイルの追加のみであることを確認済み。
