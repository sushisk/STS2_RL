"""PUBLIC_MULTISET generated-card coverage assessment.

``rng_hypothesis.compute_public_multiset()`` can only account for generated combat
cards that the Emulator records as ``CombatHistory`` ``CardGeneratedEntry`` rows.
This module records the Task-6 audit of the vendored Emulator source at
``C:\\STS2_RL\\Outputs\\azure_stage_20260723_122305\\STS2_Emulator\\Sts2Emulator``.

Coverage finding:
  * ``CardPileCmd.AddGeneratedCardsToCombat`` requires a combat pile, writes
    ``CombatManager.Instance.History.CardGenerated(...)`` before adding each card,
    then runs ``Hook.AfterCardGeneratedForCombat``. The single-card helper and
    ``AddToCombatAndPreview<T>`` both delegate to it.
  * ``CardCmd.Transform`` has a separate combat-pile path that writes
    ``History.CardGenerated`` for the replacement and then runs the generated-card
    hook.
  * The only direct ``History.CardGenerated`` writes found in the vendored source are
    those two paths plus ``CombatHistory.CardGenerated`` itself.
  * A live runtime cross-check with ``COLLISION_COURSE`` generating ``DEBRIS`` confirmed
    the current Emulator emits a ``CardGeneratedEntry`` whose ``cardInstanceId`` is
    resolvable from visible piles, matching ``compute_public_multiset``'s resolver.

The vendored source is dated 2026-07-23, so this is not a proof about unknown future
Emulator code. The active snapshot check below is intentionally source-specific: known
generation-capable Relics/Powers are classified as confirmed-covered when their source
path uses the audited logging helpers, and any named uncertain sources would mark the
Belief incomplete.
"""

from __future__ import annotations

from dataclasses import dataclass

from combat_state_snapshot import CombatStateSnapshot
from search.rng_hypothesis import compute_public_multiset


@dataclass(frozen=True)
class CoverageAssessment:
    """Whether PUBLIC_MULTISET generated-card coverage is complete for a snapshot."""

    is_complete: bool
    uncertain_sources: list[str]
    reason: str


# Active Relic/Power IDs whose vendored source can generate cards into the player's
# combat piles and was confirmed to route through CardGeneratedEntry-writing paths.
CONFIRMED_COVERED_GENERATION_SOURCES: dict[str, str] = {
    "Relic:BIIIG_HUG": "BiiigHug.AfterShuffle uses AddGeneratedCardToCombat(SOOT -> Draw).",
    "Relic:BIG_HAT": "BigHat.AfterSideTurnStart uses AddGeneratedCardsToCombat(... -> Hand).",
    "Relic:BLESSED_ANTLER": "BlessedAntler.BeforeHandDraw uses AddGeneratedCardsToCombat(DAZED -> Draw).",
    "Relic:BURNING_STICKS": "BurningSticks uses AddGeneratedCardToCombat(... -> Hand).",
    "Relic:CHOICES_PARADOX": "ChoicesParadox uses AddGeneratedCardToCombat(... -> Hand).",
    "Relic:CROSSBOW": "Crossbow uses AddGeneratedCardsToCombat(... -> Hand).",
    "Relic:FUNERARY_MASK": "FuneraryMask.BeforeHandDraw uses AddGeneratedCardToCombat(SOUL -> Draw).",
    "Relic:MUSIC_BOX": "MusicBox uses AddGeneratedCardToCombat(... -> Hand).",
    "Relic:ORANGE_DOUGH": "OrangeDough uses AddGeneratedCardsToCombat(... -> Hand).",
    "Relic:RADIANT_PEARL": "RadiantPearl.BeforeHandDraw uses AddGeneratedCardsToCombat(LUMINESCE -> Hand).",
    "Relic:TEA_OF_DISCOURTESY": "TeaOfDiscourtesy uses AddToCombatAndPreview<T>, which delegates to AddGeneratedCardToCombat.",
    "Relic:TOOLBOX": "Toolbox uses AddGeneratedCardToCombat(... -> Hand).",
    "Relic:VEXING_PUZZLEBOX": "VexingPuzzlebox uses AddGeneratedCardToCombat(... -> Hand).",
    "Power:CALL_OF_THE_VOID_POWER": "CallOfTheVoidPower uses AddGeneratedCardsToCombat(... -> Hand).",
    "Power:CALAMITY_POWER": "CalamityPower uses AddGeneratedCardToCombat(... -> Hand).",
    "Power:CREATIVE_AI_POWER": "CreativeAiPower.BeforeHandDraw uses AddGeneratedCardToCombat(... -> Hand).",
    "Power:HELLO_WORLD_POWER": "HelloWorldPower.BeforeHandDraw uses AddGeneratedCardsToCombat(... -> Hand).",
    "Power:JUGGLING_POWER": "JugglingPower.AfterCardPlayed uses AddGeneratedCardToCombat(clone -> Hand).",
    "Power:NIGHTMARE_POWER": "NightmarePower.BeforeHandDraw uses AddGeneratedCardToCombat(clone -> Hand).",
    "Power:PAINFUL_STABS_POWER": "PainfulStabsPower uses AddToCombatAndPreview<T>, which delegates to AddGeneratedCardToCombat.",
    "Power:PERSONAL_HIVE_POWER": "PersonalHivePower.AfterDamageReceived uses AddGeneratedCardToCombat(DAZED -> Draw).",
    "Power:SENTRY_MODE_POWER": "SentryModePower.BeforeHandDraw uses AddGeneratedCardToCombat(SWEEPING_GAZE -> Hand).",
    "Power:SOULBOUND_POWER": "SoulboundPower.AfterCardGeneratedForCombat uses AddGeneratedCardsToCombat(SOUL -> Draw).",
    "Power:SPECTRUM_SHIFT_POWER": "SpectrumShiftPower.BeforeHandDraw uses AddGeneratedCardsToCombat(... -> Hand).",
    "Power:WITHERING_PRESENCE_POWER": "WitheringPresencePower uses AddToCombatAndPreview<T>, which delegates to AddGeneratedCardToCombat.",
}


# No active Relic/Power card-generation source from the vendored source audit is known
# to bypass CardGeneratedEntry. Leave this dict explicit so future audits can add
# concrete source IDs with reasons instead of weakening all snapshots globally.
UNCERTAIN_GENERATION_SOURCES: dict[str, str] = {}


def _active_generation_source_keys(snapshot: CombatStateSnapshot) -> list[str]:
    keys: list[str] = []
    keys.extend(f"Relic:{relic.RelicId}" for relic in snapshot.Player.Relics)
    keys.extend(f"Power:{power.PowerId}" for power in snapshot.Player.Powers)
    for enemy in snapshot.Enemies:
        keys.extend(f"Power:{power.PowerId}" for power in enemy.Powers)
    for pet in snapshot.Player.Pets:
        keys.extend(f"Power:{power.PowerId}" for power in pet.Powers)
    return keys


def assess_public_multiset_coverage(snapshot: CombatStateSnapshot) -> CoverageAssessment:
    """Assess whether active known card-generation sources are covered by history.

    The assessment is conservative only for named audited mechanisms. A snapshot with no
    active known generation-capable Relic/Power is complete because no active mechanism
    from the audit can invalidate ``PUBLIC_MULTISET`` coverage.
    """
    active_keys = _active_generation_source_keys(snapshot)
    uncertain = sorted(
        key for key in active_keys if key in UNCERTAIN_GENERATION_SOURCES
    )
    if uncertain:
        reasons = "; ".join(f"{key}: {UNCERTAIN_GENERATION_SOURCES[key]}" for key in uncertain)
        return CoverageAssessment(
            is_complete=False,
            uncertain_sources=uncertain,
            reason=f"PUBLIC_MULTISET generated-card coverage is incomplete for active source(s): {reasons}",
        )

    covered = sorted(key for key in active_keys if key in CONFIRMED_COVERED_GENERATION_SOURCES)
    if covered:
        return CoverageAssessment(
            is_complete=True,
            uncertain_sources=[],
            reason="known active card-generation source(s) are CardGeneratedEntry-covered: " + ", ".join(covered),
        )

    return CoverageAssessment(
        is_complete=True,
        uncertain_sources=[],
        reason="no known card-generation-capable Relic/Power currently active",
    )


def compute_public_multiset_with_coverage(
    snapshot: CombatStateSnapshot,
    *,
    combat_start_deck_multiset: dict[str, int],
) -> tuple[dict[str, int], CoverageAssessment]:
    """Return Phase-6 PUBLIC_MULTISET plus the Task-6 coverage assessment."""
    public_multiset = compute_public_multiset(snapshot)
    return public_multiset, assess_public_multiset_coverage(snapshot)


def assess_public_multiset_coverage_for_combat_start(scenario_spec: dict) -> CoverageAssessment:
    """Genesis counterpart to ``assess_public_multiset_coverage()`` for a Start-of-Combat
    Pending's Combat Start Replay Root - reuses the SAME audited coverage tables, just
    reading active Relic/Power ids directly from the declared Scenario spec
    (``relics``/``player_powers``) instead of a captured Snapshot's dataclasses, since no
    Snapshot exists yet at this point (see ``CombatStartReplayRoot``)."""
    active_keys = [f"Relic:{relic_id}" for relic_id in (scenario_spec.get("relics") or [])]
    active_keys.extend(f"Power:{power.get('id', power.get('power_id'))}" for power in (scenario_spec.get("player_powers") or []))

    uncertain = sorted(key for key in active_keys if key in UNCERTAIN_GENERATION_SOURCES)
    if uncertain:
        reasons = "; ".join(f"{key}: {UNCERTAIN_GENERATION_SOURCES[key]}" for key in uncertain)
        return CoverageAssessment(
            is_complete=False,
            uncertain_sources=uncertain,
            reason=f"PUBLIC_MULTISET generated-card coverage is incomplete for active source(s): {reasons}",
        )

    covered = sorted(key for key in active_keys if key in CONFIRMED_COVERED_GENERATION_SOURCES)
    if covered:
        return CoverageAssessment(
            is_complete=True,
            uncertain_sources=[],
            reason="known active card-generation source(s) are CardGeneratedEntry-covered: " + ", ".join(covered),
        )

    return CoverageAssessment(
        is_complete=True,
        uncertain_sources=[],
        reason="no known card-generation-capable Relic/Power currently active",
    )


def compute_public_multiset_with_coverage_for_combat_start(
    scenario_spec: dict,
    *,
    combat_start_deck_multiset: dict[str, int],
) -> tuple[dict[str, int], CoverageAssessment]:
    """Genesis counterpart to ``compute_public_multiset_with_coverage()``."""
    public_multiset = dict(sorted((str(k), int(v)) for k, v in combat_start_deck_multiset.items() if int(v) != 0))
    return public_multiset, assess_public_multiset_coverage_for_combat_start(scenario_spec)
