"""Phase 3A.3/3A.4 Action fault contract - Python-side acceptance tests for
`ActionExecutionError`/`FaultedCombatSessionError` (`Combat/live_combat_session.py`) and
(Phase 3A.4) Console I/O isolation (`Sts2Emulator.Api.Internal.SafeConsoleTextWriter`/
`SafeConsoleOutput`, Emulator commits `e9fb60b`/`50cf52a`).

No pytest dependency, matching this package's other native test files
(`test_scenario_v2.py`/`test_choice_semantics.py`) - a plain assertion-based runner via
`main()`. Requires a live GameInstance.

Two fault-injection techniques, both independently reproduced from the RL side rather
than trusted secondhand from the Emulator's own report:

1. **Whole-`Console.Out`-replacement** (Phase 3A.3 original,
   `_corrupted_console_out()`): swaps `Console.Out` entirely for a disposed
   `StreamWriter`, bypassing whatever `TextWriter` was installed there (including, as
   of Phase 3A.4, `SafeConsoleTextWriter`). This confirms a genuinely unhandled Console
   failure (not going through the safety wrapper at all) still correctly faults the
   action - the wrapper does not somehow make ALL Console breakage invisible.
2. **`SafeConsoleTextWriter._inner` reflection tamper** (Phase 3A.4 addition,
   `_tampered_safe_console_inner()`): mirrors the Emulator's own Layer B methodology
   (`console_io_isolation_phase3a4_20260727.md` §7-B) - swaps only the wrapper's own
   private `_inner` field (via reflection) to a broken writer, leaving
   `SafeConsoleTextWriter` itself installed and active. This is the precise scenario
   the isolation feature exists for: an `IOException`/`ObjectDisposedException`/
   `UnauthorizedAccessException` occurring inside the wrapper's own write call must be
   swallowed (no fault), while any OTHER exception type must still fault normally (the
   safety net must not overreach).
"""

from __future__ import annotations

import contextlib
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # Combat/
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "data"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "env"))

from emulator_bridge import ensure_loaded  # noqa: E402
from live_combat_session import (  # noqa: E402
    ActionExecutionError,
    FaultedCombatSessionError,
    LiveCombatSession,
)


def _simple_spec(hand=None, enemy_hp=48):
    return {
        "character_id": "IRONCLAD", "player_hp": None, "player_max_hp": None,
        "hand": hand if hand is not None else ["STRIKE_IRONCLAD"],
        "draw_pile": [], "discard_pile": [], "exhaust_pile": [],
        "player_powers": [], "relics": [], "potions": [], "seed": 1,
        "enemies": [{"monster_id": "CALCIFIED_CULTIST", "hp": enemy_hp}],
    }


@contextlib.contextmanager
def _corrupted_console_out():
    """Temporarily replaces `Console.Out` with a disposed `StreamWriter` so any write to
    it (e.g. `Log.Info`) raises `ObjectDisposedException`. Always restores the original
    writer in `finally`, even on an unexpected exception."""
    ensure_loaded()
    from System import Console
    from System.IO import MemoryStream, StreamWriter

    original = Console.Out
    stream = MemoryStream()
    writer = StreamWriter(stream)
    writer.Dispose()
    Console.SetOut(writer)
    try:
        yield
    finally:
        Console.SetOut(original)


def _installed_safe_console_writer():
    """Navigates `Console.Out`'s actual runtime type chain
    (`TextWriter.SyncTextWriter` -> private `_out` field -> `SafeConsoleTextWriter`,
    confirmed empirically via reflection - `Console.SetOut`'s own .NET implementation
    wraps whatever is passed in `TextWriter.Synchronized`, per the Emulator report §6's
    own note) to reach the actually-installed `SafeConsoleTextWriter` instance and its
    private `_inner` `FieldInfo`. Requires `Console.Out` to already be the Phase 3A.4
    wrapper - i.e. at least one `LiveCombatSession.start_combat()`/`GameInstance.
    EnsureTestMode()` call must have already happened in this process."""
    from System import Console
    from System.Reflection import BindingFlags

    flags = BindingFlags.NonPublic | BindingFlags.Instance
    sync_writer = Console.Out
    out_field = sync_writer.GetType().GetField("_out", flags)
    safe_writer = out_field.GetValue(sync_writer)
    assert "SafeConsoleTextWriter" in safe_writer.GetType().FullName, (
        f"Console.Out is not wrapped by SafeConsoleTextWriter (Phase 3A.4 not installed?) - got {safe_writer.GetType().FullName}"
    )
    inner_field = safe_writer.GetType().GetField("_inner", flags)
    return safe_writer, inner_field


@contextlib.contextmanager
def _tampered_safe_console_inner(new_inner):
    """Phase 3A.4: swaps ONLY the installed `SafeConsoleTextWriter`'s private `_inner`
    field (leaving the wrapper itself installed and active) - see module docstring
    technique 2. Always restores the original `_inner` in `finally`."""
    ensure_loaded()
    safe_writer, inner_field = _installed_safe_console_writer()
    original_inner = inner_field.GetValue(safe_writer)
    inner_field.SetValue(safe_writer, new_inner)
    try:
        yield
    finally:
        inner_field.SetValue(safe_writer, original_inner)


def _strike_action(battle_state):
    return next(
        a for a in battle_state._cached_legal_actions  # noqa: SLF001 - white-box, same pattern used throughout this package
        if a["action_type"] == "card" and a["parameters"].get("cardId") == "STRIKE_IRONCLAD"
    )


def test_action_fault_reaches_python_as_action_execution_error():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    action = _strike_action(state)

    with _corrupted_console_out():
        try:
            session.step(state, action, target_enemy_index=0)
            raise AssertionError("expected ActionExecutionError, no exception was raised")
        except ActionExecutionError as exc:
            fault_exc = exc

    assert fault_exc.context is not None
    assert fault_exc.context.original_exception_type is not None and "ObjectDisposed" in fault_exc.context.original_exception_type, fault_exc.context
    assert fault_exc.context.original_exception_message is not None and len(fault_exc.context.original_exception_message) > 0, fault_exc.context
    # Python-side authoritative fields (not parsed from the C# string) - from the
    # DecisionFrame this session held and the `action`/target parameters passed to step().
    assert fault_exc.context.combat_session_id == state.decision_frame.combat_session_id, fault_exc.context
    assert fault_exc.context.step_index == state.decision_frame.step_index, fault_exc.context
    assert fault_exc.context.action_id == action["action_id"], fault_exc.context
    assert fault_exc.context.action_type == "card", fault_exc.context
    assert fault_exc.context.card_id == "STRIKE_IRONCLAD", fault_exc.context
    assert fault_exc.context.target_enemy_index == 0, fault_exc.context
    assert fault_exc.__cause__ is not None, "original CLR exception must be preserved via `from clr_exc`"


def test_fault_marks_session_faulted_and_does_not_return_step_result():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    action = _strike_action(state)

    with _corrupted_console_out():
        try:
            result = session.step(state, action, target_enemy_index=0)
            raise AssertionError(f"expected ActionExecutionError, got a StepResult-shaped BattleState instead: {result!r}")
        except ActionExecutionError:
            pass

    assert session._session_faulted is True  # noqa: SLF001 - white-box


def test_fault_rejects_step_get_observation_get_legal_actions_capture_snapshot():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    action = _strike_action(state)

    with _corrupted_console_out():
        try:
            session.step(state, action, target_enemy_index=0)
        except ActionExecutionError:
            pass

    # step() rejected (same action, no auto-retry - re-submitting must reject, not re-execute)
    try:
        session.step(state, action, target_enemy_index=0)
        raise AssertionError("expected FaultedCombatSessionError from step() after fault")
    except FaultedCombatSessionError:
        pass

    try:
        session.get_observation()
        raise AssertionError("expected FaultedCombatSessionError from get_observation() after fault")
    except FaultedCombatSessionError:
        pass

    try:
        session.get_legal_actions()
        raise AssertionError("expected FaultedCombatSessionError from get_legal_actions() after fault")
    except FaultedCombatSessionError:
        pass

    try:
        session.capture_snapshot()
        raise AssertionError("expected FaultedCombatSessionError from capture_snapshot() after fault")
    except FaultedCombatSessionError:
        pass


def test_fault_recovery_via_fresh_start_combat():
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    action = _strike_action(state)

    with _corrupted_console_out():
        try:
            session.step(state, action, target_enemy_index=0)
        except ActionExecutionError:
            pass

    assert session._session_faulted is True  # noqa: SLF001

    fresh_state = session.start_combat(_simple_spec())
    assert session._session_faulted is False  # noqa: SLF001 - cleared only by a successful reset, per the contract

    # normal operation resumes: a real Step() (no console corruption this time) succeeds.
    fresh_action = _strike_action(fresh_state)
    next_state = session.step(fresh_state, fresh_action, target_enemy_index=0)
    assert next_state is not None
    obs = session.get_observation()
    assert obs is not None
    legal = session.get_legal_actions()
    assert isinstance(legal, list)
    snapshot = session.capture_snapshot()
    assert snapshot is not None


def test_fault_not_confused_with_quiescent_boundary_violation():
    """Distinctness check (this task's explicit requirement: 'Action faultを通常のStep
    失敗やQuiescent違反と混同しないこと'). Structural: `ActionExecutionError`/
    `FaultedCombatSessionError` are NOT `QuiescentBoundaryViolation` (or a subclass of
    it) and vice versa - a `try/except QuiescentBoundaryViolation` block does not
    accidentally also swallow an Action fault, and a `try/except ActionExecutionError`
    block does not accidentally swallow a genuine boundary violation. The Emulator's own
    negative test (Phase 3A.3 report §6-B, `qb_negative_test.py`) already exercises a
    forced non-quiescent state via reflection and confirms `QuiescentBoundaryViolationException`
    still fires as its own distinct type on the C# side - not re-derived here (a
    reflection-based `CurrentlyRunningAction` tamper from Python would be fragile and
    duplicate coverage the Emulator side already owns); this test instead pins the
    Python-side type hierarchy directly."""
    from live_combat_session import QuiescentBoundaryViolation

    assert not issubclass(ActionExecutionError, QuiescentBoundaryViolation)
    assert not issubclass(FaultedCombatSessionError, QuiescentBoundaryViolation)
    assert not issubclass(QuiescentBoundaryViolation, ActionExecutionError)
    assert not issubclass(QuiescentBoundaryViolation, FaultedCombatSessionError)
    assert ActionExecutionError is not FaultedCombatSessionError
    assert not issubclass(ActionExecutionError, FaultedCombatSessionError)
    assert not issubclass(FaultedCombatSessionError, ActionExecutionError)


def test_normal_card_play_target_and_end_turn_never_fault():
    """Practical-level false-positive check on plain, uncorrupted play: a normal card
    play (with a target), a Target-requiring follow-up, and an End Turn system action -
    none of them may raise `ActionExecutionError`/`FaultedCombatSessionError` (no
    `Console.Out` corruption in this test - real, unmodified execution)."""
    session = LiveCombatSession()
    state = session.start_combat(_simple_spec(hand=["STRIKE_IRONCLAD", "DEFEND_IRONCLAD"]))
    action = _strike_action(state)
    state = session.step(state, action, target_enemy_index=0)
    assert session._session_faulted is False  # noqa: SLF001

    end_turn = next(a for a in state._cached_legal_actions if a["action_type"] == "system")  # noqa: SLF001
    state = session.step(state, end_turn)
    assert session._session_faulted is False  # noqa: SLF001
    assert session.last_fault_context is None


def test_console_io_isolation_does_not_fault_on_disposed_inner_writer():
    """Phase 3A.4: `ObjectDisposedException` occurring inside the installed
    `SafeConsoleTextWriter`'s own `_inner` write must be swallowed - the action must
    complete normally (a real `StepResult`, no `ActionExecutionError`)."""
    from System.IO import MemoryStream, StreamWriter

    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())  # ensures SafeConsoleTextWriter is installed (EnsureTestMode)
    action = _strike_action(state)

    disposed = StreamWriter(MemoryStream())
    disposed.Dispose()
    with _tampered_safe_console_inner(disposed):
        next_state = session.step(state, action, target_enemy_index=0)

    assert next_state is not None
    assert next_state.decision_frame != state.decision_frame
    assert session._session_faulted is False  # noqa: SLF001


def test_console_io_isolation_does_not_fault_on_broken_pipe_inner_writer():
    """Phase 3A.4: a genuine, natively-thrown `IOException` (a broken
    `AnonymousPipeServerStream` - the Emulator's own B3 technique, avoiding a
    Python-subclassed `TextWriter` since pythonnet wraps virtual-call exceptions in
    `TargetInvocationException`, which would defeat the `SafeConsoleTextWriter` catch
    filter and produce a false negative - see the Emulator report §7-B's own note on
    this exact pitfall) occurring inside `_inner` must also be swallowed."""
    import clr

    clr.AddReference("System.IO.Pipes")
    from System.IO.Pipes import AnonymousPipeServerStream, PipeDirection
    from System.IO import StreamWriter

    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    action = _strike_action(state)

    pipe = AnonymousPipeServerStream(PipeDirection.Out)
    pipe.DisposeLocalCopyOfClientHandle()
    broken_writer = StreamWriter(pipe)
    pipe.Dispose()  # server end closed - the next write on broken_writer throws a genuine IOException

    with _tampered_safe_console_inner(broken_writer):
        next_state = session.step(state, action, target_enemy_index=0)

    assert next_state is not None
    assert session._session_faulted is False  # noqa: SLF001


def test_console_io_isolation_still_faults_on_unrelated_inner_writer_exception():
    """Phase 3A.4 control case: the safety net must NOT overreach - an exception type
    other than IOException/ObjectDisposedException/UnauthorizedAccessException raised
    from `_inner` (here: `NotSupportedException`, from writing to a non-writable
    `MemoryStream`) must still fault the action normally, proving `SafeConsoleTextWriter`
    does not accidentally hide genuine problems."""
    from System.IO import MemoryStream, StreamWriter

    session = LiveCombatSession()
    state = session.start_combat(_simple_spec())
    action = _strike_action(state)

    # A fixed-size (non-expandable), writable MemoryStream: writes within its tiny
    # capacity succeed, but the real log text (e.g. "Player 1 playing card ...") is much
    # longer than 4 bytes, so StreamWriter's buffered flush overflows it, raising
    # `NotSupportedException: Memory stream is not expandable.` - a genuine, unrelated
    # exception type, not one of the 3 Phase 3A.4 neutralizes.
    fixed_capacity_stream = MemoryStream(bytes(4))
    assert fixed_capacity_stream.CanWrite is True
    unrelated_failing_writer = StreamWriter(fixed_capacity_stream)
    unrelated_failing_writer.AutoFlush = True

    with _tampered_safe_console_inner(unrelated_failing_writer):
        try:
            session.step(state, action, target_enemy_index=0)
            raise AssertionError("expected ActionExecutionError - the safety net must not suppress unrelated exception types")
        except ActionExecutionError as exc:
            assert exc.context.original_exception_type is not None
            assert "NotSupportedException" in exc.context.original_exception_type, exc.context

    assert session._session_faulted is True  # noqa: SLF001
    # recover for any subsequent test in this file's run
    session.start_combat(_simple_spec())


def _run_all():
    tests = [obj for name, obj in list(globals().items()) if name.startswith("test_") and callable(obj)]
    passed, failed = [], []
    for test in tests:
        try:
            test()
            passed.append(test.__name__)
            print(f"PASS {test.__name__}")
        except Exception:  # noqa: BLE001
            failed.append(test.__name__)
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
    print(f"\n{len(passed)} passed, {len(failed)} failed")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(_run_all())
