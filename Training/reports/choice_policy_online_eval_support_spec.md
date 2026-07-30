# Choice Policy オンライン評価支援 仕様書 兼 成果物確認結果

生成日: 2026-07-25
本タスクでは再学習・checkpoint変更を一切行っていない。対象checkpointは`checkpoints/choice_policy_8token_best/best.pt`(3-seed評価で選定済み、seed=20260725)に固定。

## 1. Checkpoint確認結果

- パス: `checkpoints/choice_policy_8token_best/best.pt`
- ファイルSHA256(再確認): `F5299E4ABF8A30A0400CBA2E5094777276B84F9C3A70D7051B0EC886C457F29F`
- ファイルサイズ: 1,369,725 bytes

### checkpoint内provenance(埋め込み済み、`checkpoint["provenance"]`)

| フィールド | 値 |
|---|---|
| emulator_commit | `722b019051e6f7ea368fef488abcc6451d6c9d47` |
| emulator_dll_sha256 | `E3C3D26D7499E93E89F2718CCB51E18A2D66559021BBB5CDCA33980BB644C036` |
| choice_semantics_baseline_version | `choice_semantics_baseline_722b019_v1_20260725` |
| choice_semantics_lookup_sha256 | `5e7d260f076047b6d0ee02eb79fcd57a06067be49c923621e84ec0df06df8d44` |
| choice_semantics_origin_alias_sha256 | `a456f0a235c84c2ee815367593c479fb5ed8479916ad4257a55b2d9ea1a33814` |
| source_choice_dataset_sha256 | `12F560F42E515FB72BBB52F0497D42E3E3FB7814FD0FDB070F8076C2CC3FE5BF` |
| merge_map_version | `choice_meaning_merge_map.v1` |
| merge_map_sha256 | `D6BBB9178550A6A2097E30946D056C91516D0BC6B673F426C07A4355BBB6D2EA` |
| split_manifest_sha256 | `5C76A0FE609527015BF608D9B3E4DC57244FCC98AF3252386A7A75C23CA2C6AA` |
| seed | `20260725` |
| training_commit | `not_a_git_repository`(`C:\STS2_RL\Training`はgit管理外) |
| selection_criteria | validation MRR → validation top-1 → illegal率(test不使用) |

### model config(`checkpoint["config"]`)

state_dim=4560, card_vocab=548, choice_meaning_vocab=9(8カテゴリ+`__UNKNOWN__`), card_embedding_dim=32, choice_meaning_embedding_dim=8, hidden_dim=64, use_choice_meaning=True, freeze_encoder=True。

## 2. 依存関係(CPU推論)

推論経路(`sts2_training/choice_inference.py` → `choice_data.py` / `encoding.py` / `model.py`)が実際にimportする外部パッケージは **`torch`のみ**(標準ライブラリ以外)。

- 確認環境: Python 3.12.7, torch 2.13.0+cpu(CUDAビルドではない = GPU不要)
- `torch.cuda.is_available()` = False の環境で動作確認済み(このマシン自体GPU非搭載)
- Emulator/pythonnet起動不要

## 3. `ChoiceDecision` 入力・出力仕様

```python
from sts2_training.choice_inference import ChoiceDecision
decision = ChoiceDecision("checkpoints/choice_policy_8token_best/best.pt")
result = decision(
    battle_state,             # observation(既存Policy/Valueと同じbattle state辞書)
    choice_legal_actions,     # legal_actionsリスト(choice_card以外は内部で無視)
    operation_mode,           # "normalized" | "passthrough" | "unknown" | None
    normalized_choice_operation,  # 例: "discard"(operationMode=="normalized"のとき)
    exception_entity_key,         # 例: "relic:GAMBLING_CHIP"(operationMode=="passthrough"のとき)
    remaining_select_count,       # 数値(int/float)
)
```

`normalized_choice_operation`と`exception_entity_key`は排他的(片方のみ渡す。両方Noneなら`operationMode`に関わらずunrecognized扱い)。

出力(`dict`):

| キー | 内容 |
|---|---|
| `ranking` | `[{"index": int, "action_id": int, "label": str}, ...]` — legal candidate cardsをスコア降順に並べたもの |
| `top1_action_id` | 最上位候補の`action_id` |
| `top1_confidence` | 最上位候補のsoftmax確率 |
| `top2_confidence` | 2位候補のsoftmax確率(候補が1件のみの場合は0.0) |
| `confidence_margin` | `top1_confidence - top2_confidence` |
| `fallback_reason` | 下記4章参照。`null`なら通常のranking利用可 |
| `provenance` | 上記1章のprovenance辞書(呼び出しごとに含まれる) |

fallback発火時も`ranking`は可能な範囲で返す(候補が無い場合のみ空リスト)。

## 4. fallback reason 一覧

| 値 | 発火条件 | rankingの信頼性 |
|---|---|---|
| `null`(None) | 正常。operationMode解決済み・meaning token認識済み・choice_card候補あり | 通常どおり利用可 |
| `"no_choice_card_candidates"` | legal_actions内に`action_type=="choice_card"`が0件(choice_confirm/choice_skipのみ) | rankingは空。Heuristic必須 |
| `"operation_mode_unknown"` | `operationMode`が`"unknown"`または`None` | rankingは候補順そのまま(未スコアリング)。Heuristic推奨 |
| `"unrecognized_choice_meaning_token"` | `normalizedChoiceOperation`/`exceptionEntityKey`が学習時の8-token(9 id含む`__UNKNOWN__`)いずれにも一致しない | rankingはCard Encoder等の情報で計算されるが低信頼。Heuristic優先を推奨 |

## 5. 8-token merge map(token一覧・変換仕様)

version: `choice_meaning_merge_map.v1`(`exports/choice_policy_v1/merge_map.v1.json`, SHA256: `D6BBB9178550A6A2097E30946D056C91516D0BC6B673F426C07A4355BBB6D2EA`)

変換対象(13 raw token → 8統合token、`__UNKNOWN__`除く):

| raw token(RL側resolved値) | 統合後 |
|---|---|
| `retrieve_to_hand` | `retrieve` |
| `retrieve_to_draw_pile_top` | `retrieve` |
| `apply_effect_in_place` | `other_normalized_rare` |
| `select_for_power_association` | `other_normalized_rare` |
| `upgrade` | `other_normalized_rare` |
| `transform_to_specific_card` | `other_normalized_rare` |
| `discard` | `discard`(変換なし) |
| `exhaust` | `exhaust`(変換なし) |
| `transform` | `transform`(変換なし) |
| `add_generated_to_hand` | `add_generated_to_hand`(変換なし) |
| `return_to_draw_pile_top` | `return_to_draw_pile_top`(変換なし、retrieve系へは統合しない) |
| `relic:GAMBLING_CHIP` | `relic:GAMBLING_CHIP`(変換なし、独立カテゴリ維持) |

未知のraw tokenは`__UNKNOWN__`(id 0)へフォールバックし、`fallback_reason="unrecognized_choice_meaning_token"`が発火する。変換ロジックは`ChoiceDecision`内部で自動適用されるため、呼び出し側は変換前(raw)の値をそのまま渡してよい。

## 6. 決定論的推論テスト結果

同一入力に対する推論を2回実行し、出力(ranking順序・confidence値含む)が完全一致することを確認した。

- 同一`ChoiceDecision`インスタンス内での2回呼び出し: **完全一致**
- checkpointを再読込した別インスタンスでの呼び出し: **完全一致**(再現性あり、ファイルI/Oや乱数状態に依存しない)

理由: 推論は`model.eval()`状態でdropout等の確率的要素を含まないため、同一入力に対して常に同一出力となる。

## 7. 期待される出力形式(実データ3件 + synthetic nested Choice)

### 実データ例1: `6703-16:4`(operationMode=normalized, meaning=add_generated_to_hand, remainingSelectCount=0)

```json
{
  "ranking": [{"index": 0, "action_id": 0, "label": "BLUDGEON"}, {"index": 1, "action_id": 1, "label": "CONFLAGRATION"}, {"index": 2, "action_id": 2, "label": "THRASH"}],
  "top1_action_id": 0,
  "top1_confidence": 0.4777,
  "top2_confidence": 0.4372,
  "confidence_margin": 0.0405,
  "fallback_reason": null
}
```
latency: 5.74ms

### 実データ例2: `3243-5:3`(operationMode=normalized, meaning=add_generated_to_hand, remainingSelectCount=0)

```json
{
  "ranking": [{"index": 0, "action_id": 0, "label": "BEAM_CELL"}, {"index": 1, "action_id": 1, "label": "BALL_LIGHTNING"}, {"index": 2, "action_id": 2, "label": "SUNDER"}],
  "top1_action_id": 0,
  "top1_confidence": 0.5365,
  "top2_confidence": 0.3391,
  "confidence_margin": 0.1975,
  "fallback_reason": null
}
```
latency: 2.89ms

### 実データ例3: `3243-5:4`(operationMode=normalized, meaning=retrieve_to_hand→retrieve, remainingSelectCount=0)

```json
{
  "ranking": [{"index": 0, "action_id": 0, "label": "DEFEND_DEFECT"}, {"index": 1, "action_id": 1, "label": "DEFEND_DEFECT"}],
  "top1_action_id": 0,
  "top1_confidence": 0.5,
  "top2_confidence": 0.5,
  "confidence_margin": 0.0,
  "fallback_reason": null
}
```
latency: 4.04ms(候補2件が同一カードのため僅差)

### synthetic nested Choice(`synthetic:nested_choice_decisions_decisions_burning_pact`, 3 decisions, 候補3/4/5)

3 decisionsとも例外なく実行、`fallback_reason: null`、候補は全て`STRIKE_REGENT`(同一カード複数、confidenceは候補数の逆数付近で均等)。latency 3.07ms/3.59ms/3.45ms。

## 8. RLへ渡す仕様まとめ

| 項目 | 内容 |
|---|---|
| 入力 | observation, choice legal actions, operationMode, normalizedChoiceOperation または exceptionEntityKey, remainingSelectCount |
| 出力 | ranked legal actions, top-1 action_id, top-1 confidence, top-2 confidence, confidence margin, fallback reason, latency(呼び出し側で計測) |
| 依存 | `torch`のみ(CPU、GPU不要) |
| 決定論性 | 同一入力→同一出力を確認済み |
| checkpoint | `checkpoints/choice_policy_8token_best/best.pt`(SHA256: `F5299E4ABF8A30A0400CBA2E5094777276B84F9C3A70D7051B0EC886C457F29F`)、再学習・変更なし |

## 禁止事項の遵守

再学習なし、checkpoint変更なし、RL/Emulator編集なし、新規特徴量追加なし、Training側でのonline adapter実装なし。本仕様書提出をもって停止する。
