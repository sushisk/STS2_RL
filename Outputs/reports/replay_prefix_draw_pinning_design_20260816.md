# `replay_mismatch` exact-instance fix: visibility-scoped Replay Prefix pinning

Status: **STS2_RL implementation in PR #64; paired Emulator contract is STS2_Emulator PR #25.**

This document supersedes the earlier version of this PR that proposed
`StepResult.Info["cardsDrawnThisStep"]` plus CardId-only pinning. Do not implement or consume
that raw-draw interface. Emulator PR #25 deliberately rejected it because a draw event is not
automatically client-visible and exposing raw ordered draw history can cross the RNG-hypothesis
privacy boundary.

## 1. Problem

A real committed action can consume the DrawPile and stop at a card `PendingChoice`. The original
reproduction is `ACROBATICS` (draw 3, then discard 1), but the replay failure is not specific to
that CardId: the same problem applies to a card transition that draws one or more cards and then
opens a hand choice/discard over the resulting hand. A Branch Worker reconstructs that Pending state
by Restoring the Held Stable Snapshot under a belief-derived DrawPile/RNG hypothesis and replaying
the committed prefix. If the substituted hypothesis draws a different candidate set,
`DecisionSignature` correctly reports `replay_mismatch` on `candidate_semantic_keys`; all siblings
sharing the prefix can then fault together. STS2_Training PR #72 makes such branch faults visible,
but the root cause belongs here.

CardId-only pinning is not sufficient. Two concrete cards can share a CardId while differing in
upgrade/cost/other instance state. Replaying with the right CardIds but the wrong physical copies
can silently reconstruct a different choice.

There is a second, separate requirement: **visible identity is not, by itself, draw provenance**.
Emulator #25 makes a concrete card identity visible at a PendingChoice, but it deliberately does not
define arbitrary `pendingChoice.options` as an ordered draw-history feed. RL therefore must not
generalize from root-DrawPile membership alone. The supported contract below instead requires the
full first-transition draw-then-choose/discard-from-hand shape, including the actual triggering card
identity and an unchanged pre-existing hand prefix.

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

The extractor accepts the generic transition shape **"play a card, draw one or more cards, then
choose/discard from the resulting hand"**, but only when all of the following are true:

- this is the **first** transition after the Held Stable root (`replay_prefix` is empty), so the
  draw cursor is root position 0;
- the triggering `SemanticAction` is a card action, and its semantic key identifies the CardId that
  was actually played;
- the resulting PendingChoice is `ActionContinuation` with `choiceOperation == "discard"`;
- both the top-level PendingChoice source zone and canonical
  `pendingChoice.choiceSemantics.sourceZone` are `hand`;
- exactly one concrete card from the Stable root Hand is absent from the PendingChoice, and its
  CardId matches the triggering action's CardId;
- every other Stable-root Hand card remains at the front of the PendingChoice in the same order and
  with the same exact public instance identity;
- the remaining PendingChoice tail consists only of distinct concrete cards from the Stable root
  DrawPile.

Under that complete shape, the visible option tail is treated as the newly drawn ordered cards for
this first transition. The pre-existing Hand cards are verified but are **not** recorded as draw
constraints; only the appended tail is recorded, in its visible order.

The important point is that support is based on the verified transition shape and the **actual
triggering card**, not on an `ACROBATICS` literal. `ACROBATICS` remains the paired cross-repo
regression, while cards such as `PREPARED` may use the same path when they satisfy the same verified
shape. A different post-step shape does not qualify merely because its options can be mapped to root
DrawPile instances.

Any later Replay Prefix entry fails closed to `()` because Emulator #25 provides no
visibility-scoped draw cursor/offset for that case. Mechanics that reveal/select directly from
`sourceZone == "drawPile"` are structurally different and remain out of this extractor; they need a
separate provenance contract.

### 3.2 Hypothesis reconstruction

For a verified constraint sequence, `apply_hypothesis_to_context()` performs two linked operations
before Restore:

1. move the constrained CardIds to the front of the CardId-level hypothesis while preserving the
   relative order of the hypothesis tail;
2. allocate those front positions to the exact constrained `CardInstanceSnapshot.InstanceId`
   values, then allocate all remaining CardIds from the remaining concrete cards using the prior
   canonical ordering.

Because the recorder emits constraints only for the verified root-first transition shape, these
front positions have a root-relative meaning within that contract. The resulting snapshot replays
the already-observed Pending choice with the same physical drawn copies, including
upgraded/unupgraded duplicates sharing a CardId. The remaining hidden future still comes from the
belief hypothesis.

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
- Pre-existing Hand cards visible in the choice are verified and excluded from draw constraints.
- The triggering action's CardId must match the one root-Hand card removed by the transition.
- No later Replay Prefix choice is flattened to root draw position 0 without a proven offset.
- Hidden draw/discard/exhaust pile masking still removes order and concrete instance identity.
- Exact ids are never added to `SearchHypothesisId` or hypothesis generation.
- Unsupported or ambiguous shapes fail closed to unpinned behavior rather than guessing.

General reconstruction of later-prefix draws, direct draw-pile reveal/select mechanics, and
mid-prefix reshuffles remains out of scope until there is a separately justified provenance/position
contract for those cases.

## 5. Tests / merge gate

Hosted pure-Python regressions cover:

- public `cardv-*` -> internal Snapshot identity translation;
- mixed-hand Acrobatics: a pre-existing Hand card remains visible internally while only the newly
  drawn exact instances become constraints;
- generic first-transition draw-then-choose behavior, including a `PREPARED`-shaped positive case;
- rejection when the triggering CardId does not match the missing root-Hand card;
- rejection when the transition is not first after the Stable root;
- rejection when scope/operation/canonical source-zone provenance does not match;
- rejection when the pre-existing Hand is reordered or mutated;
- CardId-prefix reordering while preserving the hypothesis tail;
- exact allocation of the upgraded copy among duplicate same-CardId instances;
- mask behavior: `cardInstanceId` is removed from PendingChoice options, `choice_card` parameters,
  and hidden piles before the DTO is exposed to Training.

The required repo-local `rl-hosted-contract` explicitly runs both
`Combat/tests/test_replay_prefix_visible_draw_pinning.py` and
`Combat/tests/test_replay_prefix_draw_then_choose_generalization.py`, so the generic triggering-action
contract is part of the hosted signal.

The paired RL/Emulator regression uses a normal mixed Hand (`ACROBATICS` + pre-existing
`NEUTRALIZE`) and explicitly exercises the review-critical namespace sequence:

1. capture Stable `S0` in session A;
2. `Restore(S0)`, which creates session B;
3. capture Stable `S1` in session B;
4. play Acrobatics from `S1`;
5. verify RL translated the raw visible `cardv-*` values into three internal exact-instance draw
   constraints, including distinct upgraded/unupgraded `DEFEND_SILENT` copies;
6. verify the Training-facing PendingChoice/actions contain no `cardInstanceId`;
7. require multiple `rng_id` hypotheses to replay the resulting four-option discard choice without
   `replay_mismatch`.

That paired Acrobatics regression validates the concrete cross-repo integration and privacy
boundary. It is not intended to impose an `ACROBATICS` CardId allowlist on the RL extractor.

STS2_Training PR #72 carries the production Oracle/Beam integration path for the same mixed-hand
Acrobatics reproduction. Its Training-facing assertions must follow the paired privacy contract
above: it should test candidate behavior and zero branch faults without depending on
`cardInstanceId` being present in the masked DTO.

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
- direct draw-pile reveal/select mechanics without a separately justified provenance contract;
- general mid-prefix reshuffle reconstruction;
- changing the separate `CombatStartReplayRoot` hypothesis path.
