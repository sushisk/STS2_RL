"""StateEvaluator: scores a board position for the heuristic agent (and, later, WeightOptimizer).

extract_features() turns a raw GameObservation.State dict into a small set of numeric
features; evaluate() combines them into one scalar via `weights`. Win/loss are handled
as flat bonuses/penalties dominating everything else, since no amount of feature-level
nuance should ever make a loss look better than a win.
"""

from __future__ import annotations

from legacy.potion_value_table import PotionValueTable

DEFAULT_WEIGHTS = {
    "player_hp_ratio": 40.0,
    "player_block": 0.5,
    "enemy_hp_ratio": -30.0,
    "predicted_incoming_damage": -1.0,
    "lethal_bonus": 50.0,
    "buff_debuff_score": 2.0,
    "hand_quality": 0.2,
    "potion_value": 3.0,
    "victory_bonus": 100_000.0,
    "defeat_penalty": -100_000.0,
}


class StateEvaluator:
    def __init__(self, potion_table: PotionValueTable | None = None):
        self.potion_table = potion_table or PotionValueTable()

    def extract_features(self, engine_state: dict) -> dict:
        hp = engine_state.get("hp", 0)
        max_hp = max(1, engine_state.get("maxHp", 1))
        block = engine_state.get("block", 0)

        enemies = [e for e in (engine_state.get("enemies") or []) if e.get("isAlive", True)]
        enemy_hp = sum(max(0, e.get("hp", 0)) for e in enemies)
        enemy_max_hp = sum(max(1, e.get("maxHp", 1)) for e in enemies) or 1

        predicted_incoming_damage = 0
        for e in enemies:
            intent = e.get("intent") or {}
            if "attackDamage" in intent:
                dmg = intent.get("attackDamage", 0) * intent.get("attackRepeats", 1)
                predicted_incoming_damage += max(0, dmg - block)

        buff_debuff_score = 0
        for p in engine_state.get("playerPowers") or []:
            sign = 1 if p.get("type") == "Buff" else -1
            buff_debuff_score += sign * p.get("amount", 0)

        hand_quality = len(engine_state.get("hand") or [])
        potion_value = self.potion_table.get_remaining_potion_value(engine_state)

        return {
            "player_hp_ratio": hp / max_hp,
            "player_block": block,
            "enemy_hp_ratio": enemy_hp / enemy_max_hp,
            "predicted_incoming_damage": predicted_incoming_damage,
            "lethal_bonus": 1.0 if not enemies else 0.0,
            "buff_debuff_score": buff_debuff_score,
            "hand_quality": hand_quality,
            "potion_value": potion_value,
        }

    def evaluate(self, engine_state: dict, weights: dict, *, outcome: str | None = None) -> float:
        # victory_bonus/defeat_penalty must dominate (so any win beats any non-terminal
        # state, any loss is worse than any non-terminal state) but must NOT replace the
        # feature score outright - collapsing every win to one flat constant makes wins
        # indistinguishable regardless of remaining HP/turns taken, which is harmless for
        # single-ply search (only one action usually delivers the literal lethal blow at
        # a given moment) but actively wrong for multi-turn averaging (LookaheadSearcher):
        # once *any* candidate's rollout reaches victory within the search horizon, it and
        # every other candidate that also eventually wins saturate to the identical flat
        # bonus, so the search can no longer tell a fast/safe win from one that took
        # needless damage - confirmed to cause exactly that (agent spamming End Turn while
        # taking heavy damage, since it "still wins eventually" either way).
        features = self.extract_features(engine_state)
        base = sum(weights.get(name, 0.0) * value for name, value in features.items())
        if outcome == "victory":
            return weights.get("victory_bonus", 0.0) + base
        if outcome == "defeat":
            return weights.get("defeat_penalty", 0.0) + base
        return base
