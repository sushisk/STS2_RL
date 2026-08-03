"""Zero-index 100-run multi-seed batch (RL担当指示：推論撤去後の総合テスト・デバッグ, section 3).

Reuses ONE `WholeRunSession` (one `GameInstance`) across all runs, sequentially
re-initialized via `session.start_run(seed, ...)` each time - never constructs a second
`GameInstance`. Records, per run: seed, per-Decision (boundary, action taken, action_id,
Transition when present), and whether the resulting action sequence + final run_state are
deterministic when the SAME seed is replayed.
"""

from __future__ import annotations

import json
from pathlib import Path

from execution_mode import zero_index_room_picker
from room_progression_driver import drive_rooms
from whole_run_session import zero_index_action, WholeRunSession


def run_batch(count: int = 100, base_seed: int = 1, max_steps: int = 40, min_rooms: int = 1) -> dict:
    session = WholeRunSession()
    runs = []
    for i in range(count):
        seed = base_seed + i
        summary = drive_rooms(
            session, min_rooms=min_rooms, max_steps=max_steps, seed=seed,
            action_picker=zero_index_action, room_picker=zero_index_room_picker,
        )
        action_sequence = [
            e["action"]["action_id"] for e in summary["log"] if e.get("event") == "step"
        ]
        runs.append(
            {
                "seed": seed,
                "rooms_entered": summary["rooms_entered"],
                "final_boundary": summary["final_boundary"],
                "room_kinds_seen": summary["room_kinds_seen"],
                "action_sequence": action_sequence,
                "step_count": len(action_sequence),
            }
        )
    return {"count": count, "runs": runs}


def determinism_check(seed: int, max_steps: int = 40) -> dict:
    session_a = WholeRunSession()
    summary_a = drive_rooms(
        session_a, min_rooms=1, max_steps=max_steps, seed=seed,
        action_picker=zero_index_action, room_picker=zero_index_room_picker,
    )
    del session_a
    session_b = WholeRunSession()
    summary_b = drive_rooms(
        session_b, min_rooms=1, max_steps=max_steps, seed=seed,
        action_picker=zero_index_action, room_picker=zero_index_room_picker,
    )
    seq_a = [e["action"]["action_id"] for e in summary_a["log"] if e.get("event") == "step"]
    seq_b = [e["action"]["action_id"] for e in summary_b["log"] if e.get("event") == "step"]
    run_state_a = summary_a["log"][-1].get("run_state") if summary_a["log"] else None
    run_state_b = summary_b["log"][-1].get("run_state") if summary_b["log"] else None
    return {
        "seed": seed,
        "action_sequences_match": seq_a == seq_b,
        "final_boundary_matches": summary_a["final_boundary"] == summary_b["final_boundary"],
        "run_state_matches": run_state_a == run_state_b,
        "seq_a": seq_a,
        "seq_b": seq_b,
    }


if __name__ == "__main__":
    import sys

    out_dir = Path(r"C:\STS2_RL\Outputs\reports\whole_run_logs")
    out_dir.mkdir(parents=True, exist_ok=True)

    batch = run_batch(count=100, base_seed=1, max_steps=40)
    with open(out_dir / "zero_index_100run_batch.json", "w", encoding="utf-8") as f:
        json.dump(batch, f, indent=2)

    # Documented finding (see report): zero_index can never conclude a real Combat (this
    # Emulator always lists "system"/End Turn as LegalActions[0]), so no seed reaches 10
    # rooms this way - confirmed once more here (bounded steps, not an exhaustive re-search
    # across seeds, which would be a wasted 1500-step stall for every single seed tried).
    session = WholeRunSession()
    ten_room_summary = drive_rooms(
        session, min_rooms=10, max_steps=300, seed=18,
        action_picker=zero_index_action, room_picker=zero_index_room_picker,
    )
    ten_room_summary["seed_used"] = 18
    with open(out_dir / "zero_index_10room_search_result.json", "w", encoding="utf-8") as f:
        json.dump(ten_room_summary, f, indent=2)

    det1 = determinism_check(seed=18)
    det2 = determinism_check(seed=42)
    with open(out_dir / "zero_index_determinism_check.json", "w", encoding="utf-8") as f:
        json.dump([det1, det2], f, indent=2)

    print(json.dumps({
        "batch_count": batch["count"],
        "ten_room_found": ten_room_summary is not None and ten_room_summary.get("rooms_entered", 0) >= 10,
        "ten_room_seed": ten_room_summary.get("seed_used") if ten_room_summary else None,
        "determinism_seed18": {k: v for k, v in det1.items() if k not in ("seq_a", "seq_b")},
        "determinism_seed42": {k: v for k, v in det2.items() if k not in ("seq_a", "seq_b")},
    }, indent=2), file=sys.stderr)
