"""Regression tests for zentool's multi-except formatting safeguards."""

from __future__ import annotations

import ast
import py_compile
import runpy
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from lib.tests._zen_tooling import resolve_zen_script_path
from lib.update.paths import REPO_ROOT

ZENTOOL_PATH = resolve_zen_script_path("zentool")
_EXPECTED_MULTI_EXCEPT_HANDLERS = (
    (("OSError", "configparser.Error"), None),
    (("RuntimeError", "ValueError"), "exc"),
    (("FileNotFoundError", "subprocess.SubprocessError", "OSError"), "exc"),
    (("EOFError", "KeyboardInterrupt"), None),
)


def _run(command: list[str], *, cwd: Path) -> None:
    result = subprocess.run(  # noqa: S603
        command,
        capture_output=True,
        check=False,
        cwd=cwd,
        text=True,
    )
    if result.returncode == 0:
        return

    message = (
        f"command failed: {' '.join(command)}\n"
        f"stdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    raise AssertionError(message)


def _copy_zentool_tree(tmp_path: Path, *, source: str) -> tuple[Path, Path]:
    temp_root = tmp_path / "repo"
    temp_root.mkdir()
    shutil.copy2(REPO_ROOT / "pyproject.toml", temp_root / "pyproject.toml")

    script_path = temp_root / ZENTOOL_PATH.resolve().relative_to(REPO_ROOT.resolve())
    script_path.parent.mkdir(parents=True)
    script_path.write_text(source, encoding="utf-8")
    return temp_root, script_path


def _parse_python(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _name_expr(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_expr(node.value)}.{node.attr}"
    message = f"unexpected exception type node: {ast.dump(node)}"
    raise AssertionError(message)


def _collect_multi_except_handlers(
    path: Path,
) -> list[tuple[tuple[str, ...], str | None]]:
    handlers: list[tuple[tuple[str, ...], str | None]] = []
    for node in ast.walk(_parse_python(path)):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple):
            handlers.append((
                tuple(_name_expr(item) for item in node.type.elts),
                node.name,
            ))
    return handlers


def _has_main_guard(tree: ast.Module) -> bool:
    for statement in tree.body:
        if not isinstance(statement, ast.If):
            continue
        if not _is_dunder_main_test(statement.test):
            continue
        if len(statement.body) != 1 or not isinstance(statement.body[0], ast.Raise):
            continue
        raised = statement.body[0].exc
        if not isinstance(raised, ast.Call):
            continue
        if not isinstance(raised.func, ast.Name) or raised.func.id != "SystemExit":
            continue
        if len(raised.args) != 1 or raised.keywords:
            continue
        main_call = raised.args[0]
        if isinstance(main_call, ast.Call) and isinstance(main_call.func, ast.Name):
            return main_call.func.id == "main" and not main_call.args
    return False


def _is_dunder_main_test(node: ast.expr) -> bool:
    if not isinstance(node, ast.Compare):
        return False
    if len(node.ops) != 1 or not isinstance(node.ops[0], ast.Eq):
        return False
    if len(node.comparators) != 1:
        return False
    left = node.left
    right = node.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    )


def _run_repo_python_quality_tools(temp_root: Path, script_path: Path) -> None:
    _run(
        [
            sys.executable,
            "-m",
            "lib.fix_python_multi_except",
            "--pyupgrade-exe",
            sys.executable,
            "--pyupgrade-arg=-m",
            "--pyupgrade-arg=pyupgrade",
            "--pyupgrade-arg=--py313-plus",
            str(script_path),
        ],
        cwd=REPO_ROOT,
    )
    relative_script = script_path.relative_to(temp_root)
    _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "check",
            "--fix-only",
            "--config",
            "pyproject.toml",
            str(relative_script),
        ],
        cwd=temp_root,
    )
    _run(
        [
            sys.executable,
            "-m",
            "ruff",
            "format",
            "--config",
            "pyproject.toml",
            str(relative_script),
        ],
        cwd=temp_root,
    )
    py_compile.compile(str(script_path), doraise=True)


def test_zentool_multi_except_handlers_match_expected_ast() -> None:
    """Keep zentool's known multi-except handlers easy to audit."""
    assert _collect_multi_except_handlers(ZENTOOL_PATH) == list(
        _EXPECTED_MULTI_EXCEPT_HANDLERS
    )


def test_zentool_declares_cli_entrypoint_guard() -> None:
    """The script should invoke its CLI when executed directly."""
    assert _has_main_guard(_parse_python(ZENTOOL_PATH))


def test_zentool_main_guard_executes_help(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Executing zentool directly should route through the CLI entrypoint."""
    monkeypatch.setattr(sys, "argv", [str(ZENTOOL_PATH), "--help"])

    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(ZENTOOL_PATH), run_name="__main__")

    assert exc.value.code == 0
    assert "usage: zentool" in capsys.readouterr().out.lower()


def test_parenthesized_zentool_multi_excepts_survive_python_quality_tools(
    tmp_path: Path,
) -> None:
    """Running the Python quality toolchain should not de-parenthesize zentool."""
    source = ZENTOOL_PATH.read_text(encoding="utf-8")
    temp_root, script_path = _copy_zentool_tree(tmp_path, source=source)

    _run_repo_python_quality_tools(temp_root, script_path)

    assert _collect_multi_except_handlers(script_path) == list(
        _EXPECTED_MULTI_EXCEPT_HANDLERS
    )


def test_python_quality_tools_repair_deparenthesized_zentool_multi_excepts(
    tmp_path: Path,
) -> None:
    """If a bad rewrite lands, the Python quality toolchain should repair it."""
    source = ZENTOOL_PATH.read_text(encoding="utf-8")
    broken_source = source
    for types, name in _EXPECTED_MULTI_EXCEPT_HANDLERS:
        snippet = f"except ({', '.join(types)})"
        if name is not None:
            snippet = f"{snippet} as {name}"
        snippet = f"{snippet}:"
        broken_source = broken_source.replace(
            snippet, snippet.replace("(", "", 1).replace(")", "", 1)
        )

    temp_root, script_path = _copy_zentool_tree(tmp_path, source=broken_source)

    with pytest.raises(
        py_compile.PyCompileError,
        match="multiple exception types must be parenthesized",
    ):
        py_compile.compile(str(script_path), doraise=True)

    _run_repo_python_quality_tools(temp_root, script_path)

    assert _collect_multi_except_handlers(script_path) == list(
        _EXPECTED_MULTI_EXCEPT_HANDLERS
    )
