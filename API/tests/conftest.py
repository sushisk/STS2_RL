"""Session-wide process cleanup for `API/tests`.

Several tests here load the real Emulator in-process via pythonnet (`clr.AddReference`)
or spawn `WholeRunWorkerPool`/`BranchWorkerPool` OS worker processes (each of which loads
its own CLR/Emulator copy - see `Run/worker_pool.py`'s `_worker_main`). Two related but
distinct cleanup problems have been observed after a full `pytest API/tests` run:

1. A worker pool a test forgot to `.close()` (e.g. an early `assert` failure skipping a
   `finally`) leaves its daemon worker processes running. `daemon=True` normally makes
   Python's own `multiprocessing.util._exit_function` atexit hook terminate them on
   interpreter exit - but that only fires if the interpreter actually gets to exit.
2. The pytest process itself, once it has hosted the CLR runtime for any in-process
   Emulator test, has been observed to sit alive (RSS ~100MB, matching a still-loaded
   Emulator) for minutes after printing its final summary - almost certainly a
   non-daemon thread the .NET runtime itself owns (GC/finalizer/thread-pool) that the
   CPython interpreter's normal shutdown sequence waits on. This is not something
   test-level cleanup can fix directly (the CLR runtime, once loaded via
   `clr.AddReference`, cannot be unloaded from a still-running process).

Fix for (1): force-terminate any multiprocessing children still alive at session end,
so a leaked worker never outlives the run regardless of which test leaked it.

Fix for (2): once every test has finished and pytest's own summary has been reported,
`os._exit()` the process directly instead of returning through normal interpreter
shutdown - skips whatever CLR-owned thread would otherwise block that shutdown. Safe
here specifically because this hook is the true last thing pytest does; stdout/stderr
are flushed first so no output is lost.

Uses `pytest_unconfigure`, not `pytest_sessionfinish`: the terminal reporter plugin
prints the final "N passed/failed in Ys" line from its own `pytest_sessionfinish`
implementation, and hook call order across plugins for the same hook name is not
"this file's function goes last" - `pytest_unconfigure` fires strictly afterward (part
of pytest's own final teardown sequence), so exiting there can't race the summary line.
`session.exitstatus` (rather than a hook-supplied `exitstatus` parameter, which
`pytest_unconfigure` doesn't receive) is read directly off the session via the plugin
manager's registered session object.
"""

from __future__ import annotations

import multiprocessing
import os
import sys


def pytest_unconfigure(config) -> None:
    session = getattr(config, "_ci_process_cleanup_session", None)
    exitstatus = getattr(session, "exitstatus", None)

    for child in multiprocessing.active_children():
        try:
            child.terminate()
        except Exception:  # noqa: BLE001, S110 - best-effort; we're exiting regardless
            pass
    for child in multiprocessing.active_children():
        child.join(timeout=5.0)

    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(int(exitstatus) if isinstance(exitstatus, int) else 0)


def pytest_sessionfinish(session, exitstatus: int) -> None:
    # Terminal summary reporting happens inside this same hook name (across plugins) -
    # stash the session here so pytest_unconfigure (which fires strictly after every
    # pytest_sessionfinish implementation, including the terminal reporter's) can read
    # the real exit status without pytest_unconfigure's own signature providing one.
    session.config._ci_process_cleanup_session = session
