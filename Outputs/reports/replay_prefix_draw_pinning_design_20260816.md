# Replay-prefix DrawPile restoration — current implemented design

Status: implemented in STS2_RL PR #64.

This document describes the implementation currently present on the PR branch. Earlier exact-instance / card-allowlist designs are superseded.

## Goal

A committed Replay Prefix may already have consumed cards from DrawPile before reaching a PendingChoice. Replaying that observed prefix under another CardId-level RNG hypothesis can otherwise expose a different choice and produce `replay_mismatch`.

The fix must reproduce only the already-observed sequential draws. It must not:

- turn future RNG hypotheses into physical-card hypotheses;
- freeze the unobserved DrawPile remainder;
- depend on card-name allowlists or replay-only identity/provenance fields.

The implementation therefore separates two questions:

1. **Gate A:** did observable card state move from DrawPile into the current PendingChoice?
2. **Gate B:** can the transferred cards be ordered safely as the actual sequential draw order?

Special DrawPile restoration is enabled only when both gates succeed.

## Observable card equality

Proof-side comparisons use `observable_card_key(card)` rather than CardId alone or physical instance identity.

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

It deliberately excludes option id, zone, list position and `InstanceId`.

The Snapshot projection currently has no equivalent enchantment field, so its enchantment component is `None`. Consequently, an observed enchanted card does not silently collapse to an unenchanted Snapshot card; materialization fails closed until equivalent Snapshot state exists.

`SearchHypothesisId` remains CardId-level.

## Gate A — unordered observable DrawPile -> Choice transfer

Gate A uses the immediate public pre-state and post-state of one committed transition. All equations below are multiset equations over `observable_card_key`.

Define:

```text
D0 = multiset(pre.drawPile)
D1 = multiset(post.drawPile)
O  = multiset(post.pendingChoice.options)
```

Require:

```text
D1 ⊆ D0
R = D0 - D1
R != ∅
R ⊆ O
```

`R` is therefore exactly the observable multiset that disappeared from DrawPile during this transition.

For each eligible public non-DrawPile zone that is present in **both** observations, compute the persistent part:

```text
P_z = multiset(pre[z]) ∩ multiset(post[z])
P   = Σ_z P_z
```

The implementation currently considers:

```text
hand
discardPile
exhaustPile
playPile
```

A zone absent from either observation is not used as evidence. A zone present in both observations but containing an invalid/unprojectable card causes the proof to fail closed.

Then define:

```text
E = O - R
```

and require:

```text
E ⊆ P
```

Equivalently:

```text
O = R + E
where E ⊆ P
```

Meaning: every choice option must be explained either by a card removed from DrawPile in this transition (`R`) or by a card that was already present and persisted in another published zone (`E`).

### Acrobatics example

```text
pre Hand = [ACROBATICS, H1, H2]
pre Draw = [A, B, C, D, E]

post Hand = [H1, H2, A, B, C]
post Draw = [D, E]
options   = [H1, H2, A, B, C]

R = [A, B, C]
E = [H1, H2]
```

Gate A succeeds without identifying ACROBATICS, discard semantics, source-zone metadata, or a physical card instance.

### Future draw-N-choose-M shape

If a future mechanic removes three cards from DrawPile and presents only those cards:

```text
pre Draw = [A, B, C, D, E]
post Draw = [D, E]
options   = [A, B, C]

R = [A, B, C]
E = ∅
```

Gate A already supports this shape.

**This does not yet mean special sequential-draw restoration is enabled for that mechanic. Gate B is still required.**

## Gate B — ordered sequential-draw proof

Gate A proves membership/conservation only. It does not prove that `R` was removed by sequential drawing.

Tutor, reveal, arbitrary DrawPile selection, or another mechanic could produce a similar multiset transfer. Such a path must not automatically trigger "put these cards at the DrawPile front" replay logic.

The current implementation therefore has an extensible `_ORDERED_DRAW_PROVERS` layer. At present it contains one structural prover for the verified engine path:

```text
draw -> append to Hand -> choose from Hand
```

The current prover requires:

```text
options == post.hand
N = |R|
prefix = options[:-N]
suffix = options[-N:]

multiset(prefix) == E
multiset(suffix) == R
prefix is an order-preserving subsequence of pre.hand
```

When all conditions hold:

```text
drawn_sequence = suffix
```

For the verified real-game Acrobatics path, draw completes first, drawn cards append to the back of Hand, and the subsequent Hand choice enumerates Hand in that order. Therefore the suffix order is the actual draw order.

### draw-N-choose-M extension

A future mechanic whose PendingChoice contains only drawn cards can reuse Gate A immediately (`E = ∅`). To become eligible for special restoration, it still needs an engine-level Gate-B prover demonstrating that the relevant option sequence preserves actual draw order.

The intended extension is to add another structural/common-primitive order prover, not a card-name allowlist.

## Final transition classification

For each committed transition:

```text
if an earlier Replay Prefix entry already blocked draw tracking:
    remain blocked

else if Gate A fails:
    if DrawPile multiset changed:
        block later root-relative draw tracking
    else:
        record zero draw; cursor unchanged

else if Gate B fails:
    block later root-relative draw tracking

else:
    record drawn_sequence at the current root-relative cursor
    cursor += len(drawn_sequence)
```

This matters because once an unexplained DrawPile mutation occurs, later draws can no longer be assigned trustworthy offsets relative to the Stable root.

## Replay Prefix representation

`ReplayPrefixEntry.visible_draw_constraints` stores only:

```text
(root-relative draw offset, observable_card_key)
```

`ReplayPrefixEntry.visible_draw_tracking_blocked` records that an unexplained DrawPile mutation prevents later root-relative inference.

No Snapshot `InstanceId` is stored in Replay Prefix.

When constraints are consumed, they must form one contiguous prefix:

```text
0, 1, 2, ..., N-1
```

The consumer stops at the first blocked entry.

## Materialization

The consumer first rewrites only the observed CardIds at the proven offsets in the candidate CardId-level hypothesis. Required CardIds are removed from the candidate order and inserted at the observed positions; every unobserved CardId retains its relative candidate-hypothesis order.

The resulting effective hypothesis is still CardId-level.

Concrete Snapshot allocation is then a local restore implementation detail:

1. For each pinned `observable_card_key`, find matching cards in the Stable-root Snapshot DrawPile.
2. Ignore physical identity and zone when deciding replay-equivalence.
3. Check that all Snapshot cards sharing that observable key also share the same hidden gameplay-relevant Snapshot state.
4. If hidden gameplay-relevant state differs, fail closed instead of choosing a copy by `InstanceId`.
5. Otherwise choose concrete copies canonically and allocate the unpinned remainder by the existing canonical CardId allocation.

The hidden-state check includes Snapshot data such as local cost modifiers and temporary star costs; it excludes `InstanceId` and `Zone`.

The implementation never copies the real post-transition DrawPile as the candidate remainder. Doing so would freeze unobserved future RNG.

## What is and is not an ordering dependency

The safety proof does **not** use the published `pre.drawPile` list order to decide which cards were drawn. Gate A treats pre/post DrawPile as multisets.

Gate B uses only a separately verified ordering contract for the relevant PendingChoice / Hand sequence in order to recover `drawn_sequence`.

The materializer still uses the existing RL `ordered_draw_pile_card_ids` / root-relative position convention when constructing a restore snapshot. Therefore "DrawPile-order independent" here specifically means **public observation DrawPile order is not provenance evidence for Gate A/B**; it does not mean the search materializer has no ordered DrawPile representation.

## Inputs deliberately not used as the restoration safety gate

Gate A/B does not decide safety from:

- card-name or CardId allowlists;
- `scope == ActionContinuation`;
- `choiceOperation == discard` or other operation labels;
- `sourceZone`;
- pending origin/source metadata;
- `triggering_action` card identity/type;
- `rng_id`;
- public `cardInstanceId`, HMAC tokens, or draw ordinals;
- `CardInstanceSnapshot.InstanceId`;
- raw draw history;
- the published DrawPile list order.

These values may remain useful for diagnostics, but they are not proof premises.

## Current implemented scope

Implemented today:

- generic Gate A for observable DrawPile -> PendingChoice transfer;
- Hand-append Gate B used by the verified Acrobatics engine path;
- root-relative cursor tracking with blocking after unexplained DrawPile mutation;
- observable-state materialization with hidden-state ambiguity rejection.

Not implemented speculatively:

- a Gate-B prover for draw-N-choose-M;
- tutor/reveal/arbitrary DrawPile selection as sequential draw;
- recovery across reshuffle or any other unexplained DrawPile mutation;
- replay-only public card identity/provenance.

## Regression coverage

`Combat/tests/test_replay_prefix_visible_draw_pinning.py` covers the hosted contract for:

- mixed-Hand Acrobatics through generic Gate A + Hand-append Gate B;
- absence of card allowlist / choice-semantic safety gates;
- draw-N-choose-M-shaped Gate A and the Gate-B extension boundary;
- public DrawPile-order independence of Gate A;
- zero-draw transitions;
- blocking after an unexplained DrawPile mutation;
- contiguous root-relative prefix consumption;
- same-CardId cards with different observable state;
- fail-closed hidden gameplay-state ambiguity.

`API/tests/test_acrobatics_exact_instance_replay_pinning.py` retains its historical filename for continuity, but now validates observable-state constraints rather than exact physical-instance constraints. It is a real-Emulator paired regression and is not part of the repo-local required hosted gate.

The required `rl-hosted-contract` runs compile checks plus the hosted protocol/contract tests including `Combat/tests/test_replay_prefix_visible_draw_pinning.py`.

## Cross-repo responsibility

- **STS2_Emulator #25:** expose sufficient normal gameplay state and preserve verified sequential-draw option ordering for supported engine paths; do not add a replay-only identity protocol.
- **STS2_RL #64:** own Gate A, Gate B, Replay Prefix cursor/block accounting, and observed-prefix materialization.
- **STS2_Training #72:** remain replay-provenance agnostic and surface replay branch faults for label safety.
