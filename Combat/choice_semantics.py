"""choice_semantics.py: loads Common/schemas/choice_semantics_schema.json +
choice_semantics_lookup.v1.json and resolves one pendingChoice's real semantics against
Emulator-reported facts (originEntityType/originEntityId/sourceZone/choiceOperation/
destinationZone - added to Observation.State.pendingChoice and to choice_card/
choice_skip/choice_confirm LegalActions.Parameters by Emulator commits 12df954/0d16130,
verified live against the running 0d16130 DLL through Combat's own to_plain() path
before this file was written).

LOGGING/ANALYSIS LAYER ONLY. Non-negotiable invariant: nothing in this file may ever
change which action gets chosen. choice_card/choice_skip/choice_confirm decisions are
already unconditionally routed to HeuristicAgent by policy_agent.py, independent of
anything this module resolves (a lookup miss, an ambiguous match, or a corrupt/missing
lookup file all degrade to operationMode="unknown" and change nothing about that
routing - see ChoiceSemanticsTable.resolve()'s load-error path).

Classification (this task's 分類仕様):
    operationMode="normalized"  - a shared, card/relic/power/potion-agnostic pile-move
                                   operation (discard/exhaust/upgrade/retrieve_to_hand/
                                   ...) - see NORMALIZABLE_OPERATIONS below.
    operationMode="passthrough" - the operation is real and known, but its actual effect
                                   is entity-specific and would lose meaning if merged
                                   into the generic vocabulary (GUARDS' transform to one
                                   specific card, NIGHTMARE's power-association, SNAP's
                                   in-place buff, SCROLL_BOXES' bundle-select, ...) -
                                   carries `exceptionEntityKey` ("<type>:<id>") instead.
    operationMode="unknown"     - genuinely unresolved. NEVER guessed - this module has
                                   exactly one small hardcoded set (NORMALIZABLE_OPERATIONS,
                                   the shared-vocabulary member list) and zero per-card
                                   `if` branches; every entity-specific fact lives in
                                   choice_semantics_lookup.v1.json, not in this file.

Priority order actually implemented in resolve() (this task's 正規化原則、Stage B後の
修正指示 3節で更新):
    1. A CHOICE_TYPE_RULES entry keyed on pendingChoice.choiceType (currently just
       GamblingChipDiscard) - checked FIRST, ahead of Emulator's own choiceOperation,
       and never overridden by anything below even if Emulator starts reporting a
       concrete operation for this choiceType in some future build. See
       CHOICE_TYPE_RULES's own docstring for why GAMBLING_CHIP needed this instead of
       being folded into the generic "discard" vocabulary.
    2. Emulator's own pendingChoice.choiceOperation, when concretely non-null/non-
       "unknown" - trusted as-is, NEVER overridden by the lookup table even if a lookup
       row also exists for the same entity (a row here, when present, only adds
       provenance - `semanticSource` - never changes the value used).
    3. A unique lookup-table row keyed by (normalizedOriginEntityType, originEntityId),
       when Emulator's own operation stayed "unknown"/null - the `hardcoded_entity_rule`
       tier, entity-specific rules the Choice Semantics role read directly from
       decompiled source (Common/schemas/choice_semantics_lookup.v1.json).
       `normalizedOriginEntityType` (see ORIGIN_TYPE_ALIASES below) is used here instead
       of the raw `originEntityType` so potion-triggered choices - whose raw
       originEntityType is a PascalCase C# class name (e.g. "ColorlessPotion"), not the
       schema's documented lowercase "potion" - can still match the lookup table's
       potion rows. The RAW value is never altered or discarded; both are logged.
    4. Everything else - unknown, with a specific `lookupStatus` explaining why
       (no_origin / miss / ambiguous_match / load_error).
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

SCHEMA_PATH = Path(r"C:\STS2_RL\Common\schemas\choice_semantics_schema.json")
LOOKUP_PATH = Path(r"C:\STS2_RL\Common\schemas\choice_semantics_lookup.v1.json")
ORIGIN_TYPE_ALIASES_PATH = Path(r"C:\STS2_RL\Common\schemas\choice_semantics_origin_type_aliases.v1.json")

CANONICAL_ORIGIN_TYPES = frozenset({"card", "relic", "power", "potion", "monster"})

# choiceType-keyed special rules, checked BEFORE origin/operation resolution (priority
# tier 1 - see module docstring). Each entry is a small, explicit, reviewed exception -
# NOT a per-card `if`, and NOT something this module invents on its own: GAMBLING_CHIP
# is here because Stage B's empirical probe (Combat/evaluation/online_eval/
# probe_gambling_chip_and_potions.py) showed its real effect (discard picked cards, then
# draw that many replacements) is close to but NOT the same as generic "discard" - the
# card-selection criterion differs (GAMBLING_CHIP's own choice is "which cards to KEEP
# drawing fresh copies of" framed as a discard-pile move, not a plain "send these to
# discard and stop") and the choiceType is a stable, unique identifier for it, unlike
# origin (which the same probe showed is either null or - after turn 1 - sometimes leaks
# a stale, unrelated potion's origin; see valid_origin below and the Stage B report's
# "疑わしいorigin漏洩" finding). Extending this dict for a newly-confirmed choiceType is
# a deliberate, reviewed decision each time, matching how choice_semantics_lookup.v1.json
# entries are added - not a mechanism for ad-hoc guessing.
CHOICE_TYPE_RULES: dict[str, dict[str, Any]] = {
    "GamblingChipDiscard": {
        "operation_mode": "passthrough",
        "exception_entity_key": "relic:GAMBLING_CHIP",
        "valid_origin": ("relic", "GAMBLING_CHIP"),
    },
}

REQUIRED_ENTRY_FIELDS = (
    "schema_version",
    "origin_entity_type",
    "origin_entity_id",
    "source_zone",
    "normalized_choice_operation",
    "destination_zone",
    "semantic_confidence",
    "semantic_source",
    "evidence",
)

# The only per-operation classification rule in this file - membership only, never an
# entity id. Anything the lookup table reports that is NOT in this set is, by
# construction, entity-specific (passthrough) - see choice_semantics_analysis_report.md
# section 1's 19-value enum for the full picture this set is drawn from.
NORMALIZABLE_OPERATIONS = frozenset(
    {
        "discard",
        "exhaust",
        "upgrade",
        "transform",
        "enchant",
        "remove",
        "retrieve_to_hand",
        "retrieve_to_hand_free",
        "retrieve_to_draw_pile_top",
        "return_to_draw_pile_top",
        "clone_to_hand",
        "add_generated_to_hand",
        "add_generated_to_deck",
    }
)


def _sha256_file(path: Path) -> "str | None":
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        return None


def _resolved(
    *,
    operation_mode: str,
    normalized_op: "str | None",
    exception_key: "str | None",
    semantic_source: str,
    lookup_status: str,
    matched_rule_id: "str | None",
    normalized_origin_entity_type: "str | None" = None,
    origin_validation_status: "str | None" = None,
) -> dict[str, Any]:
    return {
        "operationMode": operation_mode,
        "normalizedChoiceOperation": normalized_op,
        "exceptionEntityKey": exception_key,
        "semanticSource": semantic_source,
        "lookupStatus": lookup_status,
        "matchedRuleId": matched_rule_id,
        "normalizedOriginEntityType": normalized_origin_entity_type,
        # Only meaningful for choiceType rules that declare a `valid_origin` (currently
        # just GamblingChipDiscard) - None for every other decision, not "not
        # applicable" spelled out, to keep the common case's log rows uncluttered.
        "originValidationStatus": origin_validation_status,
    }


class ChoiceSemanticsTable:
    """Loaded exactly once per process (construct a single instance at startup, same
    load-once contract as PolicyAgent/ValueDetermination) - call .resolve() per choice
    decision. Never raises from .resolve(): any problem (missing file, malformed JSON,
    schema_ref mismatch, missing required fields, duplicate keys) is captured once at
    construction time into `self.load_error`, after which every .resolve() call
    degrades to operationMode="unknown"/lookupStatus="load_error" - see module
    docstring's invariant."""

    def __init__(
        self,
        schema_path: Path = SCHEMA_PATH,
        lookup_path: Path = LOOKUP_PATH,
        origin_aliases_path: Path = ORIGIN_TYPE_ALIASES_PATH,
    ) -> None:
        self.schema_path = schema_path
        self.lookup_path = lookup_path
        self.origin_aliases_path = origin_aliases_path
        self.load_error: "str | None" = None
        self.schema: "dict | None" = None
        self.table_version: "str | None" = None
        self.schema_sha256: "str | None" = None
        self.lookup_sha256: "str | None" = None
        self.entry_count = 0
        self._by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)

        # Origin-type-alias loading is independent of the main lookup table (a corrupt/
        # missing alias file only degrades normalizedOriginEntityType to identity/
        # "unknown" - the operation lookup for card/relic/power entities, which never
        # needed alias translation, keeps working). Tracked separately from
        # self.load_error on purpose - see this class's fine-grained-degradation note.
        self.origin_aliases_error: "str | None" = None
        self.origin_aliases_sha256: "str | None" = None
        self.origin_aliases_version: "str | None" = None
        self._origin_aliases: dict[tuple[str, str], str] = {}
        try:
            alias_payload = json.loads(origin_aliases_path.read_text(encoding="utf-8"))
            self.origin_aliases_sha256 = _sha256_file(origin_aliases_path)
            self.origin_aliases_version = alias_payload.get("table_version")
            for entry in alias_payload.get("entries") or []:
                key = (entry["raw_origin_entity_type"], entry["origin_entity_id"])
                self._origin_aliases[key] = entry["normalized_origin_entity_type"]
        except Exception as exc:  # noqa: BLE001
            self.origin_aliases_error = f"{type(exc).__name__}: {exc}"
            self._origin_aliases = {}

        try:
            self.schema = json.loads(schema_path.read_text(encoding="utf-8"))
            self.schema_sha256 = _sha256_file(schema_path)
            payload = json.loads(lookup_path.read_text(encoding="utf-8"))
            self.lookup_sha256 = _sha256_file(lookup_path)

            declared_ref = payload.get("schema_ref")
            actual_id = self.schema.get("$id")
            if declared_ref != actual_id:
                raise ValueError(f"lookup schema_ref {declared_ref!r} does not match schema $id {actual_id!r}")

            self.table_version = payload.get("table_version")
            entries = payload.get("entries") or []
            for entry in entries:
                missing = [f for f in REQUIRED_ENTRY_FIELDS if f not in entry]
                if missing:
                    raise ValueError(
                        f"lookup entry {entry.get('origin_entity_id')!r} missing required fields {missing}"
                    )
                self.entry_count += 1
                origin_type = entry.get("origin_entity_type")
                origin_id = entry.get("origin_entity_id")
                if origin_type is None:
                    continue  # __GENERIC_*__ / __EVENT_*__ placeholder rows - documentation only, not a real key
                self._by_key[(origin_type, origin_id)].append(entry)
        except Exception as exc:  # noqa: BLE001
            self.load_error = f"{type(exc).__name__}: {exc}"
            self.schema = None
            self._by_key = defaultdict(list)

    def _normalize_origin_type(self, origin_type: "str | None", origin_id: "str | None") -> "str | None":
        """Never guesses: only the exact (raw_origin_entity_type, origin_entity_id)
        pairs explicitly enumerated in choice_semantics_origin_type_aliases.v1.json are
        translated. A raw type that already IS one of the schema's canonical lowercase
        values passes through unchanged (the overwhelming majority - card/relic/power).
        Anything else, registered or not, that doesn't match becomes "unknown" - NOT a
        generic suffix/prefix string match against unregistered classes."""
        if origin_type is None:
            return None
        if origin_type in CANONICAL_ORIGIN_TYPES:
            return origin_type
        return self._origin_aliases.get((origin_type, origin_id), "unknown")

    @property
    def loaded_ok(self) -> bool:
        return self.load_error is None

    def provenance(self) -> dict[str, Any]:
        return {
            "lookupVersion": self.table_version,
            "lookupSha256": self.lookup_sha256,
            "schemaSha256": self.schema_sha256,
            "schemaPath": str(self.schema_path),
            "lookupPath": str(self.lookup_path),
            "entryCount": self.entry_count,
            "loadError": self.load_error,
            "originAliasesVersion": self.origin_aliases_version,
            "originAliasesSha256": self.origin_aliases_sha256,
            "originAliasesPath": str(self.origin_aliases_path),
            "originAliasesLoadError": self.origin_aliases_error,
        }

    @staticmethod
    def _validate_origin(valid_origin: tuple[str, str], origin_type: "str | None", origin_id: "str | None") -> str:
        """valid / missing / suspected_context_leak - see CHOICE_TYPE_RULES's docstring
        and this task's Stage-B-follow-up instructions section 1. Computed but never
        allowed to change the choiceType rule's own operationMode/exceptionEntityKey -
        callers only use this for logging/analysis, matching the "origin異常があっても
        passthrough結果は変更しない" requirement."""
        if origin_type is None and origin_id is None:
            return "missing"
        if (origin_type, origin_id) == valid_origin:
            return "valid"
        return "suspected_context_leak"

    def resolve(self, pending_choice: "dict | None") -> dict[str, Any]:
        """Returns {emulator_fact, resolved} for one choice decision's `pendingChoice`
        dict (the same dict already present on BattleState.engine_state - no separate
        Emulator call needed). `pending_choice=None`/`{}` resolves to unknown/no_origin,
        same as a normal miss - callers do not need to special-case a missing choice."""
        pending_choice = pending_choice or {}
        emulator_fact = {
            "originEntityType": pending_choice.get("originEntityType"),
            "originEntityId": pending_choice.get("originEntityId"),
            "sourceZone": pending_choice.get("sourceZone"),
            "choiceOperation": pending_choice.get("choiceOperation"),
            "destinationZone": pending_choice.get("destinationZone"),
            "choiceType": pending_choice.get("choiceType"),
            "remainingSelectCount": pending_choice.get("remainingSelectCount"),
        }

        if not self.loaded_ok:
            return {
                "emulator_fact": emulator_fact,
                "resolved": _resolved(
                    operation_mode="unknown",
                    normalized_op=None,
                    exception_key=None,
                    semantic_source="unknown",
                    lookup_status="load_error",
                    matched_rule_id=None,
                ),
            }

        origin_type = emulator_fact["originEntityType"]
        origin_id = emulator_fact["originEntityId"]
        normalized_origin_type = self._normalize_origin_type(origin_type, origin_id)
        live_operation = emulator_fact["choiceOperation"]
        live_operation_known = live_operation not in (None, "unknown")

        # Tier 1 (highest priority, per this task's Stage-B-follow-up 正規化原則 update) -
        # a choiceType-keyed special rule, checked BEFORE Emulator's own choiceOperation
        # and never overridden by it. raw origin is preserved unchanged in emulator_fact
        # regardless of what originValidationStatus comes out as.
        choice_type = emulator_fact["choiceType"]
        rule = CHOICE_TYPE_RULES.get(choice_type)
        if rule is not None:
            origin_status = self._validate_origin(rule["valid_origin"], origin_type, origin_id)
            return {
                "emulator_fact": emulator_fact,
                "resolved": _resolved(
                    operation_mode=rule["operation_mode"],
                    normalized_op=None,
                    exception_key=rule["exception_entity_key"],
                    semantic_source="hardcoded_entity_rule",
                    lookup_status="resolved_choice_type_rule",
                    matched_rule_id=choice_type,
                    normalized_origin_entity_type=normalized_origin_type,
                    origin_validation_status=origin_status,
                ),
            }

        matches = (
            self._by_key.get((normalized_origin_type, origin_id), [])
            if (normalized_origin_type and origin_id)
            else []
        )

        if live_operation_known:
            # Tier 2 - trust Emulator's own concrete value unconditionally, regardless
            # of whether a lookup row also exists (never overridden - 正規化原則).
            if len(matches) == 1:
                semantic_source = matches[0]["semantic_source"]
                matched_rule_id = matches[0]["origin_entity_id"]
                lookup_status = "resolved_emulator_fact_confirmed_by_lookup"
            elif len(matches) > 1:
                semantic_source = "emulator_fact"
                matched_rule_id = None
                lookup_status = "resolved_emulator_fact_ambiguous_lookup"
            else:
                semantic_source = "emulator_fact"
                matched_rule_id = None
                lookup_status = "resolved_emulator_fact" if (origin_type and origin_id) else "resolved_emulator_fact_no_origin"
            return {
                "emulator_fact": emulator_fact,
                "resolved": _resolved(
                    operation_mode="normalized",
                    normalized_op=live_operation,
                    exception_key=None,
                    semantic_source=semantic_source,
                    lookup_status=lookup_status,
                    matched_rule_id=matched_rule_id,
                    normalized_origin_entity_type=normalized_origin_type,
                ),
            }

        if not origin_type or not origin_id:
            return {
                "emulator_fact": emulator_fact,
                "resolved": _resolved(
                    operation_mode="unknown",
                    normalized_op=None,
                    exception_key=None,
                    semantic_source="unknown",
                    lookup_status="no_origin",
                    matched_rule_id=None,
                    normalized_origin_entity_type=normalized_origin_type,
                ),
            }

        if len(matches) > 1:
            return {
                "emulator_fact": emulator_fact,
                "resolved": _resolved(
                    operation_mode="unknown",
                    normalized_op=None,
                    exception_key=None,
                    semantic_source="unknown",
                    lookup_status="ambiguous_match",
                    matched_rule_id=None,
                    normalized_origin_entity_type=normalized_origin_type,
                ),
            }

        if not matches:
            return {
                "emulator_fact": emulator_fact,
                "resolved": _resolved(
                    operation_mode="unknown",
                    normalized_op=None,
                    exception_key=None,
                    semantic_source="unknown",
                    lookup_status="miss",
                    matched_rule_id=None,
                    normalized_origin_entity_type=normalized_origin_type,
                ),
            }

        # Tier 3 - unique hardcoded_entity_rule (or occasionally prompt_confirmed/
        # emulator_fact rows whose live operation happens to still read as unknown -
        # e.g. a future DLL regression) lookup row, keyed by normalized origin type.
        row = matches[0]
        normalized_op = row["normalized_choice_operation"]
        if normalized_op in (None, "unknown"):
            return {
                "emulator_fact": emulator_fact,
                "resolved": _resolved(
                    operation_mode="unknown",
                    normalized_op=normalized_op,
                    exception_key=None,
                    semantic_source=row["semantic_source"],
                    lookup_status="resolved_lookup_unknown",
                    matched_rule_id=row["origin_entity_id"],
                    normalized_origin_entity_type=normalized_origin_type,
                ),
            }
        if normalized_op in NORMALIZABLE_OPERATIONS:
            return {
                "emulator_fact": emulator_fact,
                "resolved": _resolved(
                    operation_mode="normalized",
                    normalized_op=normalized_op,
                    exception_key=None,
                    semantic_source=row["semantic_source"],
                    lookup_status="resolved_lookup",
                    matched_rule_id=row["origin_entity_id"],
                    normalized_origin_entity_type=normalized_origin_type,
                ),
            }
        return {
            "emulator_fact": emulator_fact,
            "resolved": _resolved(
                operation_mode="passthrough",
                normalized_op=normalized_op,
                exception_key=f"{normalized_origin_type}:{origin_id}",
                semantic_source=row["semantic_source"],
                lookup_status="resolved_lookup",
                matched_rule_id=row["origin_entity_id"],
                normalized_origin_entity_type=normalized_origin_type,
            ),
        }

    def lookup_row(self, origin_type: str, origin_id: str) -> "dict | None":
        """Escape hatch for analysis code (e.g. semantic-mismatch detection against a
        row's own `evidence`) - not used by resolve() itself beyond what's already
        returned there."""
        matches = self._by_key.get((origin_type, origin_id), [])
        return matches[0] if len(matches) == 1 else None
