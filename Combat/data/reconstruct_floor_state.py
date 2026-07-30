"""Reconstructs the player's state (deck/relics/potions/gold/HP) immediately BEFORE each
combat encounter in a real run, by sequentially replaying `map_point_history` - not just
using the run's final state (what scenario_from_runs.py did until now).

Discovery this module is built on: `map_point_history` (list[act_index] ->
list[point_index] -> {map_point_type, player_stats: [...], rooms: [...]}) carries, per
map point, a `player_stats[0]` object with `current_hp`/`max_hp` present for EVERY point
(confirmed: 100% coverage across a sample of 1335 v8 and 1130 v9 player_stats objects -
see reconstruct_floor_state_report.md for the full audit), plus itemized
gained/removed/transformed/upgraded card and relic events, potion pickup/use/discard
events, and gold deltas. The run-level `players[0]` summary this project used previously
(scenario_from_runs.py) has NONE of this - only the run's FINAL deck/relics/potions, no
HP at all. This module is what makes real per-floor reconstruction possible.

Core idea: fold over the flattened (act, floor) point sequence once, left to right,
maintaining a running {deck, relics, potions, gold, current_hp, max_hp} state. For every
point whose `rooms` include a combat room (room_type in {monster, elite, boss} - checked
per-room, since a point can hold >1 room, e.g. an event that triggers a fight), emit a
snapshot of the running state as-is (i.e. BEFORE this point's own deltas are applied) -
that snapshot is exactly "what the player had entering this fight". Then apply this
point's deltas and continue.

HP semantics: `current_hp`/`max_hp` in player_stats are ABSOLUTE values as of that point's
resolution (not deltas) - so "entering HP for point N" is simply the running state's
current_hp/max_hp as carried over from point N-1's resolution (already exact, no
arithmetic needed). Only the very first point of a run (nothing precedes it) has no such
predecessor - initialized from the character's known canonical StartingHp/no relics/no
gold/starting deck, which is a real game constant, not a guess (see CHARACTER_STARTING_HP
in scenario_from_runs.py).

Deck/relic/potion instance identity: cards_removed/cards_transformed reference
{id, floor_added_to_deck} - NOT further disambiguated when duplicates share both id and
floor_added_to_deck (e.g. two Strikes both added on floor 1, one later removed) - this
module resolves such a reference by removing/mutating the FIRST current match in
insertion order, an inherent, documented ambiguity in the source data itself (not
something a smarter algorithm here could resolve - the log doesn't carry true per-card
instance identity). upgraded_cards/downgraded_cards carry only a bare card id (no
floor_added_to_deck at all) - same principle, first unupgraded/upgraded match wins.

Every reconstructed encounter's deck/relic/gold/HP is validated where possible: the
FINAL point's running state, after the whole run is replayed, is compared against the
run's own top-level `players[0].deck`/`relics` (a genuine independent cross-check this
project did not have before either) - see `validate_run_reconstruction()`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterator

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Combat/

from slot_name_inference import ensure_inferred_slot_names
from mad_science_restore import reconcile_mad_science_state

RUNS_PATH = Path(r"C:\STS2_Data\runs-all-before-2026-06.json")

CHARACTER_STARTING_HP = {
    "IRONCLAD": 80,
    "SILENT": 70,
    "DEFECT": 75,
    "REGENT": 75,
    "NECROBINDER": 66,
}

# STS2_Decompiled_v0109/MegaCrit.Sts2.Core.Models.Characters/*.cs `StartingDeck`/
# `StartingRelics` - these are never recorded as a "gained" event in map_point_history
# (the player already has them from run creation, before point 0 of act 0) - the exact
# same category of gap CHARACTER_STARTING_HP already covers for HP, discovered here by
# validate_run_reconstruction() flagging BURNING_BLOOD missing from an otherwise-correct
# Ironclad reconstruction (see reconstruct_floor_state_report.md).
CHARACTER_STARTING_DECK = {
    "IRONCLAD": ["STRIKE_IRONCLAD"] * 5 + ["DEFEND_IRONCLAD"] * 4 + ["BASH"],
    "SILENT": ["STRIKE_SILENT"] * 5 + ["DEFEND_SILENT"] * 5 + ["NEUTRALIZE", "SURVIVOR"],
    "DEFECT": ["STRIKE_DEFECT"] * 4 + ["DEFEND_DEFECT"] * 4 + ["ZAP", "DUALCAST"],
    "REGENT": ["STRIKE_REGENT"] * 4 + ["DEFEND_REGENT"] * 4 + ["FALLING_STAR", "VENERATE"],
    "NECROBINDER": ["STRIKE_NECROBINDER"] * 4 + ["DEFEND_NECROBINDER"] * 4 + ["BODYGUARD", "UNLEASH"],
}
CHARACTER_STARTING_RELIC = {
    "IRONCLAD": "BURNING_BLOOD",
    "SILENT": "RING_OF_THE_SNAKE",
    "DEFECT": "CRACKED_CORE",
    "REGENT": "DIVINE_RIGHT",
    "NECROBINDER": "BOUND_PHYLACTERY",
}

COMBAT_ROOM_TYPES = {"monster", "elite", "boss"}


def strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


class ReplayState:
    """Mutable running state carried across one run's map_point_history replay."""

    def __init__(self, character_entry: str, ascension: "int | None" = None):
        self.character_entry = character_entry
        starting_hp = CHARACTER_STARTING_HP.get(character_entry)
        self.current_hp: "int | None" = starting_hp
        self.max_hp: "int | None" = starting_hp
        self.hp_source = "character_base_starting_hp" if starting_hp is not None else "unavailable_unknown_character"
        self.gold = 0
        self.deck: list[dict] = [
            {"id": card_id, "floor_added_to_deck": 1, "upgraded": False}
            for card_id in CHARACTER_STARTING_DECK.get(character_entry, [])
        ]
        self.relics: list[str] = (
            [CHARACTER_STARTING_RELIC[character_entry]] if character_entry in CHARACTER_STARTING_RELIC else []
        )
        self.potions: list[str] = []
        self.warnings: list[str] = []
        if character_entry not in CHARACTER_STARTING_DECK:
            self.warnings.append(f"NOTFOUND: unknown character '{character_entry}' - no starting deck/relic seed available")
        # Ascender's Bane: auto-granted at run start for Ascension 5-10 (matches
        # sts2-agent's card_reward_picker dataset builder's own documented rule, and
        # confirmed here independently - ASCENDERS_BANE was the single largest source of
        # deck-reconstruction mismatch before this seed was added, present in 6/7
        # mismatching runs in an early schema-v9 validation sample) - never recorded as
        # a map_point_history "gained" event, same category of gap as the starting
        # deck/relic above.
        if ascension is not None and 5 <= ascension <= 10:
            self.deck.append({"id": "ASCENDERS_BANE", "floor_added_to_deck": 1, "upgraded": False})

    def snapshot(self) -> dict:
        return {
            "player_hp": self.current_hp,
            "player_max_hp": self.max_hp,
            "hp_restore_source": self.hp_source,
            "gold": self.gold,
            "deck": [dict(c) for c in self.deck],
            "relics": list(self.relics),
            "potions": list(self.potions),
        }

    # -- deck instance resolution (see module docstring's identity-ambiguity note) --
    def _find_card_candidates(self, entry_id: str, floor_added_to_deck: "int | None", upgraded: "bool | None" = None) -> list:
        out = []
        for card in self.deck:
            if card["id"] != entry_id:
                continue
            if floor_added_to_deck is not None and card.get("floor_added_to_deck") != floor_added_to_deck:
                continue
            if upgraded is not None and card.get("upgraded") != upgraded:
                continue
            out.append(card)
        return out

    def add_card(self, card_ref: dict, event_choices: "list[dict] | None" = None) -> None:
        card = {
            "id": strip_prefix(card_ref["id"], "CARD."),
            "floor_added_to_deck": card_ref.get("floor_added_to_deck"),
            "upgraded": bool(card_ref.get("current_upgrade_level")),
        }
        state, status = reconcile_mad_science_state(card_ref, event_choices)
        if card["id"] == "MAD_SCIENCE":
            if state:
                card.update(state)
            else:
                self.warnings.append(f"{status.upper()}: MAD_SCIENCE added without reconstructable Tinker Time state")
        self.deck.append(card)

    def remove_card(self, card_ref: dict) -> None:
        """Ambiguity here (2+ candidates) is flagged as AMBIGUOUS only when candidates
        differ in `upgraded` state - that's the only case where picking "the wrong" one
        actually changes the resulting (id, upgraded) multiset. Removing any one of N
        interchangeable same-upgraded-state duplicates yields an identical result either
        way, so that case is NOT flagged (benign, not a real fidelity loss)."""
        entry_id = strip_prefix(card_ref["id"], "CARD.")
        floor = card_ref.get("floor_added_to_deck")
        candidates = self._find_card_candidates(entry_id, floor)
        if not candidates and floor is not None:
            candidates = self._find_card_candidates(entry_id, None)  # fall back: drop the floor pin
        if not candidates:
            self.warnings.append(f"NOTFOUND: cards_removed referenced {entry_id} with no matching instance in reconstructed deck")
            return
        if len({c["upgraded"] for c in candidates}) > 1:
            self.warnings.append(
                f"AMBIGUOUS: cards_removed referenced {entry_id} with {len(candidates)} candidate instances "
                f"of DIFFERING upgraded state - source log doesn't say which was removed, so the resulting "
                f"upgraded/unupgraded split for this card id may not match the true history; picked the "
                f"first deterministically"
            )
        self.deck.remove(candidates[0])

    def upgrade_card(self, entry_id_raw: str) -> None:
        """Ambiguity here is always benign (see remove_card's docstring) - every
        candidate already shares upgraded=False, so upgrading any one of them yields the
        same resulting multiset; not flagged."""
        entry_id = strip_prefix(entry_id_raw, "CARD.")
        candidates = self._find_card_candidates(entry_id, None, upgraded=False)
        if not candidates:
            self.warnings.append(f"NOTFOUND: upgraded_cards referenced {entry_id} with no unupgraded instance found")
            return
        candidates[0]["upgraded"] = True

    def downgrade_card(self, entry_id_raw: str) -> None:
        """See upgrade_card's docstring - same benign-ambiguity reasoning, mirrored."""
        entry_id = strip_prefix(entry_id_raw, "CARD.")
        candidates = self._find_card_candidates(entry_id, None, upgraded=True)
        if not candidates:
            self.warnings.append(f"NOTFOUND: downgraded_cards referenced {entry_id} with no upgraded instance found")
            return
        candidates[0]["upgraded"] = False

    def apply_point_stats(self, ps: dict) -> None:
        # bought_relics/bought_potions/bought_colorless are NOT independent gain events -
        # verified redundant with relic_choices/potion_choices/cards_gained respectively
        # (0 mismatches across a 1000-run/1217-relic/448-potion/345-card sample: every
        # shop purchase is also recorded via the picked-choice fields). Double-processing
        # them was this module's first confirmed over-counting bug (found via
        # validate_run_reconstruction flagging e.g. MASTER_OF_STRATEGY appearing twice in
        # a reconstructed deck that should hold it once - see
        # reconstruct_floor_state_report.md) - intentionally NOT applied below.
        event_choices = ps.get("event_choices")
        for c in ps.get("cards_gained") or []:
            self.add_card(c, event_choices=event_choices)
        for c in ps.get("cards_removed") or []:
            self.remove_card(c)
        for t in ps.get("cards_transformed") or []:
            self.remove_card(t["original_card"])
            self.add_card(t["final_card"], event_choices=event_choices)
        for entry_id in ps.get("upgraded_cards") or []:
            self.upgrade_card(entry_id)
        for entry_id in ps.get("downgraded_cards") or []:
            self.downgrade_card(entry_id)

        # Idempotent add, NOT append-always: validate_run_reconstruction() against real
        # final players[0].relics caught LAVA_LAMP/HAPPY_FLOWER/PANTOGRAPH each showing
        # up twice in a naive-append reconstruction of one real run while the run's own
        # final relics list had exactly one entry apiece, with zero relics_removed events
        # logged in between - i.e. a relic already held that gets "gained" again some
        # other way (e.g. an in-run mechanic granting a duplicate) does NOT get a second
        # list entry in the real engine's own player.Relics (most likely: it stacks on
        # the existing entry, which this log format doesn't expose a stack-count delta
        # for) - so a second "gained" event for an already-held relic id is a no-op here,
        # matching observed real behavior rather than assumed semantics.
        for rc in ps.get("relic_choices") or []:
            if rc.get("was_picked"):
                entry_id = strip_prefix(rc["choice"], "RELIC.")
                if entry_id not in self.relics:
                    self.relics.append(entry_id)
        for r in ps.get("relics_removed") or []:
            entry_id = strip_prefix(r, "RELIC.")
            if entry_id in self.relics:
                self.relics.remove(entry_id)
            else:
                self.warnings.append(f"NOTFOUND: relics_removed referenced {entry_id} not currently held")

        for pc in ps.get("potion_choices") or []:
            if pc.get("was_picked"):
                self.potions.append(strip_prefix(pc["choice"], "POTION."))
        for p in ps.get("potion_used") or []:
            entry_id = strip_prefix(p, "POTION.")
            if entry_id in self.potions:
                self.potions.remove(entry_id)
            else:
                self.warnings.append(f"NOTFOUND: potion_used referenced {entry_id} not currently held")
        for p in ps.get("potion_discarded") or []:
            entry_id = strip_prefix(p, "POTION.")
            if entry_id in self.potions:
                self.potions.remove(entry_id)
            else:
                self.warnings.append(f"NOTFOUND: potion_discarded referenced {entry_id} not currently held")

        if "current_gold" in ps:
            self.gold = ps["current_gold"]
        if "current_hp" in ps and "max_hp" in ps:
            self.current_hp = ps["current_hp"]
            self.max_hp = ps["max_hp"]
            self.hp_source = "map_point_history.player_stats.current_hp/max_hp"
        else:
            self.warnings.append("NOTFOUND: player_stats point missing current_hp/max_hp - HP status degrades to unavailable from here on")
            self.current_hp = None
            self.max_hp = None
            self.hp_source = "unavailable_missing_field"


def reconstruct_encounters_for_run(run: dict) -> list[dict]:
    """Returns one dict per combat room encountered in `run`, each carrying the
    player's reconstructed state immediately before that fight. See module docstring."""
    players = run.get("players") or []
    if not players:
        return []
    character_entry = strip_prefix(players[0].get("character", ""), "CHARACTER.")
    state = ReplayState(character_entry, ascension=run.get("ascension"))
    acts = run.get("acts") or []
    results: list[dict] = []
    map_point_index = 0

    for act_index, act_points in enumerate(run.get("map_point_history") or []):
        for floor_index, point in enumerate(act_points):
            rooms = point.get("rooms") or []
            ps_list = point.get("player_stats") or []
            ps = ps_list[0] if ps_list else {}
            combat_rooms = [r for r in rooms if r.get("room_type") in COMBAT_ROOM_TYPES]

            for room in combat_rooms:
                snap = state.snapshot()
                hp_status = _classify_hp_status(snap, state.warnings)
                results.append(
                    {
                        "source_run_id": run.get("_serverId"),
                        "map_point_index": map_point_index,
                        "build_id": run.get("build_id"),
                        "schema_version": run.get("schema_version"),
                        "act_index": act_index,
                        "act": acts[act_index] if act_index < len(acts) else None,
                        "floor": floor_index + 1,
                        "encounter_id": room.get("model_id"),
                        "monster_ids": [strip_prefix(m, "MONSTER.") for m in (room.get("monster_ids") or [])],
                        "pool_type": room.get("room_type"),
                        "character": character_entry,
                        "ascension": run.get("ascension"),
                        "deck": snap["deck"],
                        "relics": snap["relics"],
                        "potions": snap["potions"],
                        "gold": snap["gold"],
                        "player_hp": snap["player_hp"],
                        "player_max_hp": snap["player_max_hp"],
                        "hp_restore_status": hp_status,
                        "hp_restore_source": snap["hp_restore_source"],
                        "state_restore_status": "reconstructed",
                        "warnings": list(state.warnings),
                    }
                )

            state.apply_point_stats(ps)
            map_point_index += 1

    return results


def _classify_hp_status(snap: dict, warnings_so_far: list[str]) -> str:
    if snap["player_hp"] is None or snap["player_max_hp"] is None:
        return "unavailable"
    if snap["player_hp"] > snap["player_max_hp"] or snap["player_hp"] < 0 or snap["player_max_hp"] <= 0:
        return "inconsistent"
    if snap["hp_restore_source"] == "character_base_starting_hp":
        return "exact"
    if snap["hp_restore_source"] == "map_point_history.player_stats.current_hp/max_hp":
        return "exact"
    return "unavailable"


IDS_DIR = Path(r"C:\STS2_RL\Common\ids")
_canonical_ids_cache: "dict[str, set] | None" = None


def _normalize_id(entry_id: str) -> str:
    return entry_id.replace("_", "").lower()


def _load_canonical_ids() -> dict:
    """Lazily loads+caches normalized id sets from Common/ids/*.json (the v109-baseline
    canonical dictionaries built by Common/ids/build_id_dictionaries.py) - used to flag
    `unsupported_id` offline, without needing a live Emulator call just to discover a
    card/relic/potion/monster id GameInstance.ResolveCard/ResolveRelic/etc. would reject."""
    global _canonical_ids_cache
    if _canonical_ids_cache is not None:
        return _canonical_ids_cache
    out = {}
    for category, filename in [("cards", "cards.json"), ("relics", "relics.json"), ("potions", "potions.json"), ("monsters", "monsters.json")]:
        with (IDS_DIR / filename).open(encoding="utf-8") as f:
            data = json.load(f)
        out[category] = {_normalize_id(k) for k in data.keys()}
    _canonical_ids_cache = out
    return out


def find_unsupported_ids(encounter: dict) -> list[str]:
    """Returns every card/relic/potion/monster id in `encounter` that isn't in the
    v109 canonical dictionaries - these are known to make GameInstance.ResolveCard/
    ResolveRelic/ResolvePotion/ResolveMonster throw ArgumentException (confirmed
    pattern: e.g. FOLLOW_THROUGH/GRAPPLE from the original dataset audit - cards that
    existed in whatever build produced this run's data but were removed by v109)."""
    ids = _load_canonical_ids()
    bad = []
    for c in encounter["deck"]:
        if _normalize_id(c["id"]) not in ids["cards"]:
            bad.append(f"card:{c['id']}")
    for r in encounter["relics"]:
        if _normalize_id(r) not in ids["relics"]:
            bad.append(f"relic:{r}")
    for p in encounter["potions"]:
        if _normalize_id(p) not in ids["potions"]:
            bad.append(f"potion:{p}")
    for m in encounter["monster_ids"]:
        if _normalize_id(m) not in ids["monsters"]:
            bad.append(f"monster:{m}")
    return bad


def classify_restore_status(encounter: dict) -> tuple[str, list[str]]:
    """Combined offline reconstruction-quality classification (does NOT touch the
    Emulator - see validate_reconstructed_scenarios_live.py / reconstruct_all_runs.py
    for that separate stage). Priority (worst wins): history_inconsistent >
    unsupported_id > ambiguous_upgrade > exact. Returns (status, unsupported_id_list)."""
    if (
        encounter["hp_restore_status"] in ("inconsistent", "unavailable")
        or any(w.startswith("NOTFOUND:") for w in encounter["warnings"])
    ):
        return "history_inconsistent", []
    unsupported = find_unsupported_ids(encounter)
    if unsupported:
        return "unsupported_id", unsupported
    if any(w.startswith("AMBIGUOUS:") for w in encounter["warnings"]):
        return "ambiguous_upgrade", []
    return "exact", []


def validate_run_reconstruction(run: dict, final_state: dict) -> dict:
    """Compares the fully-replayed final state against the run's own top-level
    players[0].deck/relics (an independent cross-check, not available before this
    module existed - see module docstring)."""
    players = run.get("players") or []
    if not players:
        return {"comparable": False}
    real_deck = players[0].get("deck") or []
    real_relics = players[0].get("relics") or []

    def deck_multiset(cards: list[dict], from_real: bool) -> dict:
        out: dict = {}
        for c in cards:
            if from_real:
                key = (strip_prefix(c["id"], "CARD."), bool(c.get("current_upgrade_level")))
            else:
                key = (c["id"], c["upgraded"])
            out[key] = out.get(key, 0) + 1
        return out

    reconstructed_deck_ms = deck_multiset(final_state["deck"], from_real=False)
    real_deck_ms = deck_multiset(real_deck, from_real=True)
    deck_matches = reconstructed_deck_ms == real_deck_ms

    real_relic_ms: dict = {}
    for r in real_relics:
        entry_id = strip_prefix(r["id"], "RELIC.")
        real_relic_ms[entry_id] = real_relic_ms.get(entry_id, 0) + 1
    recon_relic_ms: dict = {}
    for r in final_state["relics"]:
        recon_relic_ms[r] = recon_relic_ms.get(r, 0) + 1
    relics_match = recon_relic_ms == real_relic_ms

    return {
        "comparable": True,
        "deck_matches_final_state": deck_matches,
        "deck_size_reconstructed": sum(reconstructed_deck_ms.values()),
        "deck_size_real": sum(real_deck_ms.values()),
        "relics_match_final_state": relics_match,
        "relics_reconstructed": sorted(recon_relic_ms.keys()),
        "relics_real": sorted(real_relic_ms.keys()),
    }


def encounter_to_scenario_spec(encounter: dict, monster_hp: dict[str, int], rng) -> dict:
    """Converts one reconstruct_encounters_for_run() entry into a
    battle_emulator.py-compatible scenario spec (see
    Common/schemas/combat_scenario_input_schema.json) - the real-floor-state analogue of
    scenario_from_runs.py's scenario_from_run(), which only had final-run state to work
    with. Still-open gaps versus the true historical fight (same as scenario_from_runs.py,
    for the same Emulator-API-limitation reasons): monster HP uses the canonical
    ascension-10 band (Common/ids/monsters.json), not scaled to `encounter["ascension"]";
    hand/draw pile SPLIT (which 5 of the deck's cards were in hand at encounter start) is
    not recorded anywhere in the source data and is drawn via `rng`, not reconstructed;
    potion slot assignment is insertion order (real slot indices aren't in the log)."""
    deck = encounter["deck"]
    shuffled = deck[:]
    rng.shuffle(shuffled)
    hand, draw_pile = shuffled[:5], shuffled[5:]
    spec = {
        "character_id": encounter["character"],
        "player_hp": encounter["player_hp"],
        "player_max_hp": encounter["player_max_hp"],
        "hand_cards": [
            {
                "card_id": c["id"],
                "is_upgraded": c["upgraded"],
                **({"tinker_time_type": c["tinker_time_type"], "tinker_time_rider": c["tinker_time_rider"]} if c.get("tinker_time_type") and c.get("tinker_time_rider") else {}),
            }
            for c in hand
        ],
        "draw_pile_cards": [
            {
                "card_id": c["id"],
                "is_upgraded": c["upgraded"],
                **({"tinker_time_type": c["tinker_time_type"], "tinker_time_rider": c["tinker_time_rider"]} if c.get("tinker_time_type") and c.get("tinker_time_rider") else {}),
            }
            for c in draw_pile
        ],
        "discard_pile_cards": [],
        "exhaust_pile_cards": [],
        "player_powers": [],
        "relics": encounter["relics"],
        "potions": [{"slot": i, "potion_id": p} for i, p in enumerate(encounter["potions"])],
        "stars": encounter.get("stars"),
        "seed": rng.randint(1, 2_000_000_000),
        "enemies": [{"monster_id": m, "hp": monster_hp.get(m, 50)} for m in encounter["monster_ids"]],
        "source": {
            "server_id": encounter["source_run_id"],
            "build_id": encounter["build_id"],
            "ascension": encounter["ascension"],
            "act": encounter["act"],
            "floor": encounter["floor"],
            "encounter": encounter["encounter_id"],
            # .get() rather than direct indexing: reconstruct_all_runs.py's saved
            # floor_states records use a single combined "restore_status" field
            # (classify_restore_status()'s output) instead of these two - both forms
            # call this function, so accept either.
            "hp_restore_status": encounter.get("hp_restore_status", encounter.get("restore_status")),
            "state_restore_status": encounter.get("state_restore_status", encounter.get("restore_status")),
        },
    }
    return ensure_inferred_slot_names(spec)


def load_runs(path: Path = RUNS_PATH, schema_version: "int | None" = None, limit: "int | None" = None) -> Iterator[dict]:
    count = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if schema_version is not None and rec.get("schema_version") != schema_version:
                continue
            yield rec
            count += 1
            if limit is not None and count >= limit:
                return


if __name__ == "__main__":
    for run in load_runs(limit=1):
        encounters = reconstruct_encounters_for_run(run)
        print(f"run {run.get('_serverId')} schema_v{run.get('schema_version')}: {len(encounters)} combat encounters found")
        for e in encounters[:3]:
            print(
                f"  act={e['act']} floor={e['floor']} encounter={e['encounter_id']} "
                f"deck_size={len(e['deck'])} relics={len(e['relics'])} potions={len(e['potions'])} "
                f"hp={e['player_hp']}/{e['player_max_hp']} status={e['hp_restore_status']} warnings={len(e['warnings'])}"
            )
        if encounters:
            final = ReplayState(encounters[0]["character"], ascension=run.get("ascension"))
            # Re-run to get the true final state (encounters list only has pre-combat snapshots)
            for act_points in run.get("map_point_history") or []:
                for point in act_points:
                    ps_list = point.get("player_stats") or []
                    if ps_list:
                        final.apply_point_stats(ps_list[0])
            validation = validate_run_reconstruction(run, final.snapshot())
            print("validation:", json.dumps(validation, indent=2, ensure_ascii=False))
