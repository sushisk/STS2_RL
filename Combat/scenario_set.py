"""ScenarioSet: train/validation/test split of fixed evaluation boards.

Phase 0/1 scope: a small generator producing Ironclad-vs-CalcifiedCultist starting
positions varying only by seed (drives card-draw RNG). Later phases can broaden this
(more characters/enemies/hand states) without changing the train/validation/test split
contract other modules rely on.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ScenarioSet:
    train: list = field(default_factory=list)
    validation: list = field(default_factory=list)
    test: list = field(default_factory=list)

    @staticmethod
    def default_ironclad_vs_calcified_cultist(
        train_n: int = 100,
        validation_n: int = 100,
        test_n: int = 500,
        seed_offset: int = 0,
    ) -> "ScenarioSet":
        total = train_n + validation_n + test_n
        specs = [
            _ironclad_vs_calcified_cultist_spec(seed=seed_offset + i) for i in range(total)
        ]
        return ScenarioSet(
            train=specs[:train_n],
            validation=specs[train_n : train_n + validation_n],
            test=specs[train_n + validation_n :],
        )

    @staticmethod
    def necrobinder_vs_aeonglass_benchmark(seed: int = 1) -> "ScenarioSet":
        """A single, deliberately large-branching-factor benchmark position (19-card
        hand vs a 512 HP boss) - see _necrobinder_vs_aeonglass_spec() for the id
        corrections applied to the originally-requested card/relic/monster names."""
        spec = _necrobinder_vs_aeonglass_spec(seed=seed)
        return ScenarioSet(train=[spec], validation=[], test=[])


def _ironclad_vs_calcified_cultist_spec(seed: int) -> dict:
    # Standard Ironclad starting deck (5x Strike, 4x Defend, 1x Bash), 5 drawn to hand.
    return {
        "character_id": "Ironclad",
        "player_hp": 80,
        "player_max_hp": 80,
        "hand": ["STRIKE_IRONCLAD"] * 4 + ["BASH"],
        "draw_pile": ["STRIKE_IRONCLAD"] + ["DEFEND_IRONCLAD"] * 4,
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "seed": seed,
        "enemies": [{"monster_id": "CalcifiedCultist", "hp": 48, "powers": []}],
    }


def _necrobinder_vs_aeonglass_spec(seed: int) -> dict:
    """Fixed benchmark scenario: Necrobinder, 19-card hand, vs the AEONGLASS boss.

    Every requested card/relic/monster name was checked against ModelDb.AllCards /
    AllRelics / Monsters (fuzzy-matched, ratio >= 0.92 unless noted) and corrected to
    the real id - see the report for the full before/after list. Two corrections are
    NOT cosmetic and matter for reproducing the requested position:

    - "HourGlass" has no close match in Monsters (best fuzzy hit, AEONGLASS, only
      scored 0.67 vs >=0.92 for every other name) - it's used here as the closest
      plausible boss (a 512 HP "time"-themed monster) but this is a real substitution,
      not just a spelling fix, and may not be what was actually intended.
    - CombatScenario.Hand is IReadOnlyList<string> card ids only - there is no field
      anywhere in CombatScenario/EnemyScenario for a card's upgrade level, and
      ResolveCard matches purely on normalized id (GameInstance.cs ResolveCard/
      NormalizeId), so a "+" suffix does not resolve as "upgraded" - it just fails to
      match at all. Every "X+" in the request is therefore instantiated as base
      (unupgraded) X; there is currently no way to start a scenario with a card already
      upgraded.
    """
    return {
        "character_id": "Necrobinder",
        "player_hp": 80,
        "player_max_hp": 80,
        "hand": [
            "BODYGUARD",
            "UNLEASH",
            "SCOURGE",
            "DEFY",
            "DEFY",
            "AFTERLIFE",
            "PUTREFY",
            "PUTREFY",
            "DEBILITATE",
            "BONE_SHARDS",
            "SPIRIT_OF_ASH",
            "SLEIGHT_OF_FLESH",
            "SLEIGHT_OF_FLESH",
            "NO_ESCAPE",
            "SHARED_FATE",
            "GRAVE_WARDEN",
            "WISP",
            "COUNTDOWN",
            "DEATHS_DOOR",
        ],
        "draw_pile": [],
        "discard_pile": [],
        "exhaust_pile": [],
        "player_powers": [],
        "relics": [
            "BOUND_PHYLACTERY",
            "VAMBRACE",
            "RAZOR_TOOTH",
            "BAG_OF_MARBLES",
            "MERCURY_HOURGLASS",
            "HAPPY_FLOWER",
            "BAG_OF_PREPARATION",
            "PEN_NIB",
            "ANCHOR",
        ],
        "seed": seed,
        "enemies": [
            {
                "monster_id": "AEONGLASS",
                "hp": 512,
                "max_hp": 512,
                "powers": [
                    {"id": "WITHERING_PRESENCE_POWER", "amount": 6},
                    {"id": "ARTIFACT_POWER", "amount": 3},
                ],
            }
        ],
    }
