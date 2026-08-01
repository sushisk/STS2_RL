# Emulator要求DTO 再確認報告 (2026-08-01)

基準: Combat Mermaidベースライン(commit `81006b3`。DrawPile Belief・Search Hypothesis ID・
Exact State層／Belief-Search層分離・PUBLIC_MULTISET再定義・StableShuffle 3要素化を反映済み)。
コード変更は行っていない。

## 1. DTO契約書の現在の保存場所

`C:\STS2_RL\Common\contracts\emulator_dto_contract_rl_required.v1.md`

## 2. 最新の正本ファイル名とバージョン

`emulator_dto_contract_rl_required.v1.md`(v1、初版作成2026-08-01、本再確認により同ファイルへ
§0「再確認」節と§9「RL内部型・要確認事項の一覧」節を追記。バージョン番号自体は据え置き — v1のまま)。

## 3. 最新Mermaid設計と比較して変更が必要か

**部分的に必要だった。** DrawPile Belief・Search Hypothesis ID・Exact State層の追加自体は
Emulator公開DTOへ新規要求を生じないが、旧版のDTO契約書に1件、現在の設計と矛盾する記述
(後述4)が残っていたため、これを修正した。

## 4. 変更がある場合: 対象DTO・フィールド・理由

- **対象**: §5「Card／各Pile」の「Orderedが必須か」行、および同章冒頭の要求記述。
- **フィールド**: `CombatStateSnapshot.PlayerSnapshot.DrawPile`(Ordered必須制約)。
- **理由**: 旧版は「山札順不明のCard Instance集合を入力として受け付けられること」を
  Emulator側への新規要求(不足)として記載していた。これは
  `Common/contracts/deck_unordered_input_shuffle_proposal.v1.md`が提案する
  「Emulator側でUnordered入力を受け取りサーバサイドでshuffleする」方式を前提とした記述だった。
  しかし実際に採用されたDrawPile Belief設計(`mermaid_combat_rng_hypothesis_detail.mermaid`の
  `BELIEF_GEN`)は、Hypothetical OrderedDrawPileをSearch Coordinator側(RL側)で生成し、
  既存のOrdered Snapshot形式へ直接代入してから既存の`RestoreSnapshotJson`を呼ぶ(方式B、
  RNG Hypothesisと同じ経路)。**Emulator側のOrdered必須制約はそのまま維持でよく、新しい入力形式は
  不要と判明した。** この判定を「不足」から「一致」へ修正し、旧提案は撤回はしないが優先度を
  下げる旨を明記した(§0・§5・§9)。

## 5. 変更がない場合: 確認した図・境界(今回変更しなかった項目についての確認結果)

以下は全てEmulator側への新規要求を生じないことを確認し、契約書の該当箇所は変更していない
(§0・§9に確認結果のみ追記)。

- **DrawPile Beliefの`PUBLIC_MULTISET`算出**: 必要な情報(`Hand`/`DrawPile`/`DiscardPile`/
  `ExhaustPile`/`PlayPile`・`CombatHistorySnapshot.Entries`中の`CardGeneratedEntry`)は全て
  `Combat/combat_state_snapshot.py`に既存のフィールドであり、新規DTOフィールド不要
  (`mermaid_combat_rng_hypothesis_detail.mermaid`のPUBLIC_MULTISET／BOUNDARY_TABLE境界を確認)。
- **StableShuffleのhook関連状態**: `PlayerSnapshot.Relics`/`PlayerSnapshot.Powers`で既に
  Capture/Restore対象であり、新規DTOフィールド不要
  (`mermaid_combat_snapshot_replay_detail.mermaid`のNOTE_RESHUFFLE_DETERMINISM境界を確認)。
- **Search Hypothesis ID**: Decision Context/Lease/WorkItem/Commit図いずれにおいても、
  RL側(Search Coordinator)が内部的に管理する識別子であり、Emulatorへ渡るのはこのIDが指す
  具体的なRNG値・DrawPile値そのもの(既存のSnapshot JSON編集、方式B)のみ。Emulator側はIDの
  存在自体を知らない(`mermaid_combat_rng_hypothesis_detail.mermaid`の全境界を確認)。
- **Exact State層／Belief-Search層の分離、Concrete/Authoritative/Hypothetical OrderedDrawPile
  という用語**: いずれもSearch Coordinatorが既存の同一DTO(`CombatStateSnapshot`)をどう扱うかに
  ついてのRL内部規律であり、Emulator側から見た`RestoreSnapshotJson`の呼び出し方は従来と同一
  (`mermaid_combat_rng_hypothesis_detail.mermaid`のBOUNDARY_TABLE、
  `mermaid_combat_branch_scheduler_detail.mermaid`のNOTE_ORDERED_DRAWPILEを確認)。

## 6. Emulatorへ実装を依頼すべきDTO項目の一覧

**今回の再確認範囲では、新規に実装を依頼すべきDTO項目は無い。** 既存契約書(§8総括)に記載済みの
継続課題のみが残る(いずれも本再確認以前から存在する既知の課題であり、DrawPile Belief等の追加による
新規項目ではない)。

- choice_scopeの明示的フィールド追加(§4「不足」)。
- Decision Signatureによる意味的replay mismatch検証の追加、またはRL側での実装
  (§7「不足」)。
- (参考、優先度低・撤回はしない)`deck_unordered_input_shuffle_proposal.v1.md`のUnordered入力
  受け付け機構 — DrawPile Beliefでは不要と判明したため、他用途(例: Event Room由来の真に順不明な
  Scenario入力)が将来生じない限り着手不要。

## 7. RL内部型であり、Emulatorへ要求しない項目の一覧

- `BattleState`・`DecisionFrame`・`CombatEnv`の`{action_id, reward, done, observation,
  legal_actions, info}`辞書(既存、RL内部の合成型)。
- **Search Hypothesis ID**(RNG成分＋DrawPile Order成分の組。Decision Context/Lease/WorkItem/
  Commit図で使うRL内部の仮説識別子)。
- **DrawPile Belief**(`PUBLIC_MULTISET`算出・`BELIEF_GEN`によるHypothetical OrderedDrawPile生成)。
- **Exact State層／Belief-Search層の分離**。
- **Concrete／Authoritative／Hypothetical OrderedDrawPile**という用語体系。

## 成果物

- `Common/contracts/emulator_dto_contract_rl_required.v1.md`(更新。§0「再確認」節・§9「RL内部型・
  要確認事項の一覧」節を追加、§5「Orderedが必須か」の判定を「不足」→「一致」へ修正、
  §8総括の件数を更新、dangling section参照(§9/§10)を修正)
- 本報告書: `Outputs/reports/rl_emulator_dto_reconfirmation_20260801.md`

コード変更は行っていない。実装には進まず、ここで停止する。
