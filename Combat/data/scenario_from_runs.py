"""Generates realistic BattleEmulator scenario specs (the dict shape battle_emulator.py's
build_scenario_from_spec()/ScenarioSet already use) from real human decks/relics paired
with real encounter rosters - operationalizing plan section 6.1 ("既存データの利用方針
- 戦闘開始状態の供給"): use actual decks/relics reached by human players as combat
start states, instead of only the single hand-authored Ironclad-vs-CalcifiedCultist
matchup scenario_set.py currently ships.

Two real data sources feed this, both already audited/extracted elsewhere in this repo:
  - C:\\STS2_Data\\runs-all-before-2026-06.json (via audit_runs_dataset.py) for
    per-run final deck/relic composition.
  - Common/ids/v0109_raw/json/act_encounter_pools.json + Common/ids/monsters.json for
    real per-act/pool_type (weak/normal/elite/boss) encounter rosters and monster HP.

RESOLVED as of the 2026-07-20 15:49 Emulator build (see Common/schemas/README.md's
changelog section) - this generator now restores real per-card upgrade state and real
potions, and no longer filters any relics:
  - Card upgrade state: uses CombatScenario's structured HandCards/DrawPileCards
    (CardInstanceScenario{CardId,IsUpgraded}) built from each deck entry's real
    `current_upgrade_level` (runs-all-before-2026-06.json), instead of the plain
    id-string Hand/DrawPile that dropped upgrade state entirely.
  - Potions: restored from each run's real `players[0].potions` (id + slot_index) via
    CombatScenario.Potions - previously dropped outright (no field existed to hold them).
  - The LEAD_PAPERWEIGHT/CLAWS relic-hang bug this project found and reported
    (Combat/evaluation/reports/emulator_hang/) is fixed Emulator-side (interactive
    AfterObtained() prompts auto-decline instead of hanging) - the interim RL-side
    filter that used to drop these two relics has been removed; no relics are excluded.

Still-open simplifications (real limitations of the source data or the Emulator API,
not fixed by the above):
  - Player HP/max HP are left unspecified (player_hp=player_max_hp=None -> "let nature
    take its course", per CombatScenario.PlayerHp/PlayerMaxHp's doc comments) - the
    source data (runs-all-before-2026-06.json's per-run snapshot) records final
    deck/relics/potions but never HP, current or max. Not guessed.
  - Monster HP uses the canonical ascension-10 HP band's max (Common/ids/monsters.json),
    not HP scaled to the run's actual recorded `ascension` - CombatScenario has no
    Ascension field at all (only the simpler Reset() takes one), so a run's real
    ascension cannot be *applied*, only recorded as metadata (see `source.ascension`
    below).
  - Enemy SlotName/ForcedMove/StateLog are left unset (natural roll) - the source data
    has no record of actual in-combat monster slot assignment or move history.
  - The run's final deck (all cards ever added, regardless of floor) is used as-is; no
    attempt is made to reconstruct the deck/relics/potions as of a specific mid-run floor
    (that reconstruction - via map_point_history replay - is what sts2-agent's
    card_reward_picker dataset builder does for its own purposes; out of scope here).

Run standalone for a smoke test: `python scenario_from_runs.py`
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Combat/

from slot_name_inference import ensure_inferred_slot_names
from mad_science_restore import reconcile_mad_science_state

RUNS_PATH = Path(r"C:\STS2_Data\runs-all-before-2026-06.json")
ACT_ENCOUNTER_POOLS_PATH = Path(r"C:\STS2_RL\Common\ids\v0109_raw\json\act_encounter_pools.json")
MONSTERS_CANONICAL_PATH = Path(r"C:\STS2_RL\Common\ids\monsters.json")

# Playable-character validity gate - not used to set HP anymore (player_hp/player_max_hp
# are left None; see module docstring), just to reject any character id the source data
# might contain that isn't one of these five real playable characters.
KNOWN_PLAYABLE_CHARACTERS = {"IRONCLAD", "SILENT", "DEFECT", "REGENT", "NECROBINDER"}


def strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def load_usable_runs(path: Path = RUNS_PATH) -> Iterator[dict]:
    """Yields run records passing the same 'usable for scenario generation' filter as
    audit_runs_dataset.py: not abandoned, not cheated, standard mode, has a player."""
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if (
                not rec.get("was_abandoned")
                and not rec.get("_isCheated")
                and rec.get("game_mode") == "standard"
                and rec.get("players")
            ):
                yield rec


def load_encounter_pools() -> dict[tuple[int, str], list[dict]]:
    with ACT_ENCOUNTER_POOLS_PATH.open(encoding="utf-8") as f:
        records = json.load(f)
    pools: dict[tuple[int, str], list[dict]] = {}
    for rec in records:
        key = (rec["act_number"], rec["pool_type"])
        pools.setdefault(key, []).extend(rec.get("encounters", []))
    return pools


def load_monster_hp() -> dict[str, int]:
    with MONSTERS_CANONICAL_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    out = {}
    for entry_id, rec in data.items():
        hp = (rec.get("hp") or {}).get("ascension10") or {}
        out[entry_id] = hp.get("max") or hp.get("min") or 50
    return out


def encounter_monster_ids(encounter: dict) -> list[str]:
    monsters = encounter.get("possible_monsters") or []
    if not monsters:
        for pattern in encounter.get("spawn_patterns") or []:
            monsters = pattern.get("monsters") or []
            if monsters:
                break
    return [m["entry"] for m in monsters]


def scenario_from_run(
    run: dict,
    act_number: int,
    pool_type: str,
    pools: dict[tuple[int, str], list[dict]],
    monster_hp: dict[str, int],
    rng: random.Random,
) -> "dict | None":
    """Builds one battle_emulator.py-compatible scenario spec dict, or None if this
    (run, act_number, pool_type) combination has no usable encounter/character/deck."""
    player = run["players"][0]
    character_entry = strip_prefix(player.get("character", ""), "CHARACTER.")
    if character_entry not in KNOWN_PLAYABLE_CHARACTERS:
        return None

    mad_science_history = []
    for act_points in run.get("map_point_history") or []:
        for point in act_points:
            ps_list = point.get("player_stats") or []
            if not ps_list:
                continue
            ps = ps_list[0]
            for gained in ps.get("cards_gained") or []:
                if gained.get("id") != "CARD.MAD_SCIENCE":
                    continue
                state, status = reconcile_mad_science_state(gained, ps.get("event_choices"))
                mad_science_history.append({"state": state, "status": status})

    history_idx = 0
    ambiguous_mad_science = 0
    deck_cards = []
    for c in (player.get("deck") or []):
        card = {
            "card_id": strip_prefix(c["id"], "CARD."),
            # current_upgrade_level is an int (0/1/2+) recording the real upgrade count;
            # CombatScenario.CardInstanceScenario.IsUpgraded is boolean-only, so any
            # level >= 1 collapses to True - a real, acknowledged loss of fidelity for
            # multi-upgrade cards (e.g. Searing Blow), not a guess: True still means
            # "this card really was upgraded in the source run," just not by how much.
            "is_upgraded": int(c.get("current_upgrade_level") or 0) >= 1,
        }
        if card["card_id"] == "MAD_SCIENCE":
            state, status = reconcile_mad_science_state(c, None)
            if not state and history_idx < len(mad_science_history):
                state = mad_science_history[history_idx]["state"]
                status = mad_science_history[history_idx]["status"]
            if state:
                card.update(state)
            else:
                ambiguous_mad_science += 1
            history_idx += 1
        deck_cards.append(card)
    if len(deck_cards) < 5:
        return None
    relic_ids = [strip_prefix(r["id"], "RELIC.") for r in (player.get("relics") or [])]
    potions = [
        {"slot": p["slot_index"], "potion_id": strip_prefix(p["id"], "POTION.")}
        for p in (player.get("potions") or [])
    ]

    candidates = pools.get((act_number, pool_type))
    if not candidates:
        return None
    encounter = rng.choice(candidates)
    monster_ids = encounter_monster_ids(encounter)
    if not monster_ids:
        return None

    shuffled = deck_cards[:]
    rng.shuffle(shuffled)
    hand_cards, draw_pile_cards = shuffled[:5], shuffled[5:]

    spec = {
        "character_id": character_entry,
        "player_hp": None,
        "player_max_hp": None,
        "hand_cards": hand_cards,
        "draw_pile_cards": draw_pile_cards,
        "discard_pile_cards": [],
        "exhaust_pile_cards": [],
        "player_powers": [],
        "relics": relic_ids,
        "potions": potions,
        "seed": rng.randint(1, 2_000_000_000),
        "enemies": [
            {"monster_id": m, "hp": monster_hp.get(m, 50)} for m in monster_ids
        ],
        "source": {
            "server_id": run.get("_serverId"),
            "build_id": run.get("build_id"),
            "ascension": run.get("ascension"),
            "act_number": act_number,
            "pool_type": pool_type,
            "encounter": encounter["id"]["entry"],
            "mad_science_restore_status": "exact" if ambiguous_mad_science == 0 else "ambiguous",
            "mad_science_ambiguous_count": ambiguous_mad_science,
        },
    }
    return ensure_inferred_slot_names(spec)


def generate_scenarios(
    n: int,
    act_number: int = 1,
    pool_type: str = "normal",
    character_filter: "str | None" = None,
    seed: int = 0,
    runs_path: Path = RUNS_PATH,
) -> list[dict]:
    """Draws up to n scenario specs from usable runs, deterministic given `seed`."""
    rng = random.Random(seed)
    pools = load_encounter_pools()
    monster_hp = load_monster_hp()
    runs = list(load_usable_runs(runs_path))
    rng.shuffle(runs)

    out: list[dict] = []
    for run in runs:
        if len(out) >= n:
            break
        if character_filter and run["players"][0].get("character") != character_filter:
            continue
        spec = scenario_from_run(run, act_number, pool_type, pools, monster_hp, rng)
        if spec is not None:
            out.append(spec)
    return out


if __name__ == "__main__":
    scenarios = generate_scenarios(n=5, act_number=1, pool_type="normal", seed=42)
    print(f"Generated {len(scenarios)} scenarios")
    for s in scenarios:
        deck_size = len(s["hand_cards"]) + len(s["draw_pile_cards"])
        upgraded_count = sum(
            1 for c in s["hand_cards"] + s["draw_pile_cards"] if c["is_upgraded"]
        )
        print(
            f"  {s['character_id']} deck={deck_size} (upgraded={upgraded_count}) "
            f"relics={len(s['relics'])} potions={len(s['potions'])} "
            f"vs {[e['monster_id'] for e in s['enemies']]} "
            f"(hp={[e['hp'] for e in s['enemies']]}) source={s['source']}"
        )
