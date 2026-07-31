# RL担当レビュー報告 — `mermaid_rough_combat.mermaid` (2026-08-01)

対象: `C:\STS2_Mermaid\mermaid_rough_combat.mermaid`。前提として以下を採用してレビューした
(指示通り):

- 仮説遷移は全てBranch Workerで行う(Mainは一切変更しない)。
- ActionContinuationも他の選択と同じく分類・事前評価してから上位候補のみ展開する
  (`rl_combat_target_worker_pool_review_20260731.md`§6-Aで確認事項として保留していた点の
  うち、この判断が採用されたものとして扱う)。
- RNG分岐は「Restore前に派生Snapshotへ置換する方式B」を採用する(同報告書§4-Aで
  保留していた点の解決策として、Restore後の再seed API新設ではなく、Restore前の
  Snapshot JSON側での差し替えを選ぶ)。

**本ラウンドはレビューのみ。`mermaid_rough_combat.mermaid`自体も含め、いかなるファイルも
編集していない。** 指示の「コード変更はせず停止」に従い、以下は全て報告のみである。

## 総評

大枠の設計方針(Main保持、Root Snapshot経由でのみ探索開始、採用Sequenceだけを
Mainへ適用)は前回レビューした`mermaid_combat_target_worker_pool.mermaid`と整合しており、
Classify→事前評価→Pruneという新しい2段階評価の導入は、ActionContinuationの高頻度
(全Choice決定の約96%)を扱う上で理にかなった設計だと判断する。ただし、**実装前に
埋める必要がある構造的な欠落が3点**、**指示の前提を反映するために追記が必要な点が2点**、
**より良い設計として提案したい点が2点**ある。

## 1. 構造的な欠落(修正必須)

### 1-A. 次ラウンドのRoot Snapshotが生成されていない

`SEARCH_DONE -->|続ける| NEXT_BRANCH["上位Branchを次の探索対象にする"] --> COORDINATOR`
という経路で探索を継続する設計になっているが、`COORDINATOR`自身の定義は
`"Root Snapshotと現在の選択肢からBranch探索を開始"`——**次ラウンドの`Root Snapshot`が
どこから来るのか、図のどこにも明示されていない。**

初回だけは`SNAPSHOT["Main状態からRoot Snapshotを取得"]`が供給するが、2ラウンド目以降は
`NEXT_BRANCH`で選ばれた勝ちBranch(Body/Sub問わず)の**現在の状態からCaptureSnapshotする
ステップ**が必要——これが無いと`COORDINATOR`は2周目以降入力を持たない。

**修正提案**: `NEXT_BRANCH`と`COORDINATOR`の間に、「勝ちBranchを保持しているWorkerから
CaptureSnapshotし、これを次のRoot Snapshotとする」ノードを追加する。

### 1-B. Body Branchの継続性・引き継ぎが未定義

`BODY_WORKER`は他のSub Workerと異なり、`RESTORE_1/2/N`のような明示的Restoreを経ずに
`BODY_APPLY["Body Branchを同じGameInstanceへそのまま適用"]`する設計になっている——
つまりBody WorkerだけはRestoreを介さず、**自分自身の生きた状態を暗黙に信頼する**という、
他の全ノードと非対称な扱いを受けている。これは以下2つの問題を生む。

1. **1-Aの欠落と組み合わさると特に問題になる**: もし次ラウンドの勝ちBranchが
   Sub Worker側だった場合、そのSub Workerが次の「Body」になるのか、それとも旧Body
   Workerが(古い状態のまま)Bodyであり続けるのかが図から読み取れない。後者だとすれば
   明確な誤り(古い状態のまま次の探索を続けることになる)。
2. **この関与全体の設計原則からの逸脱**: `combat_state_contract.v0.8.md`をはじめ、
   これまでの全ラウンドは「状態は必ずSnapshot経由で明示的・検証可能な形でRestoreする」
   ことを一貫した原則としてきた(`RestoreSnapshotJson`の`validate_before_destroy`契約、
   stale action_idを再利用しないルール等)。Body Workerだけ「Restoreせず自分の
   生存状態を信頼する」という例外を設けると、この原則が部分的に崩れ、監査可能性
   (「このBranchが実際にどのSnapshotから来たか」の追跡)も失われる。

**修正提案**: 次の2案のいずれかを推奨する(§4「より良い設計」でも詳述)。

- 案(a): Body/Subの非対称性を廃止し、全Branchを`mermaid_combat_target_worker_pool.mermaid`
  と同様、毎回明示的にRestoreする均一な扱いにする。実装が単純になり、上記の懸念が
  消える。「有力候補の再Restoreを省略する」という最適化の効果が実測で重要と分かった
  場合にのみ、後から個別最適化として導入する。
- 案(b): 最適化を維持するなら、「Bodyロールは固定のWorkerに紐づくのではなく、
  各ラウンドの勝者Workerへ動的に引き継がれる」ことを明示し、負けた旧Body Workerは
  Worker Poolへ返却(状態は破棄)する、という昇格/降格ロジックを図に追加する。
  加えて、Body Workerも定期的に(最低でも採用が決定した時点で)CaptureSnapshotし、
  1-Aの「次Root Snapshot」供給元として使えるようにする。

### 1-C. Fault/Rejection経路がMain側・Branch側の両方で欠落している

- **Main側**: `BOUNDARY`の分岐は`Terminal`と`Stable / Pending`のみで、**Fault分岐が
  存在しない**。`mermaid_combat.mermaid`(現行実装準拠版)には`BOUNDARY -->|Fault| FAULT`
  が明示されており、実際に`ActionFaultedException`/`FaultedCombatSessionException`という
  実在する例外経路(`live_combat_session.py`の`_handle_action_fault`)に対応している
  ——「完成形」の図から欠落させてよい理由はない。
- **Branch側**: `BRANCH_BOUNDARY`の分岐も「新しい選択が発生」と「Stable / Terminal」の
  2つのみで、**Restore拒否(`RestoreSnapshotJson`のteardown前拒否)やBranch自体の
  Fault(post-teardown failure)を表す分岐が存在しない**。前回レビューした
  `mermaid_combat_target_worker_pool.mermaid`は`BRANCH_VALID -->|No| RETURN_REJECT`と
  `BRANCH_BOUNDARY -->|Fault| RETURN_FAULT`を明示していた——今回のrough図では退行している。

**修正提案**: Main側に`BOUNDARY -->|Fault| MAIN_FAULT`相当を、Branch側に
`BRANCH_BOUNDARY -->|Fault| BRANCH_FAULT`相当と、Restore自体の拒否を表す経路
(`RestoreSnapshotJson`呼び出し前後の検証結果分岐)を追加する。Branch Faultは
「このBranchは評価不能」として`COLLECT`/`COMPARE`側で除外scoreとして扱えばよく、
探索全体を止める必要はない——ただし記録は必須(前回報告書§7の
「WorkItem再試行」「全Worker失敗時の停止条件」判断にも関わる)。

## 2. 指示の前提を反映するための追記

### 2-A. ActionContinuation分類・事前評価の実現方法(明確化・朗報)

指示通り「ActionContinuationも分類・事前評価後に上位のみ展開」を前提とした場合、
実装上の見通しは良い。前回報告書では「現行のStep()内部continuationループ
(`live_combat_session.py:701-717`)をどう扱うか」を懸念点として挙げたが、
**この内部ループはC# `Step()`自体の仕様ではなく、完全にRL Python側(または将来の
Branch Worker側)の実装判断である**——C#の`Step(int actionId)`はアトミックに1回の
engine stepを行うだけで、`Step()`を繰り返し呼んで擬似的にcontinuationを解決する
ループはRL側が能動的に構築しているものである(現行の`_default_choose_action_
continuation_live`ベースの自動解決ループも同様)。

したがって、**Branch Workerがこの自動解決ループを実装しない(1回のStep結果をそのまま
`BRANCH_BOUNDARY`として返す)だけで、ActionContinuationスコープの選択も含め、全ての
選択が自然に`CLASSIFY`/`PRE_EVAL`/`PRUNE`へ浮上する**——新しいEmulator側API変更は
不要で、Branch Worker実装時に「継続ループを移植しない」という選択をするだけで実現できる。

**図への追記提案**: このことを`BODY_WORKER`/Sub Worker群の定義に一言加えておくと、
実装時に誤って現行のcontinuation自動解決ロジックを移植してしまう事故を防げる
(例: 「Workerは1 Stepごとに必ず呼び出し元へ制御を返す。continuation自動解決は
Worker内で行わない」)。

### 2-B. RNG方式B(Restore前の派生Snapshot置換)の attach point が図に存在しない

方式Bを採用する場合、Emulator側の新規API(Restore後の特定ストリーム再seed)は
不要になる——これは前回報告書§4-Aで挙げた懸念のうち最も重い部分が解消されることを
意味し、良い判断だと思う。`RestoreSnapshotJson`はSnapshot JSON文字列をそのまま
受け取る既存APIであるため、「Root SnapshotのJSONを読み込み、対象RNGストリーム
(`SerializableRngSnapshot`のCounter/State0-3)だけを差し替えた派生JSONを作り、それを
Restoreする」という処理は完全にRL(またはBranch Worker)側だけで完結する。

ただし、**現在の`mermaid_rough_combat.mermaid`には、RNGを意図的に分岐させる仮説
(reshuffle順の複数サンプリング等、現行`LookaheadSearcher.sample_future_draw_orders()`
相当)に対応するノード自体が存在しない**——`QUEUE`/`SUB_SELECT`はいずれも
「候補アクション」の分岐であり、「同じ候補アクションに対して複数のRNG仮説を試す」という
軸は描かれていない。方式Bを正式方針とするなら、少なくとも注記として
「Sub Branch WorkItemがRNG仮説サンプリングを必要とする場合、Root Snapshot JSONを
Restore前に派生Snapshotへ置換してから各Sub Workerへ渡す(Restore後の再seedは行わない)」
という一文を`QUEUE`または`SUB_SELECT`付近に追加しておくことを推奨する——将来
Lookahead相当の機能をこの設計に統合する際、実装者がこの方針を確実に踏襲できるようにする
ため。

## 3. 軽微な確認・明確化の提案

- `APPLY_MAIN["採用したAction／Sequenceだけを<br/>Main Game Instanceへ適用"]`——
  これは「Branch Workerの状態をMainへコピーする」のではなく「採用した論理Sequenceを
  Main上で通常のStepとして再実行する」という意味だと解釈した(前回報告書のRULE_COMMIT
  「Branch状態をMainへコピーせず、採用SequenceだけをMainで再実行」と一致)。この解釈で
  正しいか確認したい——正しければ、ノード名を「Main Game Instance上で採用Sequenceを
  再実行」のように明示した方が、実装時の誤読(状態コピーだと誤解される)を防げる。
- `PRE_EVAL["分類別の評価関数で事前評価"]`——この事前評価がEmulatorを一切呼ばない
  静的・軽量なスコアリング(カードコスト、既知の敵HP等の宣言的パラメータのみで計算)
  であることを明示すべき。もし事前評価が「実際にActionを適用してみた結果」を必要と
  するなら、Restoreを伴うため軽量ではなくなり、Classify→PreEval→Pruneという
  コスト削減の意図全体が崩れる。既存の`_best_continuation_card_action`のような
  静的スコアリングパターンを踏襲する前提だと考えるが、明示を推奨する。

## 4. より良い設計に関する提案(主張)

指示により迷わず主張する。

### 4-A. Body/Sub非対称性は初期実装では廃止することを推奨する

§1-Bで述べた通り、Body Branchの「Restoreを省略する」最適化は、実装・監査コストに対して
得られる効果が実測なしでは不明であり、かつ現状の図では正しく機能させるための引き継ぎ
ロジックが欠けている。**初期実装では全Branchを均一にRestoreベースで扱い
(`mermaid_combat_target_worker_pool.mermaid`のWorker Pool設計をそのまま踏襲)、
「有力候補はRestoreを省略できる」という最適化は、実測でRestoreコストが本当に
ボトルネックだと判明してから、個別の最適化として追加することを推奨する。** 理由:

1. この関与全体を通じて確立してきた「明示的Snapshotから毎回Restoreする」という
   決定論的・監査可能な設計原則を、初手から崩さずに済む。
2. Restoreのコスト自体は(前回報告書§9で述べた通り)IPCオーバーヘッドよりも
   おそらく支配的な要素であり、複雑な最適化を急いで導入する前に、まず均一設計での
   実測を取るべきである。
3. Body/Sub非対称性を維持したまま実装すると、§1-Bで指摘した引き継ぎロジックの
   バグが探索結果の正しさに直結するリスクがある(古いBody Workerの状態が
   誤って使われた場合、探索結果がMainの実際の状況と食い違う——最も避けたい種類の
   バグである)。

### 4-B. 「新しい選択が発生」時のRECLASSIFY経路にBranch WorkerのFault/Rejectionを含めて統一する

§1-Cで指摘したFault経路の欠落と関連するが、`RECLASSIFY`→`PRUNE`という再分類ループ自体は
良い設計だと思う——ActionContinuationの各ミクロ選択を、既存の`CLASSIFY`/`PRE_EVAL`と
**同じパイプライン**で扱えるため、コードの重複を避けられる。この考え方を一歩進め、
Branch側のFault/Rejectionも「評価値が最低のcandidate」として同じ`PRUNE`ロジックへ
自然に合流させる(特別扱いの分岐を増やすのではなく、既存の評価・枝刈りパイプラインに
「Fault/Rejectedはスコア圏外」として統合する)設計を提案する。こうすることで、
探索ロジック自体の分岐を増やさずにFault処理を組み込める。

## 5. まとめ(修正点一覧)

| # | 分類 | 内容 |
|---|---|---|
| 1-A | 構造欠落(必須) | `NEXT_BRANCH → COORDINATOR`に次Root Snapshotを供給するCaptureSnapshotステップが無い |
| 1-B | 構造欠落(必須) | Body Branchの継続性・勝者Workerへの引き継ぎロジックが未定義 |
| 1-C | 構造欠落(必須) | Main側・Branch側ともにFault/Rejection分岐が図から欠落している |
| 2-A | 前提の反映(明確化) | ActionContinuation分類は「Workerがcontinuation自動解決ループを実装しない」だけで実現可能——Emulator側API変更不要、図に一言明記を推奨 |
| 2-B | 前提の反映(追記) | RNG方式Bの適用箇所(QUEUE/SUB_SELECT付近)が図に存在しない——将来のRNG仮説サンプリング統合に備えて注記を推奨 |
| 3 | 軽微な明確化 | `APPLY_MAIN`のノード名を「状態コピーではなくSequence再実行」だと分かるよう修正推奨、`PRE_EVAL`が非Restoreの静的評価であることを明示推奨 |
| 4-A | 設計提案 | Body/Sub非対称性は初期実装では廃止し、全Branch均一Restore方式を推奨(実測後に個別最適化を検討) |
| 4-B | 設計提案 | Branch Fault/RejectionをRECLASSIFY/PRUNEパイプラインへ統合し、特別扱いの分岐を増やさない設計を推奨 |

## 6. 停止

指示の通り、ここで停止する。`mermaid_rough_combat.mermaid`を含め、いかなるファイルも
変更していない。上記修正点の反映要否・Body/Sub非対称性の採否(§4-A)は最終監督者の
判断を仰ぐ。
