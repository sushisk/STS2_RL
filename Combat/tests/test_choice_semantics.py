"""Direct tests for Combat/choice_semantics.py, per this task's 検証 section: real-DLL
cases (SURVIVOR/BURNING_PACT/ARMAMENTS/HOLOGRAM/HEADBUTT/nested choice) exercised
through the actual 0d16130 Emulator via Combat/battle_emulator.py, plus pure-dict unit
cases (lookup miss/ambiguous rule/corrupted-lookup fallback) that don't need the
Emulator at all since ChoiceSemanticsTable.resolve() only ever consumes a plain dict.

Run: cd C:\\STS2_RL\\Combat\\tests && python test_choice_semantics.py
"""

from __future__ import annotations

import json
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Combat/

from battle_emulator import BattleEmulator  # noqa: E402
from emulator_bridge import to_plain, legal_actions_to_list  # noqa: E402
from choice_semantics import (  # noqa: E402
    ChoiceSemanticsTable, SCHEMA_PATH, LOOKUP_PATH, ORIGIN_TYPE_ALIASES_PATH,
)


def _step_and_get_pending(emu: BattleEmulator, state, card_id: str):
    """Plays `card_id` via a single raw Step (bypassing apply_action()'s automatic
    ActionContinuation resolution, which would otherwise auto-answer the choice before
    we ever get to inspect its pendingChoice) - mirrors STS2_Emulator/scripts/
    smoke_choice_context.py's own approach, just going through Combat's battle_emulator/
    emulator_bridge conversion layer instead of raw pythonnet."""
    legal = emu.enumerate_legal_actions(state)
    action = next(a for a in legal if a["action_type"] == "card" and a["parameters"].get("cardId") == card_id)
    game = emu._restore(state)  # noqa: SLF001 - white-box, same pattern test_scenario_v2.py uses
    result = game.Step(action["action_id"])
    return game, to_plain(result.Observation.State), to_plain(result.Observation.State).get("pendingChoice")


def test_survivor_discard():
    """FromHandForDiscard - Tier 1 (Emulator concretely reports discard), confirmed by
    a lookup row too (semantic_source=emulator_fact)."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": 80, "player_max_hp": 80, "seed": 1,
        "hand_cards": [
            {"card_id": "SURVIVOR", "is_upgraded": False},
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
            {"card_id": "DEFEND_IRONCLAD", "is_upgraded": False},
        ],
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40}],
    }
    state = emu.initialize(spec)
    _, _, pending = _step_and_get_pending(emu, state, "SURVIVOR")
    table = ChoiceSemanticsTable()
    assert table.loaded_ok, table.load_error
    result = table.resolve(pending)
    assert result["emulator_fact"]["originEntityId"] == "SURVIVOR"
    assert result["emulator_fact"]["choiceOperation"] == "discard"
    assert result["resolved"]["operationMode"] == "normalized"
    assert result["resolved"]["normalizedChoiceOperation"] == "discard"
    assert result["resolved"]["semanticSource"] == "emulator_fact"
    assert result["resolved"]["lookupStatus"] == "resolved_emulator_fact_confirmed_by_lookup"
    assert result["resolved"]["matchedRuleId"] == "SURVIVOR"
    assert result["resolved"]["exceptionEntityKey"] is None


def test_burning_pact_exhaust_prompt_confirmed():
    """Bare FromHand + ExhaustSelectionPrompt - Tier 1 via prompt-key classification."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": 80, "player_max_hp": 80, "seed": 1,
        "hand_cards": [
            {"card_id": "BURNING_PACT", "is_upgraded": False},
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
            {"card_id": "DEFEND_IRONCLAD", "is_upgraded": False},
        ],
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40}],
    }
    state = emu.initialize(spec)
    _, _, pending = _step_and_get_pending(emu, state, "BURNING_PACT")
    table = ChoiceSemanticsTable()
    result = table.resolve(pending)
    assert result["emulator_fact"]["choiceOperation"] == "exhaust"
    assert result["resolved"]["operationMode"] == "normalized"
    assert result["resolved"]["normalizedChoiceOperation"] == "exhaust"
    assert result["resolved"]["semanticSource"] == "emulator_fact"


def test_armaments_upgrade():
    """FromHandForUpgrade - no CardSelectorPrefs at all, classified from method tag
    alone, still Tier 1 (Emulator's own choiceOperation is concretely 'upgrade')."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": 80, "player_max_hp": 80, "seed": 1,
        "hand_cards": [
            {"card_id": "ARMAMENTS", "is_upgraded": False},
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
            {"card_id": "DEFEND_IRONCLAD", "is_upgraded": False},
        ],
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40}],
    }
    state = emu.initialize(spec)
    _, _, pending = _step_and_get_pending(emu, state, "ARMAMENTS")
    table = ChoiceSemanticsTable()
    result = table.resolve(pending)
    assert result["emulator_fact"]["choiceOperation"] == "upgrade"
    assert result["emulator_fact"]["destinationZone"] is None
    assert result["resolved"]["operationMode"] == "normalized"
    assert result["resolved"]["normalizedChoiceOperation"] == "upgrade"


def test_hologram_tier2_lookup():
    """FromCombatPile - Emulator's own choiceOperation stays 'unknown' (documented gap);
    resolved via the Tier-2 lookup row instead (retrieve_to_hand, a NORMALIZABLE_OPERATIONS
    member) - the exact motivating example from the original choice-semantics spec."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": 80, "player_max_hp": 80, "seed": 1,
        "hand_cards": [{"card_id": "HOLOGRAM", "is_upgraded": False}],
        "discard_pile_cards": [
            {"card_id": "DEFEND_IRONCLAD", "is_upgraded": False},
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
        ],
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40}],
    }
    state = emu.initialize(spec)
    _, _, pending = _step_and_get_pending(emu, state, "HOLOGRAM")
    table = ChoiceSemanticsTable()
    result = table.resolve(pending)
    assert result["emulator_fact"]["choiceOperation"] == "unknown"
    assert result["emulator_fact"]["originEntityId"] == "HOLOGRAM"
    assert result["resolved"]["operationMode"] == "normalized"
    assert result["resolved"]["normalizedChoiceOperation"] == "retrieve_to_hand"
    assert result["resolved"]["semanticSource"] == "hardcoded_entity_rule"
    assert result["resolved"]["lookupStatus"] == "resolved_lookup"
    assert result["resolved"]["matchedRuleId"] == "HOLOGRAM"


def test_headbutt_tier2_lookup():
    """FromCombatPile, real destination (draw pile top) Emulator itself cannot report -
    same Tier-2 shape as HOLOGRAM, different normalized operation."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": 80, "player_max_hp": 80, "seed": 1,
        "hand_cards": [{"card_id": "HEADBUTT", "is_upgraded": False}],
        "discard_pile_cards": [
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
            {"card_id": "DEFEND_IRONCLAD", "is_upgraded": False},
        ],
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40}],
    }
    state = emu.initialize(spec)
    _, _, pending = _step_and_get_pending(emu, state, "HEADBUTT")
    table = ChoiceSemanticsTable()
    result = table.resolve(pending)
    assert result["emulator_fact"]["choiceOperation"] == "unknown"
    assert result["resolved"]["operationMode"] == "normalized"
    assert result["resolved"]["normalizedChoiceOperation"] == "retrieve_to_draw_pile_top"
    assert result["resolved"]["semanticSource"] == "hardcoded_entity_rule"


def test_guards_passthrough():
    """FromHand bare (0..huge) - real operation IS known (transform_to_specific_card)
    but is NOT in NORMALIZABLE_OPERATIONS (each selected card is replaced by one
    SPECIFIC card, MINION_SACRIFICE - a plain 'transform' label would misrepresent it) -
    exercises the passthrough branch with a real DLL case, not just HOLOGRAM/HEADBUTT's
    normalized branch."""
    emu = BattleEmulator()
    spec = {
        "character_id": "IRONCLAD", "player_hp": 80, "player_max_hp": 80, "seed": 1,
        "hand_cards": [
            {"card_id": "GUARDS", "is_upgraded": False},
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
            {"card_id": "STRIKE_IRONCLAD", "is_upgraded": False},
            {"card_id": "DEFEND_IRONCLAD", "is_upgraded": False},
        ],
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40}],
    }
    state = emu.initialize(spec)
    _, _, pending = _step_and_get_pending(emu, state, "GUARDS")
    table = ChoiceSemanticsTable()
    result = table.resolve(pending)
    assert result["resolved"]["operationMode"] == "passthrough"
    assert result["resolved"]["normalizedChoiceOperation"] == "transform_to_specific_card"
    assert result["resolved"]["exceptionEntityKey"] == "card:GUARDS"
    assert result["resolved"]["semanticSource"] == "hardcoded_entity_rule"


def test_nested_choice_reattributes_origin_and_classification():
    """DECISIONS_DECISIONS (Regent) picks a Skill card to auto-play; if that card is
    BURNING_PACT, the inner choice's origin re-attributes to BURNING_PACT, not the outer
    card - and its classification correctly flips from passthrough (select_to_replay,
    DECISIONS_DECISIONS itself) to normalized (exhaust, BURNING_PACT) within the same
    nested flow, on the same live GameInstance."""
    emu = BattleEmulator()
    spec = {
        "character_id": "REGENT", "player_hp": 80, "player_max_hp": 80, "seed": 1, "stars": 6,
        "hand_cards": [
            {"card_id": "DECISIONS_DECISIONS", "is_upgraded": False},
            {"card_id": "BURNING_PACT", "is_upgraded": False},
            {"card_id": "STRIKE_REGENT", "is_upgraded": False},
            {"card_id": "STRIKE_REGENT", "is_upgraded": False},
            {"card_id": "DEFEND_REGENT", "is_upgraded": False},
        ],
        "draw_pile_cards": [{"card_id": "STRIKE_REGENT", "is_upgraded": False} for _ in range(5)],
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": 40, "max_hp": 40}],
    }
    state = emu.initialize(spec)
    game, state1, pending1 = _step_and_get_pending(emu, state, "DECISIONS_DECISIONS")
    table = ChoiceSemanticsTable()

    outer = table.resolve(pending1)
    assert outer["emulator_fact"]["originEntityId"] == "DECISIONS_DECISIONS"
    assert outer["resolved"]["operationMode"] == "passthrough"
    assert outer["resolved"]["normalizedChoiceOperation"] == "select_to_replay"

    legal1 = legal_actions_to_list(game.GetLegalActions())
    burning_pact_action = next(
        a for a in legal1 if a["action_type"] == "choice_card" and a["parameters"].get("cardId") == "BURNING_PACT"
    )
    step2 = game.Step(burning_pact_action["action_id"])
    pending2 = to_plain(step2.Observation.State).get("pendingChoice")

    inner = table.resolve(pending2)
    assert inner["emulator_fact"]["originEntityId"] == "BURNING_PACT"
    assert inner["emulator_fact"]["choiceOperation"] == "exhaust"
    assert inner["resolved"]["operationMode"] == "normalized"
    assert inner["resolved"]["normalizedChoiceOperation"] == "exhaust"


def test_lookup_miss():
    """Pure-dict unit test - no Emulator needed, resolve() only ever consumes a plain
    dict. An origin that concretely resolves (both type and id present) but is not in
    the table at all, with Emulator's own operation also unresolved."""
    table = ChoiceSemanticsTable()
    assert table.loaded_ok
    pending = {
        "originEntityType": "card", "originEntityId": "__NOT_A_REAL_CARD_FOR_TESTING__",
        "sourceZone": "hand", "choiceOperation": "unknown", "destinationZone": None,
        "choiceType": "Unsupported", "remainingSelectCount": 1,
    }
    result = table.resolve(pending)
    assert result["resolved"]["operationMode"] == "unknown"
    assert result["resolved"]["lookupStatus"] == "miss"
    assert result["resolved"]["matchedRuleId"] is None


def test_no_origin():
    """Emulator's own originEntityType/Id stayed null (e.g. FromDeckGeneric-family) and
    its choiceOperation also stayed unresolved - distinct lookupStatus from a plain
    miss, since there is no key to even attempt a lookup with."""
    table = ChoiceSemanticsTable()
    pending = {
        "originEntityType": None, "originEntityId": None, "sourceZone": "deck",
        "choiceOperation": "unknown", "destinationZone": None,
        "choiceType": "Unsupported", "remainingSelectCount": 1,
    }
    result = table.resolve(pending)
    assert result["resolved"]["operationMode"] == "unknown"
    assert result["resolved"]["lookupStatus"] == "no_origin"


def test_ambiguous_rule():
    """A lookup file with two rows sharing the same (origin_entity_type, origin_entity_id)
    key must NOT be silently collapsed to one - resolve() reports ambiguous_match/unknown
    rather than guessing which row is authoritative. Uses a temp file (real
    choice_semantics_lookup.v1.json has zero such duplicates today) so this test does
    not depend on ever finding a real ambiguous case."""
    with tempfile.TemporaryDirectory() as tmp:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        schema_copy_path = Path(tmp) / "schema.json"
        schema_copy_path.write_text(json.dumps(schema), encoding="utf-8")

        def make_entry(op: str) -> dict:
            return {
                "schema_version": "choice_semantics.v1",
                "origin_entity_type": "card",
                "origin_entity_id": "__DUPLICATE_TEST_CARD__",
                "source_zone": "hand",
                "normalized_choice_operation": op,
                "destination_zone": None,
                "semantic_confidence": "confirmed",
                "semantic_source": "hardcoded_entity_rule",
                "evidence": {
                    "emulator_choice_operation": "unknown",
                    "emulator_destination_zone": None,
                    "emulator_origin_resolvable": True,
                    "card_select_cmd_method": "FromHand (bare, source=this)",
                    "prompt_is_canonical": False,
                },
            }

        lookup_payload = {
            "schema_ref": schema["$id"],
            "table_version": "choice_semantics.test_ambiguous",
            "entries": [make_entry("discard"), make_entry("exhaust")],
        }
        lookup_copy_path = Path(tmp) / "lookup.json"
        lookup_copy_path.write_text(json.dumps(lookup_payload), encoding="utf-8")

        table = ChoiceSemanticsTable(schema_path=schema_copy_path, lookup_path=lookup_copy_path)
        assert table.loaded_ok, table.load_error

        pending = {
            "originEntityType": "card", "originEntityId": "__DUPLICATE_TEST_CARD__",
            "sourceZone": "hand", "choiceOperation": "unknown", "destinationZone": None,
            "choiceType": "Unsupported", "remainingSelectCount": 1,
        }
        result = table.resolve(pending)
        assert result["resolved"]["operationMode"] == "unknown"
        assert result["resolved"]["lookupStatus"] == "ambiguous_match"


def test_corrupted_lookup_falls_back_cleanly():
    """A malformed/missing lookup file must not raise from __init__ or from resolve() -
    every decision after that degrades to operationMode='unknown'/lookupStatus=
    'load_error', and the rest of the pipeline (which never consults this module for
    action selection - only for logging - see module docstring) is unaffected."""
    with tempfile.TemporaryDirectory() as tmp:
        corrupt_path = Path(tmp) / "corrupt_lookup.json"
        corrupt_path.write_text("{not valid json", encoding="utf-8")

        table = ChoiceSemanticsTable(schema_path=SCHEMA_PATH, lookup_path=corrupt_path)
        assert not table.loaded_ok
        assert table.load_error is not None

        pending = {
            "originEntityType": "card", "originEntityId": "SURVIVOR",
            "sourceZone": "hand", "choiceOperation": "discard", "destinationZone": "discard_pile",
            "choiceType": "Unsupported", "remainingSelectCount": 1,
        }
        result = table.resolve(pending)  # must not raise, even with a concretely-known live operation
        assert result["resolved"]["operationMode"] == "unknown"
        assert result["resolved"]["lookupStatus"] == "load_error"

        missing_path = Path(tmp) / "does_not_exist.json"
        table2 = ChoiceSemanticsTable(schema_path=SCHEMA_PATH, lookup_path=missing_path)
        assert not table2.loaded_ok
        result2 = table2.resolve(None)
        assert result2["resolved"]["operationMode"] == "unknown"
        assert result2["resolved"]["lookupStatus"] == "load_error"


def test_real_table_has_no_duplicate_keys():
    """Sanity check on the actual shipped table (not the synthetic one above) - today's
    choice_semantics_lookup.v1.json should have zero real ambiguous keys."""
    table = ChoiceSemanticsTable()
    assert table.loaded_ok, table.load_error
    duplicated = {key: len(rows) for key, rows in table._by_key.items() if len(rows) > 1}  # noqa: SLF001
    assert not duplicated, duplicated


def test_gambling_chip_discard_origin_null():
    """GamblingChipDiscard at decision_index=0 (genuine start-of-combat trigger) reports
    no origin at all - CHOICE_TYPE_RULES must still classify it as passthrough (never
    falls back to unknown just because origin is missing), with
    originValidationStatus='missing'."""
    table = ChoiceSemanticsTable()
    pending = {
        "choiceType": "GamblingChipDiscard", "originEntityType": None, "originEntityId": None,
        "sourceZone": "hand", "choiceOperation": "unknown", "destinationZone": None,
        "remainingSelectCount": 0,
    }
    result = table.resolve(pending)
    assert result["resolved"]["operationMode"] == "passthrough"
    assert result["resolved"]["exceptionEntityKey"] == "relic:GAMBLING_CHIP"
    assert result["resolved"]["normalizedChoiceOperation"] is None
    assert result["resolved"]["originValidationStatus"] == "missing"
    assert result["resolved"]["matchedRuleId"] == "GamblingChipDiscard"
    # raw origin preserved unchanged (both None here, but explicitly checked, not just
    # absent-by-coincidence)
    assert result["emulator_fact"]["originEntityType"] is None
    assert result["emulator_fact"]["originEntityId"] is None


def test_gambling_chip_discard_correct_relic_origin():
    """If origin ever DOES correctly resolve to (relic, GAMBLING_CHIP) - not observed
    live yet, but the classification must handle it - originValidationStatus='valid',
    operationMode/exceptionEntityKey unchanged."""
    table = ChoiceSemanticsTable()
    pending = {
        "choiceType": "GamblingChipDiscard", "originEntityType": "relic", "originEntityId": "GAMBLING_CHIP",
        "sourceZone": "hand", "choiceOperation": "unknown", "destinationZone": None,
        "remainingSelectCount": 0,
    }
    result = table.resolve(pending)
    assert result["resolved"]["operationMode"] == "passthrough"
    assert result["resolved"]["exceptionEntityKey"] == "relic:GAMBLING_CHIP"
    assert result["resolved"]["originValidationStatus"] == "valid"
    assert result["emulator_fact"]["originEntityType"] == "relic"
    assert result["emulator_fact"]["originEntityId"] == "GAMBLING_CHIP"


def test_gambling_chip_discard_leaked_potion_origin():
    """The Stage B empirical finding: GamblingChipDiscard firing again later in combat
    can report a stale, unrelated Potion's origin. Origin abnormality must NOT change the
    passthrough result - only originValidationStatus reflects it."""
    table = ChoiceSemanticsTable()
    pending = {
        "choiceType": "GamblingChipDiscard", "originEntityType": "PowerPotion", "originEntityId": "POWER_POTION",
        "sourceZone": None, "choiceOperation": "unknown", "destinationZone": None,
        "remainingSelectCount": 0,
    }
    result = table.resolve(pending)
    assert result["resolved"]["operationMode"] == "passthrough"
    assert result["resolved"]["exceptionEntityKey"] == "relic:GAMBLING_CHIP"
    assert result["resolved"]["normalizedChoiceOperation"] is None
    assert result["resolved"]["originValidationStatus"] == "suspected_context_leak"
    # raw origin preserved exactly as reported, not cleared/corrected
    assert result["emulator_fact"]["originEntityType"] == "PowerPotion"
    assert result["emulator_fact"]["originEntityId"] == "POWER_POTION"
    # never classified as discard, even though that IS the real underlying mechanic -
    # this task's instructions explicitly forbid folding GamblingChipDiscard into the
    # generic discard vocabulary
    assert result["resolved"]["normalizedChoiceOperation"] != "discard"


def test_gambling_chip_rule_overrides_concrete_emulator_operation():
    """Priority order requirement: the choiceType rule wins even if some future DLL
    build starts reporting a concrete choiceOperation for GamblingChipDiscard."""
    table = ChoiceSemanticsTable()
    pending = {
        "choiceType": "GamblingChipDiscard", "originEntityType": None, "originEntityId": None,
        "sourceZone": "hand", "choiceOperation": "discard", "destinationZone": "discard_pile",
        "remainingSelectCount": 0,
    }
    result = table.resolve(pending)
    assert result["resolved"]["operationMode"] == "passthrough"
    assert result["resolved"]["exceptionEntityKey"] == "relic:GAMBLING_CHIP"


def test_confirmed_potion_origin_type_normalization():
    """All 7 empirically-confirmed potion classes normalize to 'potion' while the raw
    PascalCase class name is preserved unchanged - and, since normalization feeds the
    Tier-3 lookup key, previously-miss potion choices now resolve via the lookup table
    (e.g. COLORLESS_POTION -> add_generated_to_hand, normalized; TOUCH_OF_INSANITY ->
    apply_effect_in_place, passthrough - both correctly reachable now)."""
    table = ChoiceSemanticsTable()
    confirmed = {
        "AttackPotion": "ATTACK_POTION", "Ashwater": "ASHWATER", "ColorlessPotion": "COLORLESS_POTION",
        "GamblersBrew": "GAMBLERS_BREW", "PowerPotion": "POWER_POTION", "SkillPotion": "SKILL_POTION",
        "TouchOfInsanity": "TOUCH_OF_INSANITY",
    }
    for raw_type, entity_id in confirmed.items():
        pending = {
            "choiceType": "Unsupported", "originEntityType": raw_type, "originEntityId": entity_id,
            "sourceZone": "hand", "choiceOperation": "unknown", "destinationZone": None,
            "remainingSelectCount": 0,
        }
        result = table.resolve(pending)
        assert result["resolved"]["normalizedOriginEntityType"] == "potion", (raw_type, result)
        assert result["emulator_fact"]["originEntityType"] == raw_type, "raw value must be unchanged"

    # Spot-check two concrete outcomes that were "miss" before this fix (Stage B report
    # section 6.2) and must now resolve via the lookup table.
    colorless = table.resolve({
        "choiceType": "Unsupported", "originEntityType": "ColorlessPotion", "originEntityId": "COLORLESS_POTION",
        "sourceZone": None, "choiceOperation": "unknown", "destinationZone": None, "remainingSelectCount": 0,
    })
    assert colorless["resolved"]["lookupStatus"] == "resolved_lookup"
    assert colorless["resolved"]["operationMode"] == "normalized"
    assert colorless["resolved"]["normalizedChoiceOperation"] == "add_generated_to_hand"

    touch_of_insanity = table.resolve({
        "choiceType": "Unsupported", "originEntityType": "TouchOfInsanity", "originEntityId": "TOUCH_OF_INSANITY",
        "sourceZone": "hand", "choiceOperation": "unknown", "destinationZone": None, "remainingSelectCount": 0,
    })
    assert touch_of_insanity["resolved"]["lookupStatus"] == "resolved_lookup"
    assert touch_of_insanity["resolved"]["operationMode"] == "passthrough"
    assert touch_of_insanity["resolved"]["exceptionEntityKey"] == "potion:TOUCH_OF_INSANITY"


def test_unregistered_potion_like_class_not_auto_classified():
    """An unregistered class that superficially LOOKS like a potion (PascalCase, ends in
    'Potion') must NOT be auto-classified by suffix matching - only the explicit
    enumeration in choice_semantics_origin_type_aliases.v1.json counts. Must resolve to
    normalizedOriginEntityType='unknown', not 'potion'."""
    table = ChoiceSemanticsTable()
    pending = {
        "choiceType": "Unsupported", "originEntityType": "SomeBrandNewPotion", "originEntityId": "SOME_BRAND_NEW_POTION",
        "sourceZone": "hand", "choiceOperation": "unknown", "destinationZone": None,
        "remainingSelectCount": 0,
    }
    result = table.resolve(pending)
    assert result["resolved"]["normalizedOriginEntityType"] == "unknown"
    assert result["emulator_fact"]["originEntityType"] == "SomeBrandNewPotion"  # raw preserved
    assert result["resolved"]["operationMode"] == "unknown"
    assert result["resolved"]["lookupStatus"] == "miss"


def test_raw_origin_always_preserved():
    """Across every path (choiceType rule, Tier1/2/3, unknown), emulator_fact's raw
    originEntityType/originEntityId must echo the input verbatim - never overwritten,
    corrected, or cleared."""
    table = ChoiceSemanticsTable()
    cases = [
        {"choiceType": "GamblingChipDiscard", "originEntityType": "PowerPotion", "originEntityId": "POWER_POTION",
         "sourceZone": None, "choiceOperation": "unknown", "destinationZone": None, "remainingSelectCount": 0},
        {"choiceType": "Unsupported", "originEntityType": "card", "originEntityId": "SURVIVOR",
         "sourceZone": "hand", "choiceOperation": "discard", "destinationZone": "discard_pile", "remainingSelectCount": 1},
        {"choiceType": "Unsupported", "originEntityType": "ColorlessPotion", "originEntityId": "COLORLESS_POTION",
         "sourceZone": None, "choiceOperation": "unknown", "destinationZone": None, "remainingSelectCount": 0},
        {"choiceType": "Unsupported", "originEntityType": None, "originEntityId": None,
         "sourceZone": None, "choiceOperation": "unknown", "destinationZone": None, "remainingSelectCount": 0},
    ]
    for pending in cases:
        result = table.resolve(pending)
        assert result["emulator_fact"]["originEntityType"] == pending["originEntityType"]
        assert result["emulator_fact"]["originEntityId"] == pending["originEntityId"]


def test_corrupted_origin_aliases_falls_back_cleanly():
    """A malformed/missing origin-type-aliases file degrades gracefully (identity/
    'unknown' normalization only) WITHOUT breaking the main lookup table - independent
    failure domain from test_corrupted_lookup_falls_back_cleanly. Heuristic fallback
    (the actual choice_card/choice_skip/choice_confirm routing) is unaffected either
    way, since this module never influences it - see module docstring."""
    with tempfile.TemporaryDirectory() as tmp:
        corrupt_aliases_path = Path(tmp) / "corrupt_aliases.json"
        corrupt_aliases_path.write_text("{not valid json", encoding="utf-8")

        table = ChoiceSemanticsTable(schema_path=SCHEMA_PATH, lookup_path=LOOKUP_PATH, origin_aliases_path=corrupt_aliases_path)
        assert table.loaded_ok, table.load_error  # main table load is independent, still fine
        assert table.origin_aliases_error is not None

        # A card-origin choice still resolves normally (never needed aliases).
        survivor_like = table.resolve({
            "choiceType": "Unsupported", "originEntityType": "card", "originEntityId": "SURVIVOR",
            "sourceZone": "hand", "choiceOperation": "discard", "destinationZone": "discard_pile",
            "remainingSelectCount": 1,
        })
        assert survivor_like["resolved"]["operationMode"] == "normalized"

        # A potion-origin choice degrades to unknown (no aliases available) instead of
        # raising or guessing.
        potion_like = table.resolve({
            "choiceType": "Unsupported", "originEntityType": "ColorlessPotion", "originEntityId": "COLORLESS_POTION",
            "sourceZone": None, "choiceOperation": "unknown", "destinationZone": None, "remainingSelectCount": 0,
        })
        assert potion_like["resolved"]["normalizedOriginEntityType"] == "unknown"
        assert potion_like["resolved"]["operationMode"] == "unknown"

        # GamblingChipDiscard's own passthrough rule is completely independent of the
        # aliases file and must still fire.
        gambling = table.resolve({
            "choiceType": "GamblingChipDiscard", "originEntityType": None, "originEntityId": None,
            "sourceZone": "hand", "choiceOperation": "unknown", "destinationZone": None, "remainingSelectCount": 0,
        })
        assert gambling["resolved"]["operationMode"] == "passthrough"


def main() -> int:
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = [], []
    for test in tests:
        try:
            test()
            passed.append(test.__name__)
            print(f"PASS {test.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
