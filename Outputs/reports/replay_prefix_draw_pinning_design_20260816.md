# `replay_mismatch` exact-instance fix: visibility-scoped Replay Prefix pinning

Status: **STS2_RL implementation in PR #64; paired Emulator contract is STS2_Emulator PR #25.**

This document supersedes the earlier version of this PR that proposed
`StepResult.Info["cardsDrawnThisStep"]` plus CardId-only pinning. Do not implement or consume
that raw-draw interface. Emulator PR #25 deliberately rejected it because a draw event is not
automatically client-visible and exposing raw ordered draw history can cross the RNG-hypothesis
privacy boundary.

## 1. Problem

A real committed action can consume the DrawPile and stop at a card `PendingChoice` (the confirmed
reproduction is `ACROBATICS`: draw 3, then discard 1). A Branch Worker reconstructs that Pending
state by Restoring the Held Stable Snapshot under a belief-derived DrawPile/RNG hypothesis and
replaying the committed prefix. If the substituted hypothesis draws a different candidate set,
`DecisionSignature` correctly reports `replay_mismatch` on `candidate_semantic_keys`; all siblings
sharing the prefix can then fault together. STS2_Training PR #72 makes such branch faults visible,
but the root cause belongs here.

CardId-only pinning is not sufficient. Two concrete cards can share a CardId while differing in
upgrade/cost/other instance state. Replaying with the right CardIds but the wrong physical copies
can silently reconstruct a different choice.

There is a second, separate requirement: **visible identity is not draw provenance**. Emulator #25
makes a concrete card identity visible at a PendingChoice, but it deliberately does not define
`pendingChoice.options` as an ordered draw-history feed. A card being visible and having belonged
to the Stable root DrawPile is therefore not enough to place it at a particular draw position.

## 2. Emulator #25 contract (source of truth)

Exact identity is exposed by the Emulator only where the card is already visible:

- `pendingChoice.options[*].cardInstanceId`
- matching `choice_card.parameters.cardInstanceId`
- `StepResult.Info["selectedCardInstanceId"]` for the selected card

The public identity is an opaque `cardv-<32 hex>` HMAC-derived token. It is intentionally different
from the internal sequential `CardInstanceSnapshot.InstanceId`. The HMAC key is the **current live
DecisionFrame `combatSessionId`**.

Ordinary Capture within one live session does not rotate the public token namespace. Restore does:
`RestoreSnapshot()` creates a fresh DecisionFrame combat session, preserves the source Snapshot's
stable internal card `InstanceId` values, and uses the newly-issued session id as the HMAC namespace.
Therefore a Stable Snapshot captured **after** Restore is self-describing: its own
`Metadata.CombatSessionId` plus its internal `CardInstanceSnapshot.InstanceId` values reproduce the
`cardv-*` tokens subsequently emitted in that restored live session. This is the contract RL relies
on when a Held Stable root comes from a resumed/restored execution path.

Raw `cardsDrawnThisStep` / `CardDrawnEntry` history is deliberately absent. `SearchHypothesisId`
remains CardId-level.

`cardInstanceId` is an Emulator -> RL reconstruction signal, not a Training/policy feature. RL must
consume it from the raw committed step before masking, translate it into internal Snapshot instance
constraints, and then remove it from the Training-facing `masked_emulator_dto`. This avoids adding
persistent concrete-copy linkability to the policy boundary while retaining exact replay inside RL.

## 3. RL implementation

### 3.1 Replay Prefix constraint

`ReplayPrefixEntry.visible_draw_constraints` is an ordered tuple of
`(CardId, CardInstanceSnapshot.InstanceId)`. It defaults to empty. Only real committed stepping
paths may populate it:

1. `API.instance_combat.CombatInstance.commit_action()`
2. `Combat.search.main_loop._run_exec_loop()`

Search-internal `ReplayPrefixEntry` sites keep the default empty value. A fact observed under one
hypothesis must never become a pin for a sibling hypothesis.

`visible_draw_constraints_from_pending_choice()` first translates Emulator-visible `cardv-*`
identities back into internal Snapshot instance ids by reproducing Emulator #25's HMAC mapping with
the internal Stable Snapshot `Metadata.CombatSessionId`. For a Held Stable snapshot captured after
Restore, that metadata is the restored live session's new namespace, so the translation remains
self-consistent across `Restore -> Capture -> real step`.

Crucially, it does **not** treat arbitrary visible PendingChoice options as a draw prefix. The
current minimal fix records constraints only for the confirmed, position-provable transition shape:

- this is the **first** transition after the Held Stable root (`replay_prefix` is empty), so the
  draw cursor is known to be root position 0;
- the resulting choice is an `ActionContinuation` card choice whose operation is `discard`;
- both the top-level PendingChoice source zone and canonical
  `pendingChoice.choiceSemantics.sourceZone` are `hand`;
- exactly one concrete card from the Stable root Hand is absent from the PendingChoice, and that
  missing card is `ACROBATICS`;
- every other Stable-root Hand card is still present at the front of the PendingChoice in the same
  order and with the same exact public instance identity;
- the remaining PendingChoice tail consists only of distinct concrete cards from the Stable root
  DrawPile.

Under this shape, the PendingChoice is the unchanged pre-existing Hand followed by the newly drawn
Acrobatics cards. The pre-existing Hand cards are verified but **not** recorded as draw constraints;
only the tail is recorded, in its visible order.

This fixes the normal mixed-hand case where Acrobatics was played while another card (for example
`NEUTRALIZE`) was already in Hand. The old all-or-nothing root-DrawPile check rejected the whole
choice as soon as it encountered that pre-existing card.

Any other shape fails closed to `()` instead of inferring hidden provenance. In particular, a later
Replay Prefix entry is never converted into an absolute root-position pin because Emulator #25
provides no visibility-scoped draw cursor/offset for that case.

### 3.2 Hypothesis reconstruction

For a verified constraint sequence, `apply_hypothesis_to_context()` performs two linked operations
before Restore:

1. move the constrained CardIds to the front of the CardId-level hypothesis while preserving the
   relative order of the hypothesis tail;
2. allocate those front positions to the exact constrained `CardInstanceSnapshot.InstanceId`
   values, then allocate all remaining CardIds from the remaining concrete cards using the prior
   canonical ordering.

Because the recorder only emits constraints for the root-first Acrobatics shape, these front
positions have a proven root-relative meaning. The resulting snapshot replays that already-observed
Pending choice with the same physical drawn copies, including upgraded/unupgraded duplicates sharing
a CardId. The remaining hidden future still comes from the belief hypothesis.

`SearchHypothesisId` does **not** gain `cardInstanceId`. Its digest remains RNG + CardId order. Exact
instance ids are context-local replay evidence derived from already-visible facts, not an additional
hidden RNG dimension.

### 3.3 Training-facing masking

The raw `BattleState` and raw Emulator legal actions still contain `cardInstanceId` long enough for
`commit_action()` / the real execution loop to build `visible_draw_constraints`. The generic public
mask then removes every `cardInstanceId` / `card_instance_id` key recursively, including:

- `pendingChoice.options[*].cardInstanceId`;
- `choice_card.parameters.cardInstanceId`;
- any accidental occurrence in other Training-facing fragments.

Hidden draw/discard/exhaust piles remain multisets without concrete identity or order. Training sees
the normal card fields and semantic/action identity needed to choose among candidates, but not the
Emulator's persistent concrete-copy token.

## 4. Privacy and safety properties

- No true/unsubstituted DrawPile is replayed to Training.
- No raw ordered draw-event feed is introduced.
- Public `cardv-*` identity is translated internally; the public token is never compared directly
  with the sequential Snapshot `InstanceId`.
- `cardInstanceId` is removed before constructing the Training-facing `masked_emulator_dto`.
- Restore rotates the public-card namespace with the new DecisionFrame session, while a post-Restore
  Stable Snapshot remains self-describing for RL's internal translation.
- A pre-existing Hand card visible in the Acrobatics discard choice is verified and excluded from
  draw constraints.
- No later Replay Prefix choice is flattened to root draw position 0 without a proven offset.
- Hidden draw/discard/exhaust pile masking still removes order and concrete instance identity.
- Exact ids are never added to `SearchHypothesisId` or hypothesis generation.
- Unsupported or ambiguous provenance fails closed to unpinned behavior rather than guessing.

General reconstruction of later-prefix draws, arbitrary card-selection mechanics, and mid-prefix
reshuffles remains out of scope until Emulator exposes a visibility-safe provenance/position
contract for those cases.

## 5. Tests / merge gate

Hosted pure-Python regressions cover:

- public `cardv-*` -> internal Snapshot identity translation;
- mixed-hand Acrobatics: a pre-existing Hand card remains visible internally while only the three
  newly drawn exact instances become constraints;
- rejection when the transition is not first after the Stable root;
- rejection when scope/operation/canonical source-zone provenance does not match;
- rejection when the pre-existing Hand is reordered or the missing played card is not Acrobatics;
- CardId-prefix reordering while preserving the hypothesis tail;
- exact allocation of the upgraded copy among duplicate same-CardId instances;
- mask behavior: `cardInstanceId` is removed from PendingChoice options, `choice_card` parameters,
  and hidden piles before the DTO is exposed to Training.

The paired RL/Emulator regression uses a normal mixed Hand (`ACROBATICS` + pre-existing
`NEUTRALIZE`) and now explicitly exercises the review-critical namespace sequence:

1. capture Stable `S0` in session A;
2. `Restore(S0)`, which creates session B;
3. capture Stable `S1` in session B;
4. play Acrobatics from `S1`;
5. verify RL translated the raw visible `cardv-*` values into three internal exact-instance draw
   constraints, including distinct upgraded/unupgraded `DEFEND_SILENT` copies;
6. verify the Training-facing PendingChoice/actions contain no `cardInstanceId`;
7. require multiple `rng_id` hypotheses to replay the resulting four-option discard choice without
   `replay_mismatch`.

STS2_Training PR #72 carries the production Oracle/Beam integration path for the same mixed-hand
shape. Its Training-facing assertions must follow the paired privacy contract above: it should test
candidate behavior and zero branch faults without depending on `cardInstanceId` being present in the
masked DTO.

Before treating the cross-repo fix as mergeable, run the paired integration regression against the
current Emulator PR #25 (or a build containing it) and re-run the previously failing harvested
Silent/Acrobatics reproduction with seed override `101`. Expected result: no `replay_mismatch` and
no `RuntimeError: all emulate_actions branch results faulted`.

## 6. Explicitly out of scope

- exporting `cardsDrawnThisStep` or any equivalent raw draw-history signal;
- exposing `cardInstanceId` as a Training/policy feature;
- adding concrete instance ids to `SearchHypothesisId`;
- pinning search-internal/hypothesis-fabricated Replay Prefix entries;
- inferring a later Replay Prefix draw offset from visible membership alone;
- general card-choice provenance inference beyond the proven root-first Acrobatics shape;
- general mid-prefix reshuffle reconstruction;
- changing the separate CombatStartReplayRoot hypothesis path.
