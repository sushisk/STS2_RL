"""RNG Hypothesis / DrawPile Belief helpers for Combat search Phase 6.

This module implements the Search Coordinator-side machinery from
``docs/architecture/combat/mermaid_combat_rng_hypothesis_detail.mermaid``. It produces
Hypothetical OrderedDrawPile values from public card-id multisets, combines them with a
Shuffle RNG snapshot into a Search Hypothesis ID, and derives Method-B Restore inputs by
editing a copied ``CombatStateSnapshot`` before the caller invokes Restore.

The existing Phase 2/5 ``DecisionContext.search_hypothesis_id`` slot is
``Optional[str]``. ``SearchHypothesisId`` is therefore a value object with
``to_slot_value()``: a stable, opaque string hash over the RNG component and the
Hypothetical OrderedDrawPile card-id order. The materialized card-id sequence is kept in
the value object for local substitution; only the opaque slot string should flow through
DecisionContext / WorkItem / Lease identity.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import random
from collections import Counter, deque
from dataclasses import dataclass
from typing import Any, Callable, Optional

from combat_state_snapshot import (
    CardInstanceSnapshot,
    CombatStateSnapshot,
    SerializableRngSnapshot,
    canonical_json,
)
from search.decision_context import CombatStartReplayRoot, DecisionContext
from search.replay_draw_restore import (
    ObservableCardKey,
    card_id_from_observable_key,
    observable_card_key_from_snapshot,
    snapshot_card_replay_internal_key,
)

HYPOTHESIS_SLOT_PREFIX = "rng-hyp:v1:"

CONSUME_PASSTHROUGH = "passthrough"
CONSUME_MODE = "mode"
CONSUME_TRUE_RNG_OK = "true_rng_ok"

HYPOTHESIS_MODE_STANDARD = "standard"
HYPOTHESIS_MODE_INDEPENDENT = "independent"
HYPOTHESIS_MODES = frozenset({HYPOTHESIS_MODE_STANDARD, HYPOTHESIS_MODE_INDEPENDENT})


@dataclass(frozen=True)
class SearchHypothesisId:
    """RNG component plus one Hypothetical OrderedDrawPile identity.

    ``ordered_draw_pile_card_ids`` is intentionally CardId-only. During
    ``derive_substituted_snapshot()`` those CardIds are mapped back to the root
    snapshot's card instances by canonical per-card ordering, which avoids treating the
    Authoritative OrderedDrawPile's original index order as part of the hypothesis.
    """

    rng: SerializableRngSnapshot
    ordered_draw_pile_card_ids: tuple[str, ...]
    hypothesis_index: int

    def _payload(self) -> dict:
        return {
            "version": 1,
            "hypothesis_index": self.hypothesis_index,
            "rng": _rng_to_dict(self.rng),
            "ordered_draw_pile_card_ids": list(self.ordered_draw_pile_card_ids),
        }

    def digest(self) -> str:
        text = json.dumps(self._payload(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def to_slot_value(self) -> str:
        """Return the canonical ``Optional[str]`` slot value used by DecisionContext."""
        return f"{HYPOTHESIS_SLOT_PREFIX}{self.digest()}"


@dataclass(frozen=True)
class CoverageAssessment:
    """PUBLIC_MULTISET source coverage diagnostic attached to hypothesis-involved search entries."""

    is_complete: bool
    uncertain_sources: list[str]
    reason: str


@dataclass(frozen=True)
class ConsumeCheckResult:
    """Result of the privacy-first consume gate.

    ``kind`` is one of ``passthrough``, ``mode``, or ``true_rng_ok``. ``mode`` is set
    only when ``kind == "mode"`` and distinguishes Standard from Independent hypothesis
    sets. Independent is diagnostic-only; this module exposes the type distinction but
    does not aggregate scores.
    """

    kind: str
    mode: Optional[str] = None
    reason: str = ""

    @property
    def requires_hypothesis(self) -> bool:
        return self.kind == CONSUME_MODE


@dataclass(frozen=True)
class HypothesisGridCell:
    """One Root Action x Search Hypothesis cell and its derived Restore input."""

    root_action: Any
    hypothesis: SearchHypothesisId
    search_hypothesis_id: str
    derived_snapshot: CombatStateSnapshot


@dataclass(frozen=True)
class CombatStartDerivedReplayRoot:
    """One Genesis Search Hypothesis and its derived Combat Start Replay Root."""

    hypothesis: SearchHypothesisId
    derived_replay_root: CombatStartReplayRoot


def _rng_to_dict(rng: SerializableRngSnapshot) -> dict[str, int]:
    return {
        "Counter": int(rng.Counter),
        "State0": int(rng.State0),
        "State1": int(rng.State1),
        "State2": int(rng.State2),
        "State3": int(rng.State3),
    }


def _rng_from_like(value: Any) -> SerializableRngSnapshot:
    if isinstance(value, SerializableRngSnapshot):
        return value
    if isinstance(value, dict):
        return SerializableRngSnapshot.from_dict(value)
    return SerializableRngSnapshot(
        Counter=int(value.Counter),
        State0=int(value.State0),
        State1=int(value.State1),
        State2=int(value.State2),
        State3=int(value.State3),
    )


# Currently unused by compute_public_multiset, but intentionally retained for future card-count-aware Relic/Power logic.
def _generated_card_id(entry: Any, visible_instance_ids: dict[str, str]) -> Optional[str]:
    fields = entry.Fields or {}
    for key in ("cardId", "CardId", "generatedCardId", "GeneratedCardId"):
        if fields.get(key):
            return str(fields[key])
    for key in ("card", "Card", "generatedCard", "GeneratedCard"):
        nested = fields.get(key)
        if isinstance(nested, dict):
            for nested_key in ("CardId", "cardId", "id"):
                if nested.get(nested_key):
                    return str(nested[nested_key])
    instance_id = fields.get("cardInstanceId") or fields.get("CardInstanceId") or fields.get("generatedCardInstanceId")
    if instance_id is not None:
        return visible_instance_ids.get(str(instance_id))
    return None


def compute_public_multiset(snapshot: CombatStateSnapshot) -> dict[str, int]:
    """Compute the public card-id multiset for the current hidden DrawPile by reading
    only ``CardInstances`` whose Zone is ``draw_pile`` - composition only; the actual
    order is discarded here. Pile-external history-only cards are intentionally excluded.
    """
    counts = Counter(str(card.CardId) for card in snapshot.Player.cards_in_zone("draw_pile"))
    return dict(sorted(counts.items()))


def _candidate_action_type(candidate: Any) -> str:
    if hasattr(candidate, "choice_kind") and getattr(candidate, "choice_kind"):
        return str(getattr(candidate, "choice_kind"))
    if hasattr(candidate, "semantic_action"):
        action = getattr(candidate, "semantic_action")
        if hasattr(action, "action_type"):
            return str(action.action_type)
    if isinstance(candidate, dict):
        return str(candidate.get("action_type") or candidate.get("choice_kind") or "")
    return ""


def _candidate_card_id(candidate: Any) -> Optional[str]:
    if hasattr(candidate, "semantic_action"):
        action = getattr(candidate, "semantic_action")
        semantic_key = getattr(action, "semantic_key", "")
        if action.action_type in {"card", "choice_card", "choice_reward_card"} and semantic_key:
            return str(semantic_key).split(":", 1)[-1]
    if isinstance(candidate, dict):
        params = candidate.get("parameters") or {}
        if params.get("cardId") is not None:
            return str(params["cardId"])
    return None


def consume_check(
    candidate: Any,
    decision_context: DecisionContext,
    *,
    mode: str = HYPOTHESIS_MODE_STANDARD,
    true_rng_ok: bool = False,
) -> ConsumeCheckResult:
    """Conservative gate for whether a candidate may use Authoritative RNG/order.

    Rule: ``system``, ``choice_confirm``, and ``choice_skip`` candidates pass through
    only when they are not tied to a card. All card plays, card choices, target choices,
    potions, and unknown action kinds require a hypothesis because they may draw,
    shuffle, generate cards, invoke target/RNG hooks, or depend on the true
    Authoritative OrderedDrawPile. If the context already carries a concrete
    hypothesis, non-empty replay-prefix continuation inherits it. A passthrough-derived
    ``None`` is rechecked each time because the next candidate may become RNG/order
    sensitive.

    ``true_rng_ok`` is an explicit opt-in for exact replay/determinism/debug/Main commit
    paths and must not be the default for action evaluation scoring.
    """
    if mode not in HYPOTHESIS_MODES:
        raise ValueError(f"unknown hypothesis mode {mode!r}")
    if true_rng_ok:
        return ConsumeCheckResult(CONSUME_TRUE_RNG_OK, reason="explicit true_rng_ok opt-in")
    if decision_context.replay_prefix and decision_context.search_hypothesis_id is not None:
        return ConsumeCheckResult(CONSUME_MODE, mode=mode, reason="inherited non-None Search Hypothesis ID")

    action_type = _candidate_action_type(candidate)
    card_id = _candidate_card_id(candidate)
    passthrough_action_types = {"system", "choice_confirm", "choice_skip"}
    if action_type in passthrough_action_types and card_id is None:
        return ConsumeCheckResult(
            CONSUME_PASSTHROUGH,
            reason=f"{action_type!r} candidate has no card binding and is treated as RNG/order independent",
        )
    return ConsumeCheckResult(
        CONSUME_MODE,
        mode=mode,
        reason=f"{action_type or 'unknown'!r} candidate is conservatively treated as RNG/order sensitive",
    )


def _shuffle_seed_from_rng(rng: SerializableRngSnapshot, hypothesis_index: int) -> int:
    payload = json.dumps(
        {"rng": _rng_to_dict(rng), "hypothesis_index": int(hypothesis_index)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big")


def _combat_start_seed_from_root(root_seed: int, hypothesis_index: int) -> int:
    """Derive a per-hypothesis Seed override, masked into ``CombatScenario.Seed``'s
    accepted range (a C# ``int``, i.e. signed 32-bit: 0..2**31-1 here since seeds in
    this codebase are conventionally non-negative) - the raw SHA-256 digest can exceed
    that range and overflow at the Emulator boundary otherwise."""
    payload = json.dumps(
        {"root_seed": int(root_seed), "hypothesis_index": int(hypothesis_index)},
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    digest = int.from_bytes(hashlib.sha256(payload.encode("utf-8")).digest()[:8], "big")
    return digest & 0x7FFFFFFF


def derive_combat_start_replay_roots(
    combat_start_replay_root: CombatStartReplayRoot,
    *,
    count: int,
) -> list[CombatStartDerivedReplayRoot]:
    if count < 0:
        raise ValueError("count must be non-negative")
    root_seed = int(combat_start_replay_root.scenario_spec.get("seed", 1))
    roots: list[CombatStartDerivedReplayRoot] = []
    for index in range(count):
        spec = dict(combat_start_replay_root.scenario_spec)
        spec["seed"] = _combat_start_seed_from_root(root_seed, index)
        hypothesis = SearchHypothesisId(
            rng=SerializableRngSnapshot(Counter=spec["seed"], State0=0, State1=0, State2=0, State3=0),
            ordered_draw_pile_card_ids=(),
            hypothesis_index=index,
        )
        roots.append(
            CombatStartDerivedReplayRoot(
                hypothesis=hypothesis,
                derived_replay_root=CombatStartReplayRoot(scenario_spec=spec),
            )
        )
    return roots


def _canonical_cards_from_multiset(public_multiset: dict[str, int]) -> list[str]:
    cards: list[str] = []
    for card_id, count in sorted((str(k), int(v)) for k, v in public_multiset.items()):
        if count < 0:
            raise ValueError(f"negative PUBLIC_MULTISET count for {card_id!r}")
        cards.extend([card_id] * count)
    return cards


def generate_belief_hypotheses(
    public_multiset: dict[str, int],
    *,
    count: int,
    rng_seed_source: Callable[[int], Any],
) -> list[SearchHypothesisId]:
    """Generate H1..Hn from PUBLIC_MULTISET without reading Authoritative order.

    Generation follows the diagram's StableShuffle-shaped requirement: first sort the
    public multiset into canonical CardId order, then Fisher-Yates shuffle that canonical
    order with each hypothesis's own RNG component as the seed source. The Python PRNG is
    only a deterministic coordinator-side sampler; the full ``SerializableRngSnapshot``
    is still what gets substituted into ``RunRng["Shuffle"]`` for Restore.
    """
    if count < 0:
        raise ValueError("count must be non-negative")
    canonical_cards = _canonical_cards_from_multiset(public_multiset)
    hypotheses: list[SearchHypothesisId] = []
    for index in range(count):
        rng = _rng_from_like(rng_seed_source(index))
        ordered = list(canonical_cards)
        shuffler = random.Random(_shuffle_seed_from_rng(rng, index))
        for i in range(len(ordered) - 1, 0, -1):
            j = shuffler.randrange(i + 1)
            ordered[i], ordered[j] = ordered[j], ordered[i]
        hypotheses.append(
            SearchHypothesisId(
                rng=rng,
                ordered_draw_pile_card_ids=tuple(ordered),
                hypothesis_index=index,
            )
        )
    return hypotheses


def _snapshot_plain_dict(snapshot: CombatStateSnapshot) -> dict:
    payload = dataclasses.asdict(snapshot)

    def strip_unknown(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: strip_unknown(v) for k, v in node.items() if k != "unknown_fields"}
        if isinstance(node, list):
            return [strip_unknown(v) for v in node]
        return node

    return strip_unknown(payload)


def _card_identity_key(card: CardInstanceSnapshot) -> str:
    return canonical_json(dataclasses.asdict(card), exclude_volatile=False)


def _pinned_prefix_visible_draw_constraints(
    decision_context: DecisionContext,
) -> tuple[tuple[int, ObservableCardKey], ...]:
    constraints: list[tuple[int, ObservableCardKey]] = []
    for entry in decision_context.replay_prefix:
        constraints.extend(entry.visible_draw_constraints)
        if entry.visible_draw_tracking_blocked:
            break

    constraints_tuple = tuple(constraints)
    if [offset for offset, _key in constraints_tuple] != list(range(len(constraints_tuple))):
        raise ValueError(
            "visible draw constraints must form one ordered contiguous prefix from Stable-root offset 0"
        )
    return constraints_tuple


def _reorder_hypothesis_for_visible_draw_constraints(
    hypothesis: SearchHypothesisId,
    constraints: tuple[tuple[int, ObservableCardKey], ...],
) -> SearchHypothesisId:
    remaining = list(hypothesis.ordered_draw_pile_card_ids)
    pinned: list[str] = []
    for offset, key in constraints:
        if offset != len(pinned):
            raise ValueError("visible draw constraints must be a contiguous prefix")
        card_id = card_id_from_observable_key(key)
        try:
            remaining.remove(card_id)
        except ValueError as exc:
            raise ValueError(
                f"visible draw constraint requires CardId {card_id!r} absent from the hypothesis"
            ) from exc
        pinned.append(card_id)
    return dataclasses.replace(
        hypothesis,
        ordered_draw_pile_card_ids=tuple(pinned + remaining),
    )


def _draw_pile_instances_for_hypothesis(
    root_snapshot: CombatStateSnapshot,
    ordered_card_ids: tuple[str, ...],
    pinned_observable_keys: tuple[tuple[int, ObservableCardKey], ...] = (),
) -> list[dict]:
    root_cards = root_snapshot.Player.cards_in_zone("draw_pile")
    requested = Counter(ordered_card_ids)
    available = Counter(str(card.CardId) for card in root_cards)
    if requested != available:
        raise ValueError(
            "hypothesis DrawPile multiset does not match root snapshot Player.CardInstances(draw_pile) multiset: "
            f"requested={dict(sorted(requested.items()))}, available={dict(sorted(available.items()))}"
        )

    offsets = [offset for offset, _key in pinned_observable_keys]
    if offsets != list(range(len(offsets))):
        raise ValueError("pinned draw constraints must be an ordered contiguous prefix")

    pinned_keys = tuple(key for _offset, key in pinned_observable_keys)
    for key, count in Counter(pinned_keys).items():
        matches = [card for card in root_cards if observable_card_key_from_snapshot(card) == key]
        if len(matches) < count:
            raise ValueError(
                "pinned observable card state is absent from the root DrawPile in the required count"
            )
        if len({snapshot_card_replay_internal_key(card) for card in matches}) != 1:
            raise ValueError(
                "pinned observable card state maps to multiple hidden gameplay states; "
                "public evidence is insufficient for safe replay materialization"
            )

    used_instance_ids: set[str] = set()
    pinned_cards: list[CardInstanceSnapshot] = []
    for offset, key in pinned_observable_keys:
        expected_card_id = ordered_card_ids[offset]
        if card_id_from_observable_key(key) != expected_card_id:
            raise ValueError(
                f"pinned observable card has CardId={card_id_from_observable_key(key)!r}, "
                f"but hypothesis position {offset} requires {expected_card_id!r}"
            )
        matches = sorted(
            (
                card
                for card in root_cards
                if str(card.InstanceId) not in used_instance_ids
                and observable_card_key_from_snapshot(card) == key
            ),
            key=_card_identity_key,
        )
        if not matches:
            raise ValueError("pinned observable card state was exhausted during concrete allocation")
        chosen = matches[0]
        pinned_cards.append(chosen)
        used_instance_ids.add(str(chosen.InstanceId))

    by_card_id: dict[str, deque[CardInstanceSnapshot]] = {}
    for card in sorted(
        (card for card in root_cards if str(card.InstanceId) not in used_instance_ids),
        key=_card_identity_key,
    ):
        by_card_id.setdefault(str(card.CardId), deque()).append(card)

    tail_cards: list[CardInstanceSnapshot] = []
    for card_id in ordered_card_ids[len(pinned_cards) :]:
        cards = by_card_id.get(card_id)
        if not cards:
            raise ValueError(
                f"hypothesis concrete-card allocation exhausted CardId {card_id!r} after observable-state pinning"
            )
        tail_cards.append(cards.popleft())

    return [dataclasses.asdict(card) for card in pinned_cards + tail_cards]


def derive_substituted_snapshot(
    root_snapshot: CombatStateSnapshot,
    hypothesis: SearchHypothesisId,
    *,
    pinned_draw_card_keys: tuple[tuple[int, ObservableCardKey], ...] = (),
) -> CombatStateSnapshot:
    """Return a Method-B derived snapshot with optional observed-draw state pins."""
    payload = _snapshot_plain_dict(root_snapshot)
    run_rng = payload["Rng"]["RunRng"]
    if "Shuffle" not in run_rng:
        raise ValueError('root_snapshot.Rng.RunRng is missing required "Shuffle" stream')
    run_rng["Shuffle"] = _rng_to_dict(hypothesis.rng)
    ordered_draw_pile = _draw_pile_instances_for_hypothesis(
        root_snapshot,
        hypothesis.ordered_draw_pile_card_ids,
        pinned_observable_keys=pinned_draw_card_keys,
    )
    replacement_by_id = {
        str(card["InstanceId"]): {**card, "Zone": "draw_pile", "PileIndex": index}
        for index, card in enumerate(ordered_draw_pile)
    }
    payload["Player"]["CardInstances"] = [
        replacement_by_id.get(str(card["InstanceId"]), card)
        for card in payload["Player"]["CardInstances"]
    ]
    return CombatStateSnapshot.from_dict(payload)


def build_grid(
    root_actions: list[Any],
    hypotheses: list[SearchHypothesisId],
    root_snapshot: CombatStateSnapshot,
) -> list[HypothesisGridCell]:
    """Build the Standard-mode Root Action x H1..Hn derived-snapshot grid."""
    cells: list[HypothesisGridCell] = []
    for root_action in root_actions:
        for hypothesis in hypotheses:
            cells.append(
                HypothesisGridCell(
                    root_action=root_action,
                    hypothesis=hypothesis,
                    search_hypothesis_id=hypothesis.to_slot_value(),
                    derived_snapshot=derive_substituted_snapshot(root_snapshot, hypothesis),
                )
            )
    return cells


def with_search_hypothesis(
    decision_context: DecisionContext,
    hypothesis: Optional[SearchHypothesisId],
) -> DecisionContext:
    """Return a new DecisionContext with the Phase-2 string hypothesis slot populated."""
    return dataclasses.replace(
        decision_context,
        search_hypothesis_id=None if hypothesis is None else hypothesis.to_slot_value(),
    )


def apply_hypothesis_to_context(
    decision_context: DecisionContext,
    hypothesis: SearchHypothesisId,
) -> DecisionContext:
    """Apply a CardId-level belief plus only the already-observed draw prefix.

    The hypothesis remains CardId-level. Replay constraints pin observable
    replay-equivalent card state at proven root-relative offsets; physical InstanceId is
    never part of the proof. Hidden gameplay-state ambiguity rejects materialization
    rather than guessing a concrete copy.
    """
    if isinstance(decision_context.root_snapshot, CombatStartReplayRoot):
        raise TypeError("Genesis hypotheses must use derive_combat_start_replay_roots()")

    constraints = _pinned_prefix_visible_draw_constraints(decision_context)
    effective_hypothesis = hypothesis
    if constraints:
        effective_hypothesis = _reorder_hypothesis_for_visible_draw_constraints(
            hypothesis, constraints
        )

    context = dataclasses.replace(
        decision_context,
        root_snapshot=derive_substituted_snapshot(
            decision_context.root_snapshot,
            effective_hypothesis,
            pinned_draw_card_keys=constraints,
        ),
    )
    return with_search_hypothesis(context, effective_hypothesis)
