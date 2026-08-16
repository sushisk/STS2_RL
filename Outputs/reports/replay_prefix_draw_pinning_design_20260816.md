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

## 2. Emulator #25 contract (source of truth)

Exact identity is exposed only where the card is already public:

- `pendingChoice.options[*].cardInstanceId`
- matching `choice_card.parameters.cardInstanceId`
- `StepResult.Info["selectedCardInstanceId"]` for the selected card

The same id survives Capture -> Restore -> Replay. Raw `cardsDrawnThisStep` / `CardDrawnEntry`
history is deliberately absent. `SearchHypothesisId` remains CardId-level.

RL's existing recursive mask already preserves these choice-boundary ids while pile masking drops
concrete identity/order; no new hidden-pile exposure is added by this change.

## 3. RL implementation

### 3.1 Replay Prefix constraint

`ReplayPrefixEntry.visible_draw_constraints` is an ordered tuple of `(CardId, CardInstanceId)`.
It defaults to empty. Only the two real-stepping paths populate it:

1. `API.instance_combat.CombatInstance.commit_action()`
2. `Combat.search.main_loop._run_exec_loop()`

Search-internal `ReplayPrefixEntry` sites (`branch_worker_pool.py`, `multi_round_search.py`) keep
the default empty value. A draw observed under hypothesis A must never become a fact pinned into
hypothesis B.

`visible_draw_constraints_from_pending_choice()` records a Pending option set only when all options
have the Emulator #25 exact identity and every option maps to a distinct, still-unconsumed card in
the Held Stable Snapshot's DrawPile. Previously recorded constraints are removed before checking
the next entry. Hand/discard/generated choices, legacy Emulator builds without `cardInstanceId`,
and partial/malformed identity produce `()`, not an inferred partial constraint.

This is intentionally visibility-scoped evidence: RL never consults or exports raw combat draw
history to learn these exact identities.

### 3.2 Hypothesis reconstruction

`apply_hypothesis_to_context()` validates the recorded prefix constraints against the Stable root,
then performs two linked operations before Restore:

1. move the constrained CardIds to the front of the CardId-level hypothesis in the same visible
   option order, preserving the relative order of the hypothesis tail;
2. allocate those front positions to the exact constrained `CardInstanceSnapshot.InstanceId`
   values, then allocate all remaining CardIds from the remaining concrete cards using the prior
   canonical ordering.

The resulting snapshot therefore replays the already-observed Pending choice with the same CardId
sequence *and* the same physical copies, including duplicate same-CardId cards with different
instance state. The remaining hidden future still comes from the belief hypothesis.

`SearchHypothesisId` does **not** gain `cardInstanceId`. Its digest remains RNG + CardId order. The
exact ids are context-local replay constraints derived from facts already visible at that specific
real decision boundary, not an additional hidden RNG dimension. Reordering the CardId prefix can
still change the opaque hypothesis slot value for a context with a non-empty constrained prefix;
that is expected because the derived CardId order is genuinely different.

## 4. Privacy and safety properties

- No true/unsubstituted DrawPile is replayed to Training.
- No raw ordered draw-event feed is introduced.
- A concrete id is recorded only after Emulator #25 has made it visible in the Pending choice.
- Hidden draw/discard/exhaust pile masking still removes order and concrete instance identity.
- Exact ids are never added to `SearchHypothesisId` or hypothesis generation.
- Invalid/stale constraints stop pinning conservatively; they do not crash the search or trigger
  partial exact-instance inference.

A mid-Replay-Prefix reshuffle remains a residual edge case because Emulator #25 intentionally does
not expose a hidden reshuffle/draw-event trace. The current guard only pins exact instances that can
still be justified from the Held Stable root and earlier visible constraints. Modeling arbitrary
mid-prefix reshuffles is explicitly out of scope for this fix.

## 5. Tests / merge gate

Hosted pure-Python regressions cover:

- all-or-nothing extraction of visible exact ids from Pending options;
- rejecting already-consumed/non-root/missing identities;
- stopping before an invalid replay-prefix tail;
- CardId-prefix reordering while preserving the hypothesis tail;
- exact allocation of the upgraded copy among duplicate same-CardId instances;
- mask behavior: choice-boundary `cardInstanceId` survives while hidden pile identity does not.

The cross-repo mergeability gate requires Emulator PR #25 (or a build containing it) and the real
previously-failing Acrobatics reproduction: the harvested Silent combat with seed override `101`
must complete without `replay_mismatch` / `all emulate_actions branch results faulted`.

## 6. Explicitly out of scope

- exporting `cardsDrawnThisStep` or any equivalent raw draw-history signal;
- adding concrete instance ids to `SearchHypothesisId`;
- pinning search-internal/hypothesis-fabricated ReplayPrefix entries;
- general mid-prefix reshuffle reconstruction;
- changing the separate CombatStartReplayRoot hypothesis path.
