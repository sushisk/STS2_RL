"""Snapshot wire-contract tests.

Raw dict -> DTO parser tests intentionally live in their dedicated DTO test modules.
This module owns the opposite seam: typed DTO -> Emulator JSON plus tests that
intentionally damage or enrich the wire payload itself. Restore-behavior tests should
not know these field-level wire details.
"""

from __future__ import annotations

import copy
import hashlib
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_COMBAT_DIR = _HERE.parents[0]
_OFFICIAL_EXAMPLE_PATH = Path(r"C:\STS2_Emulator\docs\contracts\combat_state_snapshot_example.v0.8.json")
for _path in (_COMBAT_DIR, _COMBAT_DIR / "data", _COMBAT_DIR / "env"):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from combat_state_snapshot import CardInstanceSnapshot, EnemySnapshot, schema_sha256  # noqa: E402
from live_combat_session import LiveCombatSession, SnapshotRestoreRejectedError  # noqa: E402
from snapshot_testkit import (  # noqa: E402
    active_enemies,
    creature_by_id,
    make_creature,
    make_power,
    make_snapshot,
    player_creature,
    to_emulator_json,
    to_emulator_payload,
)
from snapshot_wire_fixtures import (  # noqa: E402
    COMBAT_HISTORY_ENTRY_TYPES,
    add_history_support_objects,
    add_osty_pet,
    all_power_payloads,
    combat_history_entries,
    history_fixture_payload,
    history_signature,
    internal_data_for,
    power_fixture,
)

_SERIALIZE_REQUIRED_POWER_CLASSES = {
    "AdaptablePower",
    "AutomationPower",
    "ChainsOfBindingPower",
    "DampenPower",
    "DarkEmbracePower",
    "FeralPower",
    "HardenedShellPower",
    "HellraiserPower",
    "IllusionPower",
    "InterceptPower",
    "JugglingPower",
    "MockRevivePower",
    "NightmarePower",
    "OrbitPower",
    "PaleBlueDotPower",
    "PanachePower",
    "PossessSpeedPower",
    "PossessStrengthPower",
    "ReattachPower",
    "VoidFormPower",
}

_SAFE_TO_RECOMPUTE_POWER_CLASSES = {
    "AfterimagePower",
    "CalamityPower",
    "CurlUpPower",
    "GigantificationPower",
    "GravityPower",
    "ImitationLearningPower",
    "MonologuePower",
    "OblivionPower",
    "RupturePower",
    "SerpentFormPower",
    "SkittishPower",
    "StormPower",
    "StranglePower",
    "SubroutinePower",
    "VigorPower",
}

_UNSUPPORTED_UNKNOWN_POWER_CLASSES: set[str] = set()


def _simple_spec(*, relics: list[str] | None = None) -> dict:
    return {
        "character_id": "IRONCLAD",
        "player_hp": None,
        "player_max_hp": None,
        "hand": ["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": list(relics or []),
        "potions": [],
        "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 48}],
    }


def _restorable_capture(session: LiveCombatSession):
    snapshot = session.capture_snapshot()
    snapshot.Metadata.Completeness = "complete"
    snapshot.Metadata.UnsupportedFields = []
    snapshot.CombatHistory.Entries = []
    return snapshot


def _current_wire_payload() -> dict:
    session = LiveCombatSession()
    session.start_combat(_simple_spec())
    return to_emulator_payload(_restorable_capture(session))


def _json_text(payload: dict) -> str:
    return json.dumps(payload, sort_keys=False, separators=(",", ":"), ensure_ascii=True)


def _restore_capture_payload(payload: dict) -> dict:
    session = LiveCombatSession()
    json_text = _json_text(payload)
    validation = session.validate_restore_snapshot_json(json_text)
    assert validation.eligible is True, validation.rejection_codes
    session.restore_snapshot_json(json_text)
    return to_emulator_payload(session.capture_snapshot())


def _strike_action(state) -> dict:
    return next(
        action
        for action in state._cached_legal_actions  # noqa: SLF001 - test-only action lookup
        if action["action_type"] == "card"
        and (action.get("parameters") or {}).get("cardId") == "STRIKE_IRONCLAD"
    )


def _first_logical_action(state) -> tuple[str, str | None, str | None]:
    action = next(
        action
        for action in state._cached_legal_actions  # noqa: SLF001 - test-only action lookup
        if action["action_type"] == "card"
    )
    params = action.get("parameters") or {}
    return action["action_type"], params.get("cardId"), params.get("targetType")


def _find_logical_action(state, logical: tuple[str, str | None, str | None]) -> dict:
    action_type, card_id, target_type = logical
    return next(
        action
        for action in state._cached_legal_actions  # noqa: SLF001 - test-only action lookup
        if action["action_type"] == action_type
        and (action.get("parameters") or {}).get("cardId") == card_id
        and (action.get("parameters") or {}).get("targetType") == target_type
    )


def _determinism_payload(snapshot) -> dict:
    payload = to_emulator_payload(snapshot)
    payload.pop("Metadata", None)
    for enemy in payload.get("Enemies", []):
        if "Intent" in enemy:
            enemy["Intent"] = "<excluded: derived enemy intent>"
    return payload


def _card() -> CardInstanceSnapshot:
    return CardInstanceSnapshot(
        InstanceId="card-900000",
        CardId="STRIKE_IRONCLAD",
        Type="Attack",
        Rarity="Basic",
        Cost=1,
        TargetType="AnyEnemy",
        IsUpgraded=False,
        UpgradeLevel=0,
        LocalKeywords=None,
        LocalCostModifiers=[],
        TemporaryStarCosts=[],
        Zone="Hand",
        PileIndex=0,
        HasBeenRemovedFromState=False,
        Enchantment=None,
        EnchantmentStatus=None,
        TinkerTimeType=None,
        TinkerTimeRider=None,
    )


def _enemy(instance_id: str, *, alive: bool) -> EnemySnapshot:
    return EnemySnapshot(
        InstanceId=instance_id,
        Index=0,
        Name="Test Enemy",
        Hp=10 if alive else 0,
        MaxHp=10,
        Block=0,
        IsAlive=alive,
        Powers=[],
        StateLog=[],
        CombatId=1,
        MonsterId="CALCIFIED_CULTIST",
        SlotName=None,
        Intent=None,
    )


def test_to_emulator_payload_uses_production_dto_field_names():
    snapshot = make_snapshot()
    snapshot.Player.CardInstances = [_card()]
    snapshot.Metadata.unknown_fields = {"FutureMetadataField": 123}
    snapshot.unknown_fields = {"FutureRootField": 456}

    payload = to_emulator_payload(snapshot)

    assert "Metadata" in payload
    assert "Player" in payload
    assert "unknown_fields" not in payload
    assert "unknown_fields" not in payload["Metadata"]
    card = payload["Player"]["CardInstances"][0]
    assert card["InstanceId"] == "card-900000"
    assert card["CardId"] == "STRIKE_IRONCLAD"
    # These are production DTO defaults, not a second test-side schema table.
    assert card["DynamicVars"] == {}
    assert card["BaseReplayCount"] == 0
    assert card["SavedProperties"] is None


def test_to_emulator_json_matches_shared_payload():
    snapshot = make_snapshot()
    assert json.loads(to_emulator_json(snapshot)) == to_emulator_payload(snapshot)


def test_testkit_builders_return_typed_dtos():
    power = make_power(
        instance_id="power-900001",
        owner_instance_id="creature-900001",
        amount=3,
    )
    pet = make_creature(
        instance_id="creature-900001",
        owner_instance_id="player-900000",
        powers=[power],
        monster_id="OSTY",
    )
    snapshot = make_snapshot()
    snapshot.Player.Pets = [pet]

    assert snapshot.Player.Pets[0] is pet
    assert pet.Powers[0] is power
    assert power.Amount == 3


def test_creature_accessors_hide_player_enemy_pet_storage_shape():
    alive = _enemy("creature-900010", alive=True)
    dead = _enemy("creature-900011", alive=False)
    pet = make_creature(
        instance_id="creature-900012",
        owner_instance_id="player-900000",
        monster_id="OSTY",
    )
    snapshot = make_snapshot(enemies=[alive, dead])
    snapshot.Player.Pets = [pet]

    assert active_enemies(snapshot) == [alive]
    assert player_creature(snapshot) is snapshot.Player
    assert creature_by_id(snapshot, snapshot.Player.CreatureInstanceId) is snapshot.Player
    assert creature_by_id(snapshot, alive.InstanceId) is alive
    assert creature_by_id(snapshot, pet.InstanceId) is pet


def test_creature_by_id_rejects_unknown_id():
    snapshot = make_snapshot()
    try:
        creature_by_id(snapshot, "creature-999999")
        raise AssertionError("expected KeyError")
    except KeyError as exc:
        assert exc.args == ("creature-999999",)


def test_restore_capabilities_match_snapshot_wire_contract():
    session = LiveCombatSession()
    caps = session.get_restore_capabilities()

    assert caps.restore_api_version == "phase3c.8", caps
    assert caps.milestone == "phase3c.8", caps
    assert caps.contract_version == "0.6", caps
    assert caps.snapshot_schema_version == "phase3c.8", caps
    assert caps.snapshot_schema_sha256 == schema_sha256(), caps

    contract_path = Path(__file__).resolve().parents[2] / "Common" / "contracts" / "combat_state_contract.v0.5.md"
    contract_bytes = contract_path.read_bytes().replace(b"\r\n", b"\n")
    assert caps.contract_sha256 == hashlib.sha256(contract_bytes).hexdigest(), caps

    assert caps.supported_completeness == ["complete"], caps
    assert caps.supports_combat_history is True
    assert caps.supports_pets is True
    assert caps.supports_pending_choice is False
    assert caps.supports_pending_target is False
    assert caps.supports_action_continuation is False
    for class_name in _SERIALIZE_REQUIRED_POWER_CLASSES:
        assert f"serialize_required:{class_name}" in caps.supported_power_scope, class_name
    for class_name in _SAFE_TO_RECOMPUTE_POWER_CLASSES:
        assert f"safe_to_recompute:{class_name}" in caps.supported_power_scope, class_name
    assert set(caps.unsupported_power_internal_data_classes) == _UNSUPPORTED_UNKNOWN_POWER_CLASSES, caps
    assert "missing_power_internal_data" in caps.rejection_codes
    assert "invalid_power_internal_data_field" in caps.rejection_codes
    assert "invalid_power_internal_data_serializer" in caps.rejection_codes
    assert "invalid_json_required_field" in caps.rejection_codes
    assert "combat_history_non_empty" in caps.rejection_codes
    assert caps.transaction_model == "validate_before_destroy"
    assert caps.rollback_after_teardown is False
    assert caps.issues_new_combat_session is True
    assert caps.preserves_stable_ids is True


def test_legacy_json_example_is_rejected_by_current_wire_contract():
    session = LiveCombatSession()
    result = session.validate_restore_snapshot_json(_OFFICIAL_EXAMPLE_PATH.read_text(encoding="utf-8"))
    assert result.eligible is False, result
    assert "unknown_schema_version:phase3c.4" in result.rejection_codes


def test_validate_restore_snapshot_json_is_pure_for_valid_and_malformed_wire():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001
    legal_before = session.get_legal_actions()

    valid_json = to_emulator_json(_restorable_capture(session))
    invalid = json.loads(valid_json)
    del invalid["Player"]["Pets"]
    invalid_json = _json_text(invalid)

    results = [session.validate_restore_snapshot_json(valid_json) for _ in range(3)]
    assert all(result.eligible for result in results), results
    assert results[0] == results[1] == results[2]

    invalid_results = [session.validate_restore_snapshot_json(invalid_json) for _ in range(3)]
    assert all(not result.eligible for result in invalid_results), invalid_results
    assert invalid_results[0] == invalid_results[1] == invalid_results[2]
    assert any(
        "invalid_json_required_field:$.Player.Pets" in code
        for code in invalid_results[0].rejection_codes
    )

    assert session._session_faulted is False  # noqa: SLF001
    assert session._current_frame == frame_before  # noqa: SLF001
    assert session.get_legal_actions() == legal_before
    assert session.step(state, _strike_action(state), target_enemy_index=0) is not None


def test_invalid_json_restore_preserves_live_session_and_step_still_works():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001
    legal_before = session.get_legal_actions()

    invalid = to_emulator_payload(_restorable_capture(session))
    del invalid["CombatHistory"]
    invalid_json = _json_text(invalid)

    validation = session.validate_restore_snapshot_json(invalid_json)
    assert validation.eligible is False, validation
    assert any(
        "invalid_json_required_field:$.CombatHistory" in code
        for code in validation.rejection_codes
    )
    try:
        session.restore_snapshot_json(invalid_json)
        raise AssertionError("expected SnapshotRestoreRejectedError")
    except SnapshotRestoreRejectedError as exc:
        assert any(
            "invalid_json_required_field:$.CombatHistory" in code
            for code in exc.rejection_codes
        )

    assert session._session_faulted is False  # noqa: SLF001
    assert session._current_frame == frame_before  # noqa: SLF001
    assert session.get_legal_actions() == legal_before
    assert session.step(state, _strike_action(state), target_enemy_index=0) is not None


def test_restore_snapshot_json_rejects_malformed_wire_without_prior_validate():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    frame_before = session._current_frame  # noqa: SLF001

    invalid = to_emulator_payload(_restorable_capture(session))
    del invalid["CombatHistory"]
    try:
        session.restore_snapshot_json(_json_text(invalid))
        raise AssertionError("expected SnapshotRestoreRejectedError")
    except SnapshotRestoreRejectedError as exc:
        assert any(
            "invalid_json_required_field:$.CombatHistory" in code
            for code in exc.rejection_codes
        )

    assert session._session_faulted is False  # noqa: SLF001
    assert session._current_frame == frame_before  # noqa: SLF001
    assert session.step(state, _strike_action(state), target_enemy_index=0) is not None


def test_combat_history_all_17_entry_types_round_trip_via_wire_fixture():
    payload = history_fixture_payload(_current_wire_payload())
    assert [entry["EntryType"] for entry in payload["CombatHistory"]["Entries"]] == COMBAT_HISTORY_ENTRY_TYPES

    restored = _restore_capture_payload(payload)
    assert [entry["EntryType"] for entry in restored["CombatHistory"]["Entries"]] == COMBAT_HISTORY_ENTRY_TYPES
    assert history_signature(restored) == history_signature(payload)


def test_combat_history_player_turn_numbers_and_cardplay_resources_preserved():
    payload = history_fixture_payload(_current_wire_payload())
    restored = _restore_capture_payload(payload)

    for original, captured in zip(
        payload["CombatHistory"]["Entries"],
        restored["CombatHistory"]["Entries"],
    ):
        assert captured["PlayerTurnNumbers"] == original["PlayerTurnNumbers"], original["EntryType"]

    resources_by_type = {}
    for entry in restored["CombatHistory"]["Entries"]:
        fields = entry["Fields"]
        if entry["EntryType"] in ("CardPlayStartedEntry", "CardPlayFinishedEntry"):
            resources_by_type[entry["EntryType"]] = fields["resources"]
        elif entry["EntryType"] == "BlockGainedEntry":
            resources_by_type[entry["EntryType"]] = fields["cardPlay"]["resources"]

    assert resources_by_type == {
        "CardPlayStartedEntry": {
            "energySpent": 1,
            "energyValue": 2,
            "starsSpent": 3,
            "starValue": 4,
        },
        "CardPlayFinishedEntry": {
            "energySpent": 1,
            "energyValue": 2,
            "starsSpent": 3,
            "starValue": 4,
        },
        "BlockGainedEntry": {
            "energySpent": 1,
            "energyValue": 2,
            "starsSpent": 3,
            "starValue": 4,
        },
    }


def test_restore_step_determinism_with_non_empty_combat_history():
    base = history_fixture_payload(_current_wire_payload())
    entries = base["CombatHistory"]["Entries"]
    payload = history_fixture_payload(
        _current_wire_payload(),
        entries=[entries[0], entries[9], entries[14]],
    )
    json_text = _json_text(payload)
    session = LiveCombatSession()
    validation = session.validate_restore_snapshot_json(json_text)
    assert validation.eligible is True, validation.rejection_codes

    state1 = session.restore_snapshot_json(json_text)
    logical = _first_logical_action(state1)
    next1 = session.step(state1, _find_logical_action(state1, logical), target_enemy_index=0)
    after1 = _determinism_payload(session.capture_snapshot())

    state2 = session.restore_snapshot_json(json_text)
    next2 = session.step(state2, _find_logical_action(state2, logical), target_enemy_index=0)
    after2 = _determinism_payload(session.capture_snapshot())

    assert next1.engine_state == next2.engine_state
    assert after1 == after2


def test_basic_player_and_enemy_power_attachment_round_trip_via_wire():
    payload = _current_wire_payload()
    player_creature_id = payload["Player"]["CreatureInstanceId"]
    enemy_id = payload["Enemies"][0]["InstanceId"]
    payload["Player"]["Powers"] = [
        power_fixture(
            "StrengthPower",
            "power-940010",
            player_creature_id,
            None,
            amount=3,
            amount_on_turn_start=0,
        )
    ]
    payload["Enemies"][0]["Powers"] = [
        power_fixture(
            "StrengthPower",
            "power-940011",
            enemy_id,
            None,
            amount=2,
            amount_on_turn_start=0,
        )
    ]

    restored = _restore_capture_payload(payload)
    powers = {power["InstanceId"]: power for power in all_power_payloads(restored)}

    assert powers["power-940010"]["OwnerInstanceId"] == player_creature_id
    assert powers["power-940010"]["Amount"] == 3
    assert powers["power-940011"]["OwnerInstanceId"] == enemy_id
    assert powers["power-940011"]["Amount"] == 2


def test_power_internal_data_classifications_round_trip_via_wire():
    base = _current_wire_payload()
    player_creature_id = base["Player"]["CreatureInstanceId"]
    enemy_id = base["Enemies"][0]["InstanceId"]

    serialize_required = copy.deepcopy(base)
    serialize_required["Player"]["Powers"] = [
        power_fixture(
            "FeralPower",
            "power-940001",
            player_creature_id,
            internal_data_for("FeralPower", serialize_required),
        )
    ]
    restored = _restore_capture_payload(serialize_required)
    restored_feral = next(
        power for power in all_power_payloads(restored)
        if power["InstanceId"] == "power-940001"
    )
    assert restored_feral["InternalData"] == {"zeroCostAttacksPlayed": 4}
    assert restored_feral["InternalDataSerializerVersion"] == "generic-reflection-v1"

    safe = copy.deepcopy(base)
    safe["Player"]["Powers"] = [
        power_fixture(
            "AfterimagePower",
            "power-940002",
            player_creature_id,
            None,
            amount=6,
            amount_on_turn_start=4,
        )
    ]
    restored_safe = _restore_capture_payload(safe)
    restored_afterimage = next(
        power for power in all_power_payloads(restored_safe)
        if power["InstanceId"] == "power-940002"
    )
    assert restored_afterimage["Amount"] == 6
    assert restored_afterimage["AmountOnTurnStart"] == 4
    assert restored_afterimage["InternalData"] is None
    assert restored_afterimage["InternalDataSerializerVersion"] is None

    vigor = copy.deepcopy(base)
    vigor["Player"]["Powers"] = [
        power_fixture("VigorPower", "power-940003", player_creature_id, None, amount=3)
    ]
    restored_vigor = _restore_capture_payload(vigor)
    restored_vigor_power = next(
        power for power in all_power_payloads(restored_vigor)
        if power["InstanceId"] == "power-940003"
    )
    assert restored_vigor_power["Amount"] == 3
    assert restored_vigor_power["InternalData"] is None

    enemy_owned = copy.deepcopy(base)
    enemy_owned["Enemies"][0]["Powers"] = [
        power_fixture(
            "PossessStrengthPower",
            "power-940004",
            enemy_id,
            internal_data_for("PossessStrengthPower", enemy_owned),
        )
    ]
    restored_enemy = _restore_capture_payload(enemy_owned)
    restored_enemy_power = next(
        power for power in all_power_payloads(restored_enemy)
        if power["InstanceId"] == "power-940004"
    )
    assert restored_enemy_power["OwnerInstanceId"] == enemy_id
    assert restored_enemy_power["InternalData"] == {
        "stolenStrength": {player_creature_id: -3}
    }

    pet_owned = add_osty_pet(base)
    pet_id = pet_owned["Player"]["Pets"][0]["InstanceId"]
    pet_owned["Player"]["Pets"][0]["Powers"] = [
        power_fixture(
            "AutomationPower",
            "power-940005",
            pet_id,
            internal_data_for("AutomationPower", pet_owned),
        )
    ]
    restored_pet = _restore_capture_payload(pet_owned)
    restored_pet_power = next(
        power for power in all_power_payloads(restored_pet)
        if power["InstanceId"] == "power-940005"
    )
    assert restored_pet_power["OwnerInstanceId"] == pet_id
    assert restored_pet_power["InternalData"] == {"cardsLeft": 7}
