"""Helpers for restoring MAD_SCIENCE's Tinker Time-selected internal state.

The source dataset records this in two places:
1. The gained card's own `props.ints` payload (authoritative when present).
2. The enclosing Tinker Time event's `event_choices` titles (human-readable path).

RL uses the same canonical strings the Emulator API documents:
  TinkerTimeType: Attack / Skill / Power
  TinkerTimeRider: Sapping / Violence / Choking / Energized / Wisdom / Chaos /
                   Expertise / Curious / Improvement
"""

from __future__ import annotations

TYPE_INT_TO_NAME = {
    1: "Attack",
    2: "Skill",
    3: "Power",
}

RIDER_INT_TO_NAME = {
    1: "Sapping",
    2: "Violence",
    3: "Choking",
    4: "Energized",
    5: "Wisdom",
    6: "Chaos",
    7: "Expertise",
    8: "Curious",
    9: "Improvement",
}

TYPE_TITLE_TO_NAME = {
    "TINKER_TIME.pages.CHOOSE_CARD_TYPE.options.ATTACK.title": "Attack",
    "TINKER_TIME.pages.CHOOSE_CARD_TYPE.options.SKILL.title": "Skill",
    "TINKER_TIME.pages.CHOOSE_CARD_TYPE.options.POWER.title": "Power",
}

RIDER_TITLE_TO_NAME = {
    "TINKER_TIME.pages.CHOOSE_RIDER.options.SAPPING.title": "Sapping",
    "TINKER_TIME.pages.CHOOSE_RIDER.options.VIOLENCE.title": "Violence",
    "TINKER_TIME.pages.CHOOSE_RIDER.options.CHOKING.title": "Choking",
    "TINKER_TIME.pages.CHOOSE_RIDER.options.ENERGIZED.title": "Energized",
    "TINKER_TIME.pages.CHOOSE_RIDER.options.WISDOM.title": "Wisdom",
    "TINKER_TIME.pages.CHOOSE_RIDER.options.CHAOS.title": "Chaos",
    "TINKER_TIME.pages.CHOOSE_RIDER.options.EXPERTISE.title": "Expertise",
    "TINKER_TIME.pages.CHOOSE_RIDER.options.CURIOUS.title": "Curious",
    "TINKER_TIME.pages.CHOOSE_RIDER.options.IMPROVEMENT.title": "Improvement",
}


def mad_science_state_from_props(card_ref: dict) -> "dict | None":
    ints = ((card_ref.get("props") or {}).get("ints")) or []
    values = {entry.get("name"): entry.get("value") for entry in ints}
    type_name = TYPE_INT_TO_NAME.get(values.get("TinkerTimeType"))
    rider_name = RIDER_INT_TO_NAME.get(values.get("TinkerTimeRider"))
    if not type_name and not rider_name:
        return None
    if not type_name or not rider_name:
        return None
    return {"tinker_time_type": type_name, "tinker_time_rider": rider_name}


def mad_science_state_from_event_choices(event_choices: list[dict] | None) -> "dict | None":
    type_name = None
    rider_name = None
    for choice in event_choices or []:
        key = ((choice.get("title") or {}).get("key")) or ""
        type_name = type_name or TYPE_TITLE_TO_NAME.get(key)
        rider_name = rider_name or RIDER_TITLE_TO_NAME.get(key)
    if not type_name and not rider_name:
        return None
    if not type_name or not rider_name:
        return None
    return {"tinker_time_type": type_name, "tinker_time_rider": rider_name}


def reconcile_mad_science_state(card_ref: dict, event_choices: list[dict] | None) -> tuple[dict | None, str]:
    """Returns (state, status) where status is exact|missing|ambiguous|conflict."""
    props_state = mad_science_state_from_props(card_ref)
    event_state = mad_science_state_from_event_choices(event_choices)
    if props_state and event_state and props_state != event_state:
        return None, "conflict"
    if props_state:
        return props_state, "exact"
    if event_state:
        return event_state, "exact"
    return None, "missing"
