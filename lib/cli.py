"""Shared CLI defaults for Typer/Click applications."""

import importlib
import sys
from typing import TYPE_CHECKING, Final, cast

import click
import typer
from typer.core import TyperGroup
from typer.main import get_command

if TYPE_CHECKING:
    from collections.abc import Callable, MutableMapping, Sequence

    from typer import _click as typer_click

HELP_CONTEXT_SETTINGS: Final[dict[str, list[str]]] = {
    "help_option_names": ["-h", "--help"],
}


def _lazy_typer_group(module_name: str, attribute_name: str) -> type[TyperGroup]:
    """Create a Click group that imports its real Typer app only on dispatch."""

    class LazyTyperGroup(TyperGroup):
        _loaded_command: TyperGroup | None = None
        _bootstrap_commands: MutableMapping[str, typer_click.Command]

        @property
        def commands(self) -> MutableMapping[str, typer_click.Command]:
            """Expose the real command mapping to help, completion, and tests."""
            return self._load_command().commands

        @commands.setter
        def commands(
            self,
            value: MutableMapping[str, typer_click.Command],
        ) -> None:
            self._bootstrap_commands = value

        def _load_command(self) -> TyperGroup:
            if self._loaded_command is None:
                module = importlib.import_module(module_name)
                loaded_app = cast("typer.Typer", getattr(module, attribute_name))
                loaded_command = get_command(loaded_app)
                if not isinstance(loaded_command, TyperGroup):
                    msg = f"{module_name}.{attribute_name} is not a command group"
                    raise TypeError(msg)
                self._loaded_command = loaded_command
            return self._loaded_command

        def get_command(
            self,
            ctx: typer_click.Context,
            cmd_name: str,
        ) -> typer_click.Command | None:
            return self._load_command().get_command(ctx, cmd_name)

        def list_commands(self, ctx: typer_click.Context) -> list[str]:
            return self._load_command().list_commands(ctx)

        def get_help(self, ctx: typer_click.Context) -> str:
            return self._load_command().get_help(ctx)

        def invoke(self, ctx: typer_click.Context) -> object:
            args = [*ctx._protected_args, *ctx.args]  # noqa: SLF001
            result = self._load_command().main(
                args=args,
                prog_name=ctx.command_path,
                standalone_mode=False,
            )
            if isinstance(result, int) and result != 0:
                raise typer.Exit(code=result)
            return result

    return LazyTyperGroup


def make_lazy_typer_app(
    *,
    module_name: str,
    help_text: str,
    attribute_name: str = "app",
) -> typer.Typer:
    """Create a lightweight placeholder for a command-specific Typer app."""
    return typer.Typer(
        cls=_lazy_typer_group(module_name, attribute_name),
        help=help_text,
        add_completion=False,
        no_args_is_help=False,
        context_settings={
            **HELP_CONTEXT_SETTINGS,
            "allow_extra_args": True,
            "ignore_unknown_options": True,
        },
    )


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
