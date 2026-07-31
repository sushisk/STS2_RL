# RL担当レビュー報告 — `mermaid_rough_combat.mermaid`更新版 (2026-08-01)

対象: 更新後の`C:\STS2_Mermaid\mermaid_rough_combat.mermaid`(Body当該ラウンド内限定継続・
Body/Sub正規化統合・Fault等の詳細図分離を反映した版)。前回報告書
(`rl_combat_rough_worker_pool_review_20260801.md`)への回答として、「均一Restore方式」の
主張は実測に基づき却下されたことを確認した。**本ラウンドもレビューのみ、いかなるファイルも
変更していない。**

## 結論

**この設計に合意できる。** 前回指摘した3つの構造的欠落は、いずれも今回の更新で解消されて
いることを確認した。ただし、この設計特有の新しい論点が1つ発生しているため、実装前に
検討・検証してほしい点として報告する。

## 1. 前回指摘事項の解消確認

| 前回の指摘 | 今回の対応 | 判定 |
|---|---|---|
| 1-A: 次ラウンドのRoot Snapshot供給が無い | `SELECT_NEXT → NEXT_ROOTS["選択したBranchのChild Snapshotを次ラウンドのRoot Snapshot群にする"] → ROOT_CONTEXT`で明示的に解決 | **解消** |
| 1-B: Body継続性・引き継ぎロジックが未定義 | Bodyの非Restore継続を「当該ラウンド内のみ」に厳密に限定し、ラウンド境界で`NORMALIZE`によりBody/Subを同一の結果形状(Observation・Child Snapshot・Sequence)へ統合、`NEXT_ROOTS`が明記する通り次ラウンドでは「Body／Subの役割は再選定」——旧Bodyの状態を次ラウンドへ持ち越す曖昧さが消えた | **解消**(詳細は§2で新たな論点を報告) |
| 1-C: Main/Branch双方でFault/Rejection分岐が無い | `ROUGH_NOTE`で「Fault・Rejection・Timeout・Fallbackは詳細図で定義する」と明示的に対象外化——欠落ではなく意図的な分離と確認できる | **解消(意図的分離として妥当)**、ただし§4に軽微な改善提案あり |
| 2-A: ActionContinuation分類の実現方法が不明瞭 | `BRANCH_BOUNDARY -->|新しい選択が発生| CONTEXT_UPDATE --> CLASSIFY`が、ラウンド内で発生する新しい選択(継続選択含む)を全て同じCLASSIFY/PRE_EVAL/PRUNEパイプラインへ回す設計になっており、Workerが独自のcontinuation自動解決ループを持たない前提と整合する | **解消** |
| 2-B: RNG方式Bの適用箇所が図に無い | `SNAPSHOT -.-> RNG_NOTE["必要な場合はRestore前に仮説RNGを組み込んだ派生Snapshotを作成"]`で明示 | **解消** |
| 3: `APPLY_MAIN`のノード名が状態コピーと誤読されうる | `EXECUTE_MAIN["採用したAction／SequenceをMain Game Instance上で実行"]`へ改名——「実行」であって「コピー」ではないことが明確になった | **解消** |
| 3: `PRE_EVAL`が非Restoreの静的評価であることの明示 | 「分類別の**軽量**評価関数で事前評価」と明記 | **解消** |

## 2. 新たに検討・検証してほしい点(合意を妨げるものではないが、実装前に対処すべき)

### 2-A. 継続選択のカスケードによるSub Worker再構築コストの増大(性能面、要認識)

`BRANCH_BOUNDARY -->|新しい選択が発生| CONTEXT_UPDATE --> CLASSIFY --> ... --> PRUNE -->
BODY_SELECT／SUB_SELECT`というループは、ラウンド内で新しい選択(主にActionContinuation
スコープ、現行実装の実測では全Choice決定の約96%を占める)が発生するたびに再度実行される。
このとき`SUB_SELECT --> QUEUE["Sub BranchをWorker Queueへ投入<br/>Root Snapshot＋Sequence
Prefixを渡す"]`は、**常にラウンド開始時点のRoot Snapshotから、その時点までの全Sequence
Prefixを再生する**設計になっている(前回・今回共通、`mermaid_combat_target_worker_pool.mermaid`
から一貫する既存方針)。

これ自体は既存方針との整合性があり否定しないが、**継続選択が同一ラウンド内で複数回連鎖する
ケースでは、後になるほどSub WorkerのRestore+Prefix再生コストが線形に増加する**——1ラウンド内で
K回連鎖すると、K回目に新規投入されるSub Workerは(1回目より)長いPrefixを再生する必要がある。
「Restoreがボトルネック」という前提を踏まえると、この「ラウンド内連鎖によるコスト累積」は
軽視すべきでない。対処案としては次の2つが考えられるが、**いずれを採るかは設計判断であり、
ここでは選択肢の提示に留める**:

1. 現状のまま(常にラウンドRootから再生)——実装が単純、Body/Subの非対称性を連鎖選択の
   途中に持ち込まずに済む。カスケードが浅い(数回程度)なら実害は小さい可能性が高い。
2. 連鎖選択が発生した時点のWorker(それがBodyであれSubであれ)の生きた状態から、
   その場でSub Workerを分岐させる——Restoreコストは下がるが、Body/Sub非対称性を
   ラウンド内の任意の深さへ拡張することになり、今回導入した「非対称性はラウンド内限定」
   というシンプルな境界がぼやける。

**推奨**: まずは案1(現状のまま)で実装し、実測でカスケード連鎖コストが無視できないと
判明した場合にのみ案2を検討する——今回の設計判断(実測に基づき均一Restore方式を却下した
判断)と同じ姿勢を、この新しい論点にも適用するのが一貫していると考える。

### 2-B. 継続選択カスケードの深さ上限が図に存在しない

現行実装には`MAX_CONTINUATION_STEPS = 50`という安全装置があり(`live_combat_session.py`)、
これを超えると`RuntimeError`で停止する。今回の設計では`CONTEXT_UPDATE → CLASSIFY`の
ループに相当する上限が図のどこにも明示されていない——`ROUGH_NOTE`が「Timeout・Fallback」を
詳細図で扱うと述べているため、**この深さ上限もその詳細図に含まれる想定と理解した**。
念のため、詳細図の設計時に「ラウンド内の連鎖選択回数上限」を明記することを推奨する
(実装漏れ防止のための確認事項であり、今回の図自体への修正要求ではない)。

### 2-C. 新規に必要な決定性検証(実装前に追加すべきテスト)

この設計の正しさは、「Bodyがラウンド内で連続Stepを重ねて到達した状態」と「同じ論理
Sequenceを Root Snapshotから単純にRestore+再生して到達した状態」が**バイト単位で
一致する**ことに依存している。この関与全体を通じて確立してきた決定性テスト
(`test_restore_step_determinism_reselects_fresh_action`等)は、いずれも「2回の独立した
Restore同士」を比較するものであり、**「Restoreを介さない連続Step」と「Restore+再生」を
直接比較したテストはこれまで一度も実施されていない**。

Bodyの非Restore継続はGameInstance.Step()自体の決定性(既に確立済み)に基づく限り成立する
はずだが、この特定の比較(連続Step結果 vs Restore後の同一Sequence再生結果、双方の
CaptureSnapshot一致)は明示的に検証されたことがない。**実装着手前に、この新しい比較軸の
決定性テストを追加することを推奨する**(既存パターンの単純な拡張であり、大きな追加コストは
想定していない)。

## 3. 良い設計だと考える点(肯定的所見)

- Body/Sub非対称性を「ラウンド内限定」かつ「ラウンド境界で強制的に正規化・同列評価」という
  形にスコープを絞ったことで、前回懸念していた「引き継ぎロジックの曖昧さ」「監査可能性の
  喪失」がほぼ解消された。非対称性はWorker内部の実装詳細に留まり、Search Coordinatorから見た
  評価ロジックには一切漏れ出ない——関心の分離として適切。
- `NEXT_ROOTS`が「選択したBranchの**Child Snapshotを次ラウンドのRoot Snapshot群にする**」と
  複数形で書かれている点は、真のBeam幅(width > 1)を保つ設計として正しく一般化されている
  ——各survivorが独立して次ラウンドのCLASSIFY/PRE_EVAL/PRUNEへ入る、という読み方で
  相違ないか確認したい(ROOT_CONTEXTへの矢印が単一である点は表記上の簡略化と理解した)。
- Fault/Rejection/Timeout/Fallbackを明示的に「詳細図」へ分離する判断は妥当——この
  rough図が「探索の骨格」に集中できている。

## 4. 軽微な改善提案

`ROUGH_NOTE`が現在どのノードとも接続されていない、独立した注記ノードになっている。
Fault/Rejection/Timeout/FallbackがどのタイミングでMain Flow/Search Coordinator/Worker Poolへ
差し込まれるのかを示す点線矢印を`BOUNDARY`・`BRANCH_BOUNDARY`・`QUEUE`付近へ追加しておくと、
後で詳細図と統合する際に接続点を探す手間が省ける(必須ではなく、あくまで提案)。

## 5. まとめ

前回報告した3つの構造的欠落はいずれも適切に解消されており、この設計に**合意できる**。
実装前に対処・検証してほしい点は次の3つ(いずれもblockerではない):

1. §2-Aの連鎖選択コスト増大は、実測ベースで対処要否を判断する(まずは現状のまま実装)。
2. §2-Bの連鎖深さ上限は、Fault/Timeout詳細図に明記する。
3. §2-Cの「連続Step vs Restore再生」決定性テストを実装前に追加する。

## 6. 停止

指示の通り、ここで停止する。`mermaid_rough_combat.mermaid`を含め、いかなるファイルも
変更していない。
