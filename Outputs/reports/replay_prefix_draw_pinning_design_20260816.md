# Replay-prefix draw pinning — scoped design after review

Status: implemented in STS2_RL PR #64. No new STS2_Emulator runtime identity contract is required.

## Problem

A real committed prefix can draw cards and stop at a discard PendingChoice. Replaying the same prefix under a different CardId-level RNG hypothesis can otherwise reach a different candidate set and fail `candidate_semantic_keys` verification. The harvested reproduction is Silent / `ACROBATICS` / seed 101. `DAGGER_THROW` and `PHOTON_CUT` have the same relevant shape after their multi-enemy target-selection hop.

The earlier redesign added a public `cardInstanceId` HMAC namespace plus draw-ordinal suffixes. Review found that correct but unnecessarily broad. The existing Stable root and visible card DTO already contain enough information for the confirmed bug.

## Final contract

`ReplayPrefixEntry.visible_draw_constraints` is RL-internal only:

```text
(root-relative draw offset, CardId, CardInstanceSnapshot.InstanceId)
```

No concrete identity is added to Emulator DTOs or Training DTOs.

For the mechanically audited draw-then-discard shape, RL proves all of the following:

1. source card is one of the audited effects (`ACROBATICS`, `DAGGER_THROW`, `PHOTON_CUT`);
2. the result is an ActionContinuation discard choice from Hand;
3. the played source card is the one root-Hand card that disappeared;
4. every other pre-existing Hand card remains the visible option prefix with the same public gameplay state;
5. the appended visible tail has the same gameplay state as the exact ordered cards at root DrawPile offsets `0..N-1`.

Once item 5 is proven, the root Snapshot itself identifies the exact internal card at each offset. No HMAC/public physical-copy token is needed. Upgrade level, cost, type, target, rarity and Tinker state are compared; therefore two copies sharing a CardId but differing in gameplay state cannot be silently swapped. Copies that are structurally identical are behaviorally interchangeable for this purpose.

For the multi-enemy targeted cards, Emulator `BeginPlayCard` publishes `choice_target` before enqueueing `PlayCardAction`. #64 accepts exactly that one audited zero-draw prefix hop, then applies the same root-offset proof when the card resolution reaches discard. Other non-empty unknown-RNG prefixes fail closed.

## Hypothesis materialization

`SearchHypothesisId` stays CardId-level. The consumer remains general enough to collect verified constraints from arbitrary ReplayPrefix entries and pin them by root-relative offset. This forward-compatible offset handling is intentionally retained even though the currently confirmed cases have at most one draw-constrained transition per Replay Prefix window.

Constrained CardIds and internal Snapshot instances are fixed at proven offsets; all remaining concrete cards are allocated by the existing `_card_identity_key` canonical ordering.

## Deliberate scope boundary

This fix does **not** infer draw provenance for arbitrary similar-looking card or relic choices. In particular, Start-of-Combat relic choices and direct draw-pile reveal/tutor mechanics need a separate mechanically audited contract if they are later shown to cause the same replay fault. They are not widened into this bug fix merely for symmetry.

That scope reduction is intentional: it addresses the reproduced fault and confirmed 2-hop variant while avoiding a new Emulator-visible identity/session/Restore namespace.

## Regression coverage

- `Combat/tests/test_replay_prefix_visible_draw_pinning.py`: mixed-hand Acrobatics, duplicate CardId state, fail-closed unaudited shapes, DaggerThrow target hop, offset-aware consumer, exact internal allocation.
- `API/tests/test_acrobatics_exact_instance_replay_pinning.py`: paired real-Emulator path and multiple `rng_id` hypotheses without replay mismatch.

STS2_Emulator PR #25 is therefore re-scoped to documentation/coordination only; its HMAC `cardInstanceId` implementation is not part of the final contract.
