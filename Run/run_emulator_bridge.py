"""CLR bootstrap and .NET <-> Python plain-value conversion for the Whole Run API.

This is the Whole-Run counterpart of `Combat/emulator_bridge.py`. It targets
`GameInstance`'s run-level surface (`StartRun`/`GetMapRooms`/`ChooseRoom`/`Step`/
`GetObservation`/`GetLegalActions`/`GetRoomContext`/`SaveState`/`LoadState`/
`GetEventRngState`/`SetEventRngState`), documented in
`C:\\STS2_Emulator\\docs\\api\\whole_run_api_reference_20260803.md` (baseline commit
`87a0962`).

Kept deliberately independent from `Combat/emulator_bridge.py` (no import of it):
that module's `shared_game_instance()` enforces a single shared `GameInstance` per
process for Combat-only use. The Whole Run layer instead constructs exactly one
`GameInstance` per OS process itself (see `WholeRunSession`) - the same
one-instance-per-process discipline, just owned by this module instead of shared
with Combat's.

IMPORTANT: never run from a working directory inside the emulator's own repo
(C:\\STS2_Emulator) and never add that repo root to sys.path - see
`Combat/emulator_bridge.py`'s module docstring for the exact ImportError this causes.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DEFAULT_REPO_ROOT = Path(r"C:\STS2_Emulator")

_types: dict[str, Any] | None = None


def ensure_loaded(repo_root: Path | None = None) -> dict[str, Any]:
    """Bootstraps pythonnet/CoreCLR and returns the CLR types this package needs.

    Idempotent - safe to call repeatedly; only the first call actually loads the
    runtime and assembly.
    """
    global _types
    if _types is not None:
        return _types

    root = repo_root or DEFAULT_REPO_ROOT
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

    from Sts2Emulator.Api import GameInstance  # noqa: E402

    _types = {
        "GameInstance": GameInstance,
    }
    return _types


def new_game_instance(repo_root: Path | None = None):
    """A fresh `GameInstance` for the current OS process.

    Constructing/resetting a second `GameInstance` in the same process tears down
    the first's active engine singleton state (`RunManager.Instance` /
    `CombatManager.Instance` are process-wide statics - see the Whole Run API
    reference's "GameInstance singleton 依存" section). Callers that need two live
    runs at once must use two OS processes, each calling this once.
    """
    types = ensure_loaded(repo_root)
    return types["GameInstance"]()


def to_plain(value: Any) -> Any:
    """Recursively converts a pythonnet-wrapped .NET value into plain Python data."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if value.__class__.__module__ == "System" and value.__class__.__name__ == "Decimal":
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
    }


def legal_actions_to_list(actions) -> list[dict]:
    return [legal_action_to_dict(a) for a in actions]


def observation_to_dict(obs) -> dict:
    return {
        "seed": int(obs.Seed),
        "seed_text": str(obs.SeedText),
        "character_id": str(obs.CharacterId),
        "ascension": int(obs.Ascension),
        "step_index": int(obs.StepIndex),
        "choice_scope": str(obs.ChoiceScope),
        "boundary": str(obs.Boundary),
        "combat_session_id": (str(obs.CombatSessionId) if obs.CombatSessionId is not None else None),
        "turn": int(obs.Turn),
        "is_terminal": bool(obs.IsTerminal),
        "outcome": str(obs.Outcome),
        "relics": [str(r) for r in obs.Relics],
        "metrics": to_plain(obs.Metrics),
        "extras": to_plain(obs.Extras),
        "state": to_plain(obs.State),
    }


def transition_outcome_to_dict(transition) -> "dict | None":
    """`StepResult.Transition` (`TransitionOutcome?`) - non-null exactly on the Step that
    watched a combat go from in-progress to concluded (Emulator commit `dd8c800`, "Separate
    combat completion result from current Boundary in StepResult"). `Kind` is currently
    always `"combat_completed"` when present. `final_observation`'s own `boundary` field is
    a transient snapshot from the instant combat concluded (before reward/room-exit
    post-processing) - never treat it as the StepResult's current boundary; use the
    sibling `observation`/`room_context` fields on the same StepResult dict for that.
    """
    if transition is None:
        return None
    return {
        "kind": str(transition.Kind),
        "victory": bool(transition.Victory),
        "final_player_hp": int(transition.FinalPlayerHp),
        "final_player_max_hp": int(transition.FinalPlayerMaxHp),
        "final_enemies": to_plain(transition.FinalEnemies),
        "final_observation": observation_to_dict(transition.FinalObservation),
        "combat_session_id": (str(transition.CombatSessionId) if transition.CombatSessionId is not None else None),
    }


def step_result_to_dict(result) -> dict:
    """`Done` now means the RUN is over (`observation.boundary == "run_terminal"`), not
    "a combat just ended" - a combat concluding is reported via `transition` instead (see
    `transition_outcome_to_dict`). `room_context` mirrors an immediate `GetRoomContext()`
    call taken from the same generation as `observation`/`legal_actions` - Step() now runs
    all post-combat/room-exit processing before building any of the three, so they always
    agree with each other and with an immediate follow-up `GetObservation()`/
    `GetRoomContext()` call (Emulator commit `dd8c800`).
    """
    return {
        "action_id": int(result.ActionId),
        "reward": float(result.Reward),
        "done": bool(result.Done),
        "observation": observation_to_dict(result.Observation),
        "legal_actions": legal_actions_to_list(result.LegalActions),
        "room_context": room_context_to_dict(result.RoomContext),
        "transition": transition_outcome_to_dict(result.Transition),
        "info": to_plain(result.Info),
    }


def event_room_context_to_dict(event_ctx) -> dict:
    return {
        "event_id": str(event_ctx.EventId),
        "is_finished": bool(event_ctx.IsFinished),
        "current_option_text_keys": [str(k) for k in event_ctx.CurrentOptionTextKeys],
    }


def room_context_to_dict(ctx) -> dict:
    return {
        "boundary": str(ctx.Boundary),
        "room_type": (str(ctx.RoomType) if ctx.RoomType is not None else None),
        "in_room": bool(ctx.InRoom),
        "room_resolved": bool(ctx.RoomResolved),
        "at_map_boundary": bool(ctx.AtMapBoundary),
        "act_index": (int(ctx.ActIndex) if ctx.ActIndex is not None else None),
        "act_floor": (int(ctx.ActFloor) if ctx.ActFloor is not None else None),
        "column": (int(ctx.Column) if ctx.Column is not None else None),
        "row": (int(ctx.Row) if ctx.Row is not None else None),
        "event": (event_room_context_to_dict(ctx.Event) if ctx.Event is not None else None),
    }


def map_room_option_to_dict(option) -> dict:
    return {
        "room_id": int(option.RoomId),
        "column": int(option.Column),
        "row": int(option.Row),
        "point_type": str(option.PointType),
    }


def map_room_options_to_list(options) -> list[dict]:
    return [map_room_option_to_dict(o) for o in options]


def run_reset_result_to_dict(result) -> dict:
    return {
        "seed": int(result.Seed),
        "seed_text": str(result.SeedText),
        "character_id": str(result.CharacterId),
        "ascension": int(result.Ascension),
        "available_rooms": map_room_options_to_list(result.AvailableRooms),
        "metadata": to_plain(result.Metadata),
    }


def room_enter_result_to_dict(result) -> dict:
    return {
        "room_id": int(result.RoomId),
        "room_type": str(result.RoomType),
        "is_combat": bool(result.IsCombat),
        "observation": (observation_to_dict(result.Observation) if result.Observation is not None else None),
        "legal_actions": legal_actions_to_list(result.LegalActions),
        "available_rooms": map_room_options_to_list(result.AvailableRooms),
        "info": to_plain(result.Info),
    }


def run_state_summary_to_dict(summary) -> dict:
    return {
        "seed": int(summary.Seed),
        "seed_text": str(summary.SeedText),
        "character_id": str(summary.CharacterId),
        "ascension": int(summary.Ascension),
        "current_act_index": int(summary.CurrentActIndex),
        "gold": int(summary.Gold),
        "hp": int(summary.Hp),
        "max_hp": int(summary.MaxHp),
        "deck_size": int(summary.DeckSize),
        "relics": [str(r) for r in summary.Relics],
        "current_room_type": str(summary.CurrentRoomType),
        "available_rooms": map_room_options_to_list(summary.AvailableRooms),
    }


def run_summary_to_dict(summary) -> dict:
    return {
        "outcome": str(summary.Outcome),
        "floor_reached": int(summary.FloorReached),
        "score": int(summary.Score),
        "seed": int(summary.Seed),
        "seed_text": str(summary.SeedText),
        "character_id": str(summary.CharacterId),
        "ascension": int(summary.Ascension),
    }


def _serializable_rng_to_dict(rng) -> dict:
    return {
        "counter": int(rng.counter),
        "s0": int(rng.s0),
        "s1": int(rng.s1),
        "s2": int(rng.s2),
        "s3": int(rng.s3),
    }


def event_rng_snapshot_to_dict(snapshot) -> dict:
    return {
        "event_id": str(snapshot.EventId),
        "event_rng": _serializable_rng_to_dict(snapshot.EventRng),
        "player_rewards_rng": _serializable_rng_to_dict(snapshot.PlayerRewardsRng),
        "player_shops_rng": _serializable_rng_to_dict(snapshot.PlayerShopsRng),
        "player_transformations_rng": _serializable_rng_to_dict(snapshot.PlayerTransformationsRng),
    }


def apply_rng_overrides(rng, overrides: dict) -> None:
    """Mutates a live CLR `SerializableRng`'s public fields in place from a plain dict.

    There is no public constructor exposed to Python for `SerializableRng`; the only
    way to set one is to fetch a live CLR `EventRngSnapshot` via `GetEventRngState()`,
    mutate its fields in place (pythonnet exposes C# public fields as settable
    attributes), and pass the same object back to `SetEventRngState`.
    """
    for key in ("counter", "s0", "s1", "s2", "s3"):
        if key in overrides:
            setattr(rng, key, int(overrides[key]))
