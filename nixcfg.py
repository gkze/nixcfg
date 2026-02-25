#!/usr/bin/env python
"""Unified CLI for nixcfg project tasks."""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from typing import TYPE_CHECKING, Annotated, Literal, Protocol, cast

import typer

from lib.nix.schemas._codegen import main as codegen_main
from lib.nix.schemas._fetch import check as schema_check
from lib.nix.schemas._fetch import fetch as fetch_schemas
from lib.update.ci import CI_COMMANDS
from lib.update.refs import get_flake_inputs_with_refs

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib.update.cli import UpdateOptions


class _UpdateCliModule(Protocol):
    UpdateOptions: type[UpdateOptions]

    def check_required_tools(
        self,
        *,
        include_flake_edit: bool,
        source: str | None,
        needs_sources: bool,
    ) -> list[str]: ...

    async def run_updates(self, opts: UpdateOptions) -> int: ...


_is_tty = sys.stdout.isatty()

TTYMode = Literal["auto", "force", "off", "full"]
_TTY_MODES: tuple[str, ...] = ("auto", "force", "off", "full")


def _update_cli() -> _UpdateCliModule:
    return cast("_UpdateCliModule", importlib.import_module("lib.update.cli"))


app = typer.Typer(
    name="nixcfg",
    help="Unified CLI for nixcfg project tasks.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode="rich" if _is_tty else None,
)

_FORWARDED_ARGS_CONTEXT_SETTINGS = {
    "allow_extra_args": True,
    "allow_interspersed_args": False,
    "ignore_unknown_options": True,
}


def _parse_tty_mode(value: str) -> TTYMode:
    if value == "auto":
        return "auto"
    if value == "force":
        return "force"
    if value == "off":
        return "off"
    if value == "full":
        return "full"
    msg = f"Invalid --tty value: {value!r}. Expected one of: {', '.join(_TTY_MODES)}"
    raise typer.BadParameter(msg)


def _build_update_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nixcfg update")
    parser.add_argument(
        "source",
        nargs="?",
        help="Source or flake input to update (default: all)",
    )
    parser.add_argument("--list", "-l", action="store_true", dest="list_targets")
    parser.add_argument("--no-refs", "-R", action="store_true")
    parser.add_argument("--no-sources", "-S", action="store_true")
    parser.add_argument("--no-input", "-I", action="store_true")
    parser.add_argument("--check", "-c", action="store_true")
    parser.add_argument("--validate", "-v", action="store_true")
    parser.add_argument("--schema", "-s", action="store_true")
    parser.add_argument("--json", "-j", action="store_true", dest="json_output")
    parser.add_argument("--verbose", "-V", action="store_true")
    parser.add_argument("--quiet", "-q", action="store_true")
    parser.add_argument("--tty", "-t", default="auto")
    parser.add_argument(
        "--zellij-guard",
        action=argparse.BooleanOptionalAction,
        default=None,
    )
    parser.add_argument("--native-only", "-n", action="store_true")
    parser.add_argument("--http-timeout", type=int, default=None)
    parser.add_argument("--subprocess-timeout", type=int, default=None)
    parser.add_argument("--max-nix-builds", type=int, default=None)
    parser.add_argument("--log-tail-lines", type=int, default=None)
    parser.add_argument("--render-interval", type=float, default=None)
    parser.add_argument("--user-agent", default=None)
    parser.add_argument("--retries", type=int, default=None)
    parser.add_argument("--retry-backoff", type=float, default=None)
    parser.add_argument("--fake-hash", default=None)
    parser.add_argument("--deno-platforms", default=None)
    parser.add_argument("--pinned-versions", default=None)
    return parser


# ---------------------------------------------------------------------------
# update — update workflow entrypoint
# ---------------------------------------------------------------------------


@app.command(
    name="update",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    add_help_option=False,
)
def update_cmd(ctx: typer.Context) -> None:
    """Update source versions/hashes and flake input refs."""
    update_cli = _update_cli()
    parser = _build_update_parser()
    try:
        parsed = parser.parse_args(list(ctx.args))
    except SystemExit as exc:
        if isinstance(exc.code, int):
            raise typer.Exit(code=exc.code) from None
        raise typer.Exit(code=0 if exc.code is None else 2) from None

    opts = update_cli.UpdateOptions(
        source=parsed.source,
        list_targets=parsed.list_targets,
        no_refs=parsed.no_refs,
        no_sources=parsed.no_sources,
        no_input=parsed.no_input,
        check=parsed.check,
        validate=parsed.validate,
        schema=parsed.schema,
        json=parsed.json_output,
        verbose=parsed.verbose,
        quiet=parsed.quiet,
        tty=_parse_tty_mode(parsed.tty),
        zellij_guard=parsed.zellij_guard,
        native_only=parsed.native_only,
        http_timeout=parsed.http_timeout,
        subprocess_timeout=parsed.subprocess_timeout,
        max_nix_builds=parsed.max_nix_builds,
        log_tail_lines=parsed.log_tail_lines,
        render_interval=parsed.render_interval,
        user_agent=parsed.user_agent,
        retries=parsed.retries,
        retry_backoff=parsed.retry_backoff,
        fake_hash=parsed.fake_hash,
        deno_platforms=parsed.deno_platforms,
        pinned_versions=parsed.pinned_versions,
    )

    if not (opts.list_targets or opts.schema or opts.validate):
        needs_flake_edit = not opts.no_refs and not opts.native_only
        if needs_flake_edit and opts.source:
            ref_names = {i.name for i in get_flake_inputs_with_refs()}
            needs_flake_edit = opts.source in ref_names

        missing = update_cli.check_required_tools(
            include_flake_edit=needs_flake_edit,
            source=opts.source,
            needs_sources=not opts.no_sources,
        )
        if missing:
            sys.stderr.write(f"Error: Required tools not found: {', '.join(missing)}\n")
            sys.stderr.write("Please install them and ensure they are in your PATH.\n")
            raise typer.Exit(code=1)

    raise typer.Exit(code=asyncio.run(update_cli.run_updates(opts)))


# ---------------------------------------------------------------------------
# ci — subgroup: programmatically registered from CI_COMMANDS
# ---------------------------------------------------------------------------

ci_app = typer.Typer(
    name="ci",
    help="CI helper tools for update pipelines.",
    no_args_is_help=True,
    rich_markup_mode="rich" if _is_tty else None,
)
app.add_typer(ci_app)


def _make_ci_callback(cmd_name: str) -> Callable[[typer.Context], None]:
    """Create a Typer callback that dispatches to a CI_COMMANDS entry."""

    def _callback(ctx: typer.Context) -> None:
        try:
            code = CI_COMMANDS[cmd_name].func(ctx.args)
        except SystemExit as exc:
            if isinstance(exc.code, int):
                raise typer.Exit(code=exc.code) from None
            raise typer.Exit(code=0 if exc.code is None else 2) from None
        raise typer.Exit(code=code)

    # Typer uses the function name for internal dedup — give each a unique name.
    _callback.__name__ = f"ci_{cmd_name.replace('-', '_')}"
    return _callback


def _register_ci_commands() -> None:
    """Register all CI subcommands from the CI_COMMANDS dict."""
    for name, cmd in CI_COMMANDS.items():
        ci_app.command(
            name=name,
            context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
            add_help_option=False,
            help=cmd.help,
        )(_make_ci_callback(name))


_register_ci_commands()


# ---------------------------------------------------------------------------
# schema — subgroup for Nix JSON schema utilities
# ---------------------------------------------------------------------------

schema_app = typer.Typer(
    name="schema",
    help="Nix JSON schema utilities (fetch, codegen).",
    no_args_is_help=True,
    rich_markup_mode="rich" if _is_tty else None,
)
app.add_typer(schema_app)


@schema_app.command(
    name="fetch", help="Fetch Nix JSON schemas from the NixOS/nix repo."
)
def schema_fetch(
    *,
    check: Annotated[
        bool,
        typer.Option(
            "--check", help="Verify vendored schemas match the pinned commit."
        ),
    ] = False,
) -> None:
    """Download or verify vendored Nix JSON schemas."""
    if check:
        ok = schema_check()
        raise typer.Exit(code=0 if ok else 1)
    fetch_schemas()


@schema_app.command(
    name="codegen", help="Generate Pydantic models from vendored Nix schemas."
)
def schema_codegen() -> None:
    """Run the Pydantic model code generator."""
    codegen_main()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def main() -> None:
    """Run the CLI with a stable program name for help output."""
    app(prog_name="nixcfg")


if __name__ == "__main__":
    main()
