# RL担当 作業報告 — Combat詳細図の再検討8点への対応 (2026-08-01)

対象: 「詳細図は大きく改善されていますが、実装前に以下を再検討してください」で指摘された8点への
見解回答と図の修正。`C:\STS2_Mermaid\`配下のCombat詳細図7点のうち6点を修正した
(`mermaid_combat_fault_worker_detail.mermaid`は既存表現が汎用的で該当箇所がなかったため変更なし)。

RL HEAD(この報告のcommit直前): `985eac46a64beaeb0e432e5dd55121be942f9361`

**本ラウンドはMermaid図の修正と見解回答のみ。ランタイムコードの変更は一切行っていない。**

## 1. Sequence Prefixの分離: Replay Prefix と Plan Path

**見解**: 指摘の通り、従来の「Sequence Prefix」は2つの異なる役割を1つの語で混同していた。

- **Replay Prefix**: 1つのDecision Context内で、Stable Root SnapshotからPending再現のために
  Restore+Replayする際に使う、局所的・一時的な記録。評価境界(Stable/Terminal)に到達するたびに
  空へリセットされる。
- **Plan Path**: Search Coordinatorが探索開始点(Main Decision Context)からCommitまで、評価境界を
  跨いでも蓄積し続ける、探索全体の確定手順の記録。

両方に「Step成功後にSemantic Action＋観測後Signatureを追加する」という指示に従い、
`mermaid_combat_snapshot_replay_detail.mermaid`に`APPEND_RECORD`ノードを追加し、Branch Worker側は
Step成功のたびに`(Semantic Action, Observed Post-Step Signature)`をReplay Prefixへ追記するようにした。
Main側も`mermaid_combat_main_loop_detail.mermaid`のEXEC_LOOPに同様の`APPEND_RECORD`を追加した。
Plan Pathは`mermaid_combat_commit_detail.mermaid`に新設した`PLAN_PATH_APPEND`ノードで、
`SELECT_NEXT`(ラウンド継続時の勝者選択)のたびに確定した手を追記する形で実装した。
MainはPlan Pathそのものは保持せず、確定済みの`Planned Sequence`のみを受け取る
(`NOTE_REPLAY_VS_PLAN`として明記)。

## 2. ActionContinuationはchoice_kindではなくchoice_scope

**見解**: 完全に同意する。指摘通り、ActionContinuationは「このChoiceが何についてのものか」
(Card／Target／Hand／Confirm等の意味的種別=choice_kind)ではなく、「このChoiceがどの文脈で
発生したか」(TopLevelの本来の決定か、他Actionの解決過程で生じた継続選択か=choice_scope)を表す
直交した属性である。

`mermaid_combat_candidate_pipeline_detail.mermaid`を修正し、Evaluator routingの基準を
`choice_kind`(Card/Target/Hand/Confirm/その他)のみとし、`choice_scope`(TopLevel/ActionContinuation)は
全候補に付随する属性として保持しつつ、Evaluator選択そのものには使わない構成へ変更した
(旧`Continuation Choice Evaluator`という「Continuation専用Evaluator」は廃止し、choice_kindに応じた
通常のEvaluatorへ普通にrouteする)。choice_scopeはDecision Signatureのcontinuation識別情報として
比較には引き続き使われる。

## 3. Expected Signatureの分離: Current Context Signature と Expected Post-Step Signature、canonical multiset

**見解**: 完全に同意する。従来の「Decision Signature」は「今この地点に正しく到達したか」の確認と
「この1手の実行結果が想定通りか」の確認を暗黙に混同しており、特に前者(Replay Prefix再生完了直後、
新しい候補を適用する直前の到達確認)が図上に明示的なチェックポイントとして存在しない欠落があった。

`mermaid_combat_snapshot_replay_detail.mermaid`に以下を追加した。

- `DC_SIGNATURE`: 両者が共有するDecision Signatureの構造定義(Boundary種別・Choice種別・Choice scope・
  min/max selection・target制約・候補のSemantic Key集合・continuation識別情報)。候補のSemantic Key集合は
  **canonical multiset(重複を保持する多重集合)**として比較することを明記し、setへ潰して重複数の情報を
  失わないよう指示通り修正した。
- `CTX_SIG_CHECK`: Replay Prefix再生完了直後(`AT_ROOT_S`/`AT_ROOT_R`)に1回だけ行う、
  **Current Context Signature**(この地点への到達確認)の照合。不一致時は`CTX_MISMATCH`としてFault経路へ。
- `REPLAY_SIG_CHECK`: Replay Prefix内の各エントリ実行後に行う、**Expected Post-Step Signature**
  (この1手の実行結果の検証)の照合(既存のものを名称明確化)。

`mermaid_combat_main_loop_detail.mermaid`・`mermaid_combat_commit_detail.mermaid`でも
「Expected Decision Signature」という表現を全て「Expected Post-Step Signature」へ統一した。

## 4. Sub WorkerのPending到達時のLease新規確立、Holder直接Step前のLease検証

**見解**: 指摘の通り、「SubはLeaseを取得しない」と「Stepを行ったWorkerがLeaseを継続保持」は
矛盾していた。修正の考え方は、Sub/Holderを固定的なWorker種別として扱うのをやめ、
「WorkItem受領時点でLease済みかどうか」の違いとして再定義することである。

`mermaid_combat_branch_scheduler_detail.mermaid`を修正し、`LEASE_MODEL`subgraphに
`LEASE_ESTABLISH`(Lease確立の唯一の条件はStepの結果Pendingへ到達したことであり、
事前予約・種別による区別はしない)を追加した。Sub Workerも、Stepの結果Pendingへ到達すれば
その時点で新規Leaseを確立し、以後は次の評価境界までHolder Workerと同じ扱いになる
(`NOTE_NOT_FIXED_ROLE`として明記)。

Holder直接Step前の検証も追加した。`TOP_ROUTE`(Lease済みWorkerの存在確認)の後に
`LEASE_VERIFY`(worker_generation・context_id・RNG Hypothesis ID・state signatureの一致確認)を
新設し、不一致の場合は`LEASE_INVALIDATE`(該当Leaseを破棄)を経て`TOP_TO_BOOTSTRAP`
(通常のRestore+Replay経路)へフォールバックするようにした。

## 5. RNGを消費し得るPending起点でのRoot RNG差し替えの矛盾

**見解**: 指摘は正しい実害のある設計バグだった。Replay Prefixが非空のDecision Context
(Main PendingやSub Worker展開でPrefixを再生する場合)でRoot(=直前のStable)のRNGを方式Bで
差し替えると、Prefix再生自体が異なるRNG消費結果をたどり、実際に観測されたPending状態を
再現できなくなる(整合性が壊れる)。

3案の検討結果:

- **(a) RNG stream分離**: Hypothesis対象のストリームがPrefix再生中に実際には消費されていないことを
  証明できれば、Root側での差し替えは安全。ただしこれには各StepのRNG Counter差分をStepResultの
  metadataから取得できる必要があり、現行Emulator契約でこの情報が取得可能かどうか未確認。
  Emulator担当への確認が必要な事項として残す。
- **(b) Pendingでは常に実RNGを使う(Hypothesis変更禁止)**: 追加のEmulator機能を必要とせず、
  現行契約の範囲で完全に安全。今回はこれを既定ルールとして採用した。
- **(c) 限定的な境界RNG override**: Prefix再生完了後の地点でCapture→Hypothesis適用→再Restore、
  という案も検討したが、現行契約は「Pending自体のCapture/Restoreを許可しない」ため、
  この地点がPendingである限り実現不能。Emulator契約の拡張が必要になる可能性がある独立の検討課題であり、
  現時点では要求しない。

`mermaid_combat_rng_hypothesis_detail.mermaid`に`PREFIX_GATE`(Replay Prefixが空かどうかの判定)を
追加し、非空の場合は`PREFIX_NONEMPTY_RULE`により無条件で実RNG使用(`TRUE_RNG_OK`)へ強制する構成へ
修正した。`mermaid_combat_snapshot_replay_detail.mermaid`側の`RNG_SUB`分岐も、Prefix空の場合
(`ROOT_SNAP`)のみに限定し、Prefix非空の場合(`ROOT_SNAP_PENDING`)は`FORCE_TRUE_RNG`という
明示ノードへ差し替えた。(a)(b)(c)の検討結果は`NOTE_PREFIX_FUTURE`として図に残した。

## 6. 複数RNG仮説評価後の完全Sequence Commitの不適切性

**見解**: 完全に同意する。RNG仮説H1..Hnで多段先読みを行い最高スコアの(Action, H)を選んでも、
Main実RNGはいずれのHとも一致しない可能性が高く、深い手ほど「特定のHypothesisを前提とした
架空の計画」になっている。これを複数手まとめてMainへCommitするのは不適切というのは正しい懸念である。

提案の2案のうち、初期実装では**案1(集約値で先頭Actionのみ Commitし、実Step後に再探索)**を
採用することを推奨し、そのように図を修正した。理由:

- 案2(全Hypothesis共通Prefixのみ Commit)は「どこでHypothesis間の分岐が生じたか」を検出する
  追加ロジックが必要であり、初期実装の複雑度を上げる。
- 案1はMainが既に持つ「1手ごとに再解決してStepし、必要なら都度新しいdecisionを開始する」という
  既存のEXEC_LOOP構造とそのまま整合し、追加の仕組みを要さない。
- 深い先読み自体は禁止しない(スコアリングの精度向上には使える)。「先読みした結果全体をそのまま
  Commitしない」という制約だけを設ける。

`mermaid_combat_commit_detail.mermaid`の`DONE`終了経路に`HYPOTHESIS_USED`分岐を追加し、
RNG Hypothesis(Noneでないもの)が関与した勝者グループの場合は`COMMIT_FIRST_ONLY`
(Plan Path上の先頭未Commit1手のみ)、関与していない場合(RNGを消費しない候補比較のみだった場合)は
`COMMIT_FULL`(複数手を安全にCommit可能)に分岐させた。`COMMIT_FIRST_ONLY`だった場合、Main実行後に
`RESEARCH`ノードから新しいSearch呼び出しを行う経路を追加した。案2は`NOTE_COMMIT_POLICY`に
将来の最適化候補として記載し、今回は採用しないことを明記した。

## 7. Beam widthとRNG sample countの分離

**見解**: 指摘の通り、修正前の設計はAction×Hypothesisの個々の評価結果を無区別にBeam幅の
プルーニング対象としており、1つのActionが複数Hypothesisで好スコアを得ると、それだけでBeam枠を
複数消費し、他の(評価が浅かったが本来有望な)Actionを不当に排除しうる欠陥があった。

`mermaid_combat_commit_detail.mermaid`の先頭に`GROUP_BY_ACTION`を新設し、RNG Hypothesis IDを
除いたキー(Action／Plan Prefix単位)で結果をグルーピングし、グループ内のH1..Hnスコアを集計
(既定: 平均)してから、この集計後のグループに対してのみ`COMPARE`/`SELECT_NEXT`のwidthベース
枝刈りを適用する構成へ変更した。選択されたグループは`EXPAND_GROUP`でそのグループに属する
全Hypothesis変種のChild Snapshotをそれぞれ次ラウンドのDecision Contextとして展開する
(RNG sample countは維持され、個々のH変種が追加でBeam枠を消費することはない)。
`mermaid_combat_rng_hypothesis_detail.mermaid`側の`WORK_ITEM_MULTI`にもこのグルーピングへの
参照を追加した。

## 8. Main RNG非公開の責任境界

**見解**: 「Evaluator入力からの除外」で足りるが、その境界を正確に置く場所が重要である。

Search CoordinatorやBranch Workerは、方式B(Restore前のSnapshot JSON編集)を実現するために
Main RNGを含むSnapshot JSONへの**機械的なアクセスが構造的に必須**である
(Replay Prefix再生の忠実性の維持、および派生Snapshotの生成の両方に必要)。
これを完全に不可視化することは、方式B自体を実現不能にするため現実的でない。

真の境界は**Evaluator(Heuristic/Model scoring関数)の入力**に置くべきであり、単なる「使わないでください」
という規約ではなく、**型／インターフェースレベルで**Evaluatorへ渡す入力をObservation／Choice Payload
由来の特徴量のみに限定し、生のSnapshot DTO(RNGフィールドを含む)を直接受け取れない構造にすることを
推奨する。`mermaid_combat_rng_hypothesis_detail.mermaid`のPRIVACY subgraphに`BOUNDARY_TABLE`を
新設し、Emulator API／Search Coordinator／Branch Worker／Evaluator／Main Processそれぞれの
責任境界を明記した。

## 修正した図

- `mermaid_combat_main_loop_detail.mermaid`: Replay Prefix名称統一、APPEND_RECORD追加、
  Expected Post-Step Signature名称統一、Plan Pathとの違いに関する注記追加。
- `mermaid_combat_candidate_pipeline_detail.mermaid`: choice_kind/choice_scope分離、
  Evaluator構成の意味的種別ベースへの再編。
- `mermaid_combat_snapshot_replay_detail.mermaid`: Replay Prefix/Plan Path分離の定義、
  Decision Signature共通構造の定義(canonical multiset明記)、Current Context Signature検証追加、
  APPEND_RECORD追加、Prefix非空時のRNG強制ルール追加。
- `mermaid_combat_branch_scheduler_detail.mermaid`: LEASE_ESTABLISH/LEASE_VERIFY追加、
  Sub Worker着地時のLease新規確立の明記、矛盾していた記述の解消。
- `mermaid_combat_rng_hypothesis_detail.mermaid`: PREFIX_GATE追加、責任境界表追加、
  GROUP_BY_ACTIONへの参照追加。
- `mermaid_combat_commit_detail.mermaid`: GROUP_BY_ACTION新設、PLAN_PATH_APPEND新設、
  HYPOTHESIS_USED分岐によるCOMMIT_FIRST_ONLY/COMMIT_FULLの分離、RESEARCH経路追加。

`mermaid_combat_fault_worker_detail.mermaid`は該当する用語(Sequence Prefix/Expected Decision
Signature等)が含まれておらず、既存の汎用的な表現のままで矛盾がないことを確認したため変更していない。

## 未解決事項・監督者判断/Emulator担当確認が必要な事項

1. **RNG Counter差分のStepResult露出**(点5の(a)案の前提): 各StepでどのRNGストリームがどれだけ
   消費されたかをStepResultのmetadataから取得できるか、Emulator契約上未確認。
2. **境界RNG overrideのためのPending Capture許可**(点5の(c)案): 現行契約と矛盾するため、
   Emulator担当への確認・契約拡張の要否を含めた別途の検討課題とする。
3. **Stable境界を跨いだLease再利用の正式採否**: 前回から継続する未決事項。
4. **Decision Context系列内のContinuation深さ上限の具体値**: 前回から継続する未決事項。
5. **Evaluator入力の型/インターフェース分離の実装設計**: 点8で推奨した構造的分離を、実際の
   Python側の型定義としてどう表現するか(dataclass分離等)は実装設計時に決定する。

## 停止

指示の通り、上記6図の修正とこの報告のみを行い、ランタイムコードの変更は行っていない。
`git status`でSTS2_RLリポジトリの作業ツリーはこの報告ファイルの追加のみであることを確認済み。
