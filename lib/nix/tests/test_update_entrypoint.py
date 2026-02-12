"""Tests for the top-level update.py entrypoint dispatch."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest

_CI_EXIT_CODE = 7


def _load_update_script_module() -> ModuleType:
    script_path = Path(__file__).resolve().parents[3] / "update.py"
    spec = importlib.util.spec_from_file_location("_update_script", script_path)
    if spec is None or spec.loader is None:
        msg = f"Could not load update.py module from {script_path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_update_entrypoint_dispatches_ci_commands(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invoke CI dispatcher when first arg is a known CI subcommand."""
    module = _load_update_script_module()
    called: list[object] = []

    def _fake_import(name: str) -> object:
        if name == "lib.update.ci":
            return SimpleNamespace(
                CI_COMMANDS={"merge-sources"},
                main=lambda args: called.append(tuple(args)) or _CI_EXIT_CODE,
            )
        if name == "lib.update.cli":
            return SimpleNamespace(main=lambda: called.append("update"))
        msg = f"unexpected import: {name}"
        raise AssertionError(msg)

    monkeypatch.setattr(module.importlib, "import_module", _fake_import)
    monkeypatch.setattr(module.sys, "argv", ["update.py", "merge-sources", "a", "b"])

    exit_code = module.main()

    assert exit_code == _CI_EXIT_CODE  # noqa: S101
    assert called == [("merge-sources", "a", "b")]  # noqa: S101


def test_update_entrypoint_dispatches_default_update_cli(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invoke update CLI when first arg is not a CI command."""
    module = _load_update_script_module()
    called: list[str] = []

    def _fake_import(name: str) -> object:
        if name == "lib.update.ci":
            return SimpleNamespace(CI_COMMANDS={"merge-sources"}, main=lambda _args: 5)
        if name == "lib.update.cli":
            return SimpleNamespace(main=lambda: called.append("update"))
        msg = f"unexpected import: {name}"
        raise AssertionError(msg)

    monkeypatch.setattr(module.importlib, "import_module", _fake_import)
    monkeypatch.setattr(module.sys, "argv", ["update.py", "--list"])

    exit_code = module.main()

    assert exit_code == 0  # noqa: S101
    assert called == ["update"]  # noqa: S101
