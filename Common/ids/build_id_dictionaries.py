"""Builds canonical, version-aware ID dictionaries (cards/relics/potions/monsters/powers)
and a v108-vs-v109 diff report.

Why this exists: STS2_Data/json was extracted once from C:\\STS2_Decompiled (v108, build
v0.98.0-era). STS2_Emulator's imported Source tree (Sts2Emulator/Imported/Source) was
copied from C:\\STS2_Decompiled_v0109 (confirmed by matching .cs file counts under
Core.Models.Cards / Core.Models.Relics - v109 has 603/300, v108 has 600/298, emulator's
Imported/Source has 603/300). So the emulator's live ModelDb - the actual authority
ResolveCard/ResolveRelic/ResolveMonster match against - reflects v109, not v108. Any
ID dictionary meant to describe "what the emulator currently accepts" must be v109-based;
v108 is kept only to know what changed for older (e.g. runs-all-before-2026-06.json)
data provenance tracking.

Potions are extracted here too (STS2_Data's extractor does not cover
MegaCrit.Sts2.Core.Models.Potions - only Cards/Monsters/Powers/Relics/Encounters), reusing
the same Extractor.extract_models() machinery via import rather than reimplementing the
regex-based C# parsing.

Run: python build_id_dictionaries.py
Requires: C:\\STS2_RL\\Common\\ids\\v0109_raw already populated by running
  cd C:\\STS2_Data && python extract_static_data.py --source C:\\STS2_Decompiled_v0109 --output C:\\STS2_RL\\Common\\ids\\v0109_raw
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

STS2_DATA = Path(r"C:\STS2_Data")
V108_SOURCE = Path(r"C:\STS2_Decompiled")
V109_SOURCE = Path(r"C:\STS2_Decompiled_v0109")
V108_JSON = STS2_DATA / "json"
V109_RAW = Path(r"C:\STS2_RL\Common\ids\v0109_raw")
V109_JSON = V109_RAW / "json"
OUT_DIR = Path(r"C:\STS2_RL\Common\ids")
VERSIONING_DIR = Path(r"C:\STS2_RL\Common\versioning")

sys.path.insert(0, str(STS2_DATA))
import extract_static_data as extractor_mod  # noqa: E402


class PotionExtractor(extractor_mod.Extractor):
    """Adds the one base-class/category mapping extract_static_data.py doesn't cover
    (it only extracts Cards/Monsters/Powers/Relics/Encounters) - kept as a local
    subclass rather than editing the shared read-only extractor."""

    def category_for(self, base_name: str) -> str:
        if base_name == "PotionModel":
            return "potion"
        return super().category_for(base_name)


class TransitiveExtractor(PotionExtractor):
    """Fixes a real gap in extract_static_data.py's Extractor.is_model_base(): it only
    matches a class whose OWN declared base is exactly target_base (plus a few hardcoded
    one-off exceptions, e.g. DecimillipedeSegment for MonsterModel) - it does not walk
    indirect inheritance. Confirmed missed real example: STS2_Decompiled_v0109/
    MegaCrit.Sts2.Core.Models.Monsters/MysteriousKnight.cs declares
    `class MysteriousKnight : FlailKnight` (not `: MonsterModel` directly) - FlailKnight
    is itself presumably `: MonsterModel` (or further indirect), but the original
    extractor's single-hop check silently drops MysteriousKnight entirely, which is why
    MYSTERIOUS_KNIGHT was showing up as an `unsupported_id` in floor-state reconstruction
    despite being a real, resolvable v109 monster.

    Fix: for each (directory, target_base) pair extract_models() is asked to scan, first
    build a full `class_name -> declared_base` map for every class declaration in that
    directory (one pass, regardless of what base each one has), then walk that map
    transitively from an arbitrary class's declared base up to target_base. Classes whose
    base can't be resolved locally (e.g. a base defined in a different directory/
    namespace, or genuinely not a target_base subclass) correctly still don't match -
    this only ADDS coverage for indirect chains fully contained within one directory's
    .cs files, it never widens matching beyond what target_base actually means.
    Abstract/helper intermediate classes (e.g. FlailKnight itself, if never directly
    instantiated as its own monster) are still correctly flagged via the existing
    `abstract`-modifier detection in extract_models() - this class does not touch that
    logic, so entries_by_id()'s existing abstract/mock filtering keeps working unchanged.
    """

    def __init__(self, source: Path, output: Path):
        super().__init__(source, output)
        self._current_directory: "str | None" = None
        self._class_base_cache: dict[str, dict[str, str]] = {}

    def extract_models(self, directory_name: str, base_name: str, payload_fn):
        self._current_directory = directory_name
        return super().extract_models(directory_name, base_name, payload_fn)

    def _class_base_map(self, directory_name: str) -> dict[str, str]:
        if directory_name in self._class_base_cache:
            return self._class_base_cache[directory_name]
        mapping: dict[str, str] = {}
        directory = self.source / directory_name
        if directory.exists():
            pattern = extractor_mod.re.compile(
                r"public\s+(?:sealed\s+|abstract\s+|partial\s+)*class\s+(\w+)\s*:\s*(\w+)"
            )
            for path in sorted(directory.glob("*.cs")):
                text = path.read_text(encoding="utf-8-sig", errors="replace")
                for m in pattern.finditer(text):
                    mapping[m.group(1)] = m.group(2)
        self._class_base_cache[directory_name] = mapping
        return mapping

    def is_model_base(self, text: str, declared_base: str, target_base: str) -> bool:
        if super().is_model_base(text, declared_base, target_base):
            return True
        if self._current_directory is None:
            return False
        mapping = self._class_base_map(self._current_directory)
        current = declared_base
        seen: set[str] = set()
        while current in mapping and current not in seen:
            seen.add(current)
            current = mapping[current]
            if current == target_base or super().is_model_base(text, current, target_base):
                return True
        return False


def potion_payload(self, name: str, text: str, path: Path, loc) -> dict[str, Any]:
    return {
        "loc_keys": {
            "title": f"potions.{extractor_mod.slugify(name)}.title",
            "description": f"potions.{extractor_mod.slugify(name)}.description",
        },
        "rarity": self.enum_expr(text, "Rarity"),
        "potion_type": self.enum_expr(text, "PotionType"),
        "is_thrown": self.bool_expr(text, "IsThrown"),
    }


def extract_all_v109(source_root: Path) -> dict[str, list[dict[str, Any]]]:
    """Inline re-extraction of every category from v109 source using TransitiveExtractor,
    replacing the old dependency on a separately-run plain-Extractor CLI pass
    (Common/ids/v0109_raw/json/*.json) for cards/monsters/powers/relics, which lacked the
    indirect-inheritance fix above. v108 stays on STS2_Data's existing pre-extracted
    json (unchanged, historical-reference-only, not re-extracted here)."""
    ext = TransitiveExtractor(source_root, Path(r"C:\STS2_RL\Common\ids\_scratch_unused"))
    cards = ext.extract_models("MegaCrit.Sts2.Core.Models.Cards", "CardModel", ext.card_payload)
    monsters = ext.extract_models("MegaCrit.Sts2.Core.Models.Monsters", "MonsterModel", ext.monster_payload)
    powers = ext.extract_models("MegaCrit.Sts2.Core.Models.Powers", "PowerModel", ext.power_payload)
    relics = ext.extract_models("MegaCrit.Sts2.Core.Models.Relics", "RelicModel", ext.relic_payload)
    potions = ext.extract_models(
        "MegaCrit.Sts2.Core.Models.Potions", "PotionModel", lambda *a: potion_payload(ext, *a)
    )
    return {"cards": cards, "monsters": monsters, "powers": powers, "relics": relics, "potions": potions}


def extract_potions(source_root: Path) -> list[dict[str, Any]]:
    ext = PotionExtractor(source_root, Path(r"C:\STS2_RL\Common\ids\_scratch_unused"))
    return ext.extract_models(
        "MegaCrit.Sts2.Core.Models.Potions", "PotionModel", lambda *a: potion_payload(ext, *a)
    )


def load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def entries_by_id(records: list[dict]) -> dict[str, dict]:
    return {r["id"]["entry"]: r for r in records if not r.get("abstract") and not r.get("mock")}


def diff_category(v108: dict[str, dict], v109: dict[str, dict], changed_fields: list[str]) -> dict[str, Any]:
    ids108 = set(v108.keys())
    ids109 = set(v109.keys())
    added = sorted(ids109 - ids108)
    removed = sorted(ids108 - ids109)
    common = ids108 & ids109
    changed = []
    for entry_id in sorted(common):
        a, b = v108[entry_id], v109[entry_id]
        diffs = {}
        for field in changed_fields:
            va, vb = a.get(field), b.get(field)
            if va != vb:
                diffs[field] = {"v108": va, "v109": vb}
        if diffs:
            changed.append({"id": entry_id, "class_name": b.get("class_name"), "diffs": diffs})
    return {
        "v108_count": len(ids108),
        "v109_count": len(ids109),
        "added_in_v109": added,
        "removed_since_v108": removed,
        "changed": changed,
        "unchanged_count": len(common) - len(changed),
    }


def build_canonical(v108: dict[str, dict], v109: dict[str, dict], extra_fields: list[str]) -> dict[str, dict]:
    """v109-baseline canonical dict (matches what the live emulator's ModelDb accepts),
    each entry tagged with which extracted versions it's present in."""
    out: dict[str, dict] = {}
    for entry_id, rec in v109.items():
        out[entry_id] = {
            "class_name": rec.get("class_name"),
            "present_in": ["v108", "v109"] if entry_id in v108 else ["v109"],
            **{f: rec.get(f) for f in extra_fields},
        }
    for entry_id, rec in v108.items():
        if entry_id not in out:
            out[entry_id] = {
                "class_name": rec.get("class_name"),
                "present_in": ["v108"],
                **{f: rec.get(f) for f in extra_fields},
            }
    return out


def main() -> None:
    # v108 stays on STS2_Data's existing pre-extracted json (unchanged - historical
    # reference only). v109 is re-extracted inline via TransitiveExtractor (see its
    # docstring) instead of loaded from the old plain-Extractor v0109_raw/json output,
    # so indirectly-inherited classes (e.g. MysteriousKnight : FlailKnight : MonsterModel)
    # are no longer silently dropped.
    old_v109 = {
        "cards": entries_by_id(load_json(V109_JSON / "cards.json")),
        "monsters": entries_by_id(load_json(V109_JSON / "monsters.json")),
        "powers": entries_by_id(load_json(V109_JSON / "powers.json")),
        "relics": entries_by_id(load_json(V109_JSON / "relics.json")),
    }
    new_v109_raw = extract_all_v109(V109_SOURCE)
    cards109 = entries_by_id(new_v109_raw["cards"])
    monsters109 = entries_by_id(new_v109_raw["monsters"])
    powers109 = entries_by_id(new_v109_raw["powers"])
    relics109 = entries_by_id(new_v109_raw["relics"])
    potions109 = entries_by_id(new_v109_raw["potions"])

    transitive_fix_diff = {
        category: {
            "newly_resolved": sorted(set(new.keys()) - set(old_v109[category].keys())),
            "no_longer_resolved": sorted(set(old_v109[category].keys()) - set(new.keys())),
        }
        for category, new in [("cards", cards109), ("monsters", monsters109), ("powers", powers109), ("relics", relics109)]
    }
    VERSIONING_DIR.mkdir(parents=True, exist_ok=True)
    with (VERSIONING_DIR / "transitive_inheritance_fix_diff.json").open("w", encoding="utf-8") as f:
        json.dump(transitive_fix_diff, f, indent=2, ensure_ascii=False)
    print("Transitive-inheritance fix diff (new v109 extraction vs old v0109_raw):")
    for category, d in transitive_fix_diff.items():
        print(f"  {category}: newly_resolved={d['newly_resolved']} no_longer_resolved={d['no_longer_resolved']}")

    cards108 = entries_by_id(load_json(V108_JSON / "cards.json"))
    relics108 = entries_by_id(load_json(V108_JSON / "relics.json"))
    monsters108 = entries_by_id(load_json(V108_JSON / "monsters.json"))
    powers108 = entries_by_id(load_json(V108_JSON / "powers.json"))
    potions108 = entries_by_id(extract_potions(V108_SOURCE))

    report = {
        "generated_from": {
            "v108_source": str(V108_SOURCE),
            "v109_source": str(V109_SOURCE),
            "note": "v109 is the version STS2_Emulator's imported Source tree matches "
            "(verified by Core.Models.Cards/.Relics .cs file counts: v108=600/298, "
            "v109=603/300, emulator Imported/Source=603/300). Treat v109 as current.",
        },
        "cards": diff_category(cards108, cards109, ["energy_cost", "rarity", "type", "target_type"]),
        "relics": diff_category(relics108, relics109, ["rarity", "merchant_cost", "is_stackable"]),
        "potions": diff_category(potions108, potions109, ["rarity", "potion_type"]),
        "monsters": diff_category(monsters108, monsters109, ["hp"]),
        "powers": diff_category(powers108, powers109, ["stack_type", "allow_negative"]),
    }
    VERSIONING_DIR.mkdir(parents=True, exist_ok=True)
    with (VERSIONING_DIR / "id_mapping_v108_v109.json").open("w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)

    canonical = {
        "cards.json": build_canonical(cards108, cards109, ["energy_cost", "rarity", "type", "target_type"]),
        "relics.json": build_canonical(relics108, relics109, ["rarity", "merchant_cost", "is_stackable"]),
        "potions.json": build_canonical(potions108, potions109, ["rarity", "potion_type"]),
        "monsters.json": build_canonical(monsters108, monsters109, ["hp"]),
        "powers.json": build_canonical(powers108, powers109, ["stack_type", "allow_negative"]),
    }
    for filename, data in canonical.items():
        with (OUT_DIR / filename).open("w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False, sort_keys=True)

    print("Wrote:", VERSIONING_DIR / "id_mapping_v108_v109.json")
    for filename in canonical:
        print("Wrote:", OUT_DIR / filename, f"({len(canonical[filename])} entries)")
    print()
    for cat in ["cards", "relics", "potions", "monsters", "powers"]:
        d = report[cat]
        print(
            f"{cat}: v108={d['v108_count']} v109={d['v109_count']} "
            f"added={len(d['added_in_v109'])} removed={len(d['removed_since_v108'])} "
            f"changed={len(d['changed'])}"
        )


if __name__ == "__main__":
    main()
