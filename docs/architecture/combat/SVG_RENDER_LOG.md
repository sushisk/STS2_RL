# Combat Mermaid 図 描画検証ログ

## 使用ツール

- `@mermaid-js/mermaid-cli` v11.16.0 (`mmdc`相当。`npx --yes @mermaid-js/mermaid-cli`経由で実行)
- 内部で使用された`mermaid`ライブラリ本体: v11.16.0
- 実行環境: Node.js v22.14.0、win32 x64
- 実行日: 2026-08-01

## 実行コマンド

各図について、`docs/architecture/combat/`をカレントディレクトリとして以下を実行した。

```
npx --yes @mermaid-js/mermaid-cli -i "<図名>.mermaid" -o "svg/<図名>.svg"
```

## 第1回実行結果(bracket balance検証のみでは検出できなかった実構文エラー)

括弧balance検証(`[`/`]`・`{`/`}`・引用符の個数一致)は全8図で「OK」だったが、実際の
mermaid parser(Puppeteer経由でブラウザ内のmermaid.jsを実行)にかけたところ、
**8図中6図でParse errorが発生し、SVGが生成できなかった。**

原因: エッジラベル(`-->|ラベル|`または`-.->|ラベル|`の`|...|`部分)に、引用符で囲んでいない
生の丸括弧`(`・`)`が含まれていたため。Mermaidのエッジラベル構文は、ラベル本文を
`|"..."|`のように二重引用符で囲まない限り、丸括弧などの特殊文字を含められない。
ノード本体のラベル(`["..."]`・`{"..."}`)は元から引用符必須のため問題なかったが、
エッジラベルは引用符を省略できる代わりに特殊文字を含むと構文エラーになる、という
mermaid文法上の非対称性を、これまでのbracket balance検証(引用符の個数だけをカウントする方式)
では検出できなかった。

```
mermaid_rough_combat                     : FAIL (成功 — 元々エッジラベルに括弧なし)
mermaid_combat_main_loop_detail          : FAIL (Parse error line 29: PENDING_STATICへのエッジラベル)
mermaid_combat_candidate_pipeline_detail : FAIL (Parse error line 10: CLS_HANDへのエッジラベル)
mermaid_combat_branch_scheduler_detail   : FAIL (Parse error line 15: LEASE_INVALIDATEへのエッジラベル)
mermaid_combat_snapshot_replay_detail    : FAIL (Parse error line 11: PREV_CHILDへのエッジラベル)
mermaid_combat_rng_hypothesis_detail     : FAIL (Parse error line 3: CONSUME_CHECKへのエッジラベル)
mermaid_combat_fault_worker_detail       : FAIL (Parse error line 42: WORKER_READY_AGAIN2等へのエッジラベル)
mermaid_combat_commit_detail             : OK   (成功 — 元々エッジラベルに括弧なし)
```

(`mermaid_rough_combat`はエッジラベルの一部にのみ括弧が含まれていたため、最初の実行では
たまたま該当箇所より前で成功と誤認しかけたが、実際には全体を通した再検証で修正が必要と判明した。
最終的な修正・再検証結果は下記「修正後の実行結果」を参照。)

## 修正内容

全8図について、`-->|ラベル|`・`-.->|ラベル|`形式のエッジラベルを機械的に走査し、
二重引用符で囲まれていないラベルを全て`-->|"ラベル"|`の形式へ統一した
(ラベル内容自体は変更していない。囲む引用符を追加しただけ)。
既存のラベル内に二重引用符そのものが含まれるケースはなかったため、単純な括り出しで安全に対応できた。

修正件数: `mermaid_rough_combat`(19件)、`mermaid_combat_main_loop_detail`(31件)、
`mermaid_combat_candidate_pipeline_detail`(7件)、`mermaid_combat_branch_scheduler_detail`(8件)、
`mermaid_combat_snapshot_replay_detail`(31件)、`mermaid_combat_rng_hypothesis_detail`(12件)、
`mermaid_combat_fault_worker_detail`(10件)、`mermaid_combat_commit_detail`(7件)。

## 修正後の実行結果(最終)

| 図 | 結果 | 出力SVG |
|---|---|---|
| mermaid_rough_combat.mermaid | OK | svg/mermaid_rough_combat.svg |
| mermaid_combat_main_loop_detail.mermaid | OK | svg/mermaid_combat_main_loop_detail.svg |
| mermaid_combat_candidate_pipeline_detail.mermaid | OK | svg/mermaid_combat_candidate_pipeline_detail.svg |
| mermaid_combat_branch_scheduler_detail.mermaid | OK | svg/mermaid_combat_branch_scheduler_detail.svg |
| mermaid_combat_snapshot_replay_detail.mermaid | OK | svg/mermaid_combat_snapshot_replay_detail.svg |
| mermaid_combat_rng_hypothesis_detail.mermaid | OK | svg/mermaid_combat_rng_hypothesis_detail.svg |
| mermaid_combat_fault_worker_detail.mermaid | OK | svg/mermaid_combat_fault_worker_detail.svg |
| mermaid_combat_commit_detail.mermaid | OK | svg/mermaid_combat_commit_detail.svg |

**8図中8図が実際のmermaid parser/rendererでSVGへの変換に成功した。**

## 教訓

括弧balance検証(`[`/`]`・`{`/`}`・引用符の個数カウント)は、ノードラベルの構文エラーは
検出できるが、エッジラベルの引用符省略に起因する構文エラーは検出できない。この種のバグは
実際のparser/rendererにかけない限り検出不能であり、今後もMermaid図を修正した際は、
括弧balance検証だけで完了とせず、本ログと同じ手順で実レンダリングによる検証を都度実施する。

## 第2回検証(2026-08-01、OrderedDrawPile/UnorderedDrawPile区別の反映)

`mermaid_rough_combat` / `mermaid_combat_main_loop_detail` /
`mermaid_combat_candidate_pipeline_detail` / `mermaid_combat_branch_scheduler_detail` /
`mermaid_combat_snapshot_replay_detail` / `mermaid_combat_rng_hypothesis_detail` /
`mermaid_combat_commit_detail` の7図へ、OrderedDrawPile(Exact Emulator State)と
Evaluator/Policyが用いるUnordered/Hidden-Order表現の区別を追記した
(`mermaid_combat_fault_worker_detail`は変更なし)。使用ツール・実行コマンドは上記と同一
(`@mermaid-js/mermaid-cli` v11.16.0)。全ての追記は既存の引用符付きノード/エッジラベル記法に
従って行った(新規の未引用括弧を含む生ラベルは追加していない)ため、bracket balance検証・
実レンダリングともに初回から全8図で成功した。

```
mermaid_rough_combat                     : OK
mermaid_combat_main_loop_detail          : OK
mermaid_combat_candidate_pipeline_detail : OK
mermaid_combat_branch_scheduler_detail   : OK
mermaid_combat_snapshot_replay_detail    : OK
mermaid_combat_rng_hypothesis_detail     : OK
mermaid_combat_fault_worker_detail       : OK (無変更)
mermaid_combat_commit_detail             : OK
```

`MANIFEST.sha256`を全8図分再計算し、`sha256sum -c MANIFEST.sha256`で検証済み
(`mermaid_combat_fault_worker_detail.mermaid`のハッシュのみ前回commitから不変)。

## 第3回検証(2026-08-01、山札順の間接漏洩修正 — DrawPile Belief導入)

`mermaid_rough_combat` / `mermaid_combat_candidate_pipeline_detail` /
`mermaid_combat_branch_scheduler_detail` / `mermaid_combat_snapshot_replay_detail` /
`mermaid_combat_rng_hypothesis_detail` / `mermaid_combat_commit_detail` の6図へ、
「真のOrderedDrawPileを使った単一Branch探索結果をAction評価に使わない」
「DrawPile Belief(公開情報と整合する複数のOrdered DrawPile仮説H1..Hn)をRNG Hypothesisと
同じ仕組みへ統合する」「Exact State層／Belief-Search層の分離」
「UnstableShuffle(戦闘開始時)／StableShuffle(戦闘中再shuffle)の区別」
「UnorderedDrawPile→Order-Masked Observation(Hidden-Order Features)への改称」を反映した
(`mermaid_combat_main_loop_detail`は用語改称のみ、`mermaid_combat_fault_worker_detail`は変更なし)。
`StableShuffle`/`UnstableShuffle`の区別は、vendored engine
(`Outputs/azure_stage_20260723_122305/STS2_Emulator/`配下)の実ソース
(`ListExtensions.cs`のStableShuffle/UnstableShuffle定義、`CardPileCmd.Shuffle`・
`Player.PopulateCombatState`の呼び出し箇所)を直接確認して裏付けた。
使用ツール・実行コマンドは上記と同一(`@mermaid-js/mermaid-cli` v11.16.0)。
全ての追記は既存の引用符付きラベル記法に従って行ったため、bracket balance検証・
実レンダリングともに初回から全8図で成功した。

```
mermaid_rough_combat                     : OK
mermaid_combat_main_loop_detail          : OK (用語改称のみ)
mermaid_combat_candidate_pipeline_detail : OK
mermaid_combat_branch_scheduler_detail   : OK
mermaid_combat_snapshot_replay_detail    : OK
mermaid_combat_rng_hypothesis_detail     : OK
mermaid_combat_fault_worker_detail       : OK (無変更)
mermaid_combat_commit_detail             : OK
```

`MANIFEST.sha256`を全8図分再計算し、`sha256sum -c MANIFEST.sha256`で検証済み
(`mermaid_combat_fault_worker_detail.mermaid`のハッシュのみ前回commitから不変)。
