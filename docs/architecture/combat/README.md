# Combat Mermaid 設計図 — 正本ディレクトリ

このディレクトリ(`docs/architecture/combat/`)が、Combat実行基盤アーキテクチャ設計図の**唯一の正本**である。
Git管理下(STS2_RLリポジトリ)にあるファイルのみが正式な設計契約として扱われる。

## 構成

```
docs/architecture/combat/
├── mermaid_rough_combat.mermaid                       上位契約(Rough Diagram)
├── mermaid_combat_main_loop_detail.mermaid            詳細図: Main Process
├── mermaid_combat_candidate_pipeline_detail.mermaid   詳細図: Search Coordinator候補分類
├── mermaid_combat_branch_scheduler_detail.mermaid     詳細図: Lease/Worker配布
├── mermaid_combat_snapshot_replay_detail.mermaid      詳細図: Decision Context/Restore+Replay
├── mermaid_combat_rng_hypothesis_detail.mermaid       詳細図: RNG Hypothesis
├── mermaid_combat_fault_worker_detail.mermaid         詳細図: Fault分類・Worker管理
├── mermaid_combat_commit_detail.mermaid               詳細図: 評価・Commit
├── deprecated/
│   └── mermaid_combat_target_worker_pool.mermaid      旧世代設計(履歴参照専用・実装契約として使用禁止)
├── svg/                                               上記8図を実際にmermaid-cliでレンダリングしたSVG
├── SVG_RENDER_LOG.md                                  レンダリング検証ログ(使用バージョン・コマンド・成否)
├── MANIFEST.sha256                                    正本8図のSHA-256一覧(`sha256sum -c MANIFEST.sha256`で検証可能)
└── README.md                                          このファイル
```

## 契約関係

`mermaid_rough_combat.mermaid`はアーキテクチャ方針・責任境界・不変条件を示す上位契約であり、
7つの詳細図はそれを具体化する詳細契約である。両者が矛盾した場合、どちらかを自動的に優先することはせず、
実装を停止して監督者判断を求める(詳細は`mermaid_rough_combat.mermaid`冒頭のコメントを参照)。

## `C:\STS2_Mermaid`との関係

`C:\STS2_Mermaid`(Gitリポジトリ管理外のローカルディレクトリ)は、設計図を編集する際の**編集用コピー**として
残す運用とする。**Git管理下にある本ディレクトリのファイルが唯一の正本であり、`C:\STS2_Mermaid`側の
内容が正本と異なる場合は本ディレクトリの内容を正とする。** `C:\STS2_Mermaid`で編集した内容は、
この`docs/architecture/combat/`へコピーし、commitして初めて正式な設計契約の変更として扱われる。
`C:\STS2_Mermaid`自体には同旨のREADMEを別途配置している。

## 検証方法

- ファイル完全性: `sha256sum -c MANIFEST.sha256`(このディレクトリ内で実行)
- 描画検証: `SVG_RENDER_LOG.md`に記載のコマンドで`@mermaid-js/mermaid-cli`を用いて再レンダリングできる。
  括弧balance検証などの簡易チェックだけでなく、実際のmermaid parser/rendererによる検証を行うこと
  (エッジラベル中の引用符省略による構文エラーは、簡易チェックでは検出できないことが実際に確認されている。
  `SVG_RENDER_LOG.md`の「教訓」参照)。

## 設計の背景・レビュー履歴

設計の経緯・Codexとの共同レビュー履歴・監督者承認までの記録は
`Outputs/reports/rl_combat_mermaid_*.md`および`rl_combat_mermaid_scope_review_20260801.md`を参照。
