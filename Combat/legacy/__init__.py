"""Legacy Policy/Value/Heuristic inference code - NOT on the production RL execution path.

Per the "RL担当指示：推論処理撤去と受動実行基盤への整理" division of responsibility, RL's
production infra (Combat/, Combat/search/, Run/) executes Episodes/Runs, exposes
Observation/LegalActions, applies externally-specified Actions, generates Branches, and
manages Snapshot/Replay/Worker/Lease state - it does not itself run Policy/Value inference,
score candidates, or auto-select a "winner" action. That decision-making role now belongs
to Training.

Everything under this package (`heuristic_agent`, `policy_agent`, `choice_policy_agent`,
`beam_search`, `lookahead`, `state_evaluator`, `potion_value_table`, `main`, `_bench_abc`)
predates that split and DOES perform its own scoring/inference/checkpoint-loading
internally. It is kept here, working and tested, for historical/offline-analysis use
(teacher-data generation scripts, comparison/audit tooling) - never imported by
`Combat/search/main_loop.py`, `Combat/search/search_coordinator.py`,
`Combat/search/branch_worker_pool.py`, or `Run/` (the actual production execution
infra). See `Outputs/reports/rl_inference_removal_20260803.md` for the full inventory
and classification this reorganization was based on.
"""
