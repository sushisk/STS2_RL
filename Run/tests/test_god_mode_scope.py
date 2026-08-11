"""Regression guard: God Mode activation must remain test-only.

Runtime modules may expose the narrow WholeRunSession test-support wrapper, but no
non-test code is allowed to call that wrapper or the underlying CLR
EnableGodModeForTesting API. This catches the class of leak that caused STS2_RL#29,
where API/instance_whole_run.py enabled God Mode for every real Whole Run instance.
"""

from __future__ import annotations

import ast
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RUNTIME_ROOTS = (_ROOT / "API", _ROOT / "Run", _ROOT / "Combat")
_ALLOWED_TEST_SUPPORT = (_ROOT / "Run" / "whole_run_session.py").resolve()
_GOD_MODE_CALLS = {"EnableGodModeForTesting", "enable_god_mode_for_testing"}


class _GodModeCallVisitor(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path.resolve()
        self.function_stack: list[str] = []
        self.hits: list[tuple[int, str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.function_stack.append(node.name)
        self.generic_visit(node)
        self.function_stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr in _GOD_MODE_CALLS:
            # The wrapper implementation itself is intentionally retained as a
            # test-support escape hatch. Calling it from any other non-test module is
            # forbidden; tests are excluded from the scan below and may opt in there.
            allowed_wrapper_impl = (
                self.path == _ALLOWED_TEST_SUPPORT
                and bool(self.function_stack)
                and self.function_stack[-1] == "enable_god_mode_for_testing"
                and func.attr == "EnableGodModeForTesting"
            )
            if not allowed_wrapper_impl:
                self.hits.append((node.lineno, func.attr))
        self.generic_visit(node)


def _runtime_python_files():
    for root in _RUNTIME_ROOTS:
        for path in root.rglob("*.py"):
            relative_parts = path.relative_to(_ROOT).parts
            if "tests" in relative_parts or "__pycache__" in relative_parts:
                continue
            yield path


def test_god_mode_calls_are_confined_to_tests():
    violations: list[str] = []
    for path in _runtime_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _GodModeCallVisitor(path)
        visitor.visit(tree)
        relative = path.relative_to(_ROOT)
        for line, call_name in visitor.hits:
            violations.append(f"{relative}:{line}: {call_name}")

    assert not violations, (
        "God Mode activation leaked into non-test runtime code:\n" + "\n".join(violations)
    )


def _run_all() -> int:
    tests = [value for name, value in globals().items() if name.startswith("test_") and callable(value)]
    failures = 0
    for test in sorted(tests, key=lambda fn: fn.__name__):
        try:
            test()
        except Exception:
            failures += 1
            print(f"FAIL {test.__name__}")
            traceback.print_exc()
        else:
            print(f"PASS {test.__name__}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
