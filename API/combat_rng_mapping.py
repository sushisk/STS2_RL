"""Maps a Training-facing `rng_id` to exactly ONE Combat RNG (DrawPile-order) Hypothesis,
for `emulate_action`.

Combat's existing search infrastructure (`Combat/search/search_coordinator.py`'s
`_hypothesis_work_items_with_coverage`) always expands a candidate into its FULL
Hypothesis grid (`config.hypothesis_count` cells) for internal aggregation - the exact
opposite of what `emulate_action` needs (Training picks ONE `rng_id` => RL simulates
exactly ONE Hypothesis, never aggregating/choosing a winner across several). This module
reuses the same underlying primitives (`generate_belief_hypotheses`,
`derive_substituted_snapshot`, `with_search_hypothesis`)
directly, generating only the PREFIX of the belief-hypothesis sequence needed to reach
the requested index and keeping just the last one - `generate_belief_hypotheses` is a
pure function of its inputs, so `generate_belief_hypotheses(multiset, count=n)[n-1]` is
always identical to `generate_belief_hypotheses(multiset, count=n+k)[n-1]` for any k >= 0,
which is what makes "same rng_id => same Hypothesis" hold across repeated calls.

Every `emulate_action` (regardless of `rng_id`) uses a belief-derived Hypothesis
snapshot, NEVER the root's literal true DrawPile order - identical privacy posture to
Combat's existing internal search path, and the reason `rng_id` never leaks true RNG
state to Training (contract §3: "seed、RNG内部状態、DrawPile順序は公開しない").
"""

from __future__ import annotations

import dataclasses

from search.branch_worker_pool import WorkItem
from search.candidate_pipeline import PipelineCandidateRef
from search.decision_context import CombatStartReplayRoot, DecisionContext
from search.rng_hypothesis import (
    apply_hypothesis_to_context,
    compute_public_multiset,
    derive_combat_start_replay_roots,
    generate_belief_hypotheses,
)


def build_single_hypothesis_work_item(
    decision_context: DecisionContext,
    candidate: PipelineCandidateRef,
    hypothesis_index: int,
    *,
    work_kind: str,
) -> WorkItem:
    """Return exactly one `WorkItem` derived under the Hypothesis at `hypothesis_index`
    (0-based) for `candidate`, leaving `decision_context`/`candidate` themselves
    untouched.
    """
    is_combat_start = isinstance(decision_context.root_snapshot, CombatStartReplayRoot)
    count = hypothesis_index + 1
    if is_combat_start:
        derived = derive_combat_start_replay_roots(decision_context.root_snapshot, count=count)[hypothesis_index]
        context = dataclasses.replace(
            decision_context,
            root_snapshot=derived.derived_replay_root,
            search_hypothesis_id=derived.hypothesis.to_slot_value(),
        )
        return WorkItem.from_candidate_ref(context, candidate, work_kind=work_kind)
    else:
        public_multiset = compute_public_multiset(decision_context.root_snapshot)
        shuffle_rng = decision_context.root_snapshot.Rng.RunRng["Shuffle"]
        hypotheses = generate_belief_hypotheses(public_multiset, count=count, rng_seed_source=lambda _i: shuffle_rng)
        hypothesis = hypotheses[hypothesis_index]

    context = apply_hypothesis_to_context(decision_context, hypothesis)
    return WorkItem.from_candidate_ref(context, candidate, work_kind=work_kind)
