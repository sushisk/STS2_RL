"""CLR bootstrap and .NET <-> Python plain-value conversion for the STS2 emulator.

Only one `GameInstance` may be "live" per OS process - constructing/resetting a second
one tears down whatever the first had (confirmed experimentally; see battle_emulator.py
module docstring). Every caller in this package must therefore go through the single
shared instance returned by `shared_game_instance()`, never construct their own.

IMPORTANT: never run from a working directory inside the emulator's own repo
(C:\\STS2_Emulator) and never add that repo root to sys.path. Python's import system
will find the "Sts2Emulator/Api" *source folder* there and resolve it as a namespace
package, shadowing the CLR-backed "Sts2Emulator.Api" module the clr importer provides -
`from Sts2Emulator.Api import GameInstance` then fails with a confusing ImportError.
(Discovered the hard way - reproduced and confirmed before landing this workaround.)
"""

from __future__ import annotations

import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

DEFAULT_REPO_ROOT = Path(r"C:\STS2_Emulator")
_EMULATOR_REPO_ROOT_ENV = "STS2_EMULATOR_REPO_ROOT"

_types: dict[str, Any] | None = None
_shared_instance = None


def ensure_loaded(repo_root: Path | None = None) -> dict[str, Any]:
    """Bootstraps pythonnet/CoreCLR and returns the CLR types this package needs.

    Idempotent - safe to call from every module that needs a type; only the first
    call actually loads the runtime and assembly.
    """
    global _types
    if _types is not None:
        return _types

    # Integration callers can point every spawned RL worker at an isolated Emulator
    # build without replacing the ordinary Debug output (which may be in use by a
    # separate long-running Python process).  An explicit function argument remains
    # authoritative for callers that already supply one.
    root = repo_root or Path(os.environ.get(_EMULATOR_REPO_ROOT_ENV, DEFAULT_REPO_ROOT))
    cli_output_dir = root / "Sts2Emulator.Cli" / "bin" / "Debug" / "net8.0"
    dll_path = cli_output_dir / "Sts2Emulator.dll"
    runtime_config_path = cli_output_dir / "Sts2Emulator.Cli.runtimeconfig.json"
    if not dll_path.exists() or not runtime_config_path.exists():
        raise FileNotFoundError(
            f"Build output not found under {cli_output_dir}\n"
            "Run `dotnet build Sts2EmulatorPhase1.sln` first."
        )

    from pythonnet import load

    load("coreclr", runtime_config=str(runtime_config_path))
    import clr  # noqa: E402

    clr.AddReference(str(dll_path))

    from Sts2Emulator.Api import (  # noqa: E402
        ActionFaultedException,
        FaultedCombatSessionException,
        GameInstance,
        QuiescentBoundaryViolationException,
        SnapshotRestoreFailedException,
        SnapshotRestoreRejectedException,
    )
    from Sts2Emulator.Dto import (  # noqa: E402
        CardInstanceScenario,
        CombatScenario,
        EnemyScenario,
        OrbScenario,
        PendingChoiceScenario,
        PotionScenario,
        PowerStack,
        RelicStack,
    )
    from Sts2Emulator.Dto.Snapshot import CombatStateSnapshot as ClrCombatStateSnapshot  # noqa: E402
    from System.Collections.Generic import Dictionary, List  # noqa: E402
    from System import Boolean, Decimal, Int32, Object, String  # noqa: E402
    from System.Text.Json import JsonSerializer  # noqa: E402

    _types = {
        "GameInstance": GameInstance,
        "QuiescentBoundaryViolationException": QuiescentBoundaryViolationException,
        "ActionFaultedException": ActionFaultedException,
        "FaultedCombatSessionException": FaultedCombatSessionException,
        "SnapshotRestoreRejectedException": SnapshotRestoreRejectedException,
        "SnapshotRestoreFailedException": SnapshotRestoreFailedException,
        "CombatScenario": CombatScenario,
        "EnemyScenario": EnemyScenario,
        "OrbScenario": OrbScenario,
        "PendingChoiceScenario": PendingChoiceScenario,
        "PowerStack": PowerStack,
        "RelicStack": RelicStack,
        "CardInstanceScenario": CardInstanceScenario,
        "PotionScenario": PotionScenario,
        "ClrCombatStateSnapshot": ClrCombatStateSnapshot,
        "Dictionary": Dictionary,
        "List": List,
        "Boolean": Boolean,
        "Decimal": Decimal,
        "Int32": Int32,
        "Object": Object,
        "String": String,
        "JsonSerializer": JsonSerializer,
    }
    return _types


def shared_game_instance(repo_root: Path | None = None):
    """The one and only live GameInstance for this process."""
    global _shared_instance
    types = ensure_loaded(repo_root)
    if _shared_instance is None:
        _shared_instance = types["GameInstance"]()
    return _shared_instance


def str_list(items):
    types = ensure_loaded()
    lst = types["List"][types["String"]]()
    for item in items:
        lst.Add(item)
    return lst


def to_plain(value: Any) -> Any:
    """Recursively converts a pythonnet-wrapped .NET value into plain Python data.

    System.Decimal (orb basePassiveValue/baseEvokeValue - see battle_emulator.py's
    _orb_scenarios/_decimal_value, the write-side counterpart of this same value) is
    special-cased to a plain float: it has neither Keys/Values nor __iter__, so without
    this check it fell through to `return value` unchanged - a live CLR object masquerading
    as plain data. That silently broke the very first caller to copy.deepcopy() a whole
    engine_state containing orbs (BattleEmulator.clone_state()/with_shuffle_seed(), used by
    LookaheadSearcher and this evaluation harness) with `TypeError: cannot pickle 'Decimal'
    object` - deepcopy has no idea how to clone a CLR type. Every other engine_state
    consumer only ever read individual fields back out (never deep-copied the dict itself),
    which is why this went unnoticed until now.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if value.__class__.__module__ == "System" and value.__class__.__name__ == "Decimal":
        # float(value) fails - pythonnet's Decimal wrapper doesn't support Python's float()
        # protocol directly. str() round-trip is the same pattern battle_emulator.py's own
        # _decimal_value()/_stable_orb_value() already rely on for this exact type.
        return float(str(value))
    if hasattr(value, "Keys") and hasattr(value, "Values"):
        return {str(k): to_plain(value[k]) for k in value.Keys}
    if hasattr(value, "__iter__"):
        return [to_plain(item) for item in value]
    return value


def legal_action_to_dict(action) -> dict:
    return {
        "action_id": int(action.ActionId),
        "action_type": str(action.ActionType),
        "label": str(action.Label),
        "is_available": bool(action.IsAvailable),
        "parameters": to_plain(action.Parameters),
        "semantic_key": str(action.SemanticKey),
    }


def legal_actions_to_list(actions) -> list[dict]:
    return [legal_action_to_dict(a) for a in actions]


def observation_to_dict(obs) -> dict:
    return {
        "turn": int(obs.Turn),
        "is_terminal": bool(obs.IsTerminal),
        "outcome": str(obs.Outcome),
        "state": to_plain(obs.State),
    }


def _strip_python_snapshot_only_fields(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: _strip_python_snapshot_only_fields(v)
            for k, v in value.items()
            if k != "unknown_fields"
        }
    if isinstance(value, list):
        return [_strip_python_snapshot_only_fields(v) for v in value]
    return value


def _snapshot_to_json_text(snapshot: Any) -> str | None:
    if isinstance(snapshot, str):
        return snapshot
    if is_dataclass(snapshot):
        snapshot = asdict(snapshot)
    if isinstance(snapshot, dict):
        from combat_state_snapshot import canonical_json

        return canonical_json(
            _strip_python_snapshot_only_fields(snapshot),
            exclude_volatile=False,
        )
    return None


def snapshot_to_clr(snapshot: Any) -> Any:
    """Returns a CLR CombatStateSnapshot, reusing the existing Python DTO serializer.

    Raw pythonnet-wrapped snapshots are passed through unchanged. Python
    CombatStateSnapshot dataclasses or plain snapshot dicts are serialized with the
    established canonical JSON helper, then deserialized into the C# DTO.
    """
    types = ensure_loaded()
    if hasattr(snapshot, "GetType") and str(snapshot.GetType().FullName) == "Sts2Emulator.Dto.Snapshot.CombatStateSnapshot":
        return snapshot

    json_text = _snapshot_to_json_text(snapshot)
    if json_text is None:
        return snapshot
    return types["JsonSerializer"].Deserialize[types["ClrCombatStateSnapshot"]](json_text)


def restore_snapshot(game, snapshot):
    return game.RestoreSnapshot(snapshot_to_clr(snapshot))


def restore_snapshot_json(game, json_text: str):
    return game.RestoreSnapshotJson(json_text)


def validate_restore_snapshot(game, snapshot):
    return game.ValidateRestoreSnapshot(snapshot_to_clr(snapshot))


def validate_restore_snapshot_json(game, json_text: str):
    return game.ValidateRestoreSnapshotJson(json_text)


def get_restore_capabilities(game):
    return game.GetRestoreCapabilities()
