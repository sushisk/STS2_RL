"""Combat/search: Search Coordinator-side primitives for the Combat execution
infrastructure build-out (docs/architecture/combat/mermaid_combat_*.mermaid).

Phase 2 (this package's first contents, `decision_context.py`) builds only the
foundational data types - Decision Signature, Decision Context, and a single-process
replay-with-verification function against the EXISTING `LiveCombatSession` bridge. No
worker pool, no RNG Hypothesis, no Main Process state machine yet - see
`decision_context.py`'s own module docstring for the exact scope.
"""
