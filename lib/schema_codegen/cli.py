"""CLI commands for schema fetching, generation, and verification."""

import pathlib  # noqa: TC003
import sys
from typing import Annotated

import httpx
import typer

from lib.cli import HELP_CONTEXT_SETTINGS
from lib.diagnostics import redact_urls
from lib.nix.schemas import check as schema_check
from lib.nix.schemas import codegen_main
from lib.nix.schemas import fetch as fetch_schemas
from lib.schema_codegen import (
    default_config_path,
    generate_schema_codegen_target,
    list_schema_codegen_targets,
    verify_schema_codegen_target,
    write_codegen_lockfile,
)
from lib.update.paths import get_repo_root

_is_tty = sys.stdout.isatty()

app = typer.Typer(
    name="schema",
    help="Nix JSON schema utilities (fetch, codegen).",
    no_args_is_help=True,
    rich_markup_mode="rich" if _is_tty else None,
    context_settings=dict(HELP_CONTEXT_SETTINGS),
)

_SCHEMA_COMMAND_ERRORS = (
    FileNotFoundError,
    RuntimeError,
    TypeError,
    ValueError,
    httpx.HTTPError,
)


def _schema_progress(message: str) -> None:
    """Render schema command progress updates without exposing URL secrets."""
    typer.echo(redact_urls(message), err=True)


def _schema_error(prefix: str, error: BaseException) -> None:
    """Render an expected schema command failure through the redaction boundary."""
    typer.echo(f"{prefix}: {redact_urls(str(error))}", err=True)


def _display_schema_path(path: pathlib.Path) -> str:
    """Return a readable display path for schema-related outputs."""
    try:
        return str(path.relative_to(get_repo_root()))
    except ValueError:
        return str(path)


def _resolve_schema_config_path(config: pathlib.Path | None) -> pathlib.Path:
    """Resolve the default schema codegen config at command runtime."""
    return default_config_path() if config is None else config


@app.command(
    name="targets",
    help="List declarative schema codegen targets.",
)
def schema_targets(
    *,
    config: Annotated[
        pathlib.Path | None,
        typer.Option(
            "-c",
            "--config",
            help="Path to the schema codegen config file.",
        ),
    ] = None,
) -> None:
    """List available schema generation targets from the declarative config."""
    resolved_config = _resolve_schema_config_path(config)
    try:
        for target in list_schema_codegen_targets(config_path=resolved_config):
            typer.echo(f"{target.name}\t{_display_schema_path(target.output)}")
    except _SCHEMA_COMMAND_ERRORS as exc:
        _schema_error("Schema target listing failed", exc)
        raise typer.Exit(code=1) from exc


@app.command(
    name="generate",
    help="Generate models for one declarative schema codegen target.",
)
def schema_generate(
    target: Annotated[
        str,
        typer.Argument(help="Name of the configured generation target."),
    ],
    *,
    config: Annotated[
        pathlib.Path | None,
        typer.Option(
            "-c",
            "--config",
            help="Path to the schema codegen config file.",
        ),
    ] = None,
) -> None:
    """Run the declarative schema codegen pipeline for one target."""
    resolved_config = _resolve_schema_config_path(config)
    try:
        output_path = generate_schema_codegen_target(
            config_path=resolved_config,
            progress=_schema_progress,
            target_name=target,
        )
    except _SCHEMA_COMMAND_ERRORS as exc:
        _schema_error("Schema generation failed", exc)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Generated {_display_schema_path(output_path)}")


@app.command(
    name="lock",
    help="Materialize a canonical codegen lockfile from a v1 manifest.",
)
def schema_lock(
    manifest: Annotated[
        pathlib.Path,
        typer.Argument(help="Path to the canonical codegen manifest (YAML or JSON)."),
    ],
    *,
    output: Annotated[
        pathlib.Path | None,
        typer.Option(
            "-o",
            "--output",
            help="Path to write the lockfile. Defaults to codegen.lock.json next to the manifest.",
        ),
    ] = None,
    include_metadata: Annotated[
        bool,
        typer.Option(
            "-m",
            "--include-metadata",
            help="Include non-semantic timestamps and provenance metadata.",
        ),
    ] = False,
) -> None:
    """Write a deterministic lockfile for the canonical codegen manifest schema."""
    try:
        output_path = write_codegen_lockfile(
            manifest_path=manifest,
            lockfile_path=output,
            include_metadata=include_metadata,
            progress=_schema_progress,
        )
    except _SCHEMA_COMMAND_ERRORS as exc:
        _schema_error("Schema lockfile generation failed", exc)
        raise typer.Exit(code=1) from exc
    typer.echo(f"Generated {_display_schema_path(output_path)}")


@app.command(
    name="codegen",
    help="Generate Pydantic models from vendored Nix schemas.",
)
def schema_codegen() -> None:
    """Run the Pydantic model code generator."""
    try:
        codegen_main(progress=_schema_progress)
    except _SCHEMA_COMMAND_ERRORS as exc:
        _schema_error("Schema codegen failed", exc)
        raise typer.Exit(code=1) from exc


@app.command(
    name="verify",
    help="Verify both generated Python model families are fresh.",
)
def schema_verify() -> None:
    """Check generated models without modifying the working tree."""
    resolved_config = _resolve_schema_config_path(None)
    try:
        all_fresh = True
        for target in list_schema_codegen_targets(config_path=resolved_config):
            all_fresh = (
                verify_schema_codegen_target(
                    config_path=resolved_config,
                    progress=_schema_progress,
                    target_name=target.name,
                )
                and all_fresh
            )
    except _SCHEMA_COMMAND_ERRORS as exc:
        _schema_error("Generated schema verification failed", exc)
        raise typer.Exit(code=1) from exc

    if not all_fresh:
        typer.echo("Generated schema models are stale.", err=True)
        raise typer.Exit(code=1)
    typer.echo("Generated schema models are fresh.")


@app.command(
    name="fetch",
    help="Fetch Nix JSON schemas from the NixOS/nix repo.",
)
def schema_fetch(
    *,
    check: Annotated[
        bool,
        typer.Option(
            "-c",
            "--check",
            help="Verify vendored schemas against their recorded checksums.",
        ),
    ] = False,
) -> None:
    """Download or verify vendored Nix JSON schemas."""
    if check:
        ok = schema_check()
        raise typer.Exit(code=0 if ok else 1)

    try:
        fetch_schemas(progress=_schema_progress)
    except _SCHEMA_COMMAND_ERRORS as exc:
        _schema_error("Schema fetch failed", exc)
        raise typer.Exit(code=1) from exc


__all__ = ["app"]
