# Emulator修正版 反映・再検証 完了報告 (2026-07-21)

## 1. 使用したEmulator DLLの識別情報

```text
パス: C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll
ビルド日時: 2026-07-20 17:49:59
ソース最終更新: 2026-07-20 17:48:08 (ビルドの直前、正常な状態)
```

修正コード実体を`GameInstance.cs:347-410`で直接確認: `RunState.AppendToMapPointHistory`
呼び出しの追加(`CurrentMapPointHistoryEntry`がScenarioモードでは常にnullだった問題への
一般修正)と、`RewardsSet.testSelector`の一時差し替え(`RewardsCmd.OfferCustom`系の
対話報酬を自動辞退)。**個別レリック向けパッチではなく、`RewardsCmd`系取得時処理全体を
対象にした一般修正**であることをソースレベルで確認済み(指示書の説明と一致)。

## 2. `LOST_COFFER`由来251件(実際は関連284件)の再検証結果

指示の251件(LOST_COFFER由来)を含む、旧`NullReferenceException`全284件を再検証:

```text
初期化成功:     283/284 (99.65%)
初期化失敗:       1/284 (LOST_COFFER無関係、既知のInvalidOperationException系)
デッキ余分カード:  0/284
ポーション余分:    0/284
レリック不一致:    1/284 (★下記参照)
1Step成功:      283/283 (失敗なし)
決定論性:        283/283 (同一seedで再実行し完全一致)
```

**新規に特定した別種の問題**: `source_run_id=6539`で`CANDELABRA`・`FROZEN_EGG`の
2レリックが余分に付与された。原因はレリック`NEOWS_BONES`(このシナリオにはLOST_COFFER等
指定6レリックは含まれない)。ソース確認の結果、`NeowsBones.AfterObtained()`は
`RewardsSet(...).WithSkippingDisallowed().Offer()`で報酬を**スキップ不可**に設定して
おり、今回の一般修正(スキップして通過)が効かず、実際にレリックが1つ自動選択されて
付与される。低頻度(284件中1件)の既知の残存事象として記録。

## 3. 6,091件全体の新しい成功率

| 段階 | 件数 | 成功 | 成功率 |
|---|---|---|---|
| 修正前(2026-07-20時点) | 6,091 | 5,798 | 95.19% |
| LOST_COFFER系一般修正後 | 6,091 | 6,081 | **99.84%** |
| ID辞書修正後(再サンプリングで6,095件に微増) | 6,095 | 6,082 | **99.79%** |

期待値(修正前失敗284件中251件がLOST_COFFER由来として算出した「約99.46%」)を
**上回る結果**(99.84%)。LOST_COFFER以外の`RewardsCmd`系レリック(例: `ORRERY`)由来の
失敗も同修正で解消されたため。

## 4. 残存失敗の原因別件数(13件、新規カテゴリなし)

```text
Unknown enemyId "OSTY":                    5件 (既知、同一ラン群由来)
InvalidOperationException(LINQ First失敗): 5件 (既知カテゴリ、新規個体を含む)
ArgumentNullException(Dictionary key):     3件 (既知カテゴリ)
```

`NullReferenceException`は**0件**(修正前284件から完全解消)。`MYSTERIOUS_KNIGHT`
(後述のID辞書修正で新規解決)を含むシナリオに起因する失敗は0件。

## 5. 解除した除外条件

`LOST_COFFER`/`TOY_BOX`/`SMALL_CAPSULE`/`ORRERY`/`CAULDRON`/`CALLING_BELL`について、
RL側コード(`Combat/`配下全体)を検索した結果、**除外フィルタは元々存在しなかった**
(過去に`scenario_from_runs.py`へ実装していた`LEAD_PAPERWEIGHT`/`CLAWS`用フィルタは
その回の修正確認時に既に削除済みで、これら6レリックに対する同種のフィルタを
追加したことは一度もない)。重複した除外条件・失敗理由分類の残存も確認したが
該当なし。

## 6. 報酬二重付与がないことの確認

セクション2の通り、284件中283件でデッキ・ポーションへの余分な追加は0件。
唯一の例外(`NEOWS_BONES`、1件)は上記の通り原因が異なり、スキップ不可設定に
起因する既知の残存事象として切り分け済み。

## 7. ID辞書の修正内容

`Common/ids/build_id_dictionaries.py`に`TransitiveExtractor`を追加。元の
`Extractor.is_model_base()`は宣言基底クラスが目的の基底(例: `MonsterModel`)と
**直接**一致する場合のみ判定しており、間接継承(例: `MysteriousKnight : FlailKnight`、
`FlailKnight`がさらに`MonsterModel`を継承)を取りこぼしていた。修正版は対象
ディレクトリ内の全クラス宣言から`クラス名→基底クラス名`のマップを構築し、
宣言基底から目的の基底まで連鎖的に辿ることで間接継承を解決する。抽象・補助クラスの
扱い(既存の`abstract`修飾子検出によるフィルタ)は変更していない。

v109を本ツールでインライン再抽出(従来の`v0109_raw`ファイル読み込みから、
`TransitiveExtractor`による直接再抽出へ変更)し、旧抽出結果との差分を
`Common/versioning/transitive_inheritance_fix_diff.json`へ出力:

```text
cards:    newly_resolved=[] no_longer_resolved=[]
monsters: newly_resolved=['MYSTERIOUS_KNIGHT'] no_longer_resolved=[]
powers:   newly_resolved=[] no_longer_resolved=[]
relics:   newly_resolved=[] no_longer_resolved=[]
```

**新規に検出されたIDは`MYSTERIOUS_KNIGHT`の1件のみ**(誤検出・誤って含まれた
抽象/補助クラスは0件)。`monsters.json`は119→120エントリに更新。

## 8. `unsupported_id`件数の変化

```text
修正前: 3,000 / 95,626 (3.14%)
修正後: 2,838 / 95,626 (2.97%)
差分:    -162件 (-5.4%)
```

`MYSTERIOUS_KNIGHT`を含んでいた162件の戦闘状態が`unsupported_id`から
`exact`(+148件)/`ambiguous_upgrade`(+14件)へ再分類された。なお`DOORMAKER`/`DOOR`
(それぞれ413件・352件)はv109ソースに該当クラス自体が存在しないことを確認済みで、
今回の間接継承修正の対象外(真に削除済みコンテンツであり、抽出漏れではない)。

## 9. 更新した出力ファイル

```text
Combat/data/full_reconstruction/floor_states_{train,validation,test,benchmark}.jsonl  (再生成)
Combat/data/full_reconstruction/scenario_manifest.jsonl                                (再生成)
Combat/data/full_reconstruction/reconstruction_summary.json                            (更新)
Combat/data/full_reconstruction/emulator_validation.jsonl                              (更新)
Common/ids/{cards,relics,potions,monsters,powers}.json                                 (再生成)
Common/versioning/id_mapping_v108_v109.json                                            (更新)
Common/versioning/transitive_inheritance_fix_diff.json                                 (新規)
Combat/data/lostcoffer_fix_revalidation.json                                           (新規、284件の詳細再検証結果)
```

修正前後差の追跡用バックアップ:

```text
Combat/data/full_reconstruction/emulator_validation.PRE_LOSTCOFFER_FIX.jsonl
Combat/data/full_reconstruction/reconstruction_summary.PRE_LOSTCOFFER_FIX.json
Combat/data/full_reconstruction_PRE_ID_FIX/{scenario_manifest.jsonl, reconstruction_summary.json}
Common/ids/v0109_raw_PRE_TRANSITIVE_FIX/
Common/ids/{cards,relics,powers,monsters}.PRE_TRANSITIVE_FIX.json
```

## 10. Heuristicコード整理の進捗

**未着手。**指示書セクション10の優先順位に従い、今回はEmulator修正の反映・
再検証・ID辞書修正を優先し、教師データ生成経路(Heuristic整理含む)は次段階として
着手する。

## 11. Phase 2へ進めるかの判断

**進めてよいと判断する。**

* 修正版Emulator DLL(2026-07-20 17:49ビルド)の反映を確認済み
* LOST_COFFER由来284件の再検証: 283/284成功、報酬二重付与なし
* 除外フィルタ: 元々存在せず、解除対象なし
* 6,091→6,095件のEmulator検証: 99.79%成功、新規失敗カテゴリなし
* ID辞書の間接継承対応: 完了、`MYSTERIOUS_KNIGHT`を新規解決、誤検出なし
* 残存失敗(13件)はすべて既知カテゴリ(未対応の敵ID・既存の稀な例外)で説明可能
* `NEOWS_BONES`(スキップ不可報酬)という新種の残存事象を発見したが低頻度(1件)

重大な新規不整合はなし。Heuristicコード整理・教師データ生成準備へ進む。
