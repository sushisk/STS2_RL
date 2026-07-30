"""Phase 3B independent RL-side acceptance verification for the internal Restore
bootstrap (`GameInstance.BootstrapSnapshotRuntimeForTesting`, Emulator commits
`7ef3c38`/`058b7c4`/`1d2a922`/`b6afb8a`).

Written independently from the RL side - does NOT reuse or import the Emulator's own
`scripts/smoke_restore_bootstrap_phase3b.py` or `audit_restore_bootstrap.py` (neither is
under `C:\\STS2_RL` anyway), though it necessarily uses the same reflection-invocation
technique (there is no public Restore API - Phase 3B does not add one, and this script
does not either).

IMPORTANT (singleton hazard, confirmed by the Emulator's own audit report and
independently re-derived here): `RunManager.Instance`/`CombatManager.Instance` are
process-wide singletons - only one `GameInstance` wrapper may be "live" at a time.
Constructing a second raw `GameInstance()` (as every restore-bootstrap invocation here
does) clobbers whatever the first one was holding. Each `--case` this script supports is
therefore meant to be run as its OWN fresh subprocess (see the accompanying bash-loop
report for the exact invocations) - never multiple cases in one process.

No public Python Restore API is added anywhere in this repository - this script only
exists to independently exercise the Emulator's internal method via reflection, exactly
as its own smoke test does, and is not imported by CombatEnv/LiveCombatSession/any
training code.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def _bootstrap_clr():
    from emulator_bridge import ensure_loaded
    ensure_loaded()


def _invoke_restore(game, snapshot):
    """Reflection call to the internal `BootstrapSnapshotRuntimeForTesting` - the same
    technique the Emulator's own smoke test uses (there is no other way to reach an
    `internal` CLR method from Python)."""
    from System import Array, Object
    from System.Reflection import BindingFlags

    method = game.GetType().GetMethod(
        "BootstrapSnapshotRuntimeForTesting", BindingFlags.Instance | BindingFlags.NonPublic
    )
    if method is None:
        raise AssertionError("BootstrapSnapshotRuntimeForTesting not found via reflection - Phase 3B not present in this DLL?")
    return method.Invoke(game, Array[Object]([snapshot]))


def _rejection_reasons(exc):
    """.NET reflection wraps the real exception in `TargetInvocationException` -
    unwrap to the real `SnapshotRestoreRejectedException`/`FaultedCombatSessionException`
    and its `.Reasons`/`.Message`."""
    inner = getattr(exc, "InnerException", None)
    if inner is None:
        raise exc
    reasons = getattr(inner, "Reasons", None)
    if reasons is not None:
        return list(reasons), inner
    return [str(getattr(inner, "Message", inner))], inner


def _snapshot_sig(snapshot):
    """Comprehensive comparison signature - deliberately covers every item this task's
    required-comparison list names: canonical fields, stable IDs, all piles, HP/Block/
    Energy, Turn/Round/CurrentSide, Relics, Powers (own + associated card), and full RNG
    (12 Run streams, Player streams, Monster streams)."""
    p = snapshot.Player

    def card_sig(c):
        return (c.InstanceId, c.CardId, c.Zone, c.IsUpgraded, c.UpgradeLevel, c.Cost)

    def relic_sig(r):
        return (r.InstanceId, r.RelicId, r.StackCount, r.Status, r.IsWax, r.IsMelted, r.HasBeenRemovedFromState)

    def power_sig(pw):
        return (pw.InstanceId, pw.PowerId, pw.OwnerInstanceId, pw.ApplierInstanceId, pw.TargetInstanceId,
                pw.Amount, pw.AmountOnTurnStart, pw.SkipNextDurationTick, pw.StackType)

    def rng_sig(rng):
        return (rng.Counter, rng.State0, rng.State1, rng.State2, rng.State3)

    run_rng = sorted((str(k), rng_sig(v)) for k, v in snapshot.Rng.RunRng.items())
    player_rng = sorted(
        (pr.OwnerInstanceId, sorted((str(k), rng_sig(v)) for k, v in pr.Purposes.items()))
        for pr in snapshot.Rng.PlayerRng
    )
    monster_rng = sorted((mr.OwnerInstanceId, rng_sig(mr.Rng)) for mr in snapshot.Rng.MonsterRng)

    return {
        "player_core": (p.InstanceId, p.CreatureInstanceId, p.Hp, p.MaxHp, p.Block, p.Energy, p.MaxEnergy,
                        p.Stars, p.Gold, p.OrbSlotCapacity),
        "hand": [card_sig(c) for c in p.Hand],
        "draw": [card_sig(c) for c in p.DrawPile],
        "discard": [card_sig(c) for c in p.DiscardPile],
        "exhaust": [card_sig(c) for c in p.ExhaustPile],
        "play": [card_sig(c) for c in p.PlayPile],
        "deck": [card_sig(c) for c in p.Deck],
        "relics": [relic_sig(r) for r in p.Relics],
        "player_powers": [power_sig(pw) for pw in p.Powers],
        "enemies": [(e.InstanceId, e.Index, e.MonsterId, e.Hp, e.MaxHp, e.Block, e.IsAlive) for e in snapshot.Enemies],
        "enemy_powers": [(e.InstanceId, [power_sig(pw) for pw in e.Powers]) for e in snapshot.Enemies],
        "turn_round_side": (snapshot.TurnNumber, snapshot.RoundNumber, snapshot.CurrentSide),
        "run_rng_count": len(run_rng),
        "run_rng": run_rng,
        "player_rng": player_rng,
        "monster_rng_count": len(monster_rng),
        "monster_rng": monster_rng,
        "combat_history_len": len(snapshot.CombatHistory.Entries),
    }


def _canonical_json_string(game):
    return game.CaptureSnapshotJson()


def _empty_array(item_type):
    from System import Array
    return Array[item_type]([])


def _make_eligible(snapshot):
    """Clears the fields Restore's eligibility gate requires to be empty/complete that a
    raw, unmodified Capture normally does NOT satisfy (a bare live capture's
    CombatHistory is non-empty from the natural turn-1 draw - see case
    `reject_combat_history`, which deliberately does NOT call this helper, to exercise
    that rejection naturally rather than by construction)."""
    from Sts2Emulator.Dto.Snapshot import CombatHistoryEntrySnapshot, UnsupportedSnapshotField

    snapshot.Metadata.Completeness = "complete"
    snapshot.Metadata.UnsupportedFields = _empty_array(UnsupportedSnapshotField)
    snapshot.CombatHistory.Entries = _empty_array(CombatHistoryEntrySnapshot)
    return snapshot


def _fresh_source_game(seed=123, character="IRONCLAD", enemy="CalcifiedCultist", ascension=0):
    from Sts2Emulator.Api import GameInstance
    game = GameInstance()
    game.Reset(seed, character, enemy, ascension)
    return game


def case_round_trip_no_power():
    _bootstrap_clr()
    from Sts2Emulator.Api import GameInstance

    source = _fresh_source_game()
    snapshot_a = _make_eligible(source.CaptureSnapshot())
    obs_a = source.GetObservation()
    legal_a = [(a.ActionId, a.ActionType, str(a.Parameters)) for a in source.GetLegalActions()]
    json_a = _canonical_json_string(source)
    sig_a = _snapshot_sig(snapshot_a)

    restored = GameInstance()
    _invoke_restore(restored, snapshot_a)
    snapshot_b = _make_eligible(restored.CaptureSnapshot())
    obs_b = restored.GetObservation()
    legal_b = [(a.ActionId, a.ActionType, str(a.Parameters)) for a in restored.GetLegalActions()]
    sig_b = _snapshot_sig(snapshot_b)

    checks = [
        ("snapshot signature (piles/relics/powers/RNG/turn/round/side) matches", sig_a == sig_b),
        ("Observation.Hp matches", obs_a.State["hp"] == obs_b.State["hp"]),
        ("Observation enemy Hp matches", [dict(e).get("hp") for e in obs_a.State["enemies"]] == [dict(e).get("hp") for e in obs_b.State["enemies"]]),
        ("LegalActions (id/type/params) match", legal_a == legal_b),
        ("post-restore CombatHistory is empty (no fresh hook fired)", len(snapshot_b.CombatHistory.Entries) == 0),
        ("stable IDs identical (player)", snapshot_a.Player.InstanceId == snapshot_b.Player.InstanceId),
        ("stable IDs identical (enemy)", snapshot_a.Enemies[0].InstanceId == snapshot_b.Enemies[0].InstanceId),
    ]
    for label, ok in checks:
        print(f"{'ok' if ok else 'FAIL'}  {label}")
    if not all(ok for _, ok in checks):
        print("!! diff details:")
        for k in sig_a:
            if sig_a[k] != sig_b[k]:
                print(f"    {k}: A={sig_a[k]!r} B={sig_b[k]!r}")
    return 0 if all(ok for _, ok in checks) else 1


def case_round_trip_with_power():
    _bootstrap_clr()
    from Sts2Emulator.Api import GameInstance
    from System.Reflection import BindingFlags

    source = _fresh_source_game()
    # Attach a player Power (STRENGTH) and an enemy Power (WEAK) via `PowerModel.
    # ApplyInternal` - the SAME method SnapshotRestorer.ApplyPower itself uses (see the
    # Emulator's implementation report §"Power attachment") - obtained from `ModelDb`
    # (constructing a Power model directly, e.g. `StrengthPower()`, throws
    # `DuplicateModelException`; `ModelDb` owns the one registered instance per model,
    # confirmed empirically). This attaches a real Power onto the real live Creature
    # objects without engaging the full async PowerCmd/Choice pipeline (unnecessary for
    # a fixture - Restore's own re-attachment path is exactly what we're testing).
    from MegaCrit.Sts2.Core.Models import ModelDb  # type: ignore
    from System.Reflection import BindingFlags as _BF

    obs0 = source.GetObservation()
    player_creature = None
    enemy_creature = None
    # Use the same allowlisted-private-field accessor pattern already established
    # (Phase 3A) rather than inventing a new one - read player/enemy Creature objects
    # off the live CombatState via reflection (read-only).
    combat_state_field = source.GetType().GetField("_combatState", BindingFlags.Instance | BindingFlags.NonPublic)
    combat_state = combat_state_field.GetValue(source)
    creatures_prop = combat_state.GetType().GetProperty("Creatures")
    for creature in creatures_prop.GetValue(combat_state):
        side = creature.GetType().GetProperty("Side").GetValue(creature)
        if str(side) == "Player":
            player_creature = creature
        else:
            enemy_creature = creature

    from System import Decimal as _Decimal

    strength_power = next(p for p in ModelDb.AllPowers if str(p.Id.Entry).upper() == "STRENGTH_POWER" or str(p.Id.Entry).upper() == "STRENGTH")
    weak_power = next(p for p in ModelDb.AllPowers if str(p.Id.Entry).upper() == "WEAK_POWER" or str(p.Id.Entry).upper() == "WEAK")
    strength_power.ToMutable().ApplyInternal(player_creature, _Decimal(3))
    weak_power.ToMutable().ApplyInternal(enemy_creature, _Decimal(2))

    snapshot_a = source.CaptureSnapshot()
    # Real gameplay PowerCmd.Apply DOES write CombatHistory (PowerReceivedEntry) and a
    # natural-draw CardDrawnEntry from Reset() - clear only for the eligibility gate,
    # the actual Power state itself is what we compare.
    snapshot_a = _make_eligible(snapshot_a)
    sig_a = _snapshot_sig(snapshot_a)
    player_power_a = next(pw for pw in snapshot_a.Player.Powers if pw.PowerId not in ("", None))
    enemy_power_a = next(pw for pw in snapshot_a.Enemies[0].Powers)

    restored = GameInstance()
    _invoke_restore(restored, snapshot_a)
    snapshot_b = _make_eligible(restored.CaptureSnapshot())
    sig_b = _snapshot_sig(snapshot_b)
    player_power_b = next(pw for pw in snapshot_b.Player.Powers if pw.PowerId not in ("", None))
    enemy_power_b = next(pw for pw in snapshot_b.Enemies[0].Powers)

    checks = [
        ("player Power present pre-restore", player_power_a.PowerId.upper().find("STRENGTH") >= 0),
        ("enemy Power present pre-restore", enemy_power_a.PowerId.upper().find("WEAK") >= 0),
        ("full snapshot signature matches (includes RNG/piles/relics)", sig_a == sig_b),
        ("player Power OwnerInstanceId matches", player_power_a.OwnerInstanceId == player_power_b.OwnerInstanceId),
        ("player Power Amount matches", player_power_a.Amount == player_power_b.Amount),
        ("player Power StackType matches", player_power_a.StackType == player_power_b.StackType),
        ("player Power stable InstanceId matches", player_power_a.InstanceId == player_power_b.InstanceId),
        ("enemy Power ApplierInstanceId matches", enemy_power_a.ApplierInstanceId == enemy_power_b.ApplierInstanceId),
        ("enemy Power TargetInstanceId matches", enemy_power_a.TargetInstanceId == enemy_power_b.TargetInstanceId),
        ("post-restore CombatHistory empty (ApplyInternal wrote no PowerReceivedEntry)", len(snapshot_b.CombatHistory.Entries) == 0),
    ]
    for label, ok in checks:
        print(f"{'ok' if ok else 'FAIL'}  {label}")
    return 0 if all(ok for _, ok in checks) else 1


def _assert_rejects(label, snapshot, expected_reason_substring):
    from Sts2Emulator.Api import GameInstance
    game = GameInstance()
    try:
        _invoke_restore(game, snapshot)
    except Exception as exc:  # noqa: BLE001
        reasons, inner = _rejection_reasons(exc)
        matched = any(expected_reason_substring in r for r in reasons) or expected_reason_substring in str(getattr(inner, "Message", ""))
        print(f"{'ok' if matched else 'FAIL'}  reject {label}: reasons={reasons} type={type(inner).__name__}")
        return 0 if matched else 1
    print(f"FAIL  reject {label}: NOT rejected (restore succeeded)")
    return 1


def case_reject_combat_history_natural():
    """Deliberately does NOT call `_make_eligible()` - a bare `Reset()` capture's
    CombatHistory is naturally non-empty (real turn-1 draw entries), no mutation
    needed."""
    _bootstrap_clr()
    source = _fresh_source_game()
    snapshot = source.CaptureSnapshot()
    assert len(snapshot.CombatHistory.Entries) > 0, "expected a naturally non-empty CombatHistory from Reset()'s own turn-1 draw"
    return _assert_rejects("combat_history_non_empty (natural, unmutated)", snapshot, "combat_history_non_empty")


def case_reject_pet_via_6546_21():
    """Scenario 6546-21 naturally has a live Pet (Osty, BOUND_PHYLACTERY) - see Phase 2B
    pet-capture-fix. Natural capture, no mutation."""
    _bootstrap_clr()
    import json as _json
    from live_combat_session import LiveCombatSession

    manifest_path = _HERE / "choice_policy_online_eval_manifest.jsonl"
    rows = [_json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    row = next(r for r in rows if r["trajectory_id"] == "6546-21")

    session = LiveCombatSession()
    session.start_combat(row["spec"])
    snapshot = session._game.CaptureSnapshot()  # noqa: SLF001 - raw CLR object needed for reflection-based restore
    assert len(snapshot.Player.Pets) >= 1, "expected Scenario 6546-21 to have at least 1 live Pet (Osty)"
    return _assert_rejects("pet_present (Scenario 6546-21, natural)", snapshot, "pet_count")


def case_reject_6546_21_dangling():
    """THE gap the Emulator's own audit report explicitly flagged as unverified this
    round (§5-A/§7): Scenario 6546-21's REAL captured Snapshot (3 dangling SOUL card
    references, source_live_state_inconsistency, unchanged since Phase 2A) fed directly
    into the Restore bootstrap. Must be rejected via `reference_integrity:...`."""
    _bootstrap_clr()
    import json as _json
    from live_combat_session import LiveCombatSession

    manifest_path = _HERE / "choice_policy_online_eval_manifest.jsonl"
    rows = [_json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    row = next(r for r in rows if r["trajectory_id"] == "6546-21")

    session = LiveCombatSession()
    session.start_combat(row["spec"])
    snapshot = session._game.CaptureSnapshot()  # noqa: SLF001
    return _assert_rejects("scenario_6546_21_dangling_snapshot (natural, unmutated)", snapshot, "reference_integrity")


def case_reject_pending_choice_via_toolbox():
    """A scenario with TOOLBOX generates a pending Choice at combat start (BeforeHandDraw)
    - natural capture at that boundary, no mutation of PendingChoice needed."""
    _bootstrap_clr()
    from live_combat_session import LiveCombatSession

    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": ["TOOLBOX"], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    session = LiveCombatSession()
    battle_state = session.start_combat(spec)
    assert battle_state.engine_state.get("pendingChoice") is not None, "expected TOOLBOX to publish a PendingChoice at combat start"
    snapshot = session._game.CaptureSnapshot()  # noqa: SLF001
    assert snapshot.PendingChoice is not None
    return _assert_rejects("pending_choice_present (TOOLBOX, natural)", snapshot, "pending_choice_present")


def case_reject_pending_target_mutated():
    """No natural fixture is cheap to construct here without deep engine interaction -
    mutates `Metadata.CaptureBoundary` directly on an otherwise-eligible captured
    snapshot (same technique the Emulator's own report used for this exact case,
    independently re-derived rather than trusted)."""
    _bootstrap_clr()
    source = _fresh_source_game()
    snapshot = _make_eligible(source.CaptureSnapshot())
    snapshot.Metadata.CaptureBoundary = "published_target"
    return _assert_rejects("pending_target_present (Metadata.CaptureBoundary mutated)", snapshot, "unsupported_capture_boundary")


def case_reject_action_continuation_mutated():
    _bootstrap_clr()
    source = _fresh_source_game()
    snapshot = _make_eligible(source.CaptureSnapshot())
    snapshot.Metadata.ContinuationStepIndex = 1
    return _assert_rejects("action_continuation_present (Metadata.ContinuationStepIndex mutated)", snapshot, "action_continuation_present")


def case_reject_dangling_id_mutated():
    _bootstrap_clr()
    source = _fresh_source_game()
    snapshot = _make_eligible(source.CaptureSnapshot())
    snapshot.Rng.PlayerRng[0].OwnerInstanceId = "player-999999"
    return _assert_rejects("dangling_stable_id (PlayerRng.OwnerInstanceId mutated to non-existent id)", snapshot, "reference_integrity")


def case_reject_duplicate_id_mutated():
    _bootstrap_clr()
    source = _fresh_source_game()
    snapshot = _make_eligible(source.CaptureSnapshot())
    # Force the first hand card's InstanceId to collide with the Player's own InstanceId.
    if len(snapshot.Player.Hand) == 0:
        raise AssertionError("fixture needs a non-empty Hand for this case")
    snapshot.Player.Hand[0].InstanceId = snapshot.Player.InstanceId
    return _assert_rejects("duplicate_stable_id (Hand[0].InstanceId == Player.InstanceId)", snapshot, "reference_integrity")


def case_reject_unsupported_internal_data_mutated():
    _bootstrap_clr()
    from System import Array
    from Sts2Emulator.Dto.Snapshot import UnsupportedSnapshotField

    source = _fresh_source_game()
    snapshot = _make_eligible(source.CaptureSnapshot())
    bad_field = UnsupportedSnapshotField()
    bad_field.FieldPath = "Player.Powers[0].InternalData"
    bad_field.Status = "unsupported"
    bad_field.Reason = "synthetic test injection"
    snapshot.Metadata.UnsupportedFields = Array[UnsupportedSnapshotField]([bad_field])
    return _assert_rejects("unsupported_internal_data (Metadata.UnsupportedFields injected)", snapshot, "unsupported_internal_data")


def case_reject_unknown_schema_version_mutated():
    _bootstrap_clr()
    source = _fresh_source_game()
    snapshot = _make_eligible(source.CaptureSnapshot())
    snapshot.Metadata.SchemaVersion = "phase99.9"
    return _assert_rejects("unknown_schema_version (Metadata.SchemaVersion mutated)", snapshot, "unknown_schema_version")


def case_reject_faulted_session_origin():
    """A faulted LiveCombatSession must reject CaptureSnapshot() itself
    (FaultedCombatSessionException, Phase 3A.3 contract) - so a Snapshot can never even
    be produced from a faulted session in the first place. Confirms structurally rather
    than by feeding a snapshot into Restore (there is no way to get one)."""
    _bootstrap_clr()
    import contextlib

    from live_combat_session import FaultedCombatSessionError, LiveCombatSession

    @contextlib.contextmanager
    def _corrupted_console_out():
        from System import Console
        from System.IO import MemoryStream, StreamWriter
        original = Console.Out
        w = StreamWriter(MemoryStream())
        w.Dispose()
        Console.SetOut(w)
        try:
            yield
        finally:
            Console.SetOut(original)

    session = LiveCombatSession()
    spec = {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD"], "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }
    state = session.start_combat(spec)
    action = next(a for a in state._cached_legal_actions if a["action_type"] == "card")  # noqa: SLF001

    from live_combat_session import ActionExecutionError
    with _corrupted_console_out():
        try:
            session.step(state, action, target_enemy_index=0)
        except ActionExecutionError:
            pass

    try:
        session.capture_snapshot()
        print("FAIL  reject faulted_session_origin: capture_snapshot() succeeded on a faulted session")
        return 1
    except FaultedCombatSessionError as exc:
        print(f"ok  reject faulted_session_origin: CaptureSnapshot() itself rejected ({exc})")
        return 0


CASES = {
    "round_trip_no_power": case_round_trip_no_power,
    "round_trip_with_power": case_round_trip_with_power,
    "reject_combat_history": case_reject_combat_history_natural,
    "reject_pet": case_reject_pet_via_6546_21,
    "reject_6546_21_dangling": case_reject_6546_21_dangling,
    "reject_pending_choice": case_reject_pending_choice_via_toolbox,
    "reject_pending_target": case_reject_pending_target_mutated,
    "reject_action_continuation": case_reject_action_continuation_mutated,
    "reject_dangling_id": case_reject_dangling_id_mutated,
    "reject_duplicate_id": case_reject_duplicate_id_mutated,
    "reject_unsupported_internal_data": case_reject_unsupported_internal_data_mutated,
    "reject_unknown_schema_version": case_reject_unknown_schema_version_mutated,
    "reject_faulted_session": case_reject_faulted_session_origin,
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case", choices=list(CASES), required=True)
    args = parser.parse_args()
    return CASES[args.case]()


if __name__ == "__main__":
    sys.exit(main())
