"""Phase 2A diagnostic Snapshot capture/save utility (this task's "保存形式" item).

Wraps `LiveCombatSession.capture_snapshot()`'s result with a provenance envelope
(schemaVersion, schema/contract SHA256, Emulator commit, DLL SHA256, RL source manifest
SHA256, capture-time DecisionFrame) and writes it to disk. Diagnostic-only - not part of
the normal decision loop; nothing in this file is imported by `combat_env.py`/
`live_combat_session.py`/any Policy/Choice-Policy/Heuristic code path.
"""

from __future__ import annotations

import hashlib
import json
import sys
from dataclasses import asdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

CONTRACT_PATH = Path(r"C:\STS2_RL\Common\contracts\combat_state_contract.v0.3.md")
RL_SOURCE_MANIFEST_PATH = Path(r"C:\STS2_RL\Common\contracts\rl_phase1_source_manifest_20260726.json")


def _sha256_file(path: Path) -> "str | None":
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def emulator_git_commit() -> "str | None":
    import subprocess

    try:
        out = subprocess.run(["git", "-C", r"C:\STS2_Emulator", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None


def emulator_dll_sha256() -> "str | None":
    return _sha256_file(Path(r"C:\STS2_Emulator\Sts2Emulator.Cli\bin\Debug\net8.0\Sts2Emulator.dll"))


def save_snapshot_with_envelope(session, out_path: Path):
    """Captures via `session.capture_snapshot()` and writes a provenance-wrapped JSON
    file. Returns the `CombatStateSnapshot` object (for further in-process inspection by
    the caller) - the on-disk file is for diagnostic archival only."""
    snapshot = session.capture_snapshot()

    envelope = {
        "schemaVersion": snapshot.Metadata.SchemaVersion,
        "contractPath": str(CONTRACT_PATH),
        "contractSha256": _sha256_file(CONTRACT_PATH),
        "emulatorCommit": emulator_git_commit(),
        "emulatorDllSha256": emulator_dll_sha256(),
        "rlSourceManifestPath": str(RL_SOURCE_MANIFEST_PATH),
        "rlSourceManifestSha256": _sha256_file(RL_SOURCE_MANIFEST_PATH),
        "decisionFrameAtCapture": {
            "combat_session_id": snapshot.Metadata.CombatSessionId,
            "step_index": snapshot.Metadata.StepIndex,
            "continuation_step_index": snapshot.Metadata.ContinuationStepIndex,
        },
        "snapshot": snapshot_to_plain(snapshot),
    }

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return snapshot


def snapshot_to_plain(snapshot) -> dict:
    """Converts the dataclass tree back to a plain dict for JSON serialization - a
    faithful round-trip of what CaptureSnapshotJson() produced (modulo the Python
    dataclasses' own added `unknown_fields` bookkeeping, kept for diagnostic visibility)."""
    return asdict(snapshot)


if __name__ == "__main__":
    import argparse

    from combat_env import CombatEnv
    from preflight_validate import preflight_validate
    from policy_agent import build_policy_agent

    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=_HERE / "choice_policy_online_eval_manifest.jsonl")
    parser.add_argument("--trajectory-id", default="302-13")
    parser.add_argument("--out", type=Path, default=_HERE / "snapshot_diagnostic_sample.json")
    args = parser.parse_args()

    rows = [json.loads(line) for line in args.manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    row = next(r for r in rows if r["trajectory_id"] == args.trajectory_id)

    emulator, heuristic_agent, policy_agent = build_policy_agent()
    pre = preflight_validate(row["spec"], emulator)
    assert pre["status"] == "ok", pre["reasons"]

    env = CombatEnv(reward_fn=lambda *a: 0.0)
    env.adopt_state(emulator.clone_state(pre["battle_state"]), row["spec"])

    snapshot = save_snapshot_with_envelope(env._session, args.out)  # noqa: SLF001
    print(f"captured Snapshot: completeness={snapshot.Metadata.Completeness} captureBoundary={snapshot.Metadata.CaptureBoundary}")
    print(f"-> {args.out}")
