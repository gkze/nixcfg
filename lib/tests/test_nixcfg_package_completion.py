"""Tests for the packaged nixcfg completion renderer."""

from __future__ import annotations

from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_helper() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/nixcfg/render_completion.py",
        "_nixcfg_render_completion",
    )


def test_render_completion_uses_nixcfg_completion_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Completion rendering should keep the packaged CLI name and env var stable."""
    helper = _load_helper()
    calls: list[dict[str, str]] = []

    def fake_get_completion_script(**kwargs: str) -> str:
        calls.append(kwargs)
        return "completion-body"

    monkeypatch.setattr(helper, "get_completion_script", fake_get_completion_script)

    assert helper.render_completion("zsh") == "completion-body"
    assert calls == [
        {
            "prog_name": "nixcfg",
            "complete_var": "_NIXCFG_COMPLETE",
            "shell": "zsh",
        }
    ]


def test_main_prints_requested_completion(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI entrypoint should print the rendered completion script."""
    helper = _load_helper()
    monkeypatch.setattr(helper, "render_completion", lambda shell: f"{shell}-body")

    assert helper.main(["fish"]) == 0

    assert capsys.readouterr().out == "fish-body\n"


def test_main_rejects_invalid_argument_count() -> None:
    """The CLI should require exactly one shell name."""
    helper = _load_helper()

    with pytest.raises(SystemExit, match="usage: render_completion.py"):
        helper.main([])


def test_main_uses_sys_argv_when_no_explicit_args(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The helper should support direct script execution argument semantics."""
    helper = _load_helper()
    monkeypatch.setattr(helper, "render_completion", lambda shell: f"{shell}-body")
    monkeypatch.setattr("sys.argv", ["render_completion.py", "bash"])

    assert helper.main() == 0

    assert capsys.readouterr().out == "bash-body\n"
