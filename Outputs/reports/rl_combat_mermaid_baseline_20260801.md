# Combat Mermaid 成果物 正式ベースライン化 報告 (2026-08-01)

監督者による設計承認を受け、`docs/architecture/combat/`をCombat実行基盤アーキテクチャ設計図の
正式な正本ディレクトリとして確立した。全面的な設計レビューは再開せず、成果物管理と軽微な整合修正
のみを行った。

**本作業を通じ、ランタイムコードの変更は一切行っていない。**

## 対応内容

### 1. 正本8図をSTS2_RLリポジトリへ配置

`C:\STS2_Mermaid`のRough Diagram＋7詳細図を`docs/architecture/combat/`へ配置した
(8ファイル、後述のエッジラベル構文修正込み)。

### 2. `mermaid_combat_target_worker_pool.mermaid`をdeprecated/へ配置

前回作業で`C:\STS2_Mermaid\deprecated\`へ移動済みだった当該図を、
`docs/architecture/combat/deprecated/`へ同様に配置した。DEPRECATEDヘッダ
(履歴参照専用・実装契約として使用禁止・正本はRough Diagram＋7詳細図)は維持している。

### 3. `C:\STS2_Mermaid`を編集用コピーとして明記

`docs/architecture/combat/README.md`に、Git管理下のこのディレクトリが唯一の正本であり、
`C:\STS2_Mermaid`は編集用コピーである旨を明記した。あわせて`C:\STS2_Mermaid\README.md`
(Git管理外)にも同旨と運用ルール(編集→docs/architecture/combatへコピー→検証→commitして
初めて正式確定)を明記した。

### 4. Mermaid CLIによる実際のレンダリング検証

`@mermaid-js/mermaid-cli` v11.16.0(内部の`mermaid`ライブラリ本体も同バージョン、Node.js v22.14.0、
win32 x64)を用いて、8図全てを実際にSVGへ変換した。

**この過程で、括弧balance検証だけでは検出できなかった実際の構文エラーを発見した。**
8図中6図(`mermaid_combat_main_loop_detail`・`candidate_pipeline_detail`・`branch_scheduler_detail`・
`snapshot_replay_detail`・`rng_hypothesis_detail`・`fault_worker_detail`)でParse errorが発生し、
SVG生成に失敗した。原因は、エッジラベル(`-->|ラベル|`形式)に引用符で囲んでいない生の丸括弧が
含まれていたことで、Mermaidの構文上、エッジラベルは引用符(`|"ラベル"|`)で囲まない限り丸括弧等の
特殊文字を含められない、という文法上の制約に抵触していたためである。ノード本体のラベルは元々
引用符必須の記法だったため問題なく、エッジラベルのみで発生した。

対応として、全8図のエッジラベルを機械的に走査し、未引用のものを全て`|"ラベル"|`形式へ統一した
(ラベル内容自体は変更していない)。修正件数は`mermaid_rough_combat`(19件)、
`mermaid_combat_main_loop_detail`(31件)、`mermaid_combat_candidate_pipeline_detail`(7件)、
`mermaid_combat_branch_scheduler_detail`(8件)、`mermaid_combat_snapshot_replay_detail`(31件)、
`mermaid_combat_rng_hypothesis_detail`(12件)、`mermaid_combat_fault_worker_detail`(10件)、
`mermaid_combat_commit_detail`(7件)。修正後、**8図中8図が実際のparser/rendererでSVGへの変換に
成功した。**詳細は`docs/architecture/combat/SVG_RENDER_LOG.md`を参照。

### 5. 検証ログとSVGのGit管理

使用したMermaidバージョン・実行コマンド・修正前後の成否を`SVG_RENDER_LOG.md`へ記録し、
生成した8図分のSVGを`docs/architecture/combat/svg/`へ配置していずれもGit管理下に置いた。

### 6. Rough DiagramのBranchResultをOptional構造へ修正

`mermaid_rough_combat.mermaid`のNORMALIZEノードが「Observation・Child Snapshot・Plan Path」を
無条件の共通形式であるかのように記述していた(Stable経路・Terminal経路の両方からこのノードへ
接続されるにもかかわらず、Child Snapshotが常に含まれるかのような表現)。7詳細図側
(branch_scheduler_detail・commit_detail)で既に確立していた「Child SnapshotはStable時のみ、
Terminal ResultはTerminal時のみ含まれるOptional構造」に合わせ、Rough Diagram側も同じ表現へ修正した。

### 7. Main Loop図のNOTE_DISCARD修正

`mermaid_combat_main_loop_detail.mermaid`のNOTE_DISCARDが、Planned Sequence破棄条件の一つとして
「予定外のTerminal／Fault」を並記しており、StepResult FaultがあたかもDISCARD/RESYNC(通常の計画不一致)
経路の対象であるかのように読める表現になっていた。実際のグラフ構造では、StepResult Faultは
STEP_FAULT_CHECKで検出され、Transition Recordへの追記より前に即座にMain Combat Faultへ遷移する
(DISCARD/RESYNC経路を一切経由しない)。NOTE_DISCARDから「Fault」を破棄条件の列挙から除き、
StepResult Faultは別経路(NOTE_FAULT_FIRST／STEP_FAULT_JUMP)で即座にMain Combat Faultへ進む旨を
明記した。

### 8. SHA-256 manifest

正本8図のSHA-256ハッシュを`docs/architecture/combat/MANIFEST.sha256`として保存した
(`sha256sum -c MANIFEST.sha256`で完全性検証可能)。これにより、Codex／RL担当が合意した図の内容と
commit内の図が同一であることを、ハッシュ値の突合により確認できる。

## 完了条件の充足確認

- 正本8図・deprecated図・描画検証結果(SVG＋ログ)・manifest・READMEが同一commitへ含まれている。
- 作業ツリーはcommit後にcleanである(下記確認)。
- ランタイムコードの変更: 本作業を通じて一切なし。

## 使用commit・作業ツリー状態

本報告のcommitをもって完了する。commit IDは本報告commit自体を参照。

## 結論

Combat実行基盤アーキテクチャ設計図(Rough Diagram＋7詳細図)を、STS2_RLリポジトリ
`docs/architecture/combat/`の正式なベースラインとして確立した。実装には進まず、
ここでcommit IDと描画検証結果を監督者へ報告して停止する。
