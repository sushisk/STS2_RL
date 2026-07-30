"""BattleResult: outcome summary of one simulated battle, for fitness/reporting."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class BattleResult:
    win: bool
    remaining_hp: int
    remaining_potions: int
    remaining_potion_value: float
    enemy_hp_removed_ratio: float
    turn_count: int
