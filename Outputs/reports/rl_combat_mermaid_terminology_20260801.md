# Combat Mermaid Diagram 更新報告: 用語・契約修正 (2026-08-01)

前提: Combat Mermaid正式ベースライン`docs/architecture/combat/`(直前commit`feec2f1`)。
本作業は仕様整理と図の修正のみであり、**ランタイムコードの変更は一切行っていない。**

## 背景

DrawPile Belief方針の承認を受け、監督者指摘の以下4点(用語・契約の精緻化)を反映した。

## 対応内容

### 1. OrderedDrawPileの分離(Authoritative／Hypothetical／Concrete)

`OrderedDrawPile`を以下の3語へ分離した。

- **Authoritative OrderedDrawPile**: Main GameInstanceが保持する実際の並び順。唯一の「真の状態」。
- **Hypothetical OrderedDrawPile**: DrawPile Belief(BELIEF_GEN)が公開情報と整合するよう生成した
  仮説的な並び順。
- **Concrete OrderedDrawPile**: 両者の総称(「具体的に1つの並び順が定まっている」という共通点で
  括った上位概念。Order-Masked Observationのような並び順を持たない集計特徴量とは区別される)。

Belief Snapshot(Hypothetical OrderedDrawPile)を「真の状態」と記載していた箇所を全て修正し、
「仮説であり真の状態ではない」ことを明記する表現へ統一した。修正対象: `mermaid_rough_combat`・
`mermaid_combat_candidate_pipeline_detail`・`mermaid_combat_branch_scheduler_detail`・
`mermaid_combat_snapshot_replay_detail`・`mermaid_combat_rng_hypothesis_detail`・
`mermaid_combat_commit_detail`(6図)。`mermaid_combat_main_loop_detail`は該当1箇所のみ修正。

### 2. Search Hypothesis IDへの改称

RNG成分とDrawPile Order成分を含むIDを、これまで「RNG Hypothesis ID」と呼んでいたが、
RNGだけでなくDrawPile Order成分も含む複合識別子であることを明確にするため
**「Search Hypothesis ID」**へ改称した。以下の4箇所で統一的に使用されていることを確認・修正した。

- **Decision Context**(`mermaid_combat_snapshot_replay_detail`のDC_DEF): Decision Contextの
  構成要素としてのSearch Hypothesis IDフィールド。
- **Lease**(`mermaid_combat_branch_scheduler_detail`のLEASE_DEF): Leaseが
  (Decision Context, Search Hypothesis ID)の組に紐づく。
- **WorkItem**(`mermaid_combat_rng_hypothesis_detail`のWORK_ITEM系): WorkItemに付与される
  タグとしてのSearch Hypothesis ID。
- **Commit**(`mermaid_combat_commit_detail`のGROUP_BY_ROOT_ACTION): Root Action単位の集約キーから
  除外するのはSearch Hypothesis ID。

機械的な文字列置換(3ファイルで計25箇所)に加え、周辺の説明文もRNG専用の記述からRNG成分＋
DrawPile Order成分の複合識別子としての説明へ書き換えた。

### 3. DrawPile Belief生成契約の精緻化とPUBLIC_MULTISETの再確認

**契約の明文化**: DrawPile Belief生成(`BELIEF_GEN`)は「現在のDrawPile配列indexを完全に破棄し、
公開状態だけでcanonicalizeした多重集合から、Search Hypothesis IDのRNG成分をHypothesis seedとして
順列を生成する」契約であることを明記した(Emulator本体のStableShuffleと同じ考え方: 正規化＋
seed駆動の順列生成)。

**PUBLIC_MULTISETの再確認(監督者指摘通り、単純な`Deck−他Pile`では不十分と判明)**:
vendored engine実ソースを調査し、以下を確認した。

- `PileType.Deck`は戦闘中に生成されるカード(Wound/Burn/Shiv等)の正当な配置先ではない。
  `CardPileCmd.AddGeneratedCardToCombat`等は`IsCombatPile()`(Hand/Draw/Discard/Exhaust/Playの
  5種のみが該当)へのガードを持ち、Deckへの生成カード配置を許可しない。
- `Deck`フィールド自体は「部屋間で持ち越す永続デッキ」であり、`PileType.cs`のdoc commentに
  「戦闘開始時に全カードがDrawPileへクローンされ、以降の戦闘中変更はDeckへ反映されない」旨が
  明記されている。

この結果、単純な「Deck全体−Hand−DiscardPile−ExhaustPile−PlayPile」という当初案は、戦闘中に
生成されたカード(まだ手札・捨て札・墓地・場のいずれにも降りていない、DrawPile内に潜在する
生成カード)を見落とし、残りDrawPileの多重集合を過小評価するバグを含んでいた。

**修正後の算出式**: (戦闘開始時点のDeck由来の初期複製カード多重集合 ＋ CombatHistoryの
CardGeneratedEntryが示す生成カード多重集合) − 現在のHand／DiscardPile／ExhaustPile／PlayPileの
多重集合。全てCardId基準の多重集合演算とし、InstanceIdは戦闘開始時クローンにより不一致になるため
使用しない。戦闘中に直接persistent Deckへカードを追加する効果(一部Curse生成等)はcombat pileへ
配置されないため、この式には影響しない。

この式が全ての生成カード経路を実際に網羅しているか(CardGeneratedEntryが常にCardIdを記録している
か等)は未確認であり、Emulator担当への確認事項として`NOTE_PUBLIC_MULTISET_GENERATED_CARDS`に
記録した。

### 4. StableShuffle決定性の3要素化

前回報告(`rl_combat_mermaid_drawpile_belief_20260801.md`)では、StableShuffleの結果は
「(canonicalizeされたcard multiset, 完全なShuffle RNG)」の2要素で決まると記載していたが、
これは不正確だった。vendored engine実ソース(`CardPile.RandomizeOrderInternal`・
`CardPileCmd.Shuffle`)を再確認したところ、いずれも`Hook.ModifyShuffleOrder`／
`Hook.AfterShuffle`という、shuffle結果を後処理しうるHook呼び出しを含むことを確認した。
これらのHookは、shuffleに介入しうるRelic/Powerが有効な場合、shuffle結果へ影響を与えうる。

**修正後**: StableShuffleの結果は「(canonicalizeされたcard multiset, 完全なShuffle RNG,
hook関連状態[shuffleへ介入しうるRelic/Powerの現在状態])」の3要素の組で決まる。
仮説Branch(Hypothetical OrderedDrawPile)における戦闘中再shuffleの結果が真のゲームの結果と
一致するのは、その再shuffle地点でこの3要素全てが真のゲームの状態と一致する場合に限られる、
という限定を明記した。

## 修正した図

`mermaid_rough_combat.mermaid` / `mermaid_combat_candidate_pipeline_detail.mermaid` /
`mermaid_combat_branch_scheduler_detail.mermaid` / `mermaid_combat_snapshot_replay_detail.mermaid` /
`mermaid_combat_rng_hypothesis_detail.mermaid`(最も大きな変更。全面的に書き直し) /
`mermaid_combat_commit_detail.mermaid` の6図。`mermaid_combat_main_loop_detail.mermaid`は
用語統一(1箇所)のみ。`mermaid_combat_fault_worker_detail.mermaid`は変更なし。

## 図間整合性・構文検証

- 全8図についてnode参照の完全性をプログラム的に検証(未定義参照なし)。
- 全8図についてbracket/brace/quote balanceを検証(全て整合)。
- 全8図について`@mermaid-js/mermaid-cli` v11.16.0による実際のSVGレンダリングを実施し、
  8図中8図が成功した。詳細は`docs/architecture/combat/SVG_RENDER_LOG.md`「第4回検証」を参照。
- `docs/architecture/combat/MANIFEST.sha256`を再計算・再検証済み(`fault_worker_detail`のみ
  ハッシュ不変)。
- 用語の網羅的な棚卸し(`grep`によるプログラム的確認)を実施し、「真の状態」という表現が
  Hypothetical OrderedDrawPileに誤って付与されている箇所がないこと、「RNG Hypothesis ID」の
  残存がないこと、「OrderedDrawPile」が全てAuthoritative/Hypothetical/Concreteのいずれかで
  修飾されていることを確認した。

## 結論

OrderedDrawPileの3分類(Authoritative／Hypothetical／Concrete)、Search Hypothesis IDへの
用語統一、DrawPile Belief生成契約の明文化とPUBLIC_MULTISETの算出式修正(生成カード考慮)、
StableShuffle決定性の3要素化(hook関連状態を追加)を反映した。PUBLIC_MULTISETの算出式が
実際に全ての生成カード経路を網羅しているかは、継続する未確認事項としてEmulator担当への
確認を要する。

コード変更は行っていない。実装には進まず、ここで停止する。
