# Replay-prefix observed draw restoration

Status: implemented in STS2_RL PR #64.

## Goal

Replay must reproduce cards already observed as drawn in the committed Replay Prefix while leaving future RNG CardId-level and unfrozen. The safety decision must be derivable from public observation state; hidden DrawPile order is not an answer key.

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

So every option is explained either by a card removed from DrawPile (`R`) or by a persistent non-DrawPile card (`E`). DrawPile list order is not used.

## Gate B: generic public option-order extraction

Gate B accepts only one observable sequence:

```text
drawn_sequence = unique ordered subsequence S of O
where multiset(S) = R
```

Equivalently, remove exactly `E` occurrences from the ordered options and preserve the order of the remainder. If duplicate observable keys allow two different resulting sequences, Gate B fails closed.

Gate B relies on an Emulator publication contract: when freshly removed DrawPile cards are exposed in `pendingChoice.options`, the relative order of those draw-origin occurrences preserves sequential draw order.

RL does **not** verify that premise by consulting:

- `StableRoot.Player.DrawPile` order;
- public Observation DrawPile order;
- raw draw history;
- RNG state;
- physical card identity.

Using any of those as a hidden answer key would violate the public-proof boundary.

## What can and cannot be validated at runtime

RL can fail closed on evidence that is visible in the public state:

- Gate A cannot explain the DrawPile multiset mutation;
- Gate B has more than one observable draw-origin sequence;
- Replay Prefix offsets cease to be one contiguous generated prefix;
- later Replay Prefix execution fails its normal replay signature checks.

However, if an Emulator producer **consistently** publishes draw-origin options in an order different from actual sequential draw order, and no public state exposes that difference, RL cannot prove the producer wrong without consulting hidden information. That premise must therefore be validated as an Emulator contract, not by RL runtime provenance logic.

A useful additional hardening step is to re-derive Gate A/B during Replay Prefix execution from the replayed public pre/post states and compare it with the recorded constraints. That would detect public-card-state divergence that `candidate_semantic_keys` alone may miss. It still would not—and should not—act as a hidden draw-order oracle.

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

Concrete Snapshot copies are selected only during restore. If observable-equal copies have different hidden gameplay-relevant Snapshot state, materialization fails closed instead of selecting by physical identity. Snapshot state is therefore used only to materialize a public constraint safely, not to decide which draw facts were observed or in what order.

## Safety boundary

Replay safety does not depend on card-name allowlists, choice operation labels, source-zone/origin metadata, triggering action identity, `rng_id`, public instance tokens, Snapshot `InstanceId`, raw draw history, public DrawPile ordering, or Stable Snapshot DrawPile ordering.

## Validation responsibility

STS2_Emulator should lock the Gate-B publication contract with regression tests against real engine behavior: when a PendingChoice exposes cards freshly removed from DrawPile, their relative option order must preserve sequential draw order. Such tests may use engine-internal knowledge as a **test oracle**, but that information must not become a runtime RL input or replay-provenance field.

## Tests

`Combat/tests/test_replay_prefix_visible_draw_pinning.py` covers generic mixed-choice and drawn-only Gate B, duplicate ambiguity, Stable Snapshot order independence, zero-draw behavior, unexplained mutation blocking, ordered contiguous-prefix enforcement, observable-state distinctions, and hidden-state ambiguity.

`API/tests/test_acrobatics_exact_instance_replay_pinning.py` is the paired real-Emulator regression; its historical filename remains, but it validates observable-state pinning rather than exact instance identity.
