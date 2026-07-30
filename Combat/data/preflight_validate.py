"""Pre-flight scenario validation for teacher-data generation (plan Phase 2, section 8
of the "続行指示" this implements): every scenario handed to the trajectory generator is
checked here immediately after BattleEmulator.initialize(), BEFORE any Heuristic
decision is made from it. A scenario that fails any check is quarantined (never fed to
the Heuristic/CombatEnv loop) with a recorded reason - never silently used anyway.

Deliberately checks state CONTENT, not just exception occurrence. Comparing initialized
state against the scenario spec's own stated deck/relics/potions/HP/enemies/Stars is what
actually catches silent state mutations.
"""

from __future__ import annotations

from battle_emulator import BattleEmulator, BattleState
from battle_emulator import is_action_continuation_pending_choice


def _normalize(entry_id: str) -> str:
    """Matches GameInstance.cs's own NormalizeId (strip underscores, lowercase) - every
    id in this API is matched case/underscore-insensitively (e.g. 'CalcifiedCultist' ==
    'CALCIFIED_CULTIST'), so a plain string comparison here would produce false-positive
    mismatches for scenarios (like scenario_set.py's hand-authored ones) that use a
    different-but-equivalent casing than what GetObservation() reports back."""
    return entry_id.replace("_", "").lower()


def _pile_card_ids(state_dict: dict) -> list[str]:
    ids = []
    for pile in ("hand", "drawPile", "discardPile", "exhaustPile", "playPile"):
        ids.extend(c["id"] for c in state_dict.get(pile) or [])
    return sorted(_normalize(i) for i in ids)


def _spec_card_ids(spec: dict) -> list[str]:
    ids = []
    for key in ("hand", "draw_pile", "discard_pile", "exhaust_pile"):
        ids.extend(spec.get(key) or [])
    for key in ("hand_cards", "draw_pile_cards", "discard_pile_cards", "exhaust_pile_cards"):
        ids.extend(c["card_id"] for c in (spec.get(key) or []))
    return sorted(_normalize(i) for i in ids)


def _missing_mad_science_state(spec: dict) -> bool:
    for key in ("hand_cards", "draw_pile_cards", "discard_pile_cards", "exhaust_pile_cards"):
        for c in spec.get(key) or []:
            if _normalize(c["card_id"]) != _normalize("MAD_SCIENCE"):
                continue
            if not c.get("tinker_time_type") or not c.get("tinker_time_rider"):
                return True
    return False


def _missing_associated_card_state(spec: dict) -> str | None:
    for power in spec.get("player_powers") or []:
        power_id = _normalize(power.get("id", power.get("power_id", "")))
        if power_id != _normalize("NIGHTMARE_POWER"):
            continue
        associated = power.get("associated_card", power.get("associatedCard"))
        if not associated:
            return "missing_associated_card"
    return None


def _spec_dynamic_cards(spec: dict) -> list[tuple[str, bool, object, object]]:
    cards = []
    for key in ("hand_cards", "draw_pile_cards", "discard_pile_cards", "exhaust_pile_cards"):
        for c in spec.get(key) or []:
            cards.append(
                (
                    _normalize(c["card_id"]),
                    bool(c.get("is_upgraded", False)),
                    c.get("tinker_time_type"),
                    c.get("tinker_time_rider"),
                )
            )
    return sorted(cards)


def _power_card_signature(card: dict | None) -> tuple[str | None, bool, object, object] | None:
    if not card:
        return None
    return (
        _normalize(card.get("card_id", card.get("id", ""))),
        bool(card.get("is_upgraded", card.get("upgraded", False))),
        card.get("tinker_time_type", card.get("tinkerTimeType")),
        card.get("tinker_time_rider", card.get("tinkerTimeRider")),
    )


def _spec_player_powers(spec: dict) -> list[tuple[str, int, tuple[str | None, bool, object, object] | None]]:
    return sorted(
        (
            _normalize(power.get("id", power.get("power_id", ""))),
            int(power["amount"]),
            _power_card_signature(power.get("associated_card", power.get("associatedCard"))),
        )
        for power in (spec.get("player_powers") or [])
    )


def _state_player_powers(state_dict: dict) -> list[tuple[str, int, tuple[str | None, bool, object, object] | None]]:
    return sorted(
        (
            _normalize(power.get("id", "")),
            int(power["amount"]),
            _power_card_signature(power.get("associatedCard")),
        )
        for power in (state_dict.get("playerPowers") or [])
    )


def _state_dynamic_cards(state_dict: dict) -> list[tuple[str, bool, object, object]]:
    cards = []
    for key in ("hand", "drawPile", "discardPile", "exhaustPile"):
        for c in state_dict.get(key) or []:
            cards.append(
                (
                    _normalize(c["id"]),
                    bool(c.get("upgraded", False)),
                    c.get("tinkerTimeType"),
                    c.get("tinkerTimeRider"),
                )
            )
    return sorted(cards)


def _stable_orb_value(value) -> str | None:
    return None if value is None else str(value)


def _orb_signature_from_spec(spec: dict) -> tuple[int | None, list[tuple[str, object, object]]]:
    return (
        spec.get("orb_slots"),
        [
            (
                _normalize(orb["orb_id"]),
                _stable_orb_value(orb.get("base_passive_value")),
                _stable_orb_value(orb.get("base_evoke_value")),
            )
            for orb in (spec.get("orbs") or [])
        ],
    )


def _orb_signature_from_state(state_dict: dict) -> tuple[int | None, list[tuple[str, object, object]]]:
    return (
        state_dict.get("orbSlots"),
        [
            (
                _normalize(orb["id"]),
                _stable_orb_value(orb.get("basePassiveValue")),
                _stable_orb_value(orb.get("baseEvokeValue")),
            )
            for orb in (state_dict.get("orbs") or [])
        ],
    )


def _unsupported_pending_choice_type(state_dict: dict) -> str | None:
    pending = state_dict.get("pendingChoice") or {}
    choice_type = pending.get("choiceType")
    if not choice_type:
        return None
    if is_action_continuation_pending_choice(state_dict):
        return str(choice_type)
    return None


def preflight_validate(spec: dict, emulator: BattleEmulator) -> dict:
    """Returns a dict: {status: "ok"|"quarantined", reasons: [str, ...], battle_state:
    BattleState|None, diffs: dict}. `battle_state` is None whenever status is
    "quarantined" for an init-time reason (exception, empty legal actions) - callers
    must not proceed to a Heuristic decision without checking status == "ok" first."""
    reasons: list[str] = []
    diffs: dict = {}

    missing_associated_card = _missing_associated_card_state(spec)
    if missing_associated_card is not None:
        return {
            "status": "quarantined",
            "reasons": [missing_associated_card],
            "battle_state": None,
            "diffs": {"player_powers": spec.get("player_powers") or []},
        }

    try:
        state: BattleState = emulator.initialize(spec)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "quarantined",
            "reasons": [f"init_exception:{type(exc).__name__}"],
            "battle_state": None,
            "diffs": {"error_message": str(exc)[:300]},
        }

    try:
        legal = emulator.enumerate_legal_actions(state)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "quarantined",
            "reasons": [f"legal_actions_exception:{type(exc).__name__}"],
            "battle_state": None,
            "diffs": {"error_message": str(exc)[:300]},
        }
    if not legal:
        reasons.append("no_legal_actions")

    engine_state = state.engine_state

    unsupported_pending_choice = _unsupported_pending_choice_type(engine_state)
    if unsupported_pending_choice is not None:
        reasons.append(f"unsupported_pending_choice_type:{unsupported_pending_choice}")
        diffs["pending_choice"] = engine_state.get("pendingChoice")

    if _missing_mad_science_state(spec):
        reasons.append("missing_mad_science_state")

    expected_cards = _spec_card_ids(spec)
    actual_cards = _pile_card_ids(engine_state)
    if expected_cards and actual_cards != expected_cards:
        reasons.append("deck_mismatch")
        diffs["deck"] = {"expected_count": len(expected_cards), "actual_count": len(actual_cards)}
    expected_dynamic_cards = _spec_dynamic_cards(spec)
    if expected_dynamic_cards:
        actual_dynamic_cards = _state_dynamic_cards(engine_state)
        if actual_dynamic_cards != expected_dynamic_cards:
            reasons.append("card_state_mismatch")
            diffs["card_state"] = {"expected": expected_dynamic_cards, "actual": actual_dynamic_cards}
    expected_player_powers = _spec_player_powers(spec)
    if expected_player_powers:
        actual_player_powers = _state_player_powers(engine_state)
        if actual_player_powers != expected_player_powers:
            reasons.append("player_power_mismatch")
            diffs["player_powers"] = {"expected": expected_player_powers, "actual": actual_player_powers}

    expected_relics = sorted(_normalize(r) for r in (spec.get("relics") or []))
    actual_relics = sorted(_normalize(r["id"]) for r in (engine_state.get("relics") or []))
    if actual_relics != expected_relics:
        reasons.append("relic_mismatch")
        diffs["relics"] = {
            "expected": sorted(spec.get("relics") or []),
            "actual": sorted(r["id"] for r in (engine_state.get("relics") or [])),
        }

    expected_potions = sorted(_normalize(p["potion_id"]) for p in (spec.get("potions") or []))
    actual_potions = sorted(_normalize(p["id"]) for p in (engine_state.get("potions") or []) if p)
    if actual_potions != expected_potions:
        reasons.append("potion_mismatch")
        diffs["potions"] = {
            "expected": sorted(p["potion_id"] for p in (spec.get("potions") or [])),
            "actual": sorted(p["id"] for p in (engine_state.get("potions") or []) if p),
        }

    if spec.get("player_hp") is not None and engine_state.get("hp") != spec["player_hp"]:
        reasons.append("hp_mismatch")
        diffs["hp"] = {"expected": spec["player_hp"], "actual": engine_state.get("hp")}
    if spec.get("player_max_hp") is not None and engine_state.get("maxHp") != spec["player_max_hp"]:
        reasons.append("max_hp_mismatch")
        diffs["max_hp"] = {"expected": spec["player_max_hp"], "actual": engine_state.get("maxHp")}
    if spec.get("stars") is not None and engine_state.get("stars") != spec["stars"]:
        reasons.append("stars_mismatch")
        diffs["stars"] = {"expected": spec["stars"], "actual": engine_state.get("stars")}

    if spec.get("orb_slots") is not None or spec.get("orbs"):
        expected_orbs = _orb_signature_from_spec(spec)
        actual_orbs = _orb_signature_from_state(engine_state)
        if actual_orbs != expected_orbs:
            reasons.append("orb_mismatch")
            diffs["orbs"] = {"expected": expected_orbs, "actual": actual_orbs}

    expected_enemy_ids = sorted(_normalize(e["monster_id"]) for e in spec.get("enemies") or [])
    actual_enemy_ids = sorted(_normalize(e["id"]) for e in engine_state.get("enemies") or [])
    if expected_enemy_ids != actual_enemy_ids:
        reasons.append("enemy_mismatch")
        diffs["enemies"] = {
            "expected": sorted(e["monster_id"] for e in spec.get("enemies") or []),
            "actual": sorted(e["id"] for e in engine_state.get("enemies") or []),
        }

    expected_slots = [
        (i, _normalize(e["monster_id"]), e.get("slot_name"))
        for i, e in enumerate(spec.get("enemies") or [])
        if e.get("slot_name") is not None
    ]
    actual_slots = [
        (i, _normalize(e["id"]), e.get("slotName"))
        for i, e in enumerate(engine_state.get("enemies") or [])
        if e.get("slotName") is not None
    ]
    if expected_slots and actual_slots != expected_slots:
        reasons.append("slot_name_mismatch")
        diffs["slot_names"] = {
            "expected": expected_slots,
            "actual": actual_slots,
            "manifest": spec.get("slot_name_manifest"),
        }

    status = "quarantined" if reasons else "ok"
    return {"status": status, "reasons": reasons, "battle_state": state, "diffs": diffs}
