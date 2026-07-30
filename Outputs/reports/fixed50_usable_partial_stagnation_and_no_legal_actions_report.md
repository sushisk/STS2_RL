# fixed50 usable_partial 詳細レポート

Source:
- output dir: `C:\STS2_RL\Combat\data\trajectories_fixed50_20260722_actioncontinuation_fix_w4`
- date: `2026-07-22`

対象:
- `B_heuristic_stagnation` 2件
  - `fixed50:1642-31`
  - `fixed50:3342-27`
- `no_legal_actions_while_non_terminal` 1件
  - `fixed50:6485-37`

---

## fixed50:1642-31

- data_usage: `usable_partial`
- classification: `B_heuristic_stagnation`
- termination_reason: `truncated_at_max_decisions:50`
- warnings: `truncated_at_max_decisions:50`
- decision_count: `50`
- cycle_detected: `false`
- no_progress_detected: `true`
- final_outcome: `in_progress`
- final_is_terminal: `false`

### Relics
`BOUND_PHYLACTERY`, `CURSED_PEARL`, `BOOKMARK`, `CHANDELIER`, `ODDLY_SMOOTH_STONE`, `CLOAK_CLASP`, `VERY_HOT_COCOA`, `BIG_HAT`, `BOOK_OF_FIVE_RINGS`, `LUCKY_FYSH`, `TUNING_FORK`, `CHOICES_PARADOX`, `STONE_CALENDAR`, `NUNCHAKU`, `WAR_PAINT`

### Initial state
- character: `NECROBINDER`
- hp/block/energy: `66/0/3`
- enemy: `KNOWLEDGE_DEMON` `399/399`
- enemy intent: `CURSE_OF_KNOWLEDGE_MOVE`
- pendingChoice:
  - `choiceType = ChoicesParadoxAddToHand`
  - `scope = StartOfCombat`
  - `scenarioRestorable = true`
  - options:
    - `FLATTEN`
    - `SENTRY_MODE`
    - `END_OF_DAYS`
    - `SEVERANCE`
    - `NECRO_MASTERY`

### Stagnation summary
- enemy HP progression: `399` fixed for all 51 sampled points
- player HP progression: `66` fixed for all 51 sampled points
- last 10 actions:
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`

### Last recorded state
- decision_index: `49`
- player hp/block/energy: `66/0/199`
- enemy hp: `399/399`
- enemy intent: `CURSE_OF_KNOWLEDGE_MOVE`
- hand:
  - `CALCIFY`
  - `BODYGUARD`
  - `DEFEND_NECROBINDER`
  - `LETHALITY`
  - `REAP`
  - `FLATTEN`
  - `PARSE`
  - `SEANCE`
  - `FLATTEN`
  - `PARSE`

### Legal actions near termination
The last 10 decisions all had the same effective pattern:

- `End Turn`
- `CALCIFY`
- `BODYGUARD`
- `DEFEND_NECROBINDER`
- `LETHALITY`
- `REAP`
- `FLATTEN`
- `PARSE`
- `SEANCE`
- `FLATTEN`
- `PARSE`
- `DISTILLED_CHAOS`
- `POWER_POTION`

### Why this is classified as heuristic stagnation
- No repeated state hash was detected, so this is not a cycle in the current detector.
- However, enemy HP and player HP did not move at all.
- The heuristic repeatedly selected `End Turn` despite many playable actions being available.
- Energy increased each turn (`163 -> 167 -> ... -> 203`) without converting into progress.

### RL-side interpretation
This looks like a heuristic policy problem, not an Emulator fault:

- legal actions remained available
- battle state stayed non-terminal
- the policy repeatedly declined to act meaningfully

---

## fixed50:3342-27

- data_usage: `usable_partial`
- classification: `B_heuristic_stagnation`
- termination_reason: `truncated_at_max_decisions:50`
- warnings: `truncated_at_max_decisions:50`
- decision_count: `50`
- cycle_detected: `false`
- no_progress_detected: `true`
- final_outcome: `in_progress`
- final_is_terminal: `false`

### Relics
`CRACKED_CORE`, `LEAD_PAPERWEIGHT`, `VENERABLE_TEA_SET`, `STRAWBERRY`, `ETERNAL_FEATHER`, `CALLING_BELL`, `BRONZE_SCALES`, `PANTOGRAPH`, `RAINBOW_RING`, `DATA_DISK`, `JUZU_BRACELET`, `CHANDELIER`, `BING_BONG`, `MEAL_TICKET`, `JEWELRY_BOX`, `FORGOTTEN_SOUL`, `GAMBLING_CHIP`, `STONE_CALENDAR`, `STRIKE_DUMMY`, `HAPPY_FLOWER`

### Initial state
- character: `DEFECT`
- hp/block/energy: `82/0/3`
- enemy: `ENTOMANCER` `155/155`
- enemy intent: `BEES_MOVE`
- enemy attack: `3 x 7`
- pendingChoice:
  - `choiceType = GamblingChipDiscard`
  - `scope = StartOfCombat`
  - `scenarioRestorable = true`

### Stagnation summary
- enemy HP progression: `155` fixed for all 51 sampled points
- player HP progression: `82` fixed for all 51 sampled points
- last 10 actions:
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`
  - `End Turn`

### Last recorded state
- decision_index: `49`
- player hp/block/energy: `82/0/3`
- enemy hp: `155/155`
- enemy intent: `BEES_MOVE`
- hand:
  - `ZAP`
  - `CURSE_OF_THE_BELL`
  - `COOLHEADED`
  - `DEFEND_DEFECT`
  - `APOTHEOSIS`

### Legal actions near termination
The last 10 decisions were all `End Turn`, while the legal choices continued to rotate through playable cards such as:

- `DEFEND_DEFECT`
- `ULTIMATE_STRIKE`
- `FIGHT_THROUGH`
- `RAINBOW`
- `SUPERCRITICAL`
- `CREATIVE_AI`
- `HOTFIX`
- `PROWESS`
- `ZAP`
- `COOLHEADED`
- `APOTHEOSIS`

### Why this is classified as heuristic stagnation
- No repeated state hash was detected.
- Enemy HP remained `155` throughout.
- Player HP remained `82` throughout.
- The heuristic kept choosing `End Turn` even though multiple non-terminal legal actions remained available.

### RL-side interpretation
This is also a policy-quality issue:

- available actions existed
- no state mismatch or Emulator exception was observed
- combat was simply not advanced by the heuristic

---

## fixed50:6485-37

- data_usage: `usable_partial`
- classification: `null`
- termination_reason: `null`
- warnings: `no_legal_actions_while_non_terminal`
- decision_count: `11`
- cycle_detected: `false`
- no_progress_detected: `false`
- final_outcome: `in_progress`
- final_is_terminal: `false`

### Relics
`DIVINE_RIGHT`, `SILVER_CRUCIBLE`, `WHETSTONE`, `STORYBOOK`, `ROYAL_STAMP`, `BLESSED_ANTLER`, `BRONZE_SCALES`, `FENCING_MANUAL`, `VENERABLE_TEA_SET`

### Initial state
- character: `REGENT`
- hp/block/energy/stars: `75/0/4/3`
- enemy: `OWL_MAGISTRATE` `247/247`
- enemy intent: `MAGISTRATE_SCRUTINY`
- enemy attack: `16 x 1`
- pendingChoice: `null`

### Progress before failure
- enemy HP progression:
  - `247 -> 244 -> 232 -> 229`
- player HP progression:
  - `75` fixed
- last 10 actions:
  - `I_AM_INVINCIBLE`
  - `HIDDEN_CACHE`
  - `PARTICLE_WALL`
  - `CHILD_OF_THE_STARS`
  - `GLOW`
  - `PARTICLE_WALL`
  - `End Turn`
  - `FALLING_STAR`
  - `COSMIC_INDIFFERENCE`
  - `VOID_FORM`

This means the battle was making real progress and is not a simple stagnation case.

### Last recorded decision
- decision_index: `10`
- selected action: `VOID_FORM`
- action_id: `2`
- state before action:
  - hp/block/energy/stars: `75/15/3/2`
  - hand:
    - `ASCENDERS_BANE`
    - `VOID_FORM`
    - `CRASH_LANDING`
    - `FURNACE`
  - enemy:
    - `OWL_MAGISTRATE`
    - hp `232/247`
    - intent `PECK_ASSAULT`
    - attack `3 x 6`
- legal actions before action:
  - `End Turn`
  - `VOID_FORM`
  - `CRASH_LANDING`
  - `FURNACE`

### State after last recorded action
- hp/block/energy/stars: `75/25/0/2`
- hand: `(empty)`
- player powers:
  - `THORNS_POWER:3`
  - `CHILD_OF_THE_STARS_POWER:3`
  - `VOID_FORM_POWER:2`
- enemy:
  - `OWL_MAGISTRATE`
  - hp `229/247`
  - intent `PECK_ASSAULT`
  - attack `3 x 6`
- pendingChoice: `null`

### Failure shape
After this state:

- battle is still non-terminal
- no cycle was detected
- progress had occurred up to this point
- but `env.get_legal_actions()` returned an empty list

This is why the warning is:

- `no_legal_actions_while_non_terminal`

### RL-side interpretation
This is not the same issue as the earlier `ActionContinuation` bug:

- the earlier continuation-related `Illegal action` is no longer reproduced
- `COSMIC_INDIFFERENCE` continuation now resolves in the same live `GameInstance`
- the remaining issue happens later, after progress continues and `pendingChoice` is already `null`

So `fixed50:6485-37` remains a separate unresolved anomaly.

---

## Summary

### `B_heuristic_stagnation` 2件
- `fixed50:1642-31`
- `fixed50:3342-27`

Common pattern:
- `End Turn` repeated for the last 10 decisions
- enemy HP unchanged
- player HP unchanged
- legal actions remained available
- no cycle hash repetition

Interpretation:
- heuristic policy problem

### `no_legal_actions_while_non_terminal` 1件
- `fixed50:6485-37`

Pattern:
- battle progressed normally through 11 decisions
- continuation handling bug is already removed
- final non-terminal state has no legal actions

Interpretation:
- separate unresolved engine/API or state-transition anomaly candidate
