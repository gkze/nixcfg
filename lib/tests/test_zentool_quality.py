"""Regression tests for zentool's multi-except formatting safeguards."""

from __future__ import annotations

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
_EXPECTED_MULTI_EXCEPT_SNIPPETS = (
    "except (OSError, configparser.Error):",
    "except (RuntimeError, ValueError) as exc:",
    "except (FileNotFoundError, subprocess.SubprocessError, OSError) as exc:",
    "except (EOFError, KeyboardInterrupt):",
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


def _collect_multi_except_sites(path: Path) -> list[tuple[int, str]]:
    return [
        (line_number, line)
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), 1
        )
        if line.lstrip().startswith("except (") and "," in line
    ]


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


def test_zentool_multi_except_sites_match_expected_snippets() -> None:
    """Keep zentool's known multi-except handlers easy to audit."""
    sites = _collect_multi_except_sites(ZENTOOL_PATH)
    assert [line.strip() for _line_number, line in sites] == list(
        _EXPECTED_MULTI_EXCEPT_SNIPPETS
    )


def test_zentool_declares_cli_entrypoint_guard() -> None:
    """The script should invoke its CLI when executed directly."""
    source = ZENTOOL_PATH.read_text(encoding="utf-8")

    assert 'if __name__ == "__main__":' in source
    assert "raise SystemExit(main())" in source


def test_zentool_main_guard_executes_help(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Executing zentool directly should route through the CLI entrypoint."""
    monkeypatch.setattr(sys, "argv", [str(ZENTOOL_PATH), "--help"])

    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(ZENTOOL_PATH), run_name="__main__")

    assert exc.value.code == 0
    assert "usage: zentool" in capsys.readouterr().out


def test_parenthesized_zentool_multi_excepts_survive_python_quality_tools(
    tmp_path: Path,
) -> None:
    """Running the Python quality toolchain should not de-parenthesize zentool."""
    source = ZENTOOL_PATH.read_text(encoding="utf-8")
    temp_root, script_path = _copy_zentool_tree(tmp_path, source=source)

    _run_repo_python_quality_tools(temp_root, script_path)

    assert script_path.read_text(encoding="utf-8") == source


def test_python_quality_tools_repair_deparenthesized_zentool_multi_excepts(
    tmp_path: Path,
) -> None:
    """If a bad rewrite lands, the Python quality toolchain should repair it."""
    source = ZENTOOL_PATH.read_text(encoding="utf-8")
    broken_source = source
    for snippet in _EXPECTED_MULTI_EXCEPT_SNIPPETS:
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

    assert script_path.read_text(encoding="utf-8") == source
