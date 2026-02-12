"""Unified CLI for nixcfg project tasks."""

from __future__ import annotations

import sys
from typing import Annotated

import typer

_is_tty = sys.stdout.isatty()

app = typer.Typer(
    name="project",
    help="Unified CLI for nixcfg project tasks.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
    rich_markup_mode="rich" if _is_tty else None,
)

# ---------------------------------------------------------------------------
# update — delegates to the existing argparse-based update CLI
# ---------------------------------------------------------------------------

_update_help = (
    "Update source versions/hashes and flake input refs. "
    "All arguments after '--' are forwarded to the update CLI."
)


@app.command(
    name="update",
    help=_update_help,
    context_settings={"allow_extra_args": True, "allow_interspersed_args": False},
)
def update_cmd(ctx: typer.Context) -> None:
    """Run the update workflow (delegates to lib.update.cli)."""
    from lib.update.cli import main as update_main

    # Patch sys.argv so argparse inside update_main sees the forwarded args.
    original_argv = sys.argv
    sys.argv = ["project update", *ctx.args]
    try:
        update_main()
    except SystemExit as exc:
        raise typer.Exit(code=exc.code if isinstance(exc.code, int) else 0) from None
    finally:
        sys.argv = original_argv


# ---------------------------------------------------------------------------
# ci — subgroup for CI helper commands
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
    # CI subcommands forward args into argparse-based helpers. These helpers
    # define their own option parsing, so Typer/Click must not reject unknown
    # options like --output.
    "ignore_unknown_options": True,
}


@ci_app.command(
    name="build-shared-closure",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Build the union of derivations needed by multiple flake outputs.",
)
def ci_build_shared_closure(ctx: typer.Context) -> None:
    """Evaluate targets with nix build --dry-run, then realise the union."""
    from lib.update.ci.build_shared_closure import main as _main

    raise typer.Exit(code=_main(ctx.args))


@ci_app.command(
    name="dedup-cargo-lock",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Convert git-sourced gitoxide crates to crates.io and remove duplicates.",
)
def ci_dedup_cargo_lock(ctx: typer.Context) -> None:
    """Deduplicate Cargo.lock for Nix cargo vendoring."""
    from lib.update.ci.dedup_cargo_lock import main as _main

    raise typer.Exit(code=_main(ctx.args))


@ci_app.command(
    name="flake-lock-diff",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Generate a human-readable diff of flake.lock changes.",
)
def ci_flake_lock_diff(ctx: typer.Context) -> None:
    """Compare two flake.lock files and print the differences."""
    from lib.update.ci.flake_lock_diff import main as _main

    raise typer.Exit(code=_main(ctx.args))


@ci_app.command(
    name="merge-sources",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Merge per-package sources.json trees from multiple CI platform artifacts.",
)
def ci_merge_sources(ctx: typer.Context) -> None:
    """Merge per-platform source artifacts into the repo."""
    from lib.update.ci.merge_sources import main as _main

    raise typer.Exit(code=_main(ctx.args))


@ci_app.command(
    name="sources-diff",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Generate a Graphtage or unified diff for source entry JSON changes.",
)
def ci_sources_diff(ctx: typer.Context) -> None:
    """Diff old vs new sources.json files."""
    from lib.update.ci.sources_json_diff import main as _main

    raise typer.Exit(code=_main(ctx.args))


def _run_ci_workflow_step(step: str, ctx: typer.Context) -> None:
    from lib.update.ci.workflow_steps import main as _main

    raise typer.Exit(code=_main([step, *ctx.args]))


@ci_app.command(
    name="nix-flake-update",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Run nix flake update.",
)
def ci_nix_flake_update(ctx: typer.Context) -> None:
    """Run `nix flake update` for CI lock refresh."""
    _run_ci_workflow_step("nix-flake-update", ctx)


@ci_app.command(
    name="free-disk-space",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Free disk space on macOS runners.",
)
def ci_free_disk_space(ctx: typer.Context) -> None:
    """Reclaim disk space before expensive macOS builds."""
    _run_ci_workflow_step("free-disk-space", ctx)


@ci_app.command(
    name="install-darwin-tools",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Install macOS-specific tooling for CI builds.",
)
def ci_install_darwin_tools(ctx: typer.Context) -> None:
    """Install macFUSE and 1Password CLI on macOS runners."""
    _run_ci_workflow_step("install-darwin-tools", ctx)


@ci_app.command(
    name="prefetch-flake-inputs",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Pre-fetch flake inputs for build jobs.",
)
def ci_prefetch_flake_inputs(ctx: typer.Context) -> None:
    """Warm flake input downloads in CI."""
    _run_ci_workflow_step("prefetch-flake-inputs", ctx)


@ci_app.command(
    name="build-darwin-config",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Build one darwin configuration by host name.",
)
def ci_build_darwin_config(ctx: typer.Context) -> None:
    """Build a single matrix-selected Darwin configuration."""
    _run_ci_workflow_step("build-darwin-config", ctx)


@ci_app.command(
    name="smoke-check-update-app",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="Smoke-check that the update app evaluates.",
)
def ci_smoke_check_update_app(ctx: typer.Context) -> None:
    """Validate update app derivation evaluation in Linux CI."""
    _run_ci_workflow_step("smoke-check-update-app", ctx)


@ci_app.command(
    name="list-update-targets",
    context_settings=_FORWARDED_ARGS_CONTEXT_SETTINGS,
    help="List update targets discovered by the update app.",
)
def ci_list_update_targets(ctx: typer.Context) -> None:
    """Run update target listing for sanity checks."""
    _run_ci_workflow_step("list-update-targets", ctx)


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
