"""Encounter-based EnemyScenario.SlotName restoration.

Some monsters choose their opening move by Creature.SlotName instead of RNG/stateLog.
The source run history normally stores monster ids but not slot names, so reconstructing
these encounters without slot names can produce UNSET_MOVE and no legal actions. This
module restores only encounter layouts that are exact in v109 source and records the
provenance in `slot_name_manifest`.
"""

from __future__ import annotations

from typing import Any


SLOT_SOURCE_HISTORY = "source_history"
SLOT_ENCOUNTER_DEFINITION = "encounter_definition"
SLOT_INFERRED_FROM_ORDER = "inferred_from_order"
SLOT_UNAVAILABLE = "unavailable"

EXOSKELETON_WEAK_SLOTS = ("first", "second", "third")
EXOSKELETON_NORMAL_SLOTS = ("first", "second", "third", "fourth")


def _normalize_id(value: str | None) -> str:
    return (value or "").replace("_", "").replace(".", "").lower()


def _encounter_name(spec: dict) -> str:
    source = spec.get("source") or {}
    return str(source.get("encounter") or spec.get("encounter_id") or "").split(".")[-1]


def ensure_inferred_slot_names(spec: dict) -> dict:
    """Mutates `spec` in place, filling safe missing enemy slot_name values.

    Existing slot_name values are treated as source-history input and never overwritten.
    Missing values are inferred only from encounter layouts with a deterministic v109
    slot order. A manifest row is recorded for every enemy so downstream data can
    separate exact source data from encounter/order inference.
    """
    enemies = spec.get("enemies") or []
    manifest: list[dict[str, Any]] = []
    used_slots = set()

    for index, enemy in enumerate(enemies):
        slot_name = enemy.get("slot_name")
        if slot_name:
            used_slots.add(slot_name)
            manifest.append(_manifest_row(index, enemy, slot_name, SLOT_SOURCE_HISTORY, "exact"))

    encounter = _encounter_name(spec)
    if encounter == "PHROG_PARASITE_ELITE":
        wriggler_index = 0
        for index, enemy in enumerate(enemies):
            if enemy.get("slot_name"):
                continue
            monster_id = _normalize_id(enemy.get("monster_id"))
            if monster_id == _normalize_id("PHROG_PARASITE"):
                _set_slot(enemy, index, "phrog", SLOT_ENCOUNTER_DEFINITION, "exact", used_slots, manifest)
            elif monster_id == _normalize_id("WRIGGLER"):
                slot = f"wriggler{wriggler_index + 1}"
                wriggler_index += 1
                _set_slot(enemy, index, slot, SLOT_INFERRED_FROM_ORDER, "exact_for_recorded_order", used_slots, manifest)
    elif encounter in ("EXOSKELETONS_WEAK", "EXOSKELETONS_NORMAL"):
        slots = EXOSKELETON_WEAK_SLOTS if encounter == "EXOSKELETONS_WEAK" else EXOSKELETON_NORMAL_SLOTS
        exo_index = 0
        for index, enemy in enumerate(enemies):
            if enemy.get("slot_name"):
                continue
            if _normalize_id(enemy.get("monster_id")) != _normalize_id("EXOSKELETON"):
                continue
            if exo_index < len(slots):
                _set_slot(
                    enemy,
                    index,
                    slots[exo_index],
                    SLOT_INFERRED_FROM_ORDER,
                    "exact_for_recorded_order",
                    used_slots,
                    manifest,
                )
            exo_index += 1

    manifested = {row["enemy_index"] for row in manifest}
    for index, enemy in enumerate(enemies):
        if index not in manifested:
            manifest.append(_manifest_row(index, enemy, enemy.get("slot_name"), SLOT_UNAVAILABLE, "none"))

    manifest.sort(key=lambda row: row["enemy_index"])
    spec["slot_name_manifest"] = manifest
    return spec


def _set_slot(
    enemy: dict,
    index: int,
    slot_name: str,
    source: str,
    confidence: str,
    used_slots: set[str],
    manifest: list[dict[str, Any]],
) -> None:
    if slot_name in used_slots:
        raise ValueError(f"duplicate inferred SlotName '{slot_name}' in one scenario")
    enemy["slot_name"] = slot_name
    used_slots.add(slot_name)
    manifest.append(_manifest_row(index, enemy, slot_name, source, confidence))


def _manifest_row(index: int, enemy: dict, slot_name: str | None, source: str, confidence: str) -> dict:
    return {
        "enemy_index": index,
        "monster_id": enemy.get("monster_id"),
        "slot_name": slot_name,
        "slot_name_source": source,
        "slot_name_confidence": confidence,
    }
