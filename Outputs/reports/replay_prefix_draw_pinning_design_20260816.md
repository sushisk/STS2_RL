# Replay-prefix observed draw restoration

Status: implemented in STS2_RL PR #64.

## Goal

Replay must reproduce cards already observed as drawn in the committed Replay Prefix while leaving future RNG CardId-level and unfrozen.

## Observable equality

Proofs use `observable_card_key`, containing public gameplay-relevant card state:

```text
CardId, Type, Rarity, Cost, TargetType,
upgraded, UpgradeLevel,
TinkerTimeType, TinkerTimeRider,
enchantment
```

Physical `InstanceId`, option id, zone and list position are excluded.

## Gate A: unordered transfer

For immediate public pre/post states, using multisets:

```text
D0 = pre.drawPile
D1 = post.drawPile
O  = post.pendingChoice.options

require D1 ⊆ D0
R = D0 - D1
require R != ∅ and R ⊆ O

P = sum(pre[z] ∩ post[z])
    for published hand/discardPile/exhaustPile/playPile
E = O - R
require E ⊆ P
```

So every option is explained either by a card removed from DrawPile (`R`) or by a persistent non-DrawPile card (`E`). Public DrawPile order is not used.

## Gate B: generic option-order extraction

Gate B accepts only one observable sequence:

```text
drawn_sequence = unique ordered subsequence S of O
where multiset(S) = R
```

Equivalently, remove exactly `E` occurrences from the ordered options and preserve the order of the remainder.

If duplicate observable keys allow two different resulting sequences, Gate B fails closed.

## Ordering-contract sentinel

The inferred sequence is validated against the Held Stable Snapshot at the current root-relative cursor:

```text
option-derived drawn_sequence
== StableRoot.Player.DrawPile[cursor : cursor + len(R)]
```

The Stable Snapshot is validation only, never a fallback source. A mismatch records `visible_draw_tracking_error`, blocks later pinning, and produces no constraints.

This makes an incorrect PendingChoice-order premise detectable while keeping public Observation DrawPile order out of the proof.

## Replay Prefix invariant

Each entry may carry:

```text
visible_draw_constraints: (root-relative offset, observable_card_key)*
visible_draw_tracking_blocked: bool
visible_draw_tracking_error: str | None
```

Across the usable prefix, constraints must appear in generation order as exactly:

```text
0, 1, 2, ..., N-1
```

The consumer does not sort or repair malformed constraints. Any gap or reordering is an error. Tracking stops at the first blocked transition.

## Materialization

Observed CardIds are moved to the proven prefix positions while the unobserved candidate-hypothesis remainder keeps its relative order.

Concrete Snapshot copies are selected only during restore. If observable-equal copies have different hidden gameplay-relevant Snapshot state, materialization fails closed instead of selecting by physical identity.

## Safety boundary

Replay safety does not depend on card-name allowlists, choice operation labels, source-zone/origin metadata, triggering action identity, `rng_id`, public instance tokens, Snapshot `InstanceId`, raw draw history, or public DrawPile ordering.

## Tests

`Combat/tests/test_replay_prefix_visible_draw_pinning.py` covers generic mixed-choice and drawn-only Gate B, duplicate ambiguity, wrong option order versus Stable root, zero-draw behavior, unexplained mutation blocking, ordered contiguous-prefix enforcement, observable-state distinctions, and hidden-state ambiguity.

`API/tests/test_acrobatics_exact_instance_replay_pinning.py` is the paired real-Emulator regression; its historical filename remains, but it validates observable-state pinning rather than exact instance identity.
