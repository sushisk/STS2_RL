# Replay-prefix draw pinning — scoped design after review

Status: implemented in STS2_RL PR #64. No new STS2_Emulator runtime identity/provenance contract is required.

## Problem

A real committed prefix can draw cards and stop at a discard PendingChoice. Replaying the same prefix under a different CardId-level RNG hypothesis can otherwise reach a different candidate set and fail `candidate_semantic_keys` verification. The harvested reproduction is Silent / `ACROBATICS` / seed 101. `DAGGER_THROW` and `PHOTON_CUT` have the same relevant shape after their multi-enemy target-selection hop.

The earlier redesign added a public `cardInstanceId` HMAC namespace plus draw-ordinal suffixes. Review found that correct but unnecessarily broad. The existing Stable root and visible DTO already contain enough information for the confirmed bug, so the final design does not add an Emulator-side identity/provenance protocol.

## Final contract

`ReplayPrefixEntry.visible_draw_constraints` is RL-internal only:

```text
(root-relative draw offset, CardId, CardInstanceSnapshot.InstanceId)
```

No concrete identity is added to Emulator DTOs or Training DTOs.

For the mechanically audited draw-then-discard shape, RL proves all of the following:

1. the triggering producer/effect is one of the audited effects for that producer class;
2. the result is an ActionContinuation discard choice from Hand;
3. the consumed source card is the one root-Hand card that disappeared for the currently audited card-play path;
4. every other pre-existing Hand card remains the visible option prefix with the same public gameplay state;
5. the appended visible tail has the same gameplay state as the exact ordered cards at root DrawPile offsets `0..N-1`.

Once item 5 is proven, the root Snapshot itself identifies the exact internal card at each offset. No HMAC/public physical-copy token is needed. Upgrade level, cost, type, target, rarity and Tinker state are compared; therefore two copies sharing a CardId but differing in gameplay state cannot be silently swapped. Copies that are structurally identical are behaviorally interchangeable for this purpose.

For the multi-enemy targeted cards, Emulator `BeginPlayCard` publishes `choice_target` before enqueueing `PlayCardAction`. #64 accepts exactly that one audited zero-draw prefix hop, then applies the same root-offset proof when the card resolution reaches discard. Other non-empty unknown-RNG prefixes fail closed.

## Producer / consumer split

The design intentionally separates **producer-specific proof** from **source-agnostic constraint consumption**.

Current producer proof is implemented only for the mechanically audited card paths (`ACROBATICS`, `DAGGER_THROW`, `PHOTON_CUT`). That narrowness is about what is proven today, not about making the replay-pinning architecture card-only.

The retained architecture must allow future producers in these classes to emit the same internal constraint shape without changing hypothesis materialization:

- card effects;
- relic effects;
- potion effects.

Future relic/potion support should first try to prove root-relative draw facts from the Emulator data and Stable Snapshot that already exist. A new Emulator-visible identity/provenance protocol is **not** a prerequisite or assumed extension mechanism. If a particular relic/potion path cannot be proven safely from existing visible state, that path must fail closed until there is a concrete reproduced case and a minimal justified solution.

Producer-specific proof may differ by source. For example, a relic or potion does not need to satisfy the current "played source card disappeared from root Hand" proof. The common contract begins only after a producer has safely derived `(root-relative offset, CardId, internal Snapshot InstanceId)` constraints.

## Hypothesis materialization

`SearchHypothesisId` stays CardId-level. The consumer remains source-agnostic and can collect verified constraints from arbitrary ReplayPrefix entries and pin them by root-relative offset. This offset handling is intentionally retained even though the currently confirmed cases have at most one draw-constrained transition per Replay Prefix window.

Constrained CardIds and internal Snapshot instances are fixed at proven offsets; all remaining concrete cards are allocated by the existing `_card_identity_key` canonical ordering.

The materialization layer must not branch on whether a constraint originated from a card, relic, or potion. Producer classification belongs only in the proof/extraction layer. The committed-step call sites use `visible_draw_constraints_from_committed_transition()`, which dispatches to producer-specific proofs; the currently registered proof is the audited card path, and future relic/potion proofs extend that producer list without changing ReplayPrefix or hypothesis materialization.

## Deliberate current scope boundary

This patch does **not** claim that arbitrary similar-looking card, relic, or potion effects are already proven. The confirmed producer coverage remains the reproduced `ACROBATICS` path plus the structurally confirmed `DAGGER_THROW` / `PHOTON_CUT` two-hop path.

Direct draw-pile reveal/tutor mechanics, Start-of-Combat relic choices, potion-origin choices, reshuffle-crossing cases, and other unverified producer shapes remain fail-closed today. They are retained as supported **extension categories** in the RL design, not enabled speculatively.

When a relic or potion path is implemented later, it should reuse the same ReplayPrefix constraint representation and hypothesis consumer rather than introducing a parallel relic-specific or potion-specific replay-pinning mechanism.

That scope reduction addresses the reproduced fault while preserving the intended card/relic/potion extension surface and avoiding a new Emulator-visible identity/session/Restore namespace.

## Regression coverage

- `Combat/tests/test_replay_prefix_visible_draw_pinning.py`: mixed-hand Acrobatics, duplicate CardId state, fail-closed unaudited shapes, DaggerThrow target hop, offset-aware consumer, exact internal allocation.
- `API/tests/test_acrobatics_exact_instance_replay_pinning.py`: paired real-Emulator path and multiple `rng_id` hypotheses without replay mismatch.

Future relic/potion regressions should be added at the producer-proof boundary and must flow through the same existing constraint consumer/materialization tests.

STS2_Emulator PR #25 is therefore re-scoped to documentation/coordination only; its HMAC `cardInstanceId` implementation is not part of the final contract.
