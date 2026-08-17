# Replay-prefix observed draw restoration

Status: implemented in STS2_RL PR #64.

## Goal

Replay must reproduce cards already observed moving from DrawPile into Hand in the committed Replay Prefix while leaving future RNG CardId-level and unfrozen. The safety decision must come from ordinary public pre/post state; hidden DrawPile order and replay-only provenance are not answer keys.

## Observable card equality

The structural check uses public gameplay-relevant card state:

```text
CardId, Type, Rarity, Cost, TargetType,
upgraded, UpgradeLevel,
TinkerTimeType, TinkerTimeRider,
enchantment
```

Physical `InstanceId`, choice-local option id, zone and list position are excluded. Required public field types are validated instead of coerced; malformed booleans/tinker/enchantment shapes fail closed rather than becoming a different observable key accidentally.

## Supported public transition shape

For one committed transition:

```text
D0 = multiset(pre.drawPile)
D1 = multiset(post.drawPile)

require D1 subset-of D0
R = D0 - D1
require R != empty
N = |R|

H0 = ordered pre.hand
H1 = ordered post.hand
H1 = S + Q
require |Q| = N
require multiset(Q) = R
require S == H0 with exactly one card occurrence removed
```

The last condition preserves the relative order of all surviving pre-Hand cards. `Q`, the newly appended post-Hand suffix, is the observed sequence recorded in Replay Prefix.

DrawPile list order is never consulted; only its multiset difference is used. The sequence comes from ordinary Hand ordering.

## Intentional scope

This is deliberately narrower than the previous Gate A / Gate B design. There is no separate PendingChoice-origin proof and no generic option-subsequence inference.

The supported shape covers real Acrobatics-style transitions:

```text
play one Hand card -> transfer DrawPile cards into appended Hand suffix
```

The recognizer proves this public transfer shape; it does not claim to prove which internal primitive caused it. PendingChoice, choice operation/scope/source/origin metadata, option ordering, and Snapshot order are not replay-safety inputs.

Unsupported shapes fail closed when DrawPile changes, including:

- draw-to-choice without the cards appearing as the appended Hand suffix;
- multiple Hand removals or Hand reordering in the same transition;
- DrawPile mutations that cannot be explained by the post-Hand suffix;
- Tutor/reveal/direct-selection shapes that do not satisfy this Hand-transfer structure;
- later transitions after any unexplained DrawPile mutation.

If future mechanics need another observable transition shape, add a separate structural recognizer then rather than adding replay provenance to Emulator now.

## Replay Prefix invariant

Each entry may carry:

```text
visible_draw_constraints: (root-relative offset, observable_card_key)*
visible_draw_tracking_blocked: bool
visible_draw_tracking_error: str | None
```

Across the usable prefix, constraints must appear in generation order as exactly `0, 1, 2, ..., N-1`. Tracking stops at the first blocked transition. A transition with no observable DrawPile multiset change adds no constraints and does not move the cursor.

## Materialization

Observed CardIds are moved to the proven prefix positions while the unobserved candidate-hypothesis remainder keeps its relative order.

Concrete Snapshot copies are selected only during restore. If observable-equal copies have different hidden gameplay-relevant Snapshot state, materialization fails closed instead of selecting by physical identity. Snapshot state is used only to materialize an already-proven public constraint safely.

Current `CardInstanceSnapshot` does not yet project enchantment into this equality, so observed enchanted cards fail closed until the Snapshot/restore completeness work in Emulator PR #26 is integrated on the RL side.

## Information boundary

Replay evidence extraction does not depend on:

- card-name allowlists;
- PendingChoice option order or choice semantic metadata;
- triggering action identity;
- `rng_id` or raw RNG provenance;
- public instance tokens or Snapshot `InstanceId`;
- raw draw history;
- public Observation DrawPile list order;
- Stable Snapshot DrawPile list order;
- the Stable Snapshot object itself.

Snapshot state is consulted later only for safe concrete materialization of already-recorded observable constraints.

## Cross-repo responsibility

STS2_Emulator PR #25 only protects the ordinary real-game behavior needed by this structural check: for Acrobatics, the played card leaves Hand and the public DrawPile multiset decrease appears as the appended post-Hand suffix. PendingChoice publication remains a separate general Emulator contract and is not part of #64's replay proof.

Emulator does not publish a sequential-draw marker, draw ordinal, replay provenance DTO, or hidden order oracle for RL.

## Tests

`Combat/tests/test_replay_prefix_visible_draw_pinning.py` covers the supported Hand-transfer shape, PendingChoice independence, drawn-only choice fail-closed behavior, public DrawPile order independence, Hand-prefix violations, malformed public-card field fail-closed behavior, unexplained mutation blocking, contiguous-prefix enforcement, observable-state distinctions, and hidden-state ambiguity.

`API/tests/test_acrobatics_exact_instance_replay_pinning.py` remains the paired real-Emulator regression; despite its historical filename it validates observable-state replay pinning rather than exact physical identity.
