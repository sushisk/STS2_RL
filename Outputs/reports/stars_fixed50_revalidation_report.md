# Stars反映と固定50再検証レポート (2026-07-21)

## 実施内容

- Emulator DLL確認:
  - `C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll`
  - LastWriteTime: `2026-07-21 10:23:00`
  - SHA256: `2CC3CD657ACFC0D4DB5F8D8FD2A6B0ABE41A6D888B7CDE286082E8735E2715A6`
- Emulatorソース確認:
  - `CombatScenario.Stars`
  - `state["stars"]`
  - `RelicsRestoredWithoutAfterObtained` の `NEOWS_BONES`
- RL側Stars反映:
  - `Common/schemas/combat_scenario_input_schema.json`
  - `Common/schemas/combat_state_schema.json`
  - `Combat/battle_emulator.py::build_scenario_from_spec`
  - `Combat/battle_emulator.py::build_scenario_from_state`
  - `battle_state_key`
- `preflight_validate.py` の `known_issue:neows_bones_reward_duplication` 専用付与を削除。
  通常の `deck_mismatch` / `relic_mismatch` / `stars_mismatch` 検出は維持。
- 教師データdecision schemaへ時間予算・評価件数・fallback系フィールドを追加:
  - `decision_budget_exceeded`
  - `elapsed_ms`
  - `evaluated_action_count`
  - `total_legal_action_count`
  - `total_candidate_count`
  - `search_depth_reached`
  - `fallback_used`
  - `fallback_reason`
- `run_trajectory_batch.py` のJSONL出力を各write後にflush。

## テスト

```text
cd C:\STS2_RL\Combat\tests
python test_scenario_v2.py
```

結果:

```text
13 passed, 0 failed
```

追加テスト:

- `test_stars_survive_apply_action_restore`
- `test_neows_bones_preflight_no_special_quarantine`
- `test_invalid_input_exception_types` に負のStars拒否を追加

Smoke:

```text
cd C:\STS2_RL\Combat\data
python generate_heuristic_trajectories.py
```

結果: `status=ok`, `outcome=victory`, `truncated=False`, `warnings=[]`

## 固定50再検証

```text
cd C:\STS2_RL\Combat\data
python run_trajectory_batch.py --source fixed50 --out C:\STS2_RL\Combat\data\trajectories_fixed50_stars
```

出力:

- `Combat/data/trajectories_fixed50_stars/summary.json`
- `Combat/data/trajectories_fixed50_stars/trajectories.jsonl`
- `Combat/data/trajectories_fixed50_stars/trajectory_meta.jsonl`
- `Combat/data/trajectories_fixed50_stars/quarantine.jsonl`
- `Combat/data/trajectories_fixed50_stars/human_readable_logs/`

集計:

```json
{
  "total_scenarios": 50,
  "ok": 42,
  "quarantined": 8,
  "init_success_rate": 84.0,
  "combat_completion_rate": 78.57,
  "decisions_per_combat_avg": 24.62,
  "illegal_action_count": 0,
  "heuristic_exception_count": 4,
  "emulator_step_exception_count": 0,
  "decision_budget_exceeded_count": 0,
  "fallback_count": 0,
  "skipped_candidate_count": 561,
  "evaluated_candidate_count": 4514,
  "timeout_count": 0,
  "determinism_checked": 5,
  "determinism_matched": 5,
  "determinism_rate_pct": 100.0,
  "win_count": 22,
  "loss_count": 7,
  "truncated_count": 9,
  "remaining_hp_avg_on_win": 62.8,
  "potion_use_avg": 0.43,
  "avg_time_per_combat_s": 13.47,
  "avg_time_per_decision_s": 0.452,
  "quarantine_reason_counts": {
    "relic_mismatch": 5,
    "no_legal_actions": 3
  },
  "neows_bones_quarantine_count": 0
}
```

## 判定

固定50は全50件の処理自体は完了したが、クリーン完了条件は未達。

満たした条件:

- Illegal Action 0
- バッチプロセスの停止・ハングなし
- timeout 0
- determinism 5/5
- NEOWS_BONES専用quarantine 0
- Starsはinitialize/apply_action復元テストで維持確認済み

未達条件:

- quarantine 8件
- heuristic_exception 4件
- truncated 9件

## 残課題

### 1. `STUNNED` forced move復元

4件の `heuristic_exception` は、全候補評価が以下で失敗したもの:

```text
ArgumentException: Unknown move id: STUNNED
```

`build_scenario_from_state()` が `state["enemies"][i]["intent"]["stateId"]` を
`EnemyScenario.ForcedMove` に渡しているが、`STUNNED` はその敵の通常move idとして
Emulatorが受け付けない。これはStars欠損とは別の復元ギャップであり、RL側で
安全に代替できるか、Emulator側で `STUNNED` をScenario復元可能にするべきかの判断が必要。

### 2. quarantineのrelic mismatch

5件はレリック取得時副作用または置換系の差分:

- 追加例: `ORANGE_DOUGH`, `WHITE_STAR`, `BOOK_OF_FIVE_RINGS`, `LIZARD_TAIL`, `CIRCLET`
- 欠落例: `RING_OF_THE_DRAKE`, `DIVINE_DESTINY`

NEOWS_BONES由来ではない。引き継ぎ資料13.1の「AfterObtained副作用レリック」の実データ影響として扱う。

### 3. `no_legal_actions`

3件はpreflight時点でlegal actionが空。初期化はできているが、教師データには投入できないため隔離継続が妥当。

### 4. max_decisions

9件は `truncated_at_max_decisions:50`。戦闘は進行しておりプロセスは停止しなかったため、
固定50の品質評価では `--max-decisions` 増加または長期戦の別扱いが必要。
