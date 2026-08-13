"""Tests for project-wide CLI helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

import click
import typer

from lib import cli as shared_cli

if TYPE_CHECKING:
    import pytest


def test_cli_typer_factory() -> None:
    """Build a Typer app with the shared CLI defaults."""
    app = shared_cli.make_typer_app(help_text="demo")
    assert isinstance(app, typer.Typer)


def test_run_main_success_and_click_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Normalize Typer/click outcomes to exit codes."""

    def _returns_int(
        *, args: list[str] | None, prog_name: str, standalone_mode: bool
    ) -> int:
        _ = (args, prog_name, standalone_mode)
        return 7

    assert (
        shared_cli.run_main(
            _returns_int,  # type: ignore[arg-type]
            argv=["--x"],
            prog_name="demo",
            default_exit_code=0,
        )
        == 7
    )

    def _returns_none(
        *, args: list[str] | None, prog_name: str, standalone_mode: bool
    ) -> None:
        _ = (args, prog_name, standalone_mode)

    assert (
        shared_cli.run_main(
            _returns_none,  # type: ignore[arg-type]
            argv=None,
            prog_name="demo",
            default_exit_code=3,
        )
        == 3
    )

    def _raises_exit(
        *, args: list[str] | None, prog_name: str, standalone_mode: bool
    ) -> None:
        _ = (args, prog_name, standalone_mode)
        raise click.exceptions.Exit(5)

    assert (
        shared_cli.run_main(
            _raises_exit,  # type: ignore[arg-type]
            argv=None,
            prog_name="demo",
        )
        == 5
    )

    def _raises_click(
        *, args: list[str] | None, prog_name: str, standalone_mode: bool
    ) -> None:
        _ = (args, prog_name, standalone_mode)
        raise click.ClickException("bad")

    assert (
        shared_cli.run_main(
            _raises_click,  # type: ignore[arg-type]
            argv=None,
            prog_name="demo",
        )
        == 1
    )
    assert "bad" in capsys.readouterr().err


def test_make_main_delegates_to_run_main(monkeypatch: pytest.MonkeyPatch) -> None:
    """Create a conventional main(argv) wrapper with fixed defaults."""
    captured: dict[str, object] = {}

    def _run_main(
        app: typer.Typer,
        *,
        argv: list[str] | None,
        prog_name: str,
        default_exit_code: int,
    ) -> int:
        captured.update({
            "app": app,
            "argv": argv,
            "prog_name": prog_name,
            "default_exit_code": default_exit_code,
        })
        return 9

    monkeypatch.setattr(shared_cli, "run_main", _run_main)
    app = typer.Typer()
    main = shared_cli.make_main(app, prog_name="demo")
    assert main(["--x"]) == 9
    assert captured["app"] is app
    assert captured["prog_name"] == "demo"
