# Combat Mermaid Diagram 更新報告: RL安全性確認方針の修正 (2026-08-01)

前提: Combat Mermaid正式ベースライン`docs/architecture/combat/`(直前commit`cfb12a4`)。
本作業は仕様整理と図の修正のみであり、**ランタイムコードの変更は一切行っていない。**

## 背景

監督者指示に基づき、RL側の安全性確認方針を修正した。RL側(Search Coordinator／Main Process)は
カード使用後の盤面を予測・再計算しない。Power・Relic・Hook・カード固有処理を含む戦闘結果は
Emulatorの責任範囲であり、RL側はEmulatorが返すDecision Result/StepResultの報告値をそのまま
信頼する。この方針に基づき、Combat Mermaid設計の「Decision Signature」を、盤面を暗黙に含みうる
広い定義から、Action実行とDecision Boundaryの制御情報のみを確認する軽量な形式へ縮小した。

## 新しいDecision Signatureの定義

**Stable／Pending共通で確認する項目:**

- 実行前後のState Identity
- 選択したSemantic ActionとActionIdの対応
- Emulatorが実際に解決したカード、対象、selection
- 到達したBoundary種別

**Pendingの場合のみ追加で確認する項目:**

- Choice scope(TopLevel／ActionContinuation)
- Choice種別(choice_kind)
- 候補のSemantic Key集合(canonical multiset)

**撤廃した項目**(旧Decision Signatureに存在したが今回削除): min/max selection、target制約、
continuation識別情報。これらは候補の詳細構造に踏み込む情報であり、暗黙の盤面期待値作成へ
つながりかねないため除外した。継続識別が必要な場面ではChoice scope自体が引き続きその役割を担う。

**明示的に禁止**: HP、Block、Energy、Pile構成、Power、Relic、敵状態などの盤面期待値は作成せず、
Replay安全性の判定にも使用しない。

## 修正した図

- **`mermaid_combat_snapshot_replay_detail.mermaid`**(主たる変更対象): `DC_SIGNATURE`ノードを
  全面的に書き直し、上記の新定義へ変更。`CTX_SIG_CHECK`・`REPLAY_SIG_CHECK`の説明文を新定義に
  合わせて更新。新規ノート`NOTE_NO_BOARD_PREDICTION`を追加し、盤面予測禁止方針を明記。
  ファイル冒頭の`%% 目的`コメントも更新。
- **`mermaid_combat_main_loop_detail.mermaid`**: `VERIFY_TRANSITION`の説明文を新しい
  Decision Signature定義へ合わせて更新。新規ノート`NOTE_NO_BOARD_PREDICTION_MAIN`を追加。
- **`mermaid_combat_candidate_pipeline_detail.mermaid`**: `CHOICE_SCOPE`ノートから、撤廃された
  「continuation識別情報」への言及を削除し、Choice scope自体が継続識別を担う旨へ修正。

`mermaid_rough_combat.mermaid`・`mermaid_combat_branch_scheduler_detail.mermaid`・
`mermaid_combat_rng_hypothesis_detail.mermaid`・`mermaid_combat_fault_worker_detail.mermaid`・
`mermaid_combat_commit_detail.mermaid`は変更なし。State Identity検証(LEASE_VERIFY・
`commit_detail`のVERIFY)は元々Decision Signatureとは別軸として明確に分離されており、
今回の軽量化と矛盾しないことを確認した。

## 図間整合性・構文検証

- 全8図についてnode参照の完全性をプログラム的に検証(未定義参照なし)。
- 全8図についてbracket/brace/quote balanceを検証(全て整合)。
- 全8図について`@mermaid-js/mermaid-cli` v11.16.0による実際のSVGレンダリングを実施し、
  8図中8図が成功した。詳細は`docs/architecture/combat/SVG_RENDER_LOG.md`「第5回検証」を参照。
- `docs/architecture/combat/MANIFEST.sha256`を再計算・再検証済み(4図のハッシュは前回commitから
  不変)。
- 全8図を横断的に`grep`し、撤廃した「min/max selection」「target制約」「continuation識別情報」
  という文字列が他の図に残存していないことを確認した(いずれも`mermaid_combat_snapshot_replay_detail`
  と`mermaid_combat_main_loop_detail`にのみ存在し、両方とも本作業で修正済み)。

## 結論

RL側の安全性確認は、盤面の予測・再計算を一切伴わない、Action実行とDecision Boundary制御情報
(State Identity・ActionId対応・Emulator報告値の照合・Boundary種別、Pending時のみChoice scope/
choice_kind/候補集合)のみに基づく軽量な形式へ縮小された。HP/Block/Energy/Pile/Power/Relic/
敵状態などの盤面計算・戦闘結果の予測は、引き続きEmulatorの責任範囲として明確に区別されている。

コード変更は行っていない。実装には進まず、ここで停止する。
