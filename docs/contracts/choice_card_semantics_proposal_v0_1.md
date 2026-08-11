# Canonical `choice_card` semantics proposal v0.1

## Status

Draft design proposal. This change intentionally does **not** modify runtime behavior.
It records a cross-repository contract gap that should be resolved before Training starts
assigning semantic card-quality priors to `choice_card` actions.

Repositories affected by a future implementation:

- `STS2_Emulator`: source of truth for the game mechanic that created the pending card choice.
- `STS2_RL`: normalize and safely expose the mechanic through the public masked DTO.
- `STS2_Training`: consume only the canonical semantics and keep unknown semantics neutral.

## Problem

`action_type="choice_card"` currently tells Training only that a card option must be
selected. It does not tell Training *why* the card is being selected.

The optimal policy can reverse depending on that missing meaning. Examples include:

- gain a card;
- discard a card;
- exhaust a card;
- upgrade a card;
- retrieve a card from a pile;
- play/replay a selected card;
- remove a card permanently;
- transform a card.

A fixed card-quality heuristic is therefore unsafe. For example, a Curse may be a poor
gain target but an excellent discard/exhaust/remove target, while an upgrade choice is
better modeled by upgrade delta than by the card's absolute value.

Training must not infer this semantic operation from incidental fields, labels, card IDs,
UI text, localization strings, or a sequence of guessed keys such as `kind`, `operation`,
`choiceType`, or `semantics`.

## Design principle

**Emulator describes what the mechanic does; Policy decides whether the resulting option
is desirable.**

The Emulator is the correct source of truth because it owns the real pending-choice
mechanic. RL should expose a normalized, versioned public description. Training should
not reconstruct or guess the mechanic.

The wire contract must contain mechanics, not policy judgments. Fields such as
`positive_choice`, `prefer_low_value_card`, or `bad_card_target` should not be part of the
public DTO.

## Proposed public model

Keep `action_type="choice_card"` as the decision-phase/action ontology. Add a separate,
versioned semantic descriptor for the pending choice.

Illustrative shape:

```json
{
  "pendingChoice": {
    "kind": "card_selection",
    "choice_semantics": {
      "version": 1,
      "operation": "discard",
      "effect": "move",
      "source_zone": "hand",
      "destination_zone": "discard",
      "modifier": null,
      "order_matters": false,
      "replacement_allowed": false
    },
    "source_effect_id": "...",
    "minSelect": 0,
    "maxSelect": 5,
    "selectedOptionIds": ["..."],
    "options": [
      {
        "option_id": "...",
        "card": {}
      }
    ]
  }
}
```

The exact JSON field names remain subject to review. The contract requirements below are
more important than this draft spelling.

## Factorized semantics

A single ever-growing operation enum is useful for convenience but should not be the only
semantic representation. Prefer both:

1. a high-level canonical `operation` for common cases; and
2. factorized mechanical fields that describe what actually happens.

Candidate high-level operations include:

- `gain`
- `discard`
- `exhaust`
- `upgrade`
- `retrieve`
- `play`
- `replay`
- `remove`
- `transform`
- `unknown`

Candidate mechanical factors include:

- `effect`: e.g. `move`, `modify`, `play`, `replace`;
- `source_zone`;
- `destination_zone`;
- `modifier`: e.g. `upgrade`;
- `order_matters`;
- `replacement_allowed`.

This allows future mechanics to be represented without forcing Training to memorize a
closed enum, while still giving common operations a stable categorical embedding.

## Option identity and selected set

Multi-card choices require more than `selectedCount`.

The public contract should expose stable option identity within one pending-choice
lifetime and the selected set/order, for example:

- `option_id` for each offered option;
- `selectedOptionIds` for already selected options.

This avoids ambiguity when multiple copies share the same card ID but differ in upgrade
state or other instance metadata, and gives Policy enough information to reason about
marginal value after earlier selections.

`option_id` is an opaque decision-local token. It must not be assumed stable across later
unrelated decisions.

## Ordering semantics

The public descriptor should say whether selection order changes the result.

When `order_matters=false`, search may canonicalize a selected set and avoid exploring
permutations such as `A -> B` and `B -> A` as distinct branches.

When `order_matters=true`, Policy/Beam must preserve sequence.

## Source-effect identity

An optional stable `source_effect_id` is recommended in addition to generalized
semantics. Two choices can share the same operation while having different strategic
context.

Example: two discard choices may come from different cards, relics, enemies, or costs.
The learned policy can use `operation` to generalize and `source_effect_id` to learn
source-specific corrections. Unknown source IDs must not prevent operation-level
generalization.

## Ownership boundary

### Emulator

The Emulator should derive the canonical semantic descriptor directly from the real
pending-choice mechanic that stopped `GameInstance.Step()`.

It must not derive semantics from display text or Training-specific heuristics.

### RL

RL should:

- convert the Emulator descriptor to plain public data;
- validate the descriptor;
- pass only explicitly approved semantic fields through masking;
- stamp/validate the semantic version;
- preserve `unknown` when the Emulator cannot provide a supported semantic description.

RL should not decide whether the chosen card is strategically good or bad.

### Training

Training should:

- consume a typed `ChoiceCardObservation` rather than inspecting raw arbitrary
  `pendingChoice` keys;
- condition card-option ranking on canonical semantics;
- include selected-set context for multi-select choices;
- keep `unknown` semantics neutral/conservative;
- never guess semantics from labels or incidental keys.

## Combat Policy integration

The recommended learned design is one shared card-choice head conditioned on semantic
features, not a separate model for each operation.

Conceptually:

```text
Combat/card encoder -----------+
Combat state encoder ----------+--> ChoiceCardPolicy --> option/confirm/skip logits
Choice semantics encoder ------+
Selected-set encoder ----------+
```

This permits shared knowledge about card quality and combat context while allowing the
same card to receive opposite preference under different operations.

The stable combat `ValueModel` should remain outside unresolved choice continuation.
Continuation should continue to behave as a macro-action: local policy/search resolves
the pending choice, and global Value evaluates only the resulting stable/terminal Combat
state unless a future dedicated continuation-value model is intentionally introduced.

## Bootstrap behavior before this contract exists

Until canonical semantics are available end-to-end:

- `choice_card` option quality remains neutral in Training's heuristic Policy;
- confirm/skip legality and structural branch coverage may still be handled independently;
- no heuristic semantic inference from guessed DTO keys should be introduced.

This fallback is deliberate. A neutral prior is safer than silently optimizing the
opposite objective.

## Compatibility and rollout

A future implementation should be additive and versioned.

Recommended rollout order:

1. Emulator emits the semantic descriptor and tests it against representative choice
   mechanics.
2. RL bridge exposes the descriptor and masking/contract tests guarantee no hidden state
   leaks with it.
3. Training adds a typed semantic model while preserving neutral behavior when absent or
   `unknown`.
4. Only then should heuristic/learned `choice_card` option ranking use semantic card
   quality.

Old Training clients must remain safe when the new field is absent. New Training clients
must remain safe when an unfamiliar operation/version appears.

## Acceptance criteria for implementation PRs

Before semantic card-quality ranking is enabled, the cross-repository implementation
should demonstrate:

- canonical semantics originate from the Emulator mechanic, not UI strings or guessed
  Training-side keys;
- at least discard, exhaust, upgrade, retrieve/play-like, and unknown cases are covered by
  contract tests or explicitly documented unsupported cases;
- duplicate card instances are unambiguous through option identity;
- selected-set/order information is sufficient for variable-count choices;
- unknown/new operations degrade to neutral behavior rather than a guessed preference;
- RL masking audit covers every new public field;
- Training Policy consumes a typed normalized view rather than raw wire-key probing;
- unresolved continuation DTOs are not accidentally added to the normal stable Combat
  ValueModel domain.

## Non-goals of this design PR

This PR does not:

- change DTO schema version;
- change Emulator runtime behavior;
- change RL masking/runtime responses;
- change Training `PriorHeuristicPolicy` scoring;
- define policy labels such as "good card" or "bad card" in the wire contract;
- require all future card-choice mechanics to fit a permanently closed enum.

It exists to make the missing semantic contract explicit so current Training work can
remain conservative and independently mergeable.