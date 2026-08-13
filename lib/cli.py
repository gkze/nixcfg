"""Shared CLI defaults for Typer/Click applications."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Final

import click
import typer

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

HELP_CONTEXT_SETTINGS: Final[dict[str, list[str]]] = {
    "help_option_names": ["-h", "--help"],
}


def make_typer_app(*, help_text: str, no_args_is_help: bool = False) -> typer.Typer:
    """Create a Typer app with consistent project defaults."""
    return typer.Typer(
        help=help_text,
        add_completion=False,
        no_args_is_help=no_args_is_help,
        context_settings=HELP_CONTEXT_SETTINGS,
    )


def run_main(
    app: typer.Typer,
    *,
    argv: Sequence[str] | None,
    prog_name: str,
    default_exit_code: int = 0,
) -> int:
    """Run a Typer app and normalize Click/Typer exits to integer codes."""
    args = list(argv) if argv is not None else None
    try:
        result = app(args=args, prog_name=prog_name, standalone_mode=False)
    except click.exceptions.Exit as exc:
        return int(exc.exit_code)
    except click.exceptions.ClickException as exc:
        exc.show(file=sys.stderr)
        return int(exc.exit_code)
    return int(result) if isinstance(result, int) else default_exit_code


def make_main(
    app: typer.Typer,
    *,
    prog_name: str,
    default_exit_code: int = 0,
) -> Callable[..., int]:
    """Build a conventional ``main(argv)`` wrapper for a Typer app."""

    def _main(argv: Sequence[str] | None = None) -> int:
        return run_main(
            app,
            argv=argv,
            prog_name=prog_name,
            default_exit_code=default_exit_code,
        )

    return _main
