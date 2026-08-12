# Proposal: explicit, logged God Mode opt-in for Whole Run data collection (2026-08-12)

Status: **proposal only - no code changes in this PR.** Raised for review before any
implementation starts, because it deliberately reopens a path that was closed for a
documented incident (see §2).

## 1. Motivation

Full combat-logic implementation work is underway (STS2_Training, separate effort) and
needs start-of-combat state diversity: varied decks, relics, and enemy encounters across
floors/Acts. Natural (non-invincible) self-play currently dies too early to sample this -
recent 50/100-run IRONCLAD no-beam batches average floor ~10 (`floor_stats.mean` 10.04 /
10.08), rarely reaching Act 2+.

Proposed approach: collect a *separate, explicitly-tagged* batch of Whole Runs with the
player invincible (`GameInstance.EnableGodModeForTesting()`) so runs reach deep floors and
accumulate large, varied decks/relics quickly. Reward generation is unaffected by this (see
prior investigation this session - `EnableGodModeForTesting` only applies
Strength/Buffer/Regen powers to the player's Creature; it never touches `RewardsSet`/reward
RNG). The corrupted parts of this data (HP always pinned near max, `playerPowers` showing
the applied god-mode stacks, and the in-combat play-by-play itself) are corrected or
discarded downstream before use - never fed to training as-is.

## 2. Why this isn't a small change: the existing guard

`Run/tests/test_god_mode_scope.py` is an AST-based regression test asserting that **no
runtime module under `API/`, `Run/` (non-test), or `Combat/` may call
`EnableGodModeForTesting`/`enable_god_mode_for_testing`** - only
`Run/whole_run_session.py`'s thin wrapper implementation itself, and test code, are
exempt. Its docstring:

> Runtime modules may expose the narrow WholeRunSession test-support wrapper, but no
> non-test code is allowed to call that wrapper or the underlying CLR
> EnableGodModeForTesting API. This catches the class of leak that caused STS2_RL#29,
> where API/instance_whole_run.py enabled God Mode for every real Whole Run instance.

`Outputs/reports/god_mode_default_removal_20260811.md` (yesterday) documents the concrete
incident this guards against: three RL-side call sites - including
`worker_pool.py`'s `_WorkerRuntime.__init__`, i.e. **every Worker in the real Branch-search
path used by production Whole Run instances** - had God Mode unconditionally ON by
default, with no opt-out, silently making every player invincible. That was fixed by
deleting the calls entirely and adding this guard so the pattern cannot silently return.

Today, `API/` (the TCP surface `self_play.py`/Training talks to) has **no path at all** to
enable God Mode. Reopening one on purpose means the guard must gain a deliberate,
narrow, reviewable exception - not be weakened or removed.

## 3. Proposed design

**Principle:** default OFF everywhere; a single explicit per-instance opt-in; the flag is
never silently inherited or left ambiguous downstream. Two independent signals mark
god-mode data (directory placement AND an in-record flag) so a filtering mistake in one
doesn't silently include contaminated data.

### STS2_RL
- `API/instance_whole_run.py`'s `start_instance`-equivalent `instance_config` gains an
  explicit `god_mode: bool = False` field.
- The flag threads down to `Run/worker_pool.py`'s Main Run Worker start-up for that
  specific instance only (not a Worker-wide/pool-wide default) and calls
  `WholeRunSession.enable_god_mode_for_testing()` once, before `StartRun` - mirroring the
  documented persistence behavior (`GodModeEnabled` already round-trips through
  `SaveState`/`LoadState`, so every Branch/probe session descended from this root
  inherits it automatically without re-applying anywhere else).
- `Run/tests/test_god_mode_scope.py` gains one narrow, named allowlist entry for this
  specific, config-gated call site (not a blanket carve-out for `worker_pool.py`) so the
  guard keeps catching any *other*, unintended call site the way it does today.
- `WholeRunInstance` bookkeeping exposes whether an instance is running under God Mode
  (for API-level introspection/audit), independent of whether the DTO-level flag below
  is also read by a given caller.

### STS2_Emulator
- Expose the already-tracked `_godModeEnabled` (currently only serialized into the
  internal `RunSnapshot` for save/restore, never into the outward-facing
  `masked_emulator_dto`) as a top-level DTO field (e.g. `godMode`), following the same
  precedent as this session's earlier `choiceSemantics`/`willKillPlayer` DTO exposures:
  narrow, additive, no behavior change to existing consumers who ignore the new key.
  This is what lets the flag ride along in every Training JSONL record without any
  out-of-band bookkeeping.

### STS2_Training
- `self_play.py` gains a `--god-mode` CLI flag that (a) sets `god_mode=True` in the
  instance config, and (b) defaults `--output-dir` to a distinct subdirectory (e.g.
  `data/self_play/godmode/`) rather than reusing the normal collection path - so raw
  output is separated by construction, not by convention/memory.
- A separate correction/post-processing tool reads god-mode-flagged JSONL, strips the
  specific applied powers (Strength/Buffer/Regen) from `playerPowers` on every record,
  and writes to a distinct `.../godmode_corrected/` output directory - never mutating the
  raw collection in place.
- **Explicitly out of scope for this pass:** what policy replaces the pinned `hp`/`maxHp`
  values (uniform random? floor-conditioned? something else?) is a separate, still-open
  design decision, not resolved here.

## 4. Safety considerations (mirroring the #29 incident's lesson)

- Default OFF; opt-in is per-instance and explicit at `start_instance` call time, never a
  Worker/pool-wide default.
- The AST guard stays in force for every call site except the one, named, reviewed
  exception - it keeps catching accidental reintroductions elsewhere.
- The flag is redundantly visible: directory placement (collection-time) and a DTO field
  (record-level, survives any later file move/merge) - either alone would be a single
  point of failure for downstream filtering.

## 5. Open questions for review

1. Is a single boolean sufficient, or should the opt-in also record *why* (e.g. a
   `god_mode_reason` string) for future audit, given the #29 history?
2. Should the DTO-level `godMode` field be masked/omitted for any existing consumer that
   doesn't expect unknown keys, or is additive-only safe as-is (matching the
   `choiceSemantics` precedent)?
3. HP/`playerPowers` correction policy (see §3, Training, "explicitly out of scope") needs
   its own design pass before the collected data is usable - should that be a follow-up
   PR gated on this one, or designed in parallel?

No code changes are included in this PR. Filed for review of the approach before
implementation begins.
