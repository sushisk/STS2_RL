# RL担当 作業報告 — Combat詳細図の再検討5点への対応 (2026-08-01)

対象: 「詳細図は大きく改善しましたが、実装前に次を修正・回答してください」で指摘された5点の検討・
回答と図の修正。5点とも実害のある正当な指摘と判断し、却下した項目はない。
`C:\STS2_Mermaid\`配下のCombat詳細図7点のうち5点(`mermaid_combat_candidate_pipeline_detail.mermaid`・
`mermaid_combat_snapshot_replay_detail.mermaid`・`mermaid_combat_branch_scheduler_detail.mermaid`・
`mermaid_combat_rng_hypothesis_detail.mermaid`・`mermaid_combat_main_loop_detail.mermaid`・
`mermaid_combat_commit_detail.mermaid`の6点)を修正した
(`mermaid_combat_fault_worker_detail.mermaid`は該当箇所がなく変更なし)。

RL HEAD(この報告のcommit直前): `6c473c68f335d000015779398a7b29b2e02f0bd0`

**本ラウンドはMermaid図の修正と見解回答のみ。ランタイムコードの変更は一切行っていない。**

## 1. WorkItemにExpected Post-Step Signatureは存在しない(初回候補実行前)

**判断: 妥当。採用した。**

指摘の通り、未実行の候補に対して「実行結果の期待値」を付与するのは論理的に成立しない
(まだ起きていない結果を予測はできない)。これは`Current Context Signature`(このDecision Contextへの
到達確認。Search Coordinatorが既知のRoot Snapshot＋Replay Prefixから導出したものなので付与可能)と、
`Expected Post-Step Signature`(過去に実際に観測された結果。将来の再実行/検証でのみ使う)を
混同していた設計ミスだった。

`mermaid_combat_snapshot_replay_detail.mermaid`・`mermaid_combat_candidate_pipeline_detail.mermaid`・
`mermaid_combat_branch_scheduler_detail.mermaid`を修正し、WorkItemの構成を
`Current Context Signature ＋ Candidate Semantic Action`(Expected Post-Step Signatureなし)へ統一した。
Step成功後に得られる`Observed Post-Step Signature`は`Transition Record`として記録され、これが
**以後**同じActionを再実行(Replay)する際にのみ`Expected`値として使われることを明記した
(`NOTE_NO_PREDICT`として明示)。実は`STEP_CANDIDATE`(未実行候補の初回実行)自体には元々
事前検証ゲートを置いていなかったため、構造的なバグではなく用語・WorkItem構成の誤りだった。

## 2. Replay Prefix非空を一律に実RNGへ戻すのは誤り

**判断: 妥当。設計バグとして修正した。**

前回の`PREFIX_GATE`は「Prefix非空 → 常に実RNGへ強制」としていたが、これは2つの異なるケースを
混同していた。

- **(A) Hypothesis-branch内部のPending(cascade)**: Root Snapshotが既にHypothesis H由来の派生Snapshotで
  あるDecision Context内で、探索の結果Pendingへ到達した場合。この場合のRoot RNGは元々H由来であり、
  「実RNGへ戻す」という操作自体が意味を持たない(そもそも実RNGを使っていない)。正しくは同じHを
  そのまま継承し、以後の探索も制限なく継続してよい(実RNGへの依存も、真の未来RNGの漏洩リスクもない)。
- **(B) Main-observed Pending**: MainのHeld Stable Snapshot(Hypothesis ID=None、真の実RNG)を起点とする
  Decision Context。この場合はPrefix再生に実RNGが必須という前回の判断は正しいが、**その先の探索**
  (複数候補をStepして比較するLookahead)まで実RNGで行うと、Search Coordinator/Evaluatorに真の未来RNGの
  結果が事実上見えてしまい、Main RNG非公開方針に反する(私自身が導入した抜け穴だった)。

修正として、指摘通り「初期実装ではその先の確率的Lookaheadを禁止し、静的評価で次の1手を選ぶ」方針を
採用した。`mermaid_combat_main_loop_detail.mermaid`に`PENDING_STATIC`ノードを新設し、Main-observed
Pending(Replay Prefix非空)からの新規decisionは、Restore/Step/Worker分岐を一切行わない
軽量Evaluator(Card/Target/Hand/Confirm)による直接選択のみに制限した。`mermaid_combat_rng_hypothesis_detail.mermaid`の
`PREFIX_GATE`をRoot Hypothesis IDの有無で分岐する構成へ再設計し、(A)は`INHERIT_H`(継承・制限なし)、
(B)は`MAIN_PENDING_RULE`(実RNG必須＋Lookahead禁止)とした。

別案(限定的な境界RNG override)についても指摘通り提示した: Replay Prefix再生完了後の地点でPendingを
Capture→Hypothesis適用→再Restoreする案が考えられるが、現行のEmulator契約はPending自体のCapture/Restoreを
許可していないため実現不能であり、Emulator契約拡張の要否を含めた別課題としてEmulator担当への確認事項に
残した(`NOTE_PREFIX_FUTURE`として明記。前回報告からの継続事項でもある)。

## 3. Hypothesis由来の先頭ActionにExpected Post-Step Signatureを厳密条件として付けない

**判断: 妥当。採用した。**

前回`COMMIT_FIRST_ONLY`は「先頭1手のみCommit」としていたが、`BUILD_PLANNED`が全てのCommit経路で
一律にObserved値をExpected Post-Step Signatureとして付与する実装になっており、Hypothesis由来の
1手にも誤って厳密な検証条件を課してしまっていた。H上で観測した結果はHypothesis依存であり、
Mainの実RNGでは高確率で異なる結果になるため、これを厳密条件にすると正常なケースまで
`VERIFY_TRANSITION`で不一致→`DISCARD`されてしまう。

`mermaid_combat_commit_detail.mermaid`を修正し、`COMMIT_FIRST_ONLY`経路では
Expected Post-Step Signatureを一切付与しないようにした。`mermaid_combat_main_loop_detail.mermaid`の
`VERIFY_TRANSITION`の「無条件一致扱い」条件を、Direct／PENDING_STATICに加えてこのケースにも
汎用化した(`NOTE_NO_SIGNATURE`として明記)。Mainはこの1手を無条件に実行し、実StepResult観測後に
必ず新しいdecisionとして再探索する(`RESEARCH`)。

## 4. Plan Pathは各Step成功時に追記し、Stableでリセットするのはミスだけ

**判断: 妥当。設計バグとして修正した。**

前回の設計は`PLAN_PATH_APPEND`を評価境界(`SELECT_NEXT`)でのみ実行するバッチ追記としていたが、
これには実害のあるバグがあった: 探索が最初のラウンドで(`SELECT_NEXT`を一度も経由せず)
即座に`DONE=終了`へ到達した場合、Plan Pathが空のままBUILD_PLANNEDに渡ってしまう(Heuristic 1段や、
浅いBeam Searchが1ラウンドで終わるケースで発生しうる)。

指摘に従い、Plan Pathの管理方式を「親から継承し、以後は自身のStep成功のたびに追記」する方式へ
再設計した。`mermaid_combat_snapshot_replay_detail.mermaid`の`APPEND_RECORD`を、Replay PrefixとPlan Pathの
**両方**へ同じタイミングで書き込む形に変更し、新しいDecision Context生成時(Stableに到達し次のRootが
確定する瞬間)にPlan Pathを親から継承する(Replay Prefixのみが空へリセットされる)ことを明記した。
`mermaid_combat_commit_detail.mermaid`からは`PLAN_PATH_APPEND`という独立したバッチ追記ノードを削除し、
「Plan Pathは各Decision Context自身が既に追記済みであり、本図は読み出すだけ」という位置づけへ改めた。
これにより単一ラウンドで終了するケースでも正しくPlan Pathが埋まっている。

## 5. H1..HnはRoot Action単位で集約し、Beam widthと分離する。グループ同一性と欠損サンプル集約規則を明示

**判断: 妥当。ただし「今回、汎用確率的Beam Searchを実装する」という条件は満たさない設計判断とした
(理由は後述)。**

指摘の通り、前回の`GROUP_BY_ACTION`は「Action／Plan Prefix単位」という曖昧なキーでグルーピングして
おり、Hごとに合法Actionや以降の分岐構造(特にActionContinuationカスケードの形)が異なりうる場合、
深い階層でのグループ同一性が未定義だった。

この問題への対応として、**グルーピングのキーをRoot Action(この探索呼び出しの起点における最初の1手)
のみに限定**し、それより深い階層でのグルーピングは行わない設計へ変更した。Root Actionは全Hypothesisが
共通の起点(同一Decision Context)から出発するため、常に一意に定義できる。これにより「合法ActionがHごとに
分岐した場合の扱いが未定義」という問題自体を、深い階層でのグルーピングをそもそも行わないことで回避した。

副作用として、この設計はRNG Hypothesisが関与する評価を**単一ラウンドのRoot Action比較のみ**に限定する
ことを意味する(複数ラウンドにまたがる汎用的な確率的Beam Searchは今回実装しない)。これは指摘の
「汎用確率的Beamを今回実装するなら、グループ同一性とサンプル欠損時の集約規則を明示してください」
という条件文に対し、「今回は実装しない」という設計判断で応えたものである。理由:

- 深い階層のグループ同一性を正しく一般化するには、Hごとに分岐構造が異なる場合の「同一Plan Pathとみなす
  条件」(例えばSemantic Action列が完全一致すれば同一とみなすのか、到達したBoundary/Choice構造まで
  一致を要求するのか等)を新たに設計する必要があり、現時点でその設計の妥当性を検証する材料がない。
- 点3(Hypothesis由来の先頭Action限定Commit)により、そもそも深いPlan Pathの多段先読みは「スコアリングの
  精度向上のためだけ」に使われ、実行には反映されない。単一ラウンドのRoot Action比較で十分に
  「次の1手を良く選ぶ」という目的を達成でき、複雑さに見合う効果が明確でない。
- 既に指摘2で「Main-observed Pendingからの深いLookaheadを禁止する」という初期実装方針を採用しており、
  一貫性の観点からもHypothesis関連の探索は浅い(単一ラウンドの)ものに揃えるのが妥当と判断した。

欠損サンプルの集約規則は指摘通り明示した。`mermaid_combat_commit_detail.mermaid`の`AGGREGATE_RULE`
として、各Root ActionグループのスコアはH1..Hnのうち有効な(Faultでない)結果のみの平均とし、
特定のHでFaultした場合はそのHをサンプルから除外(補完しない)、有効サンプルが0件になった
Root Actionは評価不能として比較対象から除外する、と定めた。

多段化(汎用確率的Beam Search)が将来必要になった場合の要件は`NOTE_ROOT_ONLY`/`NOTE_SINGLE_ROUND`として
両図に明記し、「グループ同一性の定義とサンプル欠損時の集約規則を深さ非依存に一般化してから着手する」
という条件を残した。

## 修正した図

- `mermaid_combat_candidate_pipeline_detail.mermaid`: SPLITノードのWorkItem構成をCurrent Context
  Signature＋Candidate Semantic Actionへ修正(点1)。
- `mermaid_combat_snapshot_replay_detail.mermaid`: WorkItem構成修正(点1)、Root Hypothesis継承/
  Main-observed実RNG区別の再設計(点2)、Plan Pathの親からの継承と各Step成功時の追記(点4)、
  PREV_CHILD経路がRNG非依存Beam Search限定であることの明記(点5)。
- `mermaid_combat_branch_scheduler_detail.mermaid`: SUB_ROUTEのWorkItem構成修正(点1)。
- `mermaid_combat_rng_hypothesis_detail.mermaid`: PREFIX_GATEをRoot Hypothesis ID有無で分岐する
  構成へ再設計(点2)、GRID/WORK_ITEM_MULTIをRoot Action単位である旨へ修正(点5)、
  責任境界表にMAIN_PENDING_RULEとの関係を追記(点2/8の一貫性確認)。
- `mermaid_combat_main_loop_detail.mermaid`: PENDING_STATICノード新設(点2)、VERIFY_TRANSITIONの
  無条件一致条件を汎用化(点3)。
- `mermaid_combat_commit_detail.mermaid`: PLAN_PATH_APPEND(バッチ追記)を削除しPlan Path読み出しのみに
  変更(点4)、GROUP_BY_ACTIONをGROUP_BY_ROOT_ACTIONへ再設計しHypothesis関与時は単一ラウンドに限定、
  AGGREGATE_RULE明示(点5)、COMMIT_FIRST_ONLYからExpected Post-Step Signatureを除去(点3)。

`mermaid_combat_fault_worker_detail.mermaid`は該当する用語・ノードが含まれておらず変更していない。

## 却下した項目

なし。5点とも実害のある正当な指摘であり、全て採用した。

## 未解決事項・監督者判断/Emulator担当確認が必要な事項(継続)

1. **限定的な境界RNG override**(点2の別案): 現行契約と矛盾するため、Emulator担当への確認・
   契約拡張の要否を含めた別途の検討課題とする(前回からの継続)。
2. **RNG Counter差分のStepResult露出**(前回点5(a)案の前提): 前回からの継続事項。
3. **汎用確率的Beam Searchの正式設計**(点5で見送った深い階層のグループ同一性・集約規則の一般化):
   将来必要になった場合の新規検討課題として残す。
4. **Stable境界を跨いだLease再利用の正式採否**、**Decision Context系列内のContinuation深さ上限の
   具体値**、**Evaluator入力の型/インターフェース分離の実装設計**: いずれも前回からの継続事項。

## 停止

指示の通り、上記6図の修正とこの報告のみを行い、ランタイムコードの変更は行っていない。
`git status`でSTS2_RLリポジトリの作業ツリーはこの報告ファイルの追加のみであることを確認済み。
