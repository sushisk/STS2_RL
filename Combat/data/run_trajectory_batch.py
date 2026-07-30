"""Batch driver for generate_heuristic_trajectories.py: runs a list of scenarios through
the CombatEnv-unified pipeline, writes trajectories.jsonl + quarantine.jsonl + a
summary.json (the section-10 stat list) + a handful of human-readable per-trajectory
logs (section 11), and re-runs a determinism sample.

Run: python run_trajectory_batch.py --source fixed50
     python run_trajectory_batch.py --source reconstructed --n 100 --seed 7
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing
import os
import random
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from tempfile import gettempdir
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Combat/

from generate_heuristic_trajectories import (  # noqa: E402
    EMULATOR_COMMIT,
    HEURISTIC_VERSION,
    build_default_agent,
    emulator_dll_sha256,
    generate_trajectory,
)

DATA_DIR = Path(__file__).parent
FIXED_50_PATH = Path(__file__).resolve().parents[1] / "evaluation" / "benchmark_states" / "fixed_50_scenarios.json"
FULL_RECON_DIR = DATA_DIR / "full_reconstruction"
_WORKER_EMULATOR = None
_WORKER_AGENT = None
_WORKER_STDERR_PATH = None
_WORKER_STDERR_HANDLE = None


def load_fixed_50() -> list[tuple[dict, str, int]]:
    with FIXED_50_PATH.open(encoding="utf-8") as f:
        specs = json.load(f)
    out = []
    for i, spec in enumerate(specs):
        run_id = spec.get("source", {}).get("server_id", f"fixed50-{i}")
        out.append((spec, f"fixed50:{run_id}", i))
    return out


def load_fixed50_source_signatures() -> set[tuple[str, int | None, str | None, str | None]]:
    signatures: set[tuple[str, int | None, str | None, str | None]] = set()
    for spec, _, _ in load_fixed_50():
        source = spec.get("source") or {}
        signatures.add(
            (
                str(source.get("server_id")),
                source.get("floor"),
                source.get("encounter"),
                spec.get("character_id"),
            )
        )
    return signatures


def load_exclusion_keys_from_manifest(path: Path) -> set[tuple[str, int]]:
    keys: set[tuple[str, int]] = set()
    if not path.exists():
        return keys
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            source_run_id = row.get("source_run_id")
            source_combat_index = row.get("source_combat_index")
            if source_run_id is None or source_combat_index is None:
                continue
            keys.add((str(source_run_id), int(source_combat_index)))
    return keys


def load_reconstructed_sample(
    n: int,
    seed: int,
    monster_hp_cache=None,
    exclude_manifest_paths: list[Path] | None = None,
) -> list[tuple[dict, str, int]]:
    """Draws n scenarios from full_reconstruction's exact/ambiguous_upgrade encounters,
    converting via reconstruct_floor_state.encounter_to_scenario_spec - same source data
    Phase 1's Emulator validation already exercised at scale."""
    sys.path.insert(0, str(DATA_DIR))
    from reconstruct_floor_state import encounter_to_scenario_spec
    from scenario_from_runs import load_monster_hp

    rng = random.Random(seed)
    monster_hp = load_monster_hp()
    fixed50_signatures = load_fixed50_source_signatures()
    excluded_manifest_keys: set[tuple[str, int]] = set()
    for manifest_path in exclude_manifest_paths or []:
        excluded_manifest_keys.update(load_exclusion_keys_from_manifest(manifest_path))
    candidates = []
    for split in ("train",):  # dev sampling stays within train, never touches validation/test/benchmark
        path = FULL_RECON_DIR / f"floor_states_{split}.jsonl"
        with path.open(encoding="utf-8") as f:
            for line in f:
                rec = json.loads(line)
                if rec["restore_status"] in ("exact", "ambiguous_upgrade"):
                    signature = (
                        str(rec.get("source_run_id")),
                        rec.get("floor"),
                        rec.get("encounter_id"),
                        rec.get("character"),
                    )
                    if signature in fixed50_signatures:
                        continue
                    manifest_key = (str(rec.get("source_run_id")), int(rec.get("combat_index")))
                    if manifest_key in excluded_manifest_keys:
                        continue
                    candidates.append(rec)
    rng.shuffle(candidates)
    chosen = candidates[:n]
    out = []
    for rec in chosen:
        spec = encounter_to_scenario_spec(rec, monster_hp, rng)
        out.append((spec, rec["source_run_id"], rec["combat_index"]))
    return out


def write_scenario_manifest(path: Path, scenarios: list[tuple[dict, str, int]]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for spec, source_run_id, combat_index in scenarios:
            row = {
                "trajectory_id": f"{source_run_id}-{combat_index}",
                "source_run_id": source_run_id,
                "source_combat_index": combat_index,
                "spec": spec,
            }
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def load_scenario_manifest(path: Path) -> list[tuple[dict, str, int]]:
    scenarios = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            scenarios.append((row["spec"], row["source_run_id"], row["source_combat_index"]))
    return scenarios


def load_existing_scenario_results(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    rows: dict[str, dict] = {}
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            rows[row["trajectory_id"]] = row
    return rows


def human_readable_log(result: dict) -> str:
    lines = [f"# trajectory {result['trajectory_id']} (source_run_id={result['source_run_id']}, combat_index={result['source_combat_index']})"]
    lines.append(f"final_outcome={result['final_outcome']} decisions={result['decision_count']} truncated={result['truncated']}")
    lines.append("")
    for d in result["decisions"]:
        state = d["state"]
        lines.append(
            f"## decision {d['decision_index']}: hp={state.get('hp')}/{state.get('maxHp')} "
            f"block={state.get('block')} energy={state.get('energy')} turn={state.get('turnNumber')}"
        )
        lines.append(f"  legal_actions: {[a['label'] for a in d['legal_actions']]}")
        # action_scores may include "skipped" candidates (score=None - a restore-time
        # exception during evaluation, e.g. the Regent Stars-resource gap - see
        # heuristic_agent.py's choose_action_with_detail) alongside real-scored ones;
        # sort only the real ones, list skips separately rather than crashing on the
        # float-vs-None comparison.
        scored = [c for c in d["action_scores"] if c.get("score") is not None]
        skipped = [c for c in d["action_scores"] if c.get("score") is None]
        top_candidates = sorted(scored, key=lambda c: c["score"], reverse=True)[:5]
        lines.append("  top candidates (label, target_enemy_index, score):")
        for c in top_candidates:
            lines.append(f"    {c['label']:<20} enemy_index={c['enemy_index']} score={c['score']:.2f}")
        if skipped:
            lines.append("  skipped candidates (could not be evaluated):")
            for c in skipped:
                lines.append(f"    {c['label']:<20} enemy_index={c['enemy_index']} reason={c.get('skipped_reason')}")
        lines.append(
            f"  -> SELECTED: {d['selected_action']['label']} (enemy_index={d['selected_enemy_index']}) reward={d['reward']}"
        )
        lines.append("")
    lines.append(f"warnings: {result['warnings']}")
    return "\n".join(lines)


def check_illegal_action(decision: dict) -> bool:
    """An 'illegal action' would mean the selected action_id wasn't among the
    legal_actions this decision was offered - structurally impossible here since
    HeuristicAgent only ever picks from BattleEmulator.enumerate_legal_actions()'s own
    output, but checked explicitly anyway rather than assumed, per the audit
    instruction to detect anomalies rather than trust the pipeline blindly."""
    legal_ids = {a["action_id"] for a in decision["legal_actions"]}
    return decision["selected_action"]["action_id"] not in legal_ids


def _state_digest(state: dict) -> str:
    payload = json.dumps(state, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def analyze_trajectory_result(result: dict) -> dict:
    decisions = result.get("decisions") or []
    state_hashes = [_state_digest(d["state"]) for d in decisions]
    unique_state_count = len(set(state_hashes))
    repeated_state_count = len(state_hashes) - unique_state_count

    enemy_hp_progression = []
    player_hp_progression = []
    for d in decisions:
        state = d["state"]
        enemy_hp_progression.append(sum(max(0, e.get("hp", 0) or 0) for e in state.get("enemies") or []))
        player_hp_progression.append(state.get("hp"))
    if decisions:
        next_state = decisions[-1]["next_state"]
        enemy_hp_progression.append(sum(max(0, e.get("hp", 0) or 0) for e in next_state.get("enemies") or []))
        player_hp_progression.append(next_state.get("hp"))

    last_10_actions = [d["selected_action"]["label"] for d in decisions[-10:]]
    enemy_progress = bool(enemy_hp_progression and min(enemy_hp_progression) < max(enemy_hp_progression))
    player_progress = bool(
        [hp for hp in player_hp_progression if hp is not None]
        and min(hp for hp in player_hp_progression if hp is not None) < max(hp for hp in player_hp_progression if hp is not None)
    )
    end_turn_repetition = bool(last_10_actions) and sum(1 for a in last_10_actions if a == "End Turn") >= max(6, len(last_10_actions) - 1)

    warnings = result.get("warnings") or []
    if result.get("cycle_detected") or repeated_state_count > 0:
        truncation_classification = "C_state_or_implementation_loop"
    elif result.get("no_progress_detected"):
        truncation_classification = "B_heuristic_stagnation"
    elif result.get("truncated") and end_turn_repetition and not enemy_progress:
        truncation_classification = "B_heuristic_stagnation"
    elif result.get("truncated"):
        truncation_classification = "A_normal_long_combat" if (enemy_progress or player_progress) else "B_heuristic_stagnation"
    else:
        truncation_classification = None

    if result.get("status") == "quarantined":
        if any(str(reason).startswith("unsupported_pending_choice_type:") for reason in (result.get("reasons") or [])):
            data_usage = "exclude_emulator_issue"
        else:
            data_usage = "exclude_state_mismatch"
    elif result.get("cycle_detected"):
        data_usage = "exclude_cycle"
    elif any(w.startswith("heuristic_exception") for w in warnings):
        data_usage = "exclude_heuristic_exception"
    elif any(w.startswith("unsupported_pending_choice:") for w in warnings):
        data_usage = "exclude_emulator_issue"
    elif any(w.startswith("step_exception") for w in warnings):
        data_usage = "exclude_emulator_issue"
    elif result.get("non_learning_transition"):
        data_usage = "usable_partial"
    elif result.get("final_is_terminal") and not result.get("truncated"):
        data_usage = "usable_complete"
    elif result.get("truncated"):
        data_usage = "usable_partial" if truncation_classification in ("A_normal_long_combat", "B_heuristic_stagnation") else "exclude_cycle"
    else:
        data_usage = "usable_partial"

    return {
        "trajectory_id": result["trajectory_id"],
        "source_run_id": result["source_run_id"],
        "source_combat_index": result["source_combat_index"],
        "decision_count": result.get("decision_count", 0),
        "unique_state_count": unique_state_count,
        "repeated_state_count": repeated_state_count,
        "enemy_hp_progression": enemy_hp_progression,
        "player_hp_progression": player_hp_progression,
        "last_10_actions": last_10_actions,
        "termination_reason": result.get("termination_reason") or ("quarantined" if result.get("status") == "quarantined" else None),
        "classification": truncation_classification,
        "data_usage": data_usage,
        "cycle_detected": bool(result.get("cycle_detected")),
        "no_progress_detected": bool(result.get("no_progress_detected")) or truncation_classification == "B_heuristic_stagnation",
        "max_decisions_reached": bool(result.get("max_decisions_reached")),
        "warnings": warnings,
    }


def _trajectory_action_distribution(result: dict) -> tuple[dict[str, int], dict[str, int]]:
    action_type_counts: dict[str, int] = {}
    action_label_counts: dict[str, int] = {}
    for decision in result.get("decisions") or []:
        action = decision["selected_action"]
        action_type = action.get("action_type") or "unknown"
        label = action.get("label") or "unknown"
        action_type_counts[action_type] = action_type_counts.get(action_type, 0) + 1
        action_label_counts[label] = action_label_counts.get(label, 0) + 1
    return action_type_counts, action_label_counts


def _enrich_decisions_for_output(result: dict, quality: dict) -> None:
    for decision in result.get("decisions") or []:
        decision["scenario_id"] = result["trajectory_id"]
        decision["termination_reason"] = result.get("termination_reason")
        decision["data_usage_classification"] = quality["data_usage"]
        decision["truncation_classification"] = quality.get("classification")
        decision["emulator_commit"] = EMULATOR_COMMIT
        decision["emulator_dll_sha256"] = emulator_dll_sha256()
        decision["heuristic_version"] = decision.get("heuristic_version") or HEURISTIC_VERSION
        decision["final_outcome"] = result.get("final_outcome")


def _extract_error_events(result: dict, payload: dict, quality: dict | None = None) -> list[dict]:
    trajectory_id = result["trajectory_id"]
    base = {
        "trajectory_id": trajectory_id,
        "source_run_id": result["source_run_id"],
        "source_combat_index": result["source_combat_index"],
        "status": result["status"],
        "elapsed_s": round(payload.get("elapsed_s"), 3) if payload.get("elapsed_s") is not None else None,
        "worker_pid": payload.get("worker_pid"),
        "stderr_excerpt": payload.get("stderr_excerpt"),
        "stderr_log_path": payload.get("stderr_log_path"),
        "classification": quality.get("classification") if quality else None,
        "data_usage": quality.get("data_usage") if quality else None,
    }
    events: list[dict] = []

    if result["status"] == "quarantined":
        for reason in result.get("reasons") or []:
            events.append(
                {
                    **base,
                    "event_kind": "quarantine",
                    "event_type": reason,
                    "decision_index": result.get("first_failing_decision"),
                    "action": None,
                    "stack_trace": ((result.get("exception") or {}).get("traceback")),
                    "exception": result.get("exception"),
                }
            )
        return events

    heuristic_exception = result.get("heuristic_exception")
    if heuristic_exception:
        events.append(
            {
                **base,
                "event_kind": "heuristic_exception",
                "event_type": heuristic_exception.get("exception_type"),
                "decision_index": heuristic_exception.get("decision_index"),
                "action": heuristic_exception.get("evaluated_action"),
                "stack_trace": heuristic_exception.get("exception_traceback"),
                "exception": {
                    "type": heuristic_exception.get("exception_type"),
                    "message": heuristic_exception.get("exception_message"),
                },
            }
        )

    for warning in result.get("warnings") or []:
        if warning == "cycle_detected":
            events.append({**base, "event_kind": "warning", "event_type": warning, "decision_index": None, "action": None, "stack_trace": None, "exception": None})
        elif warning == "no_legal_actions_while_non_terminal":
            decision_index = result.get("decision_count")
            action = result["decisions"][-1]["selected_action"] if result.get("decisions") else None
            events.append({**base, "event_kind": "warning", "event_type": warning, "decision_index": decision_index, "action": action, "stack_trace": None, "exception": None})
        elif warning.startswith("step_exception:") or warning.startswith("unsupported_pending_choice:") or warning.startswith("truncated_at_time_budget:"):
            decision_index = result.get("decision_count")
            action = result["decisions"][-1]["selected_action"] if result.get("decisions") else None
            events.append({**base, "event_kind": "warning", "event_type": warning.split(":", 2)[0] + ":" + warning.split(":", 2)[1] if ":" in warning else warning, "decision_index": decision_index, "action": action, "stack_trace": None, "exception": None})

    for decision in result.get("decisions") or []:
        if check_illegal_action(decision):
            events.append(
                {
                    **base,
                    "event_kind": "illegal_action",
                    "event_type": "illegal_action",
                    "decision_index": decision["decision_index"],
                    "action": decision["selected_action"],
                    "stack_trace": None,
                    "exception": None,
                }
            )

    worker_exception = payload.get("worker_exception")
    if worker_exception:
        events.append(
            {
                **base,
                "event_kind": "worker_exception",
                "event_type": f"worker_exception:{worker_exception.get('type')}",
                "decision_index": None,
                "action": None,
                "stack_trace": worker_exception.get("traceback"),
                "exception": worker_exception,
            }
        )

    return events


def _summarize_error_events(error_events: list[dict]) -> dict:
    by_kind: dict[str, int] = {}
    by_type: dict[str, int] = {}
    scenarios_by_type: dict[str, list[str]] = {}
    for event in error_events:
        kind = event["event_kind"]
        event_type = event["event_type"]
        by_kind[kind] = by_kind.get(kind, 0) + 1
        by_type[event_type] = by_type.get(event_type, 0) + 1
        scenarios = scenarios_by_type.setdefault(event_type, [])
        if event["trajectory_id"] not in scenarios:
            scenarios.append(event["trajectory_id"])
    return {
        "total_error_events": len(error_events),
        "error_kind_counts": by_kind,
        "error_type_counts": by_type,
        "scenarios_by_type": scenarios_by_type,
    }


def _scenario_result_row(result: dict, payload: dict, quality: dict, error_events: list[dict]) -> dict:
    return {
        "trajectory_id": result["trajectory_id"],
        "source_run_id": result["source_run_id"],
        "source_combat_index": result["source_combat_index"],
        "status": result["status"],
        "elapsed_s": round(payload.get("elapsed_s"), 3) if payload.get("elapsed_s") is not None else None,
        "worker_pid": payload.get("worker_pid"),
        "stderr_excerpt": payload.get("stderr_excerpt"),
        "stderr_log_path": payload.get("stderr_log_path"),
        "worker_exception": payload.get("worker_exception"),
        "quality": quality,
        "error_events": error_events,
        "result": result,
    }


def _worker_log_path() -> Path:
    return Path(gettempdir()) / f"sts2_rl_worker_{os.getpid()}.log"


def _json_safe(value):
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _init_worker() -> None:
    global _WORKER_EMULATOR, _WORKER_AGENT, _WORKER_STDERR_PATH, _WORKER_STDERR_HANDLE
    if _WORKER_EMULATOR is None or _WORKER_AGENT is None:
        _WORKER_EMULATOR, _WORKER_AGENT = build_default_agent()
    if _WORKER_STDERR_HANDLE is None:
        _WORKER_STDERR_PATH = _worker_log_path()
        _WORKER_STDERR_HANDLE = open(_WORKER_STDERR_PATH, "a+", encoding="utf-8", buffering=1)
        try:
            os.dup2(_WORKER_STDERR_HANDLE.fileno(), 2)
            os.dup2(_WORKER_STDERR_HANDLE.fileno(), 1)
        except OSError:
            pass


def _run_single_scenario(task: dict) -> dict:
    global _WORKER_EMULATOR, _WORKER_AGENT, _WORKER_STDERR_PATH, _WORKER_STDERR_HANDLE
    if _WORKER_EMULATOR is None or _WORKER_AGENT is None:
        _init_worker()

    spec = task["spec"]
    source_run_id = task["source_run_id"]
    combat_index = task["combat_index"]
    trajectory_id = task["trajectory_id"]
    max_decisions = task["max_decisions"]

    stderr_before = 0
    stderr_after = 0
    stderr_excerpt = None
    if _WORKER_STDERR_HANDLE is not None:
        _WORKER_STDERR_HANDLE.flush()
        _WORKER_STDERR_HANDLE.write(f"\n=== BEGIN {trajectory_id} ===\n")
        _WORKER_STDERR_HANDLE.flush()
        stderr_before = _WORKER_STDERR_HANDLE.tell()

    started = time.time()
    try:
        result = generate_trajectory(
            spec,
            _WORKER_EMULATOR,
            _WORKER_AGENT,
            trajectory_id,
            source_run_id,
            combat_index,
            max_decisions=max_decisions,
        )
        result = _json_safe(result)
        worker_exception = None
    except Exception as exc:  # noqa: BLE001
        result = {
            "trajectory_id": trajectory_id,
            "source_run_id": source_run_id,
            "source_combat_index": combat_index,
            "status": "quarantined",
            "reasons": [f"worker_exception:{type(exc).__name__}"],
            "quarantine_classification": "other",
            "input_state": spec,
            "initialized_state": None,
            "diffs": [],
            "state_diff": [],
            "first_failing_decision": None,
            "exception": {
                "type": type(exc).__name__,
                "message": str(exc),
                "traceback": traceback.format_exc(),
            },
            "recommended_action": "investigate_worker_exception",
        }
        result = _json_safe(result)
        worker_exception = result["exception"]
    elapsed = time.time() - started

    if _WORKER_STDERR_HANDLE is not None:
        _WORKER_STDERR_HANDLE.write(f"=== END {trajectory_id} ===\n")
        _WORKER_STDERR_HANDLE.flush()
        stderr_after = _WORKER_STDERR_HANDLE.tell()
        try:
            with open(_WORKER_STDERR_PATH, "r", encoding="utf-8", errors="replace") as f:
                f.seek(stderr_before)
                stderr_excerpt = f.read(stderr_after - stderr_before).strip() or None
        except OSError:
            stderr_excerpt = None

    return {
        "trajectory_id": trajectory_id,
        "source_run_id": source_run_id,
        "source_combat_index": combat_index,
        "result": result,
        "elapsed_s": elapsed,
        "worker_pid": os.getpid(),
        "worker_exception": worker_exception,
        "stderr_excerpt": stderr_excerpt,
        "stderr_log_path": str(_WORKER_STDERR_PATH) if _WORKER_STDERR_PATH is not None else None,
    }


def _execute_scenarios(
    scenarios: list[tuple[dict, str, int]],
    max_decisions: int,
    workers: int,
) -> list[dict]:
    tasks = [
        {
            "spec": spec,
            "source_run_id": source_run_id,
            "combat_index": combat_index,
            "trajectory_id": f"{source_run_id}-{combat_index}",
            "max_decisions": max_decisions,
        }
        for spec, source_run_id, combat_index in scenarios
    ]
    if workers <= 1:
        _init_worker()
        return [_run_single_scenario(task) for task in tasks]

    ctx = multiprocessing.get_context("spawn")
    completed: dict[str, dict] = {}
    with ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_init_worker) as executor:
        future_map = {executor.submit(_run_single_scenario, task): task for task in tasks}
        for future in as_completed(future_map):
            task = future_map[future]
            trajectory_id = task["trajectory_id"]
            try:
                payload = future.result()
            except Exception as exc:  # noqa: BLE001
                payload = {
                    "trajectory_id": trajectory_id,
                    "source_run_id": task["source_run_id"],
                    "source_combat_index": task["combat_index"],
                    "elapsed_s": None,
                    "worker_pid": None,
                    "stderr_excerpt": None,
                    "stderr_log_path": None,
                    "worker_exception": {
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    },
                    "result": {
                        "trajectory_id": trajectory_id,
                        "source_run_id": task["source_run_id"],
                        "source_combat_index": task["combat_index"],
                        "status": "quarantined",
                        "reasons": [f"worker_crash:{type(exc).__name__}"],
                        "quarantine_classification": "other",
                        "input_state": task["spec"],
                        "initialized_state": None,
                        "diffs": [],
                        "state_diff": [],
                        "first_failing_decision": None,
                        "exception": {
                            "type": type(exc).__name__,
                            "message": str(exc),
                            "traceback": traceback.format_exc(),
                        },
                        "recommended_action": "investigate_worker_crash",
                    },
                }
            if trajectory_id in completed:
                raise RuntimeError(f"duplicate trajectory result received for {trajectory_id}")
            completed[trajectory_id] = payload

    ordered = []
    for task in tasks:
        trajectory_id = task["trajectory_id"]
        if trajectory_id not in completed:
            raise RuntimeError(f"missing trajectory result for {trajectory_id}")
        ordered.append(completed[trajectory_id])
    return ordered


def run_batch(
    scenarios: list[tuple[dict, str, int]],
    out_dir: Path,
    max_decisions: int = 50,
    determinism_sample: int = 5,
    workers: int = 1,
    resume: bool = False,
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)

    trajectories_path = out_dir / "trajectories.jsonl"
    quarantine_path = out_dir / "quarantine.jsonl"
    trajectory_meta_path = out_dir / "trajectory_meta.jsonl"  # one row per COMBAT (not per decision) - carries warnings/outcome even for trajectories with 0 decisions
    quality_path = out_dir / "trajectory_quality.jsonl"
    scenario_manifest_path = out_dir / "scenario_manifest.jsonl"
    scenario_results_path = out_dir / "scenario_results.jsonl"
    error_events_path = out_dir / "error_events.jsonl"
    error_summary_path = out_dir / "error_summary.json"
    logs_dir = out_dir / "human_readable_logs"
    repros_dir = out_dir / "generated_repros"
    logs_dir.mkdir(exist_ok=True)
    repros_dir.mkdir(exist_ok=True)

    if resume:
        if not scenario_manifest_path.exists():
            raise FileNotFoundError(f"--resume requested but scenario manifest not found: {scenario_manifest_path}")
        manifest_scenarios = load_scenario_manifest(scenario_manifest_path)
        manifest_ids = [f"{source_run_id}-{combat_index}" for _, source_run_id, combat_index in manifest_scenarios]
        requested_ids = [f"{source_run_id}-{combat_index}" for _, source_run_id, combat_index in scenarios]
        if requested_ids and requested_ids != manifest_ids:
            raise RuntimeError("resume requested with a scenario list that differs from the saved scenario manifest")
        scenarios = manifest_scenarios
    else:
        write_scenario_manifest(scenario_manifest_path, scenarios)

    existing_rows = load_existing_scenario_results(scenario_results_path) if resume else {}
    seen_trajectory_ids = set(existing_rows)
    pending_scenarios = [
        (spec, source_run_id, combat_index)
        for spec, source_run_id, combat_index in scenarios
        if f"{source_run_id}-{combat_index}" not in seen_trajectory_ids
    ]

    file_mode = "a" if resume else "w"
    all_rows = dict(existing_rows)

    with (
        trajectories_path.open(file_mode, encoding="utf-8") as traj_f,
        quarantine_path.open(file_mode, encoding="utf-8") as quar_f,
        trajectory_meta_path.open(file_mode, encoding="utf-8") as meta_f,
        quality_path.open(file_mode, encoding="utf-8") as quality_f,
        scenario_results_path.open(file_mode, encoding="utf-8") as result_f,
        error_events_path.open(file_mode, encoding="utf-8") as error_f,
    ):
        if pending_scenarios:
            worker_payloads = _execute_scenarios(pending_scenarios, max_decisions=max_decisions, workers=workers)
        else:
            worker_payloads = []
        for payload in worker_payloads:
            trajectory_id = payload["trajectory_id"]
            result = payload["result"]
            elapsed = payload["elapsed_s"]
            if trajectory_id in seen_trajectory_ids:
                raise RuntimeError(f"duplicate trajectory_id while writing outputs: {trajectory_id}")
            seen_trajectory_ids.add(trajectory_id)

            quality = analyze_trajectory_result(result)
            _enrich_decisions_for_output(result, quality)
            error_events = _extract_error_events(result, payload, quality)
            row = _scenario_result_row(result, payload, quality, error_events)
            all_rows[trajectory_id] = row

            if result["status"] == "quarantined":
                result.setdefault("worker_pid", payload.get("worker_pid"))
                result.setdefault("stderr_excerpt", payload.get("stderr_excerpt"))
                result.setdefault("stderr_log_path", payload.get("stderr_log_path"))
                result.setdefault("worker_exception", payload.get("worker_exception"))
                quar_f.write(json.dumps(result, ensure_ascii=False, default=str) + "\n")
                quar_f.flush()
            else:
                for d in result["decisions"]:
                    traj_f.write(json.dumps(d, ensure_ascii=False, default=str) + "\n")
                traj_f.flush()
                meta_f.write(
                    json.dumps(
                        {
                            "trajectory_id": result["trajectory_id"],
                            "source_run_id": result["source_run_id"],
                            "source_combat_index": result["source_combat_index"],
                            "decision_count": result["decision_count"],
                            "truncated": result["truncated"],
                            "final_outcome": result["final_outcome"],
                            "final_is_terminal": result["final_is_terminal"],
                            "warnings": result["warnings"],
                            "termination_reason": result.get("termination_reason"),
                            "max_decisions_reached": result.get("max_decisions_reached"),
                            "cycle_detected": result.get("cycle_detected"),
                            "no_progress_detected": result.get("no_progress_detected"),
                            "termination_limit": result.get("termination_limit"),
                            "non_learning_transition": result.get("non_learning_transition"),
                            "heuristic_exception": result.get("heuristic_exception"),
                            "worker_pid": payload.get("worker_pid"),
                            "stderr_excerpt": payload.get("stderr_excerpt"),
                            "stderr_log_path": payload.get("stderr_log_path"),
                            "worker_exception": payload.get("worker_exception"),
                            "elapsed_s": round(elapsed, 3),
                        },
                        ensure_ascii=False,
                        default=str,
                    )
                    + "\n"
                )
                meta_f.flush()
                if len([r for r in all_rows.values() if r["status"] == "ok"]) <= 10:
                    (logs_dir / f"{trajectory_id.replace(':', '_')}.md").write_text(human_readable_log(result), encoding="utf-8")

            quality_f.write(json.dumps(quality, ensure_ascii=False, default=str) + "\n")
            quality_f.flush()
            result_f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
            result_f.flush()
            for event in error_events:
                error_f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            error_f.flush()

    ok_results = []
    quarantined = []
    illegal_action_count = 0
    total_decision_count = 0
    total_decision_time = 0.0
    decision_budget_exceeded_count = 0
    fallback_count = 0
    skipped_candidate_count = 0
    evaluated_candidate_count = 0
    per_combat_times = []
    win_count = 0
    loss_count = 0
    truncated_count = 0
    cycle_detected_count = 0
    no_progress_detected_count = 0
    data_usage_counts = {}
    truncation_classification_counts = {}
    remaining_hp = []
    potion_use_counts = []
    error_events_all: list[dict] = []
    selected_action_type_counts: dict[str, int] = {}
    selected_action_label_counts: dict[str, int] = {}

    ordered_rows = []
    for spec, source_run_id, combat_index in scenarios:
        trajectory_id = f"{source_run_id}-{combat_index}"
        row = all_rows.get(trajectory_id)
        if row is None:
            raise RuntimeError(f"missing scenario result row for {trajectory_id}")
        ordered_rows.append(row)

    for row in ordered_rows:
        result = row["result"]
        quality = row["quality"]
        error_events_all.extend(row.get("error_events") or [])
        data_usage_counts[quality["data_usage"]] = data_usage_counts.get(quality["data_usage"], 0) + 1
        if quality["classification"]:
            truncation_classification_counts[quality["classification"]] = truncation_classification_counts.get(quality["classification"], 0) + 1
        if quality.get("no_progress_detected"):
            no_progress_detected_count += 1

        if result["status"] == "quarantined":
            quarantined.append(result)
            continue

        ok_results.append(result)
        elapsed_s = row.get("elapsed_s")
        if elapsed_s is not None:
            per_combat_times.append(elapsed_s)
        total_decision_count += result["decision_count"]
        if result["truncated"]:
            truncated_count += 1
        if result.get("cycle_detected"):
            cycle_detected_count += 1
        for d in result["decisions"]:
            if check_illegal_action(d):
                illegal_action_count += 1
            total_decision_time += d.get("elapsed_ms", 0.0) / 1000.0
            if d.get("decision_budget_exceeded"):
                decision_budget_exceeded_count += 1
            if d.get("fallback_used"):
                fallback_count += 1
            skipped_candidate_count += sum(1 for c in d.get("action_scores", []) if c.get("score") is None)
            evaluated_candidate_count += d.get("evaluated_action_count", 0)
        type_counts, label_counts = _trajectory_action_distribution(result)
        for key, value in type_counts.items():
            selected_action_type_counts[key] = selected_action_type_counts.get(key, 0) + value
        for key, value in label_counts.items():
            selected_action_label_counts[key] = selected_action_label_counts.get(key, 0) + value
        outcome = (result["final_outcome"] or "").lower()
        if outcome == "victory":
            win_count += 1
            if result["decisions"]:
                remaining_hp.append(result["decisions"][-1]["next_state"].get("hp"))
        elif outcome == "defeat":
            loss_count += 1
        potions_used = sum(1 for d in result["decisions"] if d["selected_action"]["action_type"] == "potion")
        potion_use_counts.append(potions_used)

    # determinism check: re-run a sample of the ok scenarios and compare decision sequences
    determinism_checked = 0
    determinism_matched = 0
    sample = ok_results[:determinism_sample]
    for result in sample:
        spec, source_run_id, combat_index = next(
            (s, r, c) for s, r, c in scenarios if f"{r}-{c}" == result["trajectory_id"]
        )
        rerun = _execute_scenarios(
            [(spec, source_run_id, combat_index)],
            max_decisions=max_decisions,
            workers=1,
        )[0]["result"]
        determinism_checked += 1
        seq_a = [(d["selected_action"]["action_id"], d["selected_enemy_index"]) for d in result["decisions"]]
        seq_b = [(d["selected_action"]["action_id"], d["selected_enemy_index"]) for d in rerun["decisions"]]
        if seq_a == seq_b and result["final_outcome"] == rerun["final_outcome"]:
            determinism_matched += 1

    summary = {
        "total_scenarios": len(scenarios),
        "ok": len(ok_results),
        "quarantined": len(quarantined),
        "resume_used": resume,
        "already_completed_before_resume": len(existing_rows) if resume else 0,
        "newly_executed_this_invocation": len(pending_scenarios),
        "init_success_rate": round(100 * len(ok_results) / len(scenarios), 2) if scenarios else None,
        "combat_completion_rate": round(100 * (len(ok_results) - truncated_count) / len(ok_results), 2) if ok_results else None,
        "decisions_per_combat_avg": round(total_decision_count / len(ok_results), 2) if ok_results else None,
        "illegal_action_count": illegal_action_count,
        "heuristic_exception_count": sum(1 for r in ok_results for w in r["warnings"] if w.startswith("heuristic_exception")),
        "emulator_step_exception_count": sum(1 for r in ok_results for w in r["warnings"] if w.startswith("step_exception")),
        "decision_budget_exceeded_count": decision_budget_exceeded_count,
        "fallback_count": fallback_count,
        "skipped_candidate_count": skipped_candidate_count,
        "evaluated_candidate_count": evaluated_candidate_count,
        "timeout_count": sum(
            1 for r in quarantined if any("Timeout" in reason for reason in r["reasons"])
        ),
        "determinism_checked": determinism_checked,
        "determinism_matched": determinism_matched,
        "determinism_rate_pct": round(100 * determinism_matched / determinism_checked, 2) if determinism_checked else None,
        "win_count": win_count,
        "loss_count": loss_count,
        "truncated_count": truncated_count,
        "cycle_detected_count": cycle_detected_count,
        "no_progress_detected_count": no_progress_detected_count,
        "data_usage_counts": data_usage_counts,
        "truncation_classification_counts": truncation_classification_counts,
        "remaining_hp_avg_on_win": round(sum(remaining_hp) / len(remaining_hp), 1) if remaining_hp else None,
        "potion_use_avg": round(sum(potion_use_counts) / len(potion_use_counts), 2) if potion_use_counts else None,
        "avg_time_per_combat_s": round(sum(per_combat_times) / len(per_combat_times), 3) if per_combat_times else None,
        "avg_time_per_decision_s": round(total_decision_time / total_decision_count, 4) if total_decision_count else None,
        "quarantine_reason_counts": _count_reasons(quarantined),
        "error_summary": _summarize_error_events(error_events_all),
        "neows_bones_quarantine_count": sum(
            1 for r in quarantined if any("neows_bones" in reason for reason in r["reasons"])
        ),
        "selected_action_type_counts": selected_action_type_counts,
        "top_selected_action_labels": dict(sorted(selected_action_label_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:50]),
        "end_turn_count": selected_action_label_counts.get("End Turn", 0),
        "end_turn_rate_pct": round(
            100.0 * selected_action_label_counts.get("End Turn", 0) / total_decision_count,
            2,
        ) if total_decision_count else None,
        "emulator_commit": EMULATOR_COMMIT,
        "emulator_dll_sha256": emulator_dll_sha256(),
        "heuristic_version": HEURISTIC_VERSION,
        "workers": workers,
    }
    with (out_dir / "summary.json").open("w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    with error_summary_path.open("w", encoding="utf-8") as f:
        json.dump(summary["error_summary"], f, indent=2, ensure_ascii=False)

    for event in error_events_all:
        _write_repro_script(repros_dir, event["trajectory_id"])

    print(json.dumps(summary, indent=2, ensure_ascii=False))
    print(
        f"\nWrote {trajectories_path}, {quarantine_path}, {quality_path}, {scenario_results_path}, "
        f"{error_events_path}, {error_summary_path}, and {len(list(logs_dir.glob('*.md')))} human-readable logs to {logs_dir}"
    )
    return summary


def _count_reasons(quarantined: list[dict]) -> dict:
    counts: dict = {}
    for r in quarantined:
        for reason in r["reasons"]:
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def _write_repro_script(repros_dir: Path, trajectory_id: str) -> Path:
    safe_name = trajectory_id.replace(":", "_").replace("-", "_")
    path = repros_dir / f"repro_{safe_name}.py"
    script = f"""from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
SCRIPT = ROOT / "Combat" / "data" / "repro_from_batch_run.py"
RUN_DIR = Path(__file__).resolve().parents[1]

raise SystemExit(
    subprocess.call(
        [
            sys.executable,
            str(SCRIPT),
            "--run-dir",
            str(RUN_DIR),
            "--trajectory-id",
            "{trajectory_id}",
        ]
    )
)
"""
    path.write_text(script, encoding="utf-8")
    return path


if __name__ == "__main__":
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", choices=["fixed50", "reconstructed"], default="fixed50")
    parser.add_argument("--n", type=int, default=100)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--out", type=str, default=None)
    parser.add_argument("--max-decisions", type=int, default=50)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--determinism-sample", type=int, default=5)
    parser.add_argument("--exclude-manifest", action="append", default=[])
    args = parser.parse_args()

    if args.out:
        out_dir = Path(args.out)
    elif args.source == "fixed50":
        out_dir = DATA_DIR / "trajectories_fixed50"
    else:
        out_dir = DATA_DIR / f"trajectories_dev{args.n}"

    scenario_manifest_path = out_dir / "scenario_manifest.jsonl"
    if args.resume and scenario_manifest_path.exists():
        scenarios = load_scenario_manifest(scenario_manifest_path)
        run_batch(
            scenarios,
            out_dir,
            max_decisions=args.max_decisions,
            determinism_sample=args.determinism_sample,
            workers=args.workers,
            resume=True,
        )
        raise SystemExit(0)

    if args.source == "fixed50":
        scenarios = load_fixed_50()
    else:
        scenarios = load_reconstructed_sample(
            args.n,
            args.seed,
            exclude_manifest_paths=[Path(p) for p in args.exclude_manifest],
        )

    run_batch(
        scenarios,
        out_dir,
        max_decisions=args.max_decisions,
        determinism_sample=args.determinism_sample,
        workers=args.workers,
        resume=args.resume,
    )
