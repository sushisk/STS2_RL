"""RL-Training Communication API (contract: docs/contracts/rl_training_dto_documentation_v0_5.md).

This package implements the RL side of the contract only. It never initializes
pythonnet/CLR itself at import time - CLR initialization happens transitively only when
an `Instance` subclass (in `instance_combat.py`/`instance_whole_run.py`) actually
constructs a `LiveCombatSession`/`WholeRunSession`, which only ever happens inside the RL
Runtime OS process (`api_runtime.RLApiServerProcess`), never inside a Training-side
`RLApiClient`.
"""
