# Replay-prefix DrawPile restoration — observable transfer + ordered draw proof

Status: implemented in STS2_RL PR #64. No new Emulator-visible identity/provenance protocol is required.

## Problem

A committed Replay Prefix may consume cards from DrawPile before reaching a PendingChoice. Replaying that already-observed prefix under a different CardId-level RNG hypothesis can otherwise expose a different candidate set and produce `replay_mismatch`.

The fix must constrain only the already-observed past. It must not turn future RNG hypotheses into physical-card hypotheses or freeze the unobserved DrawPile remainder.

## Observable card equality

All proof-side comparisons use an `observable_card_key`: CardId plus public gameplay-affecting state such as cost, upgrade state, Tinker state and enchantment. Physical instance identity, option id, zone and list position are excluded.

`SearchHypothesisId` remains CardId-level.

## Gate A — unordered DrawPile -> Choice transfer

For the immediate pre/post states of one committed transition, using multisets of `observable_card_key`:

```text
D0 = pre.drawPile
D1 = post.drawPile
O  = post.pendingChoice.options

require D1 ⊆ D0
R = D0 - D1
require R != ∅
require R ⊆ O
```

For each eligible non-DrawPile public zone that exists in both observations:

```text
P_z = pre[z] ∩ post[z]
P   = Σ_z P_z        # multiset sum
```

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

This proves that every PendingChoice option is explained either by a card removed from DrawPile in this transition (`R`) or by a card already present and persistent in another published zone (`E`).

This gate does not use CardId allowlists, `choiceOperation`, `sourceZone`, `scope`, origin metadata, `rng_id`, InstanceId, HMAC/public card identity, raw draw history, or DrawPile publication order.

### Examples

Acrobatics:

```text
options = surviving Hand + drawn cards
R = drawn cards
E = surviving Hand
```

A future draw-N-choose-M mechanic that presents only the drawn cards:

```text
options = drawn cards
R = drawn cards
E = ∅
```

Both use the same Gate A.

## Gate B — ordered sequential-draw proof

Gate A proves transfer, not sequential draw provenance. Tutor/reveal/arbitrary DrawPile selection may create a similar multiset change and must not automatically trigger "put these cards at the DrawPile front" replay logic.

Therefore sequential-draw restoration requires a second proof that an ordered subsequence corresponding to `R` is the real draw/addition order.

Current Gate B is structural rather than card-specific. For the verified engine path used by Acrobatics it requires:

```text
pendingChoice.options == post.hand
options == surviving/pre-existing Hand subsequence + contiguous drawn suffix
multiset(drawn suffix) == R
```

The real-game implementation draws first, appends drawn cards to Hand, and then enumerates Hand for the choice, so the suffix order is the actual draw order.

A future draw-N-choose-M path can reuse Gate A immediately. It should add a Gate-B order prover only after its common game/Emulator primitive is verified to preserve draw order in the published options.

Special restoration is therefore:

```text
Gate A succeeds
AND
Gate B succeeds
```

## Replay Prefix draw cursor

`ReplayPrefixEntry.visible_draw_constraints` stores only:

```text
(root-relative draw offset, observable_card_key)
```

No Snapshot InstanceId is stored.

The cursor advances only for Gate-A+Gate-B-proven sequential draws. A transition whose DrawPile multiset does not change does not advance it. If DrawPile changes but Gate A or Gate B cannot explain the mutation, that entry marks draw tracking blocked; later root-relative offsets are not inferred past that point.

This replaces card-specific zero-draw allowlists with state-based accounting.

## Materialization

The hypothesis consumer pins the observed `observable_card_key` values at their proven root-relative offsets and preserves the candidate hypothesis order for every unobserved position.

It must not copy the entire real post-step DrawPile as the candidate remainder, because that would freeze unobserved future RNG.

Concrete Snapshot instances are selected only as a local implementation detail. If one observable key maps to multiple Snapshot cards with different hidden gameplay-affecting state (for example different local cost modifier state), public evidence is insufficient and materialization fails closed rather than selecting by InstanceId.

If the copies differ only by physical identity/zone, they are replay-equivalent and any canonical concrete allocation is acceptable.

Current Snapshot cards do not expose enchantment in the same representation as Observation; an observed enchanted key therefore fails closed rather than silently discarding that behavior difference.

## Boundaries deliberately not used as the safety gate

The restoration decision does not depend on:

- card-name/CardId allowlists;
- `ActionContinuation`;
- discard/select operation labels;
- source zone or pending origin metadata;
- `rng_id`;
- public or internal physical card identity;
- raw draw history;
- public DrawPile ordering.

Those values may remain useful for diagnostics, but they do not determine whether special restoration is safe.

## Regression coverage

`Combat/tests/test_replay_prefix_visible_draw_pinning.py` covers:

- mixed-Hand Acrobatics through generic Gate A + Hand-append Gate B;
- no card allowlist / choice-semantic safety gate;
- draw-N-choose-M-shaped Gate A and the Gate-B extension point;
- DrawPile publication order independence;
- zero-draw transitions;
- blocking after an unexplained DrawPile mutation;
- contiguous root-relative prefix consumption;
- same-CardId cards with different observable state;
- fail-closed hidden gameplay-state ambiguity.

`API/tests/test_acrobatics_exact_instance_replay_pinning.py` retains the paired real-Emulator filename for continuity, but now asserts observable-state constraints rather than exact physical InstanceId constraints.

## Cross-repo responsibility

- STS2_Emulator #25: preserve sufficient public gameplay state and verified sequential-draw option ordering; no replay-only identity protocol.
- STS2_RL #64: own Gate A, Gate B, Replay Prefix cursor accounting, and observed-prefix materialization.
- STS2_Training #72: remain provenance-agnostic and make replay faults visible instead of silently dropping branches.
