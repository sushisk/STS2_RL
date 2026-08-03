"""PotionValueTable: converts held potions into a comparable numeric value.

Rarity gives the base value; specific potion ids can override it. Used both by
StateEvaluator (to score a board's held potions) and by fitness calculation
(remaining potion value on wins) in later phases.
"""

from __future__ import annotations


def _normalize(name: str) -> str:
    return name.strip().upper().replace(" ", "_")


DEFAULT_RARITY_VALUE = {
    "COMMON": 1.0,
    "UNCOMMON": 1.5,
    "RARE": 2.5,
}

DEFAULT_NAME_OVERRIDE = {
    "FOCUS_POTION": 3.0,
    "DUPLICATION_POTION": 3.0,
    "FIRE_POTION": 1.2,
    "BLOCK_POTION": 1.0,
}


class PotionValueTable:
    def __init__(self, rarity_value: dict | None = None, name_override: dict | None = None):
        self.rarity_value = {
            _normalize(k): v for k, v in (rarity_value or DEFAULT_RARITY_VALUE).items()
        }
        self.name_override = {
            _normalize(k): v for k, v in (name_override or DEFAULT_NAME_OVERRIDE).items()
        }

    def get_value_by_rarity(self, rarity: str | None) -> float:
        return self.rarity_value.get(_normalize(rarity or "common"), 1.0)

    def get_value_by_potion_name(self, potion_name: str | None) -> float | None:
        if not potion_name:
            return None
        return self.name_override.get(_normalize(potion_name))

    def get_potion_value(self, potion: dict) -> float:
        potion_id = potion.get("id")
        override = self.get_value_by_potion_name(potion_id)
        if override is not None:
            return override
        return self.get_value_by_rarity(potion.get("rarity"))

    def get_remaining_potion_value(self, state: dict) -> float:
        potions = state.get("potions") or []
        return sum(self.get_potion_value(p) for p in potions if p)
