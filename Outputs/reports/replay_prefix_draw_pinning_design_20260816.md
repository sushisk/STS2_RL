# Replay-prefix DrawPile restoration — current implemented design

Status: implemented in STS2_RL PR #64. Earlier exact-instance, card-allowlist, producer-specific, and Hand-specific Gate-B designs are superseded.

## Goal

A committed Replay Prefix may already have consumed cards from DrawPile before reaching a PendingChoice. Replaying that observed prefix under another CardId-level RNG hypothesis must reproduce only the already-observed draws without freezing the unobserved DrawPile remainder or introducing replay-only physical-card identity.

The implementation separates two questions:

1. **Gate A — membership/conservation:** which observable card states left DrawPile and are represented in the current PendingChoice?
2. **Generic Gate B — order:** after removing the non-draw-origin options explained by Gate A, is there one unambiguous option-order sequence for the draw-origin cards?

A third check validates Gate B's ordering premise against the Held Stable Snapshot. Special DrawPile restoration is enabled only when all required checks succeed.

## Observable card equality

All proof-side comparisons use `observable_card_key(card)` rather than CardId alone or physical instance identity.

The current key contains:

```text
CardId
Type
Rarity
Cost
TargetType
upgraded
UpgradeLevel
TinkerTimeType
TinkerTimeRider
enchantment(id, amount, status)
```

It excludes option id, zone, list position, and `InstanceId`. `SearchHypothesisId` remains CardId-level.

The Snapshot projection currently has no equivalent enchantment field, so its enchantment component is `None`. An observed enchanted card therefore fails closed during full materialization rather than silently collapsing to an unenchanted Snapshot card.

## Gate A — unordered observable DrawPile -> Choice transfer

For the immediate public pre/post states of one committed transition, all operations are multisets over `observable_card_key`:

```text
D0 = multiset(pre.drawPile)
D1 = multiset(post.drawPile)
O  = multiset(post.pendingChoice.options)

require D1 ⊆ D0
R = D0 - D1
require R != ∅
require R ⊆ O
```

For each eligible public non-DrawPile zone present in both observations:

```text
P_z = multiset(pre[z]) ∩ multiset(post[z])
P   = Σ_z P_z
```

The current eligible zones are `hand`, `discardPile`, `exhaustPile`, and `playPile`. A zone absent from either observation is neutral and is not evidence.

Then:

```text
E = O - R
require E ⊆ P
```

Equivalently:

```text
O = R + E
where E ⊆ P
```

`R` is the observable multiset removed from DrawPile. `E` is the remainder of the choice explained by cards that already existed and persisted outside DrawPile.

Gate A uses no card-name allowlist, choice-operation metadata, physical identity, `rng_id`, or published DrawPile list order.

### Examples

Acrobatics-like choice:

```text
options = surviving Hand + drawn cards
R = drawn cards
E = surviving Hand
```

Draw-N-choose-M-style choice presenting only the drawn cards:

```text
options = drawn cards
R = drawn cards
E = ∅
```

Both now flow into the same generic Gate B.

## Generic Gate B — remove E, preserve option order

Gate B no longer contains an Acrobatics/Hand-specific prover or `_ORDERED_DRAW_PROVERS` registry.

Given Gate A's `R`, `E`, and the ordered PendingChoice sequence `O`, Gate B considers the ordered subsequences of `O` whose multiset is exactly `R`:

```text
drawn_sequence = unique ordered subsequence S of O
where multiset(S) = R
```

Equivalently, remove exactly the `E` occurrences from `O` and preserve the relative order of everything that remains.

This directly supports both:

```text
surviving Hand + drawn cards
```

and:

```text
drawn cards only
```

without knowing the card name, producer type, choice operation, or source zone.

### Duplicate ambiguity

If the same `observable_card_key` occurs in both `R` and `E`, several occurrence-level assignments may be possible.

Multiple assignments are acceptable only when they collapse to the **same observable-key `drawn_sequence`**. If valid assignments yield different sequences, Gate B fails closed because draw order is not uniquely recoverable from public evidence.

The implementation enumerates at most two distinct resulting sequences; finding the second is sufficient to prove ambiguity.

## Detecting a wrong option-order premise

Generic Gate B assumes that the relative order of draw-origin cards in `pendingChoice.options` is their real sequential draw order. That assumption is deliberately checked rather than trusted silently.

After Gate B derives `drawn_sequence`, RL compares it with the corresponding root-relative slice of the **Held Stable Snapshot**:

```text
option-derived drawn_sequence
==
StableRoot.Player.DrawPile[cursor : cursor + len(R)]
```

This Stable Snapshot is an independent contract sentinel. It is **not** a fallback derivation source: if the option-derived order disagrees, RL does not replace it with Snapshot order.

On disagreement:

```text
visible_draw_constraints = ()
visible_draw_tracking_blocked = True
visible_draw_tracking_error = diagnostic containing
    inferred option-derived CardId order
    expected Stable-root CardId order
    root-relative offsets
```

Therefore a future Emulator/game change that violates the PendingChoice ordering premise becomes an explicit recorded error instead of silently producing a wrong replay reconstruction.

The sentinel comparison uses the gameplay fields representable in both Observation and current Snapshot. Snapshot currently lacks enchantment; full observable-state materialization still fails closed for that representation gap.

### Important ordering boundary

Public Observation `pre.drawPile` / `post.drawPile` list order is **not** used as provenance evidence. Gate A treats those lists as multisets.

The ordered Held Stable Snapshot is used only to validate the option-derived Gate-B sequence. Thus reordering the published Observation DrawPile does not change the inferred transfer/order when the same independent Stable root is supplied.

## Replay Prefix accounting

Each `ReplayPrefixEntry` can carry:

```text
visible_draw_constraints: (root-relative offset, observable_card_key)*
visible_draw_tracking_blocked: bool
visible_draw_tracking_error: str | None
```

Transition handling is:

```text
Gate A + unique Gate B + Stable-root order validation succeed:
    record drawn_sequence at current cursor
    cursor += len(drawn_sequence)

DrawPile multiset unchanged:
    zero draw
    cursor unchanged

DrawPile changed but Gate A fails:
    block later tracking and record diagnostic

Gate B is ambiguous:
    block later tracking and record diagnostic

Gate-B order disagrees with Stable root:
    block later tracking and record ordering-contract diagnostic
```

Once blocked, later root-relative offsets are never guessed past the unexplained transition. Consumed constraints must form one contiguous Stable-root prefix `0..N-1`.

## Materialization

The consumer pins only the observed `observable_card_key` values at proven root-relative offsets. Every unobserved CardId keeps its relative candidate-hypothesis order.

Concrete Snapshot instances are local restore details only. Physical `InstanceId` is not a proof input and is not stored in Replay Prefix.

For a pinned observable key, materialization verifies that all matching Stable-root Snapshot cards share the same hidden gameplay-relevant state after excluding physical `InstanceId` and `Zone`. If observable-equal copies differ in hidden state such as local cost modifiers or temporary star costs, materialization fails closed instead of selecting a physical copy arbitrarily.

The implementation never copies the real post-transition DrawPile as the hypothesis remainder, because that would freeze unobserved future RNG.

## Inputs deliberately not used as the safety gate

Special restoration does not depend on:

- card-name/CardId allowlists;
- `scope == ActionContinuation`;
- discard/select operation labels;
- `sourceZone`;
- pending origin/source metadata;
- `triggering_action` identity/type;
- `rng_id`;
- public `cardInstanceId`, HMAC tokens, or draw ordinals;
- `CardInstanceSnapshot.InstanceId`;
- raw draw history;
- published Observation DrawPile list order.

## Regression coverage

`Combat/tests/test_replay_prefix_visible_draw_pinning.py` covers:

- Acrobatics-like mixed Hand through generic Gate A + Gate B;
- drawn-only / draw-N-choose-M-shaped choices through the same generic Gate B;
- absence of card allowlist / choice-semantic gates;
- public Observation DrawPile-order independence;
- explicit detection of wrong PendingChoice draw order against the Stable root;
- R/E duplicate ambiguity fail-closed behavior;
- zero-draw transitions;
- blocking after unexplained DrawPile mutation;
- contiguous root-relative prefix consumption;
- same-CardId cards with different observable state;
- fail-closed hidden gameplay-state ambiguity.

`API/tests/test_acrobatics_exact_instance_replay_pinning.py` retains its historical filename for continuity but validates observable-state replay constraints rather than exact physical-instance constraints.

## Cross-repo responsibility

- **STS2_Emulator #25:** preserve sufficient ordinary public gameplay state and PendingChoice option ordering; no replay-only identity/provenance protocol.
- **STS2_RL #64:** own Gate A, generic Gate B, Stable-root order sentinel, Replay Prefix cursor/error accounting, and observed-prefix materialization.
- **STS2_Training #72:** remain replay-provenance agnostic and surface replay branch faults for label safety.
