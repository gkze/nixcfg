#!/usr/bin/env python
"""Unified CLI for nixcfg project tasks."""

from __future__ import annotations

import asyncio
import sys
from typing import Annotated, Literal, cast

import typer

_is_tty = sys.stdout.isatty()

TTYMode = Literal["auto", "force", "off", "full"]
_TTY_MODES: tuple[str, ...] = ("auto", "force", "off", "full")

app = typer.Typer(
    name="nixcfg",
    help="Unified CLI for nixcfg project tasks.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode="rich" if _is_tty else None,
)

# ---------------------------------------------------------------------------
# update — native Typer command that builds UpdateOptions directly
# ---------------------------------------------------------------------------


@app.command(name="update")
def update_cmd(  # noqa: PLR0913
    source: Annotated[
        str | None,
        typer.Argument(help="Source or flake input to update (default: all)"),
    ] = None,
    *,
    list_targets: Annotated[
        bool,
        typer.Option("--list", "-l", help="List available sources and inputs"),
    ] = False,
    no_refs: Annotated[
        bool,
        typer.Option("--no-refs", "-R", help="Skip flake input ref updates"),
    ] = False,
    no_sources: Annotated[
        bool,
        typer.Option("--no-sources", "-S", help="Skip sources.json hash updates"),
    ] = False,
    no_input: Annotated[
        bool,
        typer.Option(
            "--no-input", "-I", help="Skip flake input lock refresh before hashing"
        ),
    ] = False,
    check: Annotated[
        bool,
        typer.Option("--check", "-c", help="Dry run: check without applying"),
    ] = False,
    validate: Annotated[
        bool,
        typer.Option("--validate", "-v", help="Validate sources.json and exit"),
    ] = False,
    schema: Annotated[
        bool,
        typer.Option("--schema", "-s", help="Output JSON schema for sources.json"),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option("--json", "-j", help="Output results as JSON"),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("--verbose", "-V", help="Stream build log lines to stdout"),
    ] = False,
    quiet: Annotated[
        bool,
        typer.Option("--quiet", "-q", help="Suppress progress output"),
    ] = False,
    tty: Annotated[
        str,
        typer.Option(
            "--tty",
            "-t",
            help="TTY rendering: auto|force|off|full",
        ),
    ] = "auto",
    zellij_guard: Annotated[
        bool | None,
        typer.Option(help="Disable live rendering under Zellij"),
    ] = None,
    native_only: Annotated[
        bool,
        typer.Option(
            "--native-only",
            "-n",
            help="Only compute hashes for current platform (CI). Implies --no-refs.",
        ),
    ] = False,
    http_timeout: Annotated[
        int | None, typer.Option(help="HTTP timeout seconds")
    ] = None,
    subprocess_timeout: Annotated[
        int | None, typer.Option(help="Subprocess timeout seconds")
    ] = None,
    max_nix_builds: Annotated[
        int | None, typer.Option(help="Max concurrent nix build processes")
    ] = None,
    log_tail_lines: Annotated[int | None, typer.Option(help="Log tail lines")] = None,
    render_interval: Annotated[
        float | None, typer.Option(help="TTY render interval seconds")
    ] = None,
    user_agent: Annotated[str | None, typer.Option(help="HTTP user agent")] = None,
    retries: Annotated[int | None, typer.Option(help="HTTP retries")] = None,
    retry_backoff: Annotated[
        float | None, typer.Option(help="HTTP retry backoff seconds")
    ] = None,
    fake_hash: Annotated[str | None, typer.Option(help="Fake hash placeholder")] = None,
    deno_platforms: Annotated[
        str | None, typer.Option(help="Comma-separated Deno platforms")
    ] = None,
) -> None:
    """Update source versions/hashes and flake input refs."""
    from lib.update.cli import UpdateOptions, check_required_tools, run_updates
    from lib.update.refs import get_flake_inputs_with_refs

    if tty not in _TTY_MODES:
        msg = f"Invalid --tty value: {tty!r}. Expected one of: {', '.join(_TTY_MODES)}"
        raise typer.BadParameter(msg)
    tty_mode = cast("TTYMode", tty)

    opts = UpdateOptions(
        source=source,
        list_targets=list_targets,
        no_refs=no_refs,
        no_sources=no_sources,
        no_input=no_input,
        check=check,
        validate=validate,
        schema=schema,
        json=json_output,
        verbose=verbose,
        quiet=quiet,
        tty=tty_mode,
        zellij_guard=zellij_guard,
        native_only=native_only,
        http_timeout=http_timeout,
        subprocess_timeout=subprocess_timeout,
        max_nix_builds=max_nix_builds,
        log_tail_lines=log_tail_lines,
        render_interval=render_interval,
        user_agent=user_agent,
        retries=retries,
        retry_backoff=retry_backoff,
        fake_hash=fake_hash,
        deno_platforms=deno_platforms,
    )

    if not (opts.list_targets or opts.schema or opts.validate):
        needs_flake_edit = not opts.no_refs and not opts.native_only
        if needs_flake_edit and opts.source:
            ref_names = {i.name for i in get_flake_inputs_with_refs()}
            needs_flake_edit = opts.source in ref_names

        missing = check_required_tools(
            include_flake_edit=needs_flake_edit,
            source=opts.source,
            needs_sources=not opts.no_sources,
        )
        if missing:
            sys.stderr.write(f"Error: Required tools not found: {', '.join(missing)}\n")
            sys.stderr.write("Please install them and ensure they are in your PATH.\n")
            raise typer.Exit(code=1)

    raise typer.Exit(code=asyncio.run(run_updates(opts)))


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

_FORWARDED_ARGS_CONTEXT_SETTINGS = {
    "allow_extra_args": True,
    "allow_interspersed_args": False,
    "ignore_unknown_options": True,
}


def _make_ci_callback(
    cmd_name: str,
) -> typer.models.CommandFunctionType:
    """Create a Typer callback that dispatches to a CI_COMMANDS entry."""

    def _callback(ctx: typer.Context) -> None:
        from lib.update.ci import CI_COMMANDS

        raise typer.Exit(code=CI_COMMANDS[cmd_name].func(ctx.args))

    # Typer uses the function name for internal dedup — give each a unique name.
    _callback.__name__ = f"ci_{cmd_name.replace('-', '_')}"
    return _callback  # type: ignore[return-value]


def _register_ci_commands() -> None:
    """Register all CI subcommands from the CI_COMMANDS dict."""
    from lib.update.ci import CI_COMMANDS

    # generate-pr-body stays as a native Typer command below.
    skip = {"generate-pr-body"}
    for name, cmd in CI_COMMANDS.items():
        if name in skip:
            continue
        ci_app.command(
            name=name,
            context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
            help=cmd.help,
        )(_make_ci_callback(name))


_register_ci_commands()


# generate-pr-body: native Typer command with typed parameters
@ci_app.command(
    name="generate-pr-body",
    help="Generate pull request body markdown for update runs.",
)
def ci_generate_pr_body(  # noqa: PLR0913
    output: Annotated[str, typer.Option(help="Path to write PR body")],
    workflow_url: Annotated[str, typer.Option(help="Workflow run URL")],
    server_url: Annotated[str, typer.Option(help="GitHub server URL")],
    repository: Annotated[str, typer.Option(help="owner/repo")],
    base_ref: Annotated[str, typer.Option(help="Base branch for compare")],
    compare_head: Annotated[
        str, typer.Option(help="Head branch used in compare links")
    ] = "update_flake_lock_action",
) -> None:
    """Render update summary markdown consumed by create-pull-request."""
    from lib.update.ci.workflow_steps import generate_pr_body

    raise typer.Exit(
        code=generate_pr_body(
            output=output,
            workflow_url=workflow_url,
            server_url=server_url,
            repository=repository,
            base_ref=base_ref,
            compare_head=compare_head,
        )
    )


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
    check: Annotated[
        bool,
        typer.Option(
            "--check", help="Verify vendored schemas match the pinned commit."
        ),
    ] = False,
) -> None:
    """Download or verify vendored Nix JSON schemas."""
    from lib.nix.schemas._fetch import check as schema_check
    from lib.nix.schemas._fetch import fetch

    if check:
        ok = schema_check()
        raise typer.Exit(code=0 if ok else 1)
    fetch()


@schema_app.command(
    name="codegen", help="Generate Pydantic models from vendored Nix schemas."
)
def schema_codegen() -> None:
    """Run the Pydantic model code generator."""
    from lib.nix.schemas._codegen import main as codegen_main

    codegen_main()


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
