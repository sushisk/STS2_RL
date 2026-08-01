# Combat Mermaid Diagram 更新報告: OrderedDrawPile／UnorderedDrawPile区別 (2026-08-01)

前提: Combat Mermaid正式ベースライン`docs/architecture/combat/`(前回commit`4e0605a`時点)。
本作業は仕様整理と図の修正のみであり、**ランタイムコードの変更は一切行っていない。**

## 背景

`Common/contracts/emulator_dto_contract_rl_required.v1.md`・
`Common/contracts/deck_unordered_input_shuffle_proposal.v1.md`(前回調査)で明らかになった通り、
現行EmulatorのDrawPile DTOは常にOrdered配列(index 0が山札上/次に引くカード)であり、
実ゲームでは山札順は非公開である。この事実がCombat Mermaid設計にまだ明示的に反映されておらず、
「Emulatorが内部で保持する正確なOrdered情報」と「RL側の探索・評価が使ってよい表現」の境界が
図上で曖昧だったため、監督者指示に基づき整理・反映した。

## 方針決定: OrderedDrawPileとUnorderedDrawPileの使い分け

RLの目的(将来の実ゲーム接続時に性能を保証すること)を踏まえ、以下の使い分けを図全体で統一した。

- **OrderedDrawPile(Exact Emulator State)を使う箇所**: Main Game Instance・Root Snapshot・
  Branch WorkerのGameInstance(WORKER_STEP/WORKER_RESTORE/HOLDER_STEP/BOOTSTRAP_STEP/SUB_RESTORE)、
  およびRestore/Replay/CaptureSnapshotの経路全て。これらはEmulatorの正しい実行結果
  (特にDraw処理)を得るために真のOrdered情報を必要とし、かつMermaid設計の既存原則
  (Search CoordinatorがRNGへ「機械的アクセス」を行うのは正しい、という整理と同型)に合致する。
- **UnorderedDrawPile(Hidden-Order表現)を使う箇所**: 全てのEvaluator/Heuristic/Model scoring
  入力(candidate_pipeline_detailのCard/Target/Hand/Confirm/Other Evaluator、main_loop_detailの
  PENDING_STATIC、commit_detailのSTAGE1/COMPARE_PLAIN)。これらへは山札由来の集計特徴量
  (残り枚数・カード種別頻度等、並び順を含まない)のみを渡し、真のOrdered配列そのものは
  一切渡さない。

**判断の理由**: 「どちらか一方を選ぶ」問題ではなく、既存のRNG非公開設計(Main RNG非公開の
責任境界)と全く同型の二層構造(内部実行は正確な状態を使う／外部への評価入力は非公開情報を
除いた表現を使う)を、DrawPile順序にもそのまま適用するのが正しい設計である。実ゲームでは
山札順が非公開である以上、探索・評価ロジックがOrdered情報に依存する形で学習・調整されると、
実ゲーム接続時に同じ情報が得られず性能保証が崩れる。一方、Restore/Replay/Step自体は真の
Ordered情報なしには正しく動作しない。この二重の要求を両立させる唯一の方法が、
「内部実行=Ordered、評価入力=Unordered」という層分離である。

## 変更した図・変更内容

### `mermaid_rough_combat.mermaid`

新規ノード`DRAWPILE_NOTE`を追加。DrawPileが常にOrdered配列であること、Main/Root Snapshot/
Branch WorkerがこのExact Emulator Stateを保持・操作すること、PRE_EVAL・COMPARE(評価関数)には
Unordered表現のみを渡すこと、RestoreがOrdered DrawPile全体＋完全なShuffle RNGを復元すれば
将来の再shuffleも一致するという決定性の根拠、を1箇所にまとめて記載し、各詳細図の該当ノートへの
参照を付けた。

### `mermaid_combat_candidate_pipeline_detail.mermaid`

新規ノード`NOTE_DRAWPILE_HIDDEN`を追加。EVAL_CARD/EVAL_TARGET/EVAL_HAND/EVAL_CONFIRM/EVAL_OTHERの
5つのEvaluator全てに、真のOrderedDrawPileを渡さず、Unordered相当の集計特徴量のみを渡すことを
明記した。

### `mermaid_combat_rng_hypothesis_detail.mermaid`

既存のPRIVACY subgraph(Main RNGの非公開)を拡張し、`DrawPile`の非公開も同じ責任境界表
(`BOUNDARY_TABLE`)で扱うよう統合した。新規ノード`DRAWPILE_ORDER`を追加し、
`Emulator API`・`Search Coordinator`・`Branch Worker`・`Evaluator`・`Main Process`の各責任者ごとに、
RNGとOrderedDrawPileの扱い方を対で明記した(責任境界表自体は既存のRNG非公開原則をそのまま
DrawPileへ拡張する形とした)。

### `mermaid_combat_snapshot_replay_detail.mermaid`

- `DC_DEF`(Decision Contextの構成要素)に、Stable Root SnapshotがOrderedDrawPileと完全なRNGを
  含む旨を明記。
- `RESTORE`ノードに、OrderedDrawPile等を含めた厳密復元である旨を追記。
- 新規ノート`NOTE_ORDERED_TRUTH`: Exact Emulator State(Ordered)とRL評価が参照してよい
  Unordered/Hidden-Order表現の区別を明記。
- 新規ノート`NOTE_RESHUFFLE_DETERMINISM`: OrderedDrawPile全体・関連状態・完全なShuffle RNGを
  Restoreすれば、同一Semantic Action系列に対する将来の再shuffleが一致するという決定性の根拠と、
  その根拠がEmulatorのシャッフルアルゴリズム(Fisher-Yates系UnstableShuffle)の性質
  (対象リストの初期順序とRNG状態のみに依存)に由来すること、およびこの保証は
  山札順不明入力から仮説的に確定させた順序には及ばないことを明記した
  (前回調査`deck_unordered_input_shuffle_proposal.v1.md`の「UnstableShuffleの入力順依存性」
  の知見を反映)。

### `mermaid_combat_branch_scheduler_detail.mermaid`

新規ノート`NOTE_ORDERED_DRAWPILE`を追加。HOLDER_STEP/BOOTSTRAP_STEP/SUB_RESTOREいずれの
Worker GameInstanceも、正しいDraw処理のためOrderedDrawPileをそのまま保持・操作すること、
この情報をEvaluator/Policy側へ転送してはならないことを明記した。

### `mermaid_combat_main_loop_detail.mermaid`

`PENDING_STATIC`ノードの説明を拡張し、Main-observed Pendingの静的評価経路で使われる
Evaluatorにも、真のOrderedDrawPileをそのまま渡さずUnordered相当の集計特徴量のみを渡すことを
明記した。

### `mermaid_combat_commit_detail.mermaid`

新規ノート`NOTE_DRAWPILE_HIDDEN_COMMIT`を追加。STAGE1(Hypothesis評価)・
COMPARE_PLAIN(RNG非依存のHeuristic/Model scoring)いずれの評価関数も、真のOrderedDrawPileを
そのままスコア関数の入力として渡さないことを明記した。

### `mermaid_combat_fault_worker_detail.mermaid`

変更なし(本テーマに直接関連する箇所がないため)。

## 図間整合性・構文検証

- 全8図についてnode参照の完全性をプログラム的に検証(未定義参照なし)。
- 全8図についてbracket/brace/quote balanceを検証(全て整合)。
- 全8図について`@mermaid-js/mermaid-cli` v11.16.0による実際のSVGレンダリングを実施し、
  8図中8図が成功した(既存のクォート済みラベル記法に従って追記したため、新規の構文エラーは
  発生しなかった)。詳細は`docs/architecture/combat/SVG_RENDER_LOG.md`「第2回検証」を参照。
- `docs/architecture/combat/MANIFEST.sha256`を再計算・再検証済み(`fault_worker_detail`のみ
  ハッシュ不変)。

## 結論

Combat Mermaid Diagram群に、「Emulatorが内部で保持する正確なOrderedDrawPile(Exact Emulator
State)」と「RL側の探索・評価が使うべきUnordered/Hidden-Order表現」の区別を、rough図・
7詳細図のうち6詳細図(fault_worker_detailを除く)に反映した。Restore経路がOrdered情報＋
完全なShuffle RNGを用いて将来の再shuffleの一致を保証する性質も明記した。

コード変更は行っていない。実装には進まず、ここで停止する。
