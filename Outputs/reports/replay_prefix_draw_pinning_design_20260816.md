# `replay_mismatch` (candidate_semantic_keys) fix: pin recorded draw outcomes into RNG-hypothesis reconstruction

Status: design, not yet implemented; reviewed once by an independent codex pass (2026-08-16),
corrections from that review already folded in below (marked inline where they materially changed
the design — B.0's `BattleState.step_info` plumbing, B.3's `Counter`-based pinning guard, and the
justification for excluding the three search-internal `ReplayPrefixEntry` sites). All file:line
citations in this doc were independently re-verified against current source by that review and
confirmed accurate. Written for an implementer (human or codex) who has NOT followed the
investigation live — every claim below is either cited to a specific file:line, or marked as an
explicit design decision made in this doc. Two repos are touched: `C:\STS2_Emulator` (C#, the game
engine) and `C:\STS2_RL` (Python, the RL/beam-search server). This doc lives in STS2_RL's
`Outputs/reports/` but Part A is Emulator-side work.

## 0. Problem statement

STS2_RL's beam search explores multiple hypothetical futures ("branches") from a decision
point. To model uncertainty about not-yet-drawn cards, it swaps the DrawPile-order RNG for one
specific stream when constructing a hypothesis
(`Combat/search/rng_hypothesis.py:371-393`, `derive_substituted_snapshot`): it substitutes
`root_snapshot.Rng.RunRng["Shuffle"]` and `root_snapshot.Player.DrawPile` with a hypothesized
permutation of the same card multiset.

Separately, when a fresh Branch Worker needs to reconstruct a branch's state, it replays a
recorded list of prior actions (`DecisionContext.replay_prefix`, a `list[ReplayPrefixEntry]`)
against a snapshot, then checks that what it observes matches what was recorded
(`Combat/search/decision_context.py`'s `replay_decision_context()`,
`Combat/search/branch_worker_pool.py:516-538`).

**The bug**: if `replay_prefix` contains an already-committed real action that drew cards and
then presented a card-selection `PendingChoice` over the result (e.g. `ACROBATICS`: "Draw 3,
discard 1" — the discard choice's candidates are whatever 3 cards were drawn), and the branch
being reconstructed uses a DIFFERENT RNG hypothesis than the one that was live when that action
really happened, replaying it will draw a DIFFERENT 3 cards, so the resulting `PendingChoice`
offers a different candidate set than what was recorded. The post-replay consistency check
correctly detects this (`stage="signature"`, `diverged_fields=["candidate_semantic_keys"]`) and
faults the branch (`fault_kind="replay_mismatch"`). When this happens for every sibling under one
parent (a structural consequence of them sharing the identical `replay_prefix` — see the prior
investigation note this doc supersedes for detail if it exists in this session's history), the
whole search round can lose 100% of its branches, and `Combat/decision/beam_search.py:408`
(STS2_Training-side) raises `RuntimeError("all emulate_actions branch results faulted")`.

**Confirmed root cause, not speculation**: reproduced via direct Oracle-collect re-run with
`seed=101` forced (`dataclasses.replace(scenario, seed=101)`) against scenario
`silent-1786531240-00002-seed-216695918-179e585c-combat25.json` (Silent, hand includes
`ACROBATICS`). Fault reproduced 100% of the time, independent of caller (both STS2_Training's
`stable_pruner_rl` and plain Oracle collect hit it identically once seeds matched).

**Why the obvious fix ("substitute the hypothesis AFTER replaying `replay_prefix`, not before")
is wrong**: `API/combat_rng_mapping.py:17-20`'s docstring documents an explicit privacy
requirement — "Every `emulate_action` (regardless of `rng_id`) uses a belief-derived Hypothesis
snapshot, NEVER the root's literal true DrawPile order... the reason `rng_id` never leaks true
RNG state to Training." Replaying history against the TRUE (unsubstituted) snapshot, even just
for the replay_prefix portion, would expose the true draw order through whatever candidate set
that replay reveals. The fix below avoids this by never using the true order — it only ever
reuses information Training/the client ALREADY has (its own recorded action's candidate list).

## 1. Fix overview

`ReplayPrefixEntry` (real, already-committed history) gains a new field recording which concrete
cards were drawn from the pile during that step, as reported directly by the Emulator (Part A).
When STS2_RL builds a hypothesis-derived snapshot for a `DecisionContext` whose `replay_prefix` is
non-empty, it PINS those already-known card identities at the front of the hypothesis's
`ordered_draw_pile_card_ids` sequence, in the same chronological order they were really drawn, and
lets the hypothesis's own belief distribution randomize only the remaining, not-yet-consumed
cards (Part B). This makes `replay_decision_context()`'s natural re-execution of the recorded
`ACROBATICS`-style step draw exactly the recorded cards again, regardless of which hypothesis
index is being explored — eliminating the `candidate_semantic_keys` divergence at its source, with
no change to Emulator game logic and no change to what candidate set Training itself ever sees.

This does not leak new information: the client already knows exactly which cards it discarded/
selected at that step (`expected_signature.candidate_semantic_keys` is already a client-visible,
already-recorded fact — it's an echo of the client's own past action, not new knowledge).

A genuine residual risk exists (mid-`replay_prefix` reshuffles) and is handled by a conservative
safety guard that DISABLES pinning past a suspected reshuffle point rather than attempting to
model reshuffles (out of scope for this fix — see §3, "Explicitly out of scope").

---

## Part A — STS2_Emulator changes

### A.1 New DTO: report which cards were drawn during a `Step()` call

New file `Sts2Emulator\Dto\DrawnCardSnapshot.cs`, following the exact shape/style of the existing
`InferredCardRemovalSnapshot` (`Sts2Emulator\Dto\Snapshot\CombatHistorySnapshot.cs:62-69`):

```csharp
namespace Sts2Emulator.Dto;

public sealed class DrawnCardSnapshot
{
    public int StepIndex { get; set; }
    public string CardInstanceId { get; set; } = string.Empty;
    public string CardId { get; set; } = string.Empty;
}
```

### A.2 Populate it inside `GameInstance.Step()`

File: `Sts2Emulator\Api\GameInstance.cs`. `Step(int actionId)` starts at line 3243 and already
captures `preStepHistoryCount = CombatManager.Instance.History.Entries.Count()` at line 3255,
later consumed by the existing (unrelated, do not modify) `InferCardRemovalsFromStep` call at
line 3328. Add a new, independent private helper right next to it — do not fold this into
`InferCardRemovalsFromStep`, which is a different, best-effort heuristic (per its own doc comment,
`CombatHistorySnapshot.cs:44-57`) with a different purpose; drawing is unambiguous (every draw
produces exactly one `CardDrawnEntry`) and deserves its own simple, precise helper:

```csharp
private DrawnCardSnapshot[] ExtractDrawnCardsThisStep(int preStepHistoryCount)
{
    var newHistoryEntries = CombatManager.Instance.History.Entries.Skip(preStepHistoryCount);
    return newHistoryEntries
        .OfType<CardDrawnEntry>()
        .Select(e => new DrawnCardSnapshot
        {
            StepIndex = _stepIndex,
            CardId = e.Card.Id.Entry,
            CardInstanceId = _snapshotBuilder.GetOrAssignCardInstanceId(e.Card),
        })
        .ToArray();
}
```

`CardDrawnEntry` is `Sts2Emulator\Imported\Source\MegaCrit.Sts2.Core.Combat.History.Entries\CardDrawnEntry.cs`
(`.Card` holds the drawn `CardModel`, confirmed at line 9). `_snapshotBuilder` is an existing
`GameInstance` instance field (`GameInstance.cs:196`) already used the same way nearby
(`InferCardRemovalsFromStep`, lines 3454/3468) — no new plumbing needed.
`GetOrAssignCardInstanceId` (`Sts2Emulator\Api\Internal\RealEngine\SnapshotBuilder.cs:180`) mints a
stable `"card-{seq:D6}"` id per distinct live `CardModel` object and — important for this fix's
correctness — these ids are re-seeded/preserved across a Restore round-trip
(`SnapshotRestorer.cs:611`, `GameInstance.cs:885`, `SnapshotBuilder.cs:138-166`), so they remain
meaningful identity anchors across the snapshot-restore cycles this fix's consumers perform. (Part
B below only actually needs `CardId`, not `CardInstanceId` — `CardInstanceId` is included for
completeness/future use and because it costs nothing extra to compute here.)

Call it in `Step()` right after where `InferCardRemovalsFromStep` is called (line 3328), reusing
the same `preStepHistoryCount` already in scope:

```csharp
var drawnCardsThisStep = ExtractDrawnCardsThisStep(preStepHistoryCount);
```

### A.3 Surface it on `StepResult.Info`

`StepResult.Info` (`Sts2Emulator\Dto\StepResult.cs:21`, `Dictionary<string, object?>`) is the
established home for "this specific thing happened during step resolution" facts (`cardId`,
`targetId`, `choiceAction`, `selectedCardId`, etc. — see the conditional-set sites around
`GameInstance.cs:3329-3356, 4079, 4118, 4149-4150, 4166-4168, 4214-4235`). Follow that SAME
established convention: **set the key only when non-empty**, not an always-present empty array
(this repo's own `Info` convention is conditional presence; the "always present, possibly empty
array" convention the earlier research cited applies to typed struct fields like `LegalActions`,
not `Info`'s loose dict — this is a deliberate, explicit choice, not an oversight):

```csharp
if (drawnCardsThisStep.Length > 0)
{
    info["cardsDrawnThisStep"] = drawnCardsThisStep
        .Select(d => new Dictionary<string, object?>
        {
            ["cardId"] = d.CardId,
            ["cardInstanceId"] = d.CardInstanceId,
        })
        .ToArray();
}
```
(Match wherever the existing `info[...] = ...` assignments actually happen in `Step()` — insert
this alongside them, same dictionary variable.)

This is a pure-additive `Info` key. Per `docs\reports\action_semantic_key_design_20260816.md:168`,
additive fields like this have precedent for NOT requiring a DTO/schema version bump (cites
`HandCards`/`enemyIndex` as prior no-bump additions). Follow that precedent — no contract version
change needed for this field.

### A.4 Smoke test

New `scripts\smoke_drawn_cards_this_step.py`, modeled on `scripts\smoke_action_semantic_key.py`'s
skeleton (pythonnet bootstrap via `pythonnet.load("coreclr", ...)` + `clr.AddReference`, then
direct `Sts2Emulator.Dto`/`Sts2Emulator.Api` imports — see that script lines 15-27 for the
bootstrap boilerplate). Construct a scenario whose draw pile is rigged (via direct
`CombatScenario`/deck construction, same style as existing smoke scripts) so that a single
`Step()` playing a card that draws (e.g. a scenario built directly around `ACROBATICS`, or
whatever forced-draw-pile-order mechanism existing smoke scripts already use — check
`scripts\smoke_card_upgrade.py`/`scripts\smoke_potions.py` for the established
scenario-construction helpers first, reuse them rather than inventing new ones) draws at least two
copies of the same `CardId` in one step. Assert:
- `result.Info["cardsDrawnThisStep"]` is present, length matches the number of cards drawn.
- Each entry has the correct `cardId`.
- Two same-`CardId` entries have DISTINCT `cardInstanceId`.
- Order matches actual draw order (front-of-pile order).

Add to `scripts\README.md`'s table, matching existing rows' format.

### A.5 Build/verify

`dotnet build Sts2EmulatorPhase1.sln` (0 errors), run the new smoke test, run the existing smoke
suite (`scripts\smoke_cli.ps1`) to confirm no regression. Follow this session's established PR
workflow: feature branch → `gh pr create` → checks → merge. **STS2_RL's work (Part B) depends on a
build of `Sts2Emulator.dll` that includes this change — confirm how STS2_RL currently obtains/
references its Emulator build (copied binary vs. project reference vs. path env var) before
starting Part B, and make sure whatever STS2_RL points at is rebuilt/updated after this merges.**

---

## Part B — STS2_RL changes

### B.0 Prerequisite: `StepResult.Info` does NOT currently reach Python — add `BattleState.step_info`

**Confirmed (not just suspected) via direct read of `Combat/battle_emulator.py`: `Info` is
currently dropped.** `step_live_action()` (`battle_emulator.py:850-916`) calls
`result = game.Step(...)`, then reads only `result.LegalActions`/`result.Transition`/
`result.Observation` before handing off to `_wrap()` (two call sites, lines 903-909 for the
`combat_completed` branch and 911-916 for the normal branch). `_wrap()`
(`battle_emulator.py:714-776`) builds a `BattleState` from `obs`/`turn`/`enemy_max_hps`/
`shuffle_rng_seed`/`legal_actions` only — `result.Info` is never passed in and `BattleState`
(`battle_emulator.py:57-82`) has no field for it at all. `LiveCombatSession.step()`
(`Combat/live_combat_session.py:777`) returns this `BattleState` unchanged. So `Info` — and
therefore `cardsDrawnThisStep` from Part A — never reaches `Combat/search/decision_context.py`
today. (A separate `Run/run_emulator_bridge.py` bridge does carry `info` through in its own dicts,
but that is the whole-Run path, not the Combat path this fix needs — do not confuse the two or
assume fixing one fixes the other.)

**Required change, concretely:**

1. Add a new field to `BattleState` (`battle_emulator.py:57-82`), next to `_cached_legal_actions`/
   `decision_frame`:
   ```python
   # Raw StepResult.Info from whichever Step() call produced this state, converted to a plain
   # dict (to_plain(result.Info)) - None for a BattleState not produced by a real step_live_action()
   # call (initialize(), or a state that predates this field). Carried forward unchanged by
   # clone_state()/with_shuffle_seed() (like _cached_legal_actions/decision_frame) since it
   # describes how THIS state was reached, not a fresh event - never merged/cleared on fork.
   step_info: "dict | None" = field(default=None, repr=False)
   ```
2. In `_wrap()` (`battle_emulator.py:714-721`), add a `step_info: "dict | None" = None` parameter
   and thread it into the returned `BattleState(..., step_info=step_info)`.
3. At `step_live_action()`'s two `_wrap()` call sites (lines 903-909, 911-916), pass
   `step_info=to_plain(result.Info)`.
4. At `initialize()`'s `_wrap()` call (line 809) and `wrap_live_state()`'s passthrough (line
   842-848), leave `step_info` at its default `None` — no real `Step()` happened.
5. In `clone_state()` (`battle_emulator.py:814-826`) and `with_shuffle_seed()`
   (`battle_emulator.py:778-798`), which construct `BattleState(...)` directly (not via `_wrap`),
   add `step_info=battle_state.step_info` alongside the existing `_cached_legal_actions=`/
   `decision_frame=` passthroughs — same reasoning: this field describes the ALREADY-REACHED
   state, not a new event, so it must survive cloning/forking exactly like those two fields do.

Once this lands, `ReplayPrefixEntry` construction sites (B.2 below) read
`battle_state.step_info.get("cardsDrawnThisStep")` (guard for `step_info` being `None`), not
`step_result.Info` directly — confirm the exact local variable name holding the relevant
`BattleState` at each call site before writing this, per B.2's own instruction.

### B.1 `ReplayPrefixEntry` gains a `drawn_card_ids` field

File: `Combat/search/decision_context.py`. Current definition (lines 322-334):

```python
@dataclass(frozen=True)
class ReplayPrefixEntry:
    semantic_action: SemanticAction
    expected_signature: DecisionSignature
    target_index: "Optional[int]" = None
    target_enemy_index: "Optional[int]" = None
```

Add:

```python
    drawn_card_ids: "tuple[str, ...]" = ()
```

Default `()` (not `None`) — always iterable, no `is None` checks needed at any consumption site.
Only `CardId`, not `CardInstanceId` — pinning (B.3) only needs `CardId` to match against
`root_snapshot.Player.DrawPile`'s public multiset; `CardInstanceId` is Emulator-internal identity
not exposed to the RNG-hypothesis multiset machinery. Add a one-line doc-comment addition to the
class docstring, matching this file's existing documentation density.

### B.2 Populate it at both REAL-stepping call sites

Two call sites construct `ReplayPrefixEntry` from genuinely real (not hypothesis-derived)
stepping — both must populate the new field, or pinning will silently never trigger:

**Site 1 — `API/instance_combat.py:301-306`** (`CombatInstance.commit_action()`, the Training RPC
handler for a real committed action):

```python
entry = ReplayPrefixEntry(
    semantic_action=_semantic_action_for(chosen),
    expected_signature=observed_signature,
    target_index=target_index,
    target_enemy_index=target_enemy_index,
    drawn_card_ids=_drawn_card_ids_from_step_info(next_state),  # new
)
```

where `_drawn_card_ids_from_step_info` is a small new helper (place near `_semantic_action_for` in
the same file), consuming the `BattleState.step_info` field added in B.0 above — NOT
`step_result.Info` directly, since real call sites only ever hold the `BattleState`
`LiveCombatSession.step()` returns, never the raw `StepResult`:

```python
def _drawn_card_ids_from_step_info(battle_state: "BattleState") -> tuple[str, ...]:
    entries = (battle_state.step_info or {}).get("cardsDrawnThisStep") or []
    return tuple(str(e["cardId"]) for e in entries)
```

`next_state` is this method's actual local variable holding the result of
`self._session.step(self._root_state, ...)` (`instance_combat.py:277`) — confirmed by re-reading
the current source, not a placeholder name.

**Site 2 — `Combat/search/main_loop.py:461-466`** (`_run_exec_loop`'s EXEC_LOOP real-stepping
path):

```python
entry = ReplayPrefixEntry(
    semantic_action=planned_step.semantic_action,
    expected_signature=observed_signature,
    target_index=planned_step.target_index,
    target_enemy_index=planned_step.target_enemy_index,
    drawn_card_ids=_drawn_card_ids_from_step_info(next_result),  # new
)
```

Reuse the same helper (import from `API/instance_combat.py`, or relocate the helper to a shared
module both files already import from if one exists — check before introducing a new shared
module just for this one function). `next_result` is this call site's actual local variable
holding `loop_state.session.step(...)`'s return (`main_loop.py:432`) — confirmed by re-reading the
current source, not a placeholder name.

**Do NOT populate this field** at the other three `ReplayPrefixEntry` construction sites found
(`branch_worker_pool.py:436-441`, `multi_round_search.py:151-156`, `:204-209`) — leave them at the
new field's default `()`. This is not merely "rarer/smaller-scope" (an earlier draft of this doc
under-justified the exclusion this way — codex review flagged it): these three sites record a
Step's result against a `root_snapshot`/state that may ALREADY be a hypothesis-derived (i.e.
fabricated, not really-happened) draw order. The whole premise of pinning — "reuse a fact the
client already legitimately knows from its own real, committed action, so nothing new leaks" —
only holds for a draw that genuinely happened in real, ground-truth history. Pinning a draw that
occurred against an already-substituted hypothesis snapshot would be pinning one hypothesis's
fabrication into a DIFFERENT hypothesis's reconstruction, which is not sound (there is no reason
hypothesis A's imagined draw order should apply inside hypothesis B). So the exclusion here is
correct by construction, not just lower-priority — only Sites 1/2 (`instance_combat.py`,
`main_loop.py`) ever step a session whose snapshot lineage is guaranteed to be real/ground-truth
at the moment of that specific Step. (Whether within-hypothesis multi-step continuations have
their OWN, differently-scoped version of a similar consistency problem — pinning within a single
hypothesis's own multi-depth exploration — is a real, separate question, genuinely not
investigated here; flagged as a candidate follow-up in §3, not asserted safe.)

### B.3 Pin recorded draws into hypothesis construction, with a reshuffle-safety guard

File: `Combat/search/rng_hypothesis.py`. Integration point: `apply_hypothesis_to_context`
(lines 427-444) is the single function both real call paths funnel through for the snapshot that
actually reaches a `WorkItem` — confirmed by tracing both `API/combat_rng_mapping.py:65` (single-
hypothesis path) and `Combat/search/search_coordinator.py:290` (grid path; note
`search_coordinator.py`'s OWN direct call to `derive_substituted_snapshot` via `build_grid`,
`rng_hypothesis.py:396-413`, produces a `HypothesisGridCell.derived_snapshot` that is never read
anywhere in the repo — confirmed dead code, do not spend effort updating it, only
`apply_hypothesis_to_context`'s output matters).

Add two new private helpers and one call site edit, all in `rng_hypothesis.py`:

**Codex review correction**: an earlier draft of `_pinned_prefix_card_ids` guarded only on TOTAL
draw count vs. root pile size. Codex review caught that this is insufficient — it does not
guarantee the pinned ids are actually a sub-multiset of the root pile PER CARD ID. Concrete
counter-example from the review: root pile is `["STRIKE", "DEFEND"]` (size 2), recorded prefix
drew `("STRIKE", "STRIKE")` (also size 2) — the size-only guard would pass this through, and
`_reorder_hypothesis_with_pinned_prefix` would only discover the problem later via a `list.remove`
`ValueError` (safe, since it's caught, but the guard's OWN stated guarantee — "safe to pin" — would
be false when it returned that prefix). Fixed below with a proper `Counter`-based per-card-id
guard, so `_pinned_prefix_card_ids`'s return value is a verified-valid sub-multiset by
construction, and the `ValueError` fallback in `_reorder_hypothesis_with_pinned_prefix` becomes
truly defensive (should not trigger in practice, not a load-bearing part of correctness):

```python
from collections import Counter

def _pinned_prefix_card_ids(decision_context: "DecisionContext") -> tuple[str, ...]:
    """Card ids already known to have been drawn during `decision_context.replay_prefix`'s real
    history, in draw order, verified safe to pin into a hypothesis reconstruction. Walks
    `replay_prefix` entries in order, accumulating a running per-card-id count; stops (excluding
    the entry that would overflow, and everything after it) the moment pinning would require MORE
    copies of some card id than `root_snapshot.Player.DrawPile` actually contains. Beyond that
    point a mid-replay_prefix reshuffle must have occurred (some draws came from the discard pile,
    not the root pile's original multiset), which this fix does not attempt to model - see design
    doc `Outputs/reports/replay_prefix_draw_pinning_design_20260816.md` section 3. Note this can
    under-pin WITHIN the first overflowing entry too: if that entry's own draws only partially
    exceed the remaining root-pile budget (e.g. it drew 2 cards but only 1 more copy was available
    before a reshuffle), this function drops the WHOLE entry, not just the excess card - simpler
    and always safe, at the cost of occasionally pinning one fewer card than would technically be
    recoverable. Acceptable per this fix's scope (see section 3); do not try to be cleverer here.
    """
    root_multiset = Counter(card.CardId for card in decision_context.root_snapshot.Player.DrawPile)
    pinned: list[str] = []
    running: Counter = Counter()
    for entry in decision_context.replay_prefix:
        candidate = running + Counter(entry.drawn_card_ids)
        if any(candidate[card_id] > root_multiset.get(card_id, 0) for card_id in candidate):
            break
        running = candidate
        pinned.extend(entry.drawn_card_ids)
    return tuple(pinned)


def _reorder_hypothesis_with_pinned_prefix(
    hypothesis: "SearchHypothesisId", pinned_prefix: "tuple[str, ...]"
) -> "SearchHypothesisId":
    """Move `pinned_prefix`'s card ids to the front of `hypothesis.ordered_draw_pile_card_ids`,
    preserving the relative order of everything else. Raises ValueError (via list.remove) if a
    pinned card id isn't present in the hypothesis's own multiset at all - given
    `_pinned_prefix_card_ids`'s Counter-based guard, this should never actually happen in
    practice (defensive only, not load-bearing); callers must still treat it as a signal to skip
    pinning for this hypothesis rather than a fatal error, in case some future caller ever invokes
    this helper with an unverified `pinned_prefix`.
    """
    remaining = list(hypothesis.ordered_draw_pile_card_ids)
    for card_id in pinned_prefix:
        remaining.remove(card_id)
    return dataclasses.replace(
        hypothesis,
        ordered_draw_pile_card_ids=tuple(pinned_prefix) + tuple(remaining),
    )
```

Edit `apply_hypothesis_to_context` (lines 427-444) — insert pinning before the existing
`derive_substituted_snapshot` call:

```python
def apply_hypothesis_to_context(
    decision_context: DecisionContext,
    hypothesis: SearchHypothesisId,
) -> DecisionContext:
    if isinstance(decision_context.root_snapshot, CombatStartReplayRoot):
        raise TypeError("Genesis hypotheses must use derive_combat_start_replay_roots()")
    pinned = _pinned_prefix_card_ids(decision_context)
    if pinned:
        try:
            hypothesis = _reorder_hypothesis_with_pinned_prefix(hypothesis, pinned)
        except ValueError:
            # Pinned card id not in this hypothesis's multiset at all - treat as "pinning not
            # safely possible here" and fall back to the existing (unpinned) behavior for this
            # hypothesis rather than crashing the search. Should be rare given the size guard
            # in _pinned_prefix_card_ids; worth a log line if this codebase has a logger already
            # in scope here (check existing logging conventions in this module/file).
            pass
    context = dataclasses.replace(
        decision_context, root_snapshot=derive_substituted_snapshot(decision_context.root_snapshot, hypothesis)
    )
    return with_search_hypothesis(context, hypothesis)
```

Everything else in this function is unchanged. `derive_substituted_snapshot`'s own signature and
`_draw_pile_instances_for_hypothesis`'s multiset-equality check (lines 352-368) need NO changes —
a pinned-and-reordered `hypothesis.ordered_draw_pile_card_ids` still satisfies the same multiset
invariant by construction.

**Note for the design doc's own record, not an action item**: `SearchHypothesisId.digest()`/
`to_slot_value()` (lines 68-74) incorporate `ordered_draw_pile_card_ids`, so a pinned hypothesis
legitimately produces a different opaque `search_hypothesis_id` slot value than the same
`hypothesis_index`'s raw/unpinned form would have. This is correct (the derived snapshot really is
different) — call it out in the PR description so nobody mistakes it for a regression when they
notice hypothesis ids shifting for `DecisionContext`s with non-empty `replay_prefix`.

### B.4 Tests

- **`Combat/tests/test_rng_hypothesis.py`**: add unit tests for `_pinned_prefix_card_ids` (empty
  `replay_prefix` → `()`; single entry with draws → those ids in order; multiple entries →
  concatenated in order; an entry whose cumulative total would exceed root pile size → pinning
  stops before that entry, confirming the reshuffle guard; **the codex-review counter-example
  specifically**: root pile `["STRIKE", "DEFEND"]`, one entry with `drawn_card_ids=("STRIKE",
  "STRIKE")` → pinning must stop with `()`, not attempt to pin two `STRIKE`s the root pile doesn't
  have) and
  `_reorder_hypothesis_with_pinned_prefix` (pinned ids land at front in given order; remainder
  keeps relative order; multiset is preserved; a pinned id absent from the hypothesis's own list
  raises `ValueError`). Model structure after the existing
  `test_apply_hypothesis_to_context_matches_manual_replace_plus_with_search_hypothesis` (line 265)
  — add a sibling asserting `apply_hypothesis_to_context` on a context WITH a drawing
  `replay_prefix` entry produces a snapshot whose `Player.DrawPile` front matches the pinned cards
  exactly, across multiple different `hypothesis_index` values (proving the candidate set is now
  hypothesis-index-INDEPENDENT for the pinned portion, which is the actual property this whole fix
  is for).
- **New integration test**, modeled on `API/tests/test_pending_choice_hypothesis_order_independence.py`
  (added 2026-08-15, closest existing structural analog — already drives the full production path
  `CombatInstance.emulate_action()` → `build_single_hypothesis_work_item` →
  `apply_hypothesis_to_context` → `derive_substituted_snapshot` → Restore → replay, starting from a
  real `commit_action()`-recorded `replay_prefix` that reached a Pending boundary via a
  DrawPile-consuming card). That existing test asserts a different property (resolution-outcome
  invariance); the new test should assert THIS fix's actual property: reproduce the confirmed
  ACROBATICS scenario (or a synthetic minimal equivalent scenario built directly, cheaper to
  construct and iterate on than reusing the full harvested scenario file) and confirm that
  `emulate_actions` across multiple `rng_id` values no longer raises/faults with
  `fault_kind="replay_mismatch"`/`diverged_fields=["candidate_semantic_keys"]` for a branch whose
  `replay_prefix` includes the ACROBATICS-style step.
- **`Combat/tests/test_decision_context.py`**: confirm `ReplayPrefixEntry`'s existing direct
  construction call sites (lines 366, 391, 397, 430, 487, 512, 533) still work with the new
  optional field (they should, given the default) — no changes needed if the default is set
  correctly, but run the file to confirm.

### B.5 Final cross-repo verification (mergeability gate)

Matching this session's established practice for Emulator/RL fixes: after both Part A and Part B
are merged, re-run the exact reproduction case established during investigation — Oracle collect
against `data/scenarios/godmode_harvested/silent-1786531240-00002-seed-216695918-179e585c-combat25.json`
with `seed` explicitly overridden to `101` (see STS2_Training's `oracle_initial_batch.run_one(...,
seed_override=101)`, already extended with this parameter this session) — and confirm it completes
without `RuntimeError: all emulate_actions branch results faulted` / any `replay_mismatch` warning
in the log. This is real, previously-failing, now-must-pass evidence, not just unit tests passing.

---

## 2. Rollout order

1. Part A (STS2_Emulator): implement, test, merge.
2. Confirm STS2_RL's Emulator reference is updated to a build that includes Part A.
3. Part B.0: confirm/fix the `Info` passthrough gap (prerequisite, blocks everything else in Part B).
4. Part B.1-B.3: implement, unit test.
5. Part B.4 integration test.
6. Part B.5 final verification against the real reproduction case.
7. PR, review, merge — following this session's established codex-implements /
   human-plus-codex-reviews / real-data-mergeability-gate workflow.

## 3. Explicitly out of scope (do not attempt in this fix)

- Modeling reshuffles that occur mid-`replay_prefix` (the `Counter`-based per-card-id guard in
  `_pinned_prefix_card_ids` deliberately disables pinning past a suspected reshuffle point rather
  than attempting to reconstruct what really happened — doing so would need a new Emulator-side
  "a reshuffle occurred at step N" signal, which is a bigger, separate change; the residual bug
  (an unpinned tail can still legitimately fault) is accepted as a smaller, rarer remaining gap
  after this fix, not eliminated entirely).
- Any change to `branch_worker_pool.py:436-441`/`multi_round_search.py`'s `ReplayPrefixEntry`
  construction sites (within-search-continuation recording, not real-stepping). §B.2 already
  explains why these are structurally out of scope, not just lower-priority: they can record a
  Step against an already-hypothesis-derived (fabricated) snapshot, so pinning from them would mix
  one hypothesis's fabricated draw order into a different hypothesis's reconstruction — unsound by
  construction, not merely unexplored. Whether within-hypothesis multi-depth continuations have
  their OWN, differently-scoped version of a similar consistency problem remains a genuinely
  separate, not-investigated question.
- The `CombatStartReplayRoot` ("Genesis hypothesis") path (`combat_rng_mapping.py:49-58`,
  `search_coordinator.py:255-275`) — bypasses `apply_hypothesis_to_context` entirely via
  `derive_combat_start_replay_roots`; whether `replay_prefix` can ever be non-empty on a
  Combat-Start-Pending-rooted context wasn't confirmed either way. If it can, this fix does not
  cover it — flag as a follow-up if it turns out to matter.
- `build_grid`'s unused `HypothesisGridCell.derived_snapshot` field — confirmed dead code, not
  touched by this fix, not worth cleaning up as part of this change (separate, unrelated
  housekeeping).
