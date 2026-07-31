"""CLI wrapper for shared crate2nix regeneration logic."""

from __future__ import annotations

import pathlib  # noqa: TC003
from typing import Annotated

import typer

from lib.cargo_nix_normalizer_cli import normalize_file
from lib.update.ci._cli import make_main, make_typer_app
from lib.update.crate2nix import (
    REPO_ROOT,
    TARGETS,
    Crate2NixTarget,
    RefreshResult,
    _current_platform,
    _load_normalizer,
    _normalize_json_text,
    _normalize_trailing_newline,
    _refresh_target,
    _resolve_targets,
    _stabilize_generated_command_comment,
    _stabilize_generated_root_src_paths,
    _target_has_changes,
    _write_target,
    crate2nix_artifact_updates,
    run,
    stream_crate2nix_artifact_updates,
)

app = make_typer_app(
    help_text="Check or refresh checked-in crate2nix artifacts.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def cli(
    ctx: typer.Context,
    *,
    package: Annotated[
        list[str] | None,
        typer.Option(
            "--package",
            "-p",
            help="Limit the run to one or more crate2nix targets.",
        ),
    ] = None,
    write: Annotated[
        bool,
        typer.Option(
            "--write",
            "-w",
            help="Write refreshed Cargo.nix and crate-hashes.json files back to the repo.",
        ),
    ] = False,
) -> None:
    """Check or refresh checked-in crate2nix artifacts."""
    if ctx.invoked_subcommand is not None:
        return
    raise typer.Exit(code=run(packages=tuple(package or ()), write=write))


@app.command(name="normalize")
def normalize_cli(
    target: Annotated[
        str,
        typer.Argument(help="Registered crate2nix target name."),
    ],
    path: Annotated[
        pathlib.Path | None,
        typer.Argument(
            help="Cargo.nix path; defaults to the target's checked-in file."
        ),
    ] = None,
) -> None:
    """Normalize one generated Cargo.nix through its registered callback."""
    target_spec = TARGETS.get(target)
    if target_spec is None:
        known = ", ".join(sorted(TARGETS))
        msg = f"Unknown crate2nix target {target!r}; expected one of: {known}"
        raise typer.BadParameter(msg, param_hint="TARGET")

    cargo_nix = target_spec.cargo_nix if path is None else path.expanduser()
    if not cargo_nix.is_absolute():
        cargo_nix = REPO_ROOT / cargo_nix
    raise typer.Exit(
        code=normalize_file(
            normalize=_load_normalizer(target_spec.normalizer_path),
            path=cargo_nix,
        )
    )


main = make_main(app, prog_name="pipeline crate2nix")


if __name__ == "__main__":  # pragma: no cover -- CLI entrypoint
    raise SystemExit(main())


__all__ = [
    "REPO_ROOT",
    "TARGETS",
    "Crate2NixTarget",
    "RefreshResult",
    "_current_platform",
    "_load_normalizer",
    "_normalize_json_text",
    "_normalize_trailing_newline",
    "_refresh_target",
    "_resolve_targets",
    "_stabilize_generated_command_comment",
    "_stabilize_generated_root_src_paths",
    "_target_has_changes",
    "_write_target",
    "app",
    "cli",
    "crate2nix_artifact_updates",
    "main",
    "normalize_cli",
    "run",
    "stream_crate2nix_artifact_updates",
]
