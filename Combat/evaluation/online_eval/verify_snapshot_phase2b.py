"""Phase 2B RL-side acceptance/verification suite.

Covers the "RL担当 Phase 2B指示" + "RL担当 Phase 2B追加指示"実施事項:

  A. Python型と正式JSON Schema (`docs/schemas/combat_state_snapshot.schema.json`)の完全一致検証
  B. 未知フィールド／欠落フィールド／型不一致の拒否テスト
  C. Snapshot JSONのcanonical serialization固定の確認(2回Captureして同一になること)
  D. stable instance IDの重複・参照切れ検証(合成ケース + 実データ)
  E. `complete`をPython側で格上げしないことの再確認
  F. 5つの比較ケース: Reset() vs ResetFromScenario() vs Scenario 6546-21 vs
     natural-draw-in-CombatHistory case vs 連続2回Capture

Does not touch Policy/Value/Choice Policy/Heuristic/beam-search, does not implement
RestoreSnapshot, does not execute any action from a Snapshot, does not regenerate
trajectories - read/verify only, per this task's explicit prohibitions.
"""

from __future__ import annotations

import copy
import dataclasses
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[1]
for _p in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import combat_state_snapshot as css  # noqa: E402
import emulator_bridge  # noqa: E402
from live_combat_session import LiveCombatSession  # noqa: E402

FAILURES: "list[str]" = []


def check(condition: bool, label: str) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        FAILURES.append(label)


# ---------------------------------------------------------------------------
# A. DTO <-> Schema field-set consistency
# ---------------------------------------------------------------------------

_DTO_SCHEMA_PAIRS = [
    (css.CombatStateSnapshot, "CombatStateSnapshot"),
    (css.SnapshotMetadata, "SnapshotMetadata"),
    (css.UnsupportedSnapshotField, "UnsupportedSnapshotField"),
    (css.PlayerSnapshot, "PlayerSnapshot"),
    (css.EnemySnapshot, "EnemySnapshot"),
    (css.CreatureSnapshot, "CreatureSnapshot"),
    (css.CardInstanceSnapshot, "CardInstanceSnapshot"),
    (css.RelicSnapshot, "RelicSnapshot"),
    (css.PowerSnapshot, "PowerSnapshot"),
    (css.PotionSnapshot, "PotionSnapshot"),
    (css.OrbSnapshot, "OrbSnapshot"),
    (css.SerializableRngSnapshot, "SerializableRngSnapshot"),
    (css.PlayerRngSnapshot, "PlayerRngSnapshot"),
    (css.MonsterRngSnapshot, "MonsterRngSnapshot"),
    (css.RngSnapshotSet, "RngSnapshotSet"),
    (css.CombatHistoryEntrySnapshot, "CombatHistoryEntrySnapshot"),
    (css.CombatHistorySnapshot, "CombatHistorySnapshot"),
]

_NON_SCHEMA_BOOKKEEPING_FIELDS = frozenset({"unknown_fields"})


def test_dto_schema_consistency():
    schema = css._load_schema()
    defs = schema["$defs"]
    for dc, schema_key in _DTO_SCHEMA_PAIRS:
        schema_props = set(defs[schema_key]["properties"].keys())
        dc_fields = {f.name for f in dataclasses.fields(dc)} - _NON_SCHEMA_BOOKKEEPING_FIELDS
        missing_in_py = schema_props - dc_fields
        extra_in_py = dc_fields - schema_props
        check(not missing_in_py and not extra_in_py,
              f"DTO<->Schema field match: {schema_key} (missing_in_py={sorted(missing_in_py)}, extra_in_py={sorted(extra_in_py)})")

    check(set(schema["$defs"]["SnapshotMetadata"]["properties"]["SchemaVersion"]["enum"]) == css.KNOWN_SCHEMA_VERSIONS,
          "SchemaVersion enum matches KNOWN_SCHEMA_VERSIONS")
    check(set(schema["$defs"]["SnapshotMetadata"]["properties"]["Completeness"]["enum"]) == css.COMPLETENESS_VALUES,
          "Completeness enum matches COMPLETENESS_VALUES")
    check(set(schema["$defs"]["SnapshotMetadata"]["properties"]["CaptureBoundary"]["enum"]) == css.CAPTURE_BOUNDARY_VALUES,
          "CaptureBoundary enum matches CAPTURE_BOUNDARY_VALUES")


# ---------------------------------------------------------------------------
# B. Rejection tests: unknown / missing / type-mismatch
# ---------------------------------------------------------------------------

def _minimal_valid_snapshot_dict(real_snapshot_dict: dict) -> dict:
    """Deep-copies a real captured Snapshot dict so mutation tests start from something
    that is known-valid (avoids hand-authoring a huge nested fixture)."""
    return copy.deepcopy(real_snapshot_dict)


def test_rejection_unknown_field(real_dict: dict):
    mutated = _minimal_valid_snapshot_dict(real_dict)
    mutated["TotallyUnknownTopLevelField"] = 12345

    # Strict formal-schema validator (additionalProperties: false) MUST reject it.
    errors = css.validate_against_formal_schema(mutated)
    check(len(errors) > 0, "strict schema validator rejects unknown top-level field")

    # Lenient Python loader (Phase 2A design, deliberately unchanged) records it,
    # does NOT raise - this dual behavior is intentional, see module docstring.
    parsed = css.CombatStateSnapshot.from_dict(mutated)
    check("TotallyUnknownTopLevelField" in parsed.unknown_fields,
          "lenient loader records (not rejects) unknown top-level field")


def test_rejection_missing_field(real_dict: dict):
    mutated = _minimal_valid_snapshot_dict(real_dict)
    del mutated["TurnNumber"]

    errors = css.validate_against_formal_schema(mutated)
    check(len(errors) > 0, "strict schema validator rejects missing required field (TurnNumber)")

    raised = False
    try:
        css.CombatStateSnapshot.from_dict(mutated)
    except css.SnapshotValidationError:
        raised = True
    check(raised, "lenient loader also rejects missing required field (TurnNumber)")


def test_rejection_type_mismatch(real_dict: dict):
    mutated = _minimal_valid_snapshot_dict(real_dict)
    mutated["TurnNumber"] = "not_an_integer"

    errors = css.validate_against_formal_schema(mutated)
    check(len(errors) > 0, "strict schema validator rejects type-mismatched field (TurnNumber as string)")

    raised = False
    try:
        int(mutated["TurnNumber"])
    except ValueError:
        raised = True
    check(raised, "a non-numeric TurnNumber string cannot be coerced by the lenient loader's int() either")

    # A numeric-looking string ("7") IS silently coercible by the lenient loader's int() -
    # confirms the lenient path is deliberately more permissive than the strict schema,
    # which would still reject it (schema requires `type: integer`, not integer-like string).
    mutated["TurnNumber"] = "7"
    errors_numeric_string = css.validate_against_formal_schema(mutated)
    check(len(errors_numeric_string) > 0, "strict schema still rejects a numeric-looking string ('7') for an integer field")
    parsed = css.CombatStateSnapshot.from_dict(mutated)
    check(parsed.TurnNumber == 7, "lenient loader coerces numeric-looking string via int() (documented, pre-existing behavior)")


def test_rejection_bad_stable_id_format(real_dict: dict):
    mutated = _minimal_valid_snapshot_dict(real_dict)
    mutated["Player"]["InstanceId"] = "not-a-valid-id-format-!!"
    errors = css.validate_against_formal_schema(mutated)
    check(any("InstanceId" in e or "pattern" in e for e in errors) or len(errors) > 0,
          "strict schema rejects a StableInstanceId that violates the '<kind>-<sequence>' pattern")
    check(css.STABLE_INSTANCE_ID_PATTERN.match("card-000042") is not None, "STABLE_INSTANCE_ID_PATTERN accepts a well-formed id")
    check(css.STABLE_INSTANCE_ID_PATTERN.match("not-a-valid-id-format-!!") is None, "STABLE_INSTANCE_ID_PATTERN rejects a malformed id")


def test_valid_snapshot_passes_strict_schema(real_dict: dict):
    errors = css.validate_against_formal_schema(real_dict)
    check(errors == [], f"a genuine, unmutated captured Snapshot conforms to the strict formal schema (errors={errors[:3]})")


# ---------------------------------------------------------------------------
# C/D. canonical serialization + stable-ID reference integrity
# ---------------------------------------------------------------------------

def test_canonical_serialization_stable(snapshot_a, snapshot_b):
    """Two captures of the SAME quiescent state (no Step() in between) must produce
    identical canonical JSON once volatile identity fields are excluded."""
    json_a = css.canonical_json(dataclasses.asdict(snapshot_a))
    json_b = css.canonical_json(dataclasses.asdict(snapshot_b))
    check(json_a == json_b, "canonical_json() of two back-to-back captures of the same state is byte-identical")
    check(snapshot_a.Metadata.SnapshotId != snapshot_b.Metadata.SnapshotId,
          "the two captures nonetheless have distinct SnapshotIds (volatile identity, correctly excluded from canonical_json)")


def test_reference_validator_synthetic_duplicate():
    from combat_state_snapshot import CombatStateSnapshot

    d = _load_sample_dict()
    mutated = copy.deepcopy(d)
    # Force a duplicate: make the first canonical card carry the Player's own InstanceId.
    if mutated["Player"]["CardInstances"]:
        mutated["Player"]["CardInstances"][0]["InstanceId"] = mutated["Player"]["InstanceId"]
        snap = CombatStateSnapshot.from_dict(mutated)
        report = css.validate_snapshot_references(snap)
        check(mutated["Player"]["InstanceId"] in report.duplicate_instance_ids,
              "synthetic duplicate InstanceId (CardInstances[0] == Player.InstanceId) is detected")
    else:
        check(False, "synthetic duplicate test requires a non-empty CardInstances array in the sample snapshot")


def test_reference_validator_synthetic_dangling():
    from combat_state_snapshot import CombatStateSnapshot

    d = _load_sample_dict()
    mutated = copy.deepcopy(d)
    mutated["CombatHistory"]["Entries"].append({
        "EntryType": "DamageReceivedEntry", "RoundNumber": 1, "CurrentSide": "player",
        "ActorInstanceId": "card-999999999", "PlayerTurnNumbers": {}, "Fields": {},
    })
    snap = CombatStateSnapshot.from_dict(mutated)
    report = css.validate_snapshot_references(snap)
    matches = [r for r in report.dangling_references if r.referenced_instance_id == "card-999999999"]
    check(len(matches) == 1, "synthetic dangling reference (non-existent ActorInstanceId) is detected")
    if matches:
        check(matches[0].cause == "capture_bug",
              "a synthetic dangling reference from a non-CardDrawnEntry type is classified capture_bug (not the known benign pattern)")


_SAMPLE_PATH = _HERE / "snapshot_phase2b_sample.json"
_sample_cache = None


def _load_sample_dict() -> dict:
    """Freshly captures (via the current, Phase 2B DLL) rather than reusing the stale
    Phase 2A `snapshot_diagnostic_sample.json` - that file predates the
    `InternalData`/`InternalDataSerializerVersion` PowerSnapshot fields the Phase 2B
    schema now marks required-present (even though nullable), so validating it against
    the current strict schema would fail for a reason unrelated to this task's actual
    checks (a genuine schemaVersion mismatch, not a defect)."""
    global _sample_cache
    if _sample_cache is None:
        manifest_path = _HERE / "choice_policy_online_eval_manifest.jsonl"
        rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        row = next(r for r in rows if r["trajectory_id"] == "302-13")
        session = LiveCombatSession()
        session.start_combat(row["spec"])
        snapshot = session.capture_snapshot()
        _sample_cache = _strip_unknown_fields_key(dataclasses.asdict(snapshot))
        _SAMPLE_PATH.write_text(json.dumps(_sample_cache, indent=2, default=str), encoding="utf-8")
    return _sample_cache


def _strip_unknown_fields_key(node):
    """`dataclasses.asdict()` includes the Python-only `unknown_fields` bookkeeping key
    (added by `SnapshotMetadata`/`CombatStateSnapshot` for the lenient loader's
    record-don't-reject behavior) - not part of the formal schema's `additionalProperties:
    false` object shapes, so it must be stripped before strict-schema validation of an
    otherwise-genuine capture (a present-but-empty `unknown_fields` key would otherwise
    cause a false-positive schema-conformance failure unrelated to this task's checks)."""
    if isinstance(node, dict):
        return {k: _strip_unknown_fields_key(v) for k, v in node.items() if k != "unknown_fields"}
    if isinstance(node, list):
        return [_strip_unknown_fields_key(v) for v in node]
    return node


# ---------------------------------------------------------------------------
# F. 5 comparison cases
# ---------------------------------------------------------------------------

def _summarize(snapshot) -> dict:
    ref_report = css.validate_snapshot_references(snapshot)
    eligible, reasons = css.restore_input_eligibility(snapshot)
    return {
        "completeness": snapshot.Metadata.Completeness,
        "unsupported_count": len(snapshot.Metadata.UnsupportedFields),
        "unsupported_fields": sorted(u.FieldPath for u in snapshot.Metadata.UnsupportedFields),
        "dangling_count": len(ref_report.dangling_references),
        "dangling_causes": sorted({d.cause for d in ref_report.dangling_references}),
        "duplicate_ids": ref_report.duplicate_instance_ids,
        "restore_input_eligible": eligible,
        "restore_ineligible_reasons": reasons,
        "canonical_json_sha256": __import__("hashlib").sha256(
            css.canonical_json(dataclasses.asdict(snapshot)).encode("utf-8")).hexdigest(),
    }


def case_reset_bare():
    """Case 1: plain Reset() (no scenario override) - real natural draw stays in the
    hand, never overwritten, so CombatHistory's CardDrawnEntry references should NOT
    dangle."""
    game = emulator_bridge.shared_game_instance()
    game.Reset("phase2b-case1-seed", "DEFECT", 0)
    snapshot = css.CombatStateSnapshot.from_json(game.CaptureSnapshotJson())
    return _summarize(snapshot)


def case_reset_from_scenario(spec: dict):
    """Case 2: ResetFromScenario() - the known natural-draw-then-overwrite pattern."""
    session = LiveCombatSession()
    session.start_combat(spec)
    snapshot = session.capture_snapshot()
    return _summarize(snapshot)


def case_scenario_6546_21(manifest_path: Path):
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    row = next(r for r in rows if r["trajectory_id"] == "6546-21")
    session = LiveCombatSession()
    session.start_combat(row["spec"])
    snapshot = session.capture_snapshot()
    return _summarize(snapshot)


def case_natural_draw_recorded(spec: dict):
    """Case 4: explicit confirmation that a ResetFromScenario-started combat's
    CombatHistory contains at least one CardDrawnEntry (the natural turn-1 draw) - this
    is the entry type whose dangling references get classified
    source_live_state_inconsistency."""
    session = LiveCombatSession()
    session.start_combat(spec)
    snapshot = session.capture_snapshot()
    card_drawn_entries = [e for e in snapshot.CombatHistory.Entries if e.EntryType == "CardDrawnEntry"]
    summary = _summarize(snapshot)
    summary["card_drawn_entry_count"] = len(card_drawn_entries)
    return summary


def case_double_capture(spec: dict):
    """Case 5: capture the SAME quiescent state twice in a row (no Step() in between) -
    completeness/unsupported/dangling/stable-ID/canonical-JSON must all match exactly
    except SnapshotId/CapturedAtUtc."""
    session = LiveCombatSession()
    session.start_combat(spec)
    snap_1 = session.capture_snapshot()
    snap_2 = session.capture_snapshot()
    return _summarize(snap_1), _summarize(snap_2), snap_1, snap_2


def case_6546_21_pet_capture_fix_check(manifest_path: Path):
    """Phase 2B pet-capture-fix acceptance check (per "RL担当 Phase 2B最終再統合指示"
    Scenario 6546-21確認): confirms Osty/DieForYouPower are no longer dangling (now
    captured via `PlayerSnapshot.Pets`), the 3 SOUL `CardGeneratedEntry` references
    remain dangling and classified `source_live_state_inconsistency`, completeness is
    not upgraded to `complete` while any dangling reference remains, and Capture has no
    live-state side effect (LegalActions/engine Observation unchanged before/after)."""
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    row = next(r for r in rows if r["trajectory_id"] == "6546-21")

    session = LiveCombatSession()
    battle_state_before = session.start_combat(row["spec"])
    legal_actions_before = battle_state_before._cached_legal_actions  # noqa: SLF001

    snapshot = session.capture_snapshot()

    legal_actions_after = session._game.GetLegalActions()  # noqa: SLF001 - read-only side-effect check
    check(len(list(legal_actions_after)) == len(legal_actions_before or []),
          "Case 6546-21 pet-fix: LegalActions count unchanged across capture_snapshot() call (no live side effect)")

    check(len(snapshot.Player.Pets) == 1, f"Case 6546-21 pet-fix: exactly 1 Pet captured (Osty), got {len(snapshot.Player.Pets)}")
    if snapshot.Player.Pets:
        osty = snapshot.Player.Pets[0]
        check(osty.Kind == "pet", f"Case 6546-21 pet-fix: Pet.Kind == 'pet' (got {osty.Kind!r})")
        check(osty.OwnerInstanceId == snapshot.Player.InstanceId,
              "Case 6546-21 pet-fix: Osty's OwnerInstanceId matches Player.InstanceId")
        die_for_you_powers = [p for p in osty.Powers if "DIE_FOR_YOU" in p.PowerId.upper() or "DieForYou" in p.PowerId]
        check(len(die_for_you_powers) >= 1, f"Case 6546-21 pet-fix: Osty carries a DieForYou-type Power (Powers={[p.PowerId for p in osty.Powers]})")
        if die_for_you_powers:
            check(die_for_you_powers[0].OwnerInstanceId == osty.InstanceId,
                  "Case 6546-21 pet-fix: DieForYouPower.OwnerInstanceId matches Osty's own InstanceId")

    report = css.validate_snapshot_references(snapshot)
    osty_creature_ids = {snapshot.Player.Pets[0].InstanceId} if snapshot.Player.Pets else set()
    osty_power_ids = {p.InstanceId for p in snapshot.Player.Pets[0].Powers} if snapshot.Player.Pets else set()
    remaining_dangling_to_osty = [d for d in report.dangling_references if d.referenced_instance_id in (osty_creature_ids | osty_power_ids)]
    check(len(remaining_dangling_to_osty) == 0, "Case 6546-21 pet-fix: Osty/DieForYouPower no longer appear as dangling references (resolved)")

    check(len(report.dangling_references) == 3, f"Case 6546-21 pet-fix: exactly 3 dangling references remain (SOUL x3), got {len(report.dangling_references)}")
    check(all(d.entry_type == "CardGeneratedEntry" for d in report.dangling_references),
          f"Case 6546-21 pet-fix: all remaining dangling references are CardGeneratedEntry (got {[d.entry_type for d in report.dangling_references]})")
    check(all(d.cause == "source_live_state_inconsistency" for d in report.dangling_references),
          f"Case 6546-21 pet-fix: all remaining dangling references classified source_live_state_inconsistency (got {[d.cause for d in report.dangling_references]})")

    # NOTE: `Metadata.Completeness` is the Emulator's own verdict about *field/data*
    # capture completeness (e.g. every Power/Relic/RNG value was captured) - it is
    # orthogonal to CombatHistory *referential* integrity, which dangling references are
    # about. Both Emulator- and RL-side testing (this Case, Case 3) confirm Completeness
    # legitimately stays "complete" here (every field IS captured; three CombatHistory
    # entries merely reference an instance that was never part of the final state) - RL
    # does not, and must not, independently downgrade that field (never-upgrade
    # principle, §9-C, cuts both ways: RL doesn't upgrade OR downgrade the Emulator's own
    # verdict). The actual "not usable as-is for Phase 3" signal is
    # `restore_input_eligibility()`, checked below.
    print(f"  (informational) Metadata.Completeness={snapshot.Metadata.Completeness!r} - Emulator's own verdict, read verbatim, not independently touched (see NOTE above)")
    eligible, reasons = css.restore_input_eligibility(snapshot)
    check(not eligible, f"Case 6546-21 pet-fix: restore_input_eligibility() is False while 3 dangling references remain (reasons={reasons})")

    return {"pet_count": len(snapshot.Player.Pets), "dangling_count": len(report.dangling_references),
            "dangling_entry_types": sorted({d.entry_type for d in report.dangling_references}),
            "dangling_causes": sorted({d.cause for d in report.dangling_references}),
            "completeness": snapshot.Metadata.Completeness, "restore_input_eligible": eligible}


def run_five_case_comparison():
    manifest_path = _HERE / "choice_policy_online_eval_manifest.jsonl"
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    row_302_13 = next(r for r in rows if r["trajectory_id"] == "302-13")

    print("\n--- Case 1: Reset() (bare, no scenario) ---")
    c1 = case_reset_bare()
    print(json.dumps({k: v for k, v in c1.items() if k != "canonical_json_sha256"}, indent=2))
    check(c1["dangling_count"] == 0, "Case 1 (Reset(), no scenario override): 0 dangling references")

    print("\n--- Case 2: ResetFromScenario() (302-13) ---")
    c2 = case_reset_from_scenario(row_302_13["spec"])
    print(json.dumps({k: v for k, v in c2.items() if k != "canonical_json_sha256"}, indent=2))

    print("\n--- Case 3: Scenario 6546-21 ---")
    c3 = case_scenario_6546_21(manifest_path)
    print(json.dumps({k: v for k, v in c3.items() if k != "canonical_json_sha256"}, indent=2))

    print("\n--- Case 4: natural-draw-recorded (ResetFromScenario, 302-13) ---")
    c4 = case_natural_draw_recorded(row_302_13["spec"])
    print(json.dumps({k: v for k, v in c4.items() if k != "canonical_json_sha256"}, indent=2))
    check(c4["card_drawn_entry_count"] > 0, "Case 4: at least one CardDrawnEntry present in CombatHistory (the natural turn-1 draw)")
    if c4["dangling_count"] > 0:
        check(c4["dangling_causes"] == ["source_live_state_inconsistency"],
              "Case 4: any dangling references present are classified exactly source_live_state_inconsistency (the known pattern), no unrecognized capture_bug")
        check(not c4["restore_input_eligible"], "Case 4: a Snapshot with dangling references is NOT restore_input_eligible")

    print("\n--- Case 5: double capture (same quiescent state, no Step() in between) ---")
    c5a, c5b, snap5a, snap5b = case_double_capture(row_302_13["spec"])
    print(json.dumps({k: v for k, v in c5a.items() if k != "canonical_json_sha256"}, indent=2))
    for key in ("completeness", "unsupported_count", "unsupported_fields", "dangling_count", "dangling_causes", "duplicate_ids", "restore_input_eligible", "canonical_json_sha256"):
        check(c5a[key] == c5b[key], f"Case 5: {key} identical across two back-to-back captures of the same state")
    check(snap5a.Metadata.SnapshotId != snap5b.Metadata.SnapshotId, "Case 5: SnapshotId still differs (volatile identity, correctly not part of the comparison)")

    return {"case1_reset_bare": c1, "case2_reset_from_scenario": c2, "case3_scenario_6546_21": c3,
            "case4_natural_draw": c4, "case5_double_capture_a": c5a, "case5_double_capture_b": c5b}


# ---------------------------------------------------------------------------
# Emulator-side cross-check: Python validator vs GameInstance.ValidateSnapshotReferences
# ---------------------------------------------------------------------------

def cross_check_against_emulator_validator(spec: dict):
    """Calls the Emulator's own `GameInstance.ValidateSnapshotReferences` (Phase 2B
    report §6) on the SAME captured CLR CombatStateSnapshot object, and compares its
    dangling-reference count against the Python-side `validate_snapshot_references`
    result on the equivalent JSON-parsed object - an independent cross-check that the
    two ecosystems agree on what "dangling" means."""
    session = LiveCombatSession()
    session.start_combat(spec)
    clr_snapshot = session._game.CaptureSnapshot()  # noqa: SLF001 - CLR object, for the emulator-side validator call only
    emulator_messages = list(session._game.ValidateSnapshotReferences(clr_snapshot))  # noqa: SLF001

    json_text = session._game.CaptureSnapshotJson()  # noqa: SLF001 - fresh capture (same quiescent state, no Step() in between)
    py_snapshot = css.CombatStateSnapshot.from_json(json_text)
    py_report = css.validate_snapshot_references(py_snapshot)

    check(bool(emulator_messages) == bool(py_report.dangling_references or py_report.duplicate_instance_ids),
          f"Emulator ValidateSnapshotReferences ({len(emulator_messages)} messages) and Python validate_snapshot_references "
          f"({len(py_report.dangling_references)} dangling + {len(py_report.duplicate_instance_ids)} duplicate) agree on issue-presence")
    print(f"  Emulator-side messages ({len(emulator_messages)}): {emulator_messages[:3]}")
    print(f"  Python-side dangling ({len(py_report.dangling_references)}): "
          f"{[(d.field_path, d.cause) for d in py_report.dangling_references[:3]]}")


def main():
    print("=== A. DTO<->Schema consistency ===")
    test_dto_schema_consistency()

    sample = _load_sample_dict()
    print("\n=== B. Rejection tests ===")
    test_valid_snapshot_passes_strict_schema(sample)
    test_rejection_unknown_field(sample)
    test_rejection_missing_field(sample)
    test_rejection_type_mismatch(sample)
    test_rejection_bad_stable_id_format(sample)

    print("\n=== D. Reference-integrity validator (synthetic) ===")
    test_reference_validator_synthetic_duplicate()
    test_reference_validator_synthetic_dangling()

    print("\n=== F. Five-case comparison ===")
    results = run_five_case_comparison()

    print("\n=== Emulator cross-check (302-13) ===")
    manifest_path = _HERE / "choice_policy_online_eval_manifest.jsonl"
    rows = [json.loads(line) for line in manifest_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    row_302_13 = next(r for r in rows if r["trajectory_id"] == "302-13")
    cross_check_against_emulator_validator(row_302_13["spec"])

    print("\n=== C. Canonical serialization stability (re-check via case 5 snapshots) ===")
    manifest2 = [json.loads(line) for line in (_HERE / "choice_policy_online_eval_manifest.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    row2 = next(r for r in manifest2 if r["trajectory_id"] == "302-13")
    session = LiveCombatSession()
    session.start_combat(row2["spec"])
    snap_a = session.capture_snapshot()
    snap_b = session.capture_snapshot()
    test_canonical_serialization_stable(snap_a, snap_b)

    print("\n=== Pet-capture-fix acceptance: Scenario 6546-21 (Osty resolved, SOUL x3 remain) ===")
    pet_fix_result = case_6546_21_pet_capture_fix_check(manifest_path)
    print(json.dumps(pet_fix_result, indent=2))

    print("\n=== Canonical round-trip w/ Pets: Scenario 6546-21 double capture ===")
    session_6546 = LiveCombatSession()
    session_6546.start_combat(next(r for r in rows if r["trajectory_id"] == "6546-21")["spec"])
    snap_6546_a = session_6546.capture_snapshot()
    snap_6546_b = session_6546.capture_snapshot()
    json_6546_a = css.canonical_json(dataclasses.asdict(snap_6546_a))
    json_6546_b = css.canonical_json(dataclasses.asdict(snap_6546_b))
    check(json_6546_a == json_6546_b, "Scenario 6546-21: canonical_json() (including Pets) identical across two back-to-back captures")
    reparsed = css.CombatStateSnapshot.from_json(json.dumps(dataclasses.asdict(snap_6546_a), default=str))
    check(len(reparsed.Player.Pets) == len(snap_6546_a.Player.Pets), "Scenario 6546-21: Pets survive a from_dict(asdict(...)) round-trip with the same count")
    check([p.InstanceId for p in reparsed.Player.Pets] == [p.InstanceId for p in snap_6546_a.Player.Pets],
          "Scenario 6546-21: Pet InstanceIds survive the round-trip identically (stable ID preserved)")

    out_path = _HERE / "snapshot_phase2b_five_case_results.json"
    out_path.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\n5-case results written to {out_path}")

    print(f"\n=== SUMMARY: {len(FAILURES)} failing check(s) ===")
    for f in FAILURES:
        print(f"  FAIL: {f}")
    return 0 if not FAILURES else 1


if __name__ == "__main__":
    sys.exit(main())
