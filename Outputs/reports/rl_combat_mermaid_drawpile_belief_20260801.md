# Combat Mermaid Diagram 更新報告: 山札順の間接漏洩修正 — DrawPile Belief導入 (2026-08-01)

前提: Combat Mermaid正式ベースライン`docs/architecture/combat/`(直前commit`ead7e4c`)。
本作業は仕様整理と図の修正のみであり、**ランタイムコードの変更は一切行っていない。**

## 背景

前回作業で「Evaluatorへ真のOrderedDrawPileを直接渡さない」という直接漏洩対策を図へ反映したが、
監督者指摘の通り、**真のOrderedDrawPileを使って実行した単一Branch探索の結果を、そのままPolicyの
Action評価(win/loss判定)に使うと、生DTOを渡さなくても結果を通じて山札順が間接的に漏洩する**という
問題が残っていた。単一Branchのスコアは、そのBranchが実際にたどった未来のDraw結果に直接依存するため、
特徴量として渡さなくても「どちらのActionがより高スコアだったか」という比較結果自体に真の山札順の
影響が織り込まれてしまう。本作業ではこの間接漏洩を構造的に防ぐ設計を図へ反映した。

## 対応した7項目

1. **実山札順を使った単一Branch探索をPolicyのAction評価に使用しない**: 標準比較経路
   (win/loss判定に使う経路)では、真のOrderedDrawPileを一切参照せず、必ずDrawPile Belief
   仮説を経由してからBranch Workerへ投入する構造とした。
2. **Hidden-Order Beliefの導入**: `rng_hypothesis_detail`に`PUBLIC_MULTISET`(公開情報から
   算出した残りDrawPileのカード多重集合)→`BELIEF_GEN`(その多重集合と整合するOrdered DrawPile
   仮説をH1..Hnそれぞれに生成)という新規機構を追加した。
3. **同一仮説集合H1..HnでのEmulator実行とスコア集約**: 既存のRNG Hypothesis機構
   (`GRID`→`DERIVE_STD`→`WORK_ITEM_MULTI_STD`→`GROUP_BY_ROOT_ACTION`)をそのまま再利用し、
   各Hypothesis IDを「RNG成分＋DrawPile Order成分」の組として拡張した。DrawPile Beliefを
   RNG Hypothesisとは別の並行機構として新設するのではなく、既存の集約ロジック
   (共通H集合・最低coverage・欠損ペナルティ)にそのまま統合した。
4. **Exact State層とBelief/Search層の分離**: Main自身の真のOrderedDrawPileは正本保存
   (CaptureSnapshot)・実行(Main GameInstance.Step)・Capture／Restoreにのみ使用し、
   Belief生成やAction評価には使用しないことを明記した(`NOTE_EXACT_VS_BELIEF_LAYER`)。
   Search CoordinatorがRoot Snapshotへ真の値でアクセスするのはExact State層
   (Restore/Replayの忠実性・PUBLIC_MULTISET算出)に限られる。
5. **用語改称**: 「UnorderedDrawPile相当の集計特徴量」という表現を、単なる集計特徴量を指す
   箇所全て(4箇所: `main_loop_detail`・`commit_detail`・`candidate_pipeline_detail`・
   `rng_hypothesis_detail`)で「Order-Masked Observation(Hidden-Order Features)」へ改称した。
   山札順仮説集合は新規概念「DrawPile Belief」として明確に分離した(集計特徴量とは別物)。
6. **Shuffle説明の分離**: vendored engineの実ソースを直接確認し、戦闘開始時の
   `UnstableShuffle`(`Player.PopulateCombatState`→`CardPile.RandomizeOrderInternal`。
   Fisher-Yates、シャッフル対象リストの初期順序に依存)と、戦闘中再shuffleの
   `StableShuffle`(`CardPileCmd.Shuffle`→`ListExtensions.StableShuffle`。対象リストを
   先にIComparable実装で正規化ソートしてからUnstableShuffleを適用するため、初期順序に
   依存せずRNG状態のみで決定される)が、実際に異なるメソッドであることを確認し、図へ反映した。
7. **仮説内決定性と真の順序一致の区別**: 「DrawPile Belief仮説を使ったRestore/Replayの
   仮説内決定性(同じ仮説＋同じ入力なら常に同じ結果)は成立するが、その仮説自体が真のゲームの
   実際の山札順と一致することは保証されない」という区別を明記した。ただし、戦闘中の
   `StableShuffle`は入力順に依存しないため、捨て札の中身(公開情報)とShuffle RNG状態を
   正しく捕捉していれば、そのshuffleの結果は仮説の如何によらず真の結果と一致しうる、という
   細部の性質も併記した。

## エンジンソース確認による重要な発見

`Outputs/azure_stage_20260723_122305/STS2_Emulator/`配下の実ソースを直接確認し、以下を確認した
(前回調査の「UnstableShuffleは入力順に依存する」という知見をさらに精緻化するもの)。

- `MegaCrit.Sts2.Core.Extensions.ListExtensions.cs`に`UnstableShuffle`とは別に
  `StableShuffle`という拡張メソッドが実在する。`StableShuffle`は対象リストを
  `IComparable<T>`で正規化ソートしてから`UnstableShuffle`を適用するため、
  「結果は初期順序に依存せずRNG状態のみで決定される」という、doc commentに明記された
  通りの性質を持つ。
- 戦闘開始時のDrawPile初期化(`Player.PopulateCombatState`)は`UnstableShuffle`(入力順依存)を
  使用する。
- 戦闘中の「山札が尽きて捨て札から再構成」イベント(`CardPileCmd.Shuffle`)は`StableShuffle`
  (入力順非依存)を使用する。

この発見により、「山札順が公開情報から予測困難な度合い」は戦闘開始時と戦闘中再shuffleとで
性質が異なることが明らかになった。戦闘中再shuffleは、捨て札の中身が公開情報である以上、
Shuffle RNG状態を正しく捕捉していれば実質的に真の結果を予測できる。一方、戦闘開始時の
初期山札順は、永続Deckの並び(それ自体が完全に公開情報とは言い切れない)に依存するため、
真の順序の予測はより困難である。DrawPile Belief機構は、この2つの性質を区別せず一律に
「公開情報と整合する仮説の集合」として扱う設計としている。

## 修正した図

`mermaid_rough_combat.mermaid` / `mermaid_combat_candidate_pipeline_detail.mermaid` /
`mermaid_combat_branch_scheduler_detail.mermaid` / `mermaid_combat_snapshot_replay_detail.mermaid` /
`mermaid_combat_rng_hypothesis_detail.mermaid` / `mermaid_combat_commit_detail.mermaid` の6図。
`mermaid_combat_main_loop_detail.mermaid`は用語改称(UnorderedDrawPile→Order-Masked Observation)
のみ。`mermaid_combat_fault_worker_detail.mermaid`は変更なし(本テーマに直接関連する箇所がないため)。

### `mermaid_combat_rng_hypothesis_detail.mermaid`(最も大きな変更)

- `CONSUME_CHECK`の判断基準を「未来の非公開RNG」から「未来の非公開RNG、または現在のOrderedDrawPile
  の並び順」へ拡張し、`PASSTHROUGH`の適用条件に「DrawPileの並び順にも依存しないこと」を追加した。
- `TRUE_RNG_OK`を「Root SnapshotのRNG・OrderedDrawPileをそのまま使用してよい」へ拡張し、
  「Action評価目的の探索候補比較には使わない」という限定を明記した。
- 新規ノード`PUBLIC_MULTISET`・`BELIEF_GEN`を追加し、`STANDARD_SET`→`GRID`→`DERIVE_STD`の
  既存経路へ統合した。
- 新規ノート`NOTE_NO_SINGLE_BRANCH_TRUE_ORDER`(単一Branch間接漏洩防止)、
  `NOTE_EXACT_VS_BELIEF_LAYER`(層分離)、`NOTE_BELIEF_DETERMINISM`(仮説内決定性と真の順序の区別)
  を追加した。
- `PRIVACY`/`BOUNDARY_TABLE`を拡張し、単一Branch間接漏洩の禁止とExact State層/Belief層分離を
  責任境界表へ明記した。

### `mermaid_combat_snapshot_replay_detail.mermaid`

`NOTE_RESHUFFLE_DETERMINISM`を全面的に書き直し、UnstableShuffle(戦闘開始時)／StableShuffle
(戦闘中再shuffle)の区別と、それぞれの決定性・真の順序一致可能性の違いを明記した。
`NOTE_ORDERED_TRUTH`も、単一Branch間接漏洩の禁止とExact State層/Belief層分離への参照を追加した。

### `mermaid_combat_branch_scheduler_detail.mermaid`

`NOTE_ORDERED_DRAWPILE`を修正し、「Worker GameInstanceが保持する並び順は常に真の順序とは限らず、
標準比較経路ではDrawPile Belief仮説、真のRNGを使ってよい例外用途でのみ真の順序」という区別を
明記した。

### `mermaid_combat_commit_detail.mermaid`

`STAGE1`・`GROUP_BY_ROOT_ACTION`のHypothesis ID定義を「RNG成分＋DrawPile Order成分の組」へ拡張し、
`NOTE_DRAWPILE_HIDDEN_COMMIT`に単一Branch間接漏洩禁止の言及を追加した。

### `mermaid_rough_combat.mermaid`

`DRAWPILE_NOTE`を全面的に書き直し、7項目全ての要点(単一Branch禁止・DrawPile Belief・層分離・
用語改称・Shuffle分離・仮説内決定性の限界)を1箇所に要約し、各詳細図の該当ノートへの参照を付けた。

## 図間整合性・構文検証

- 全8図についてnode参照の完全性をプログラム的に検証(未定義参照なし)。
- 全8図についてbracket/brace/quote balanceを検証(全て整合)。
- 全8図について`@mermaid-js/mermaid-cli` v11.16.0による実際のSVGレンダリングを実施し、
  8図中8図が成功した。詳細は`docs/architecture/combat/SVG_RENDER_LOG.md`「第3回検証」を参照。
- `docs/architecture/combat/MANIFEST.sha256`を再計算・再検証済み(`fault_worker_detail`のみ
  ハッシュ不変)。

## 結論

真のOrderedDrawPileを使った単一Branch探索結果がAction評価へ間接的に漏洩する問題を、
DrawPile Belief(H1..Hn仮説集合)を既存のRNG Hypothesis機構へ統合する形で構造的に解消した。
RL側の内部構造をExact State層(正本保存・実行・Capture/Restore専用)とBelief/Search層
(Action評価専用)へ明示的に分離し、戦闘開始時UnstableShuffleと戦闘中再shuffleのStableShuffleの
性質の違い(エンジン実ソースで確認済み)も図へ反映した。

コード変更は行っていない。実装には進まず、ここで停止する。
