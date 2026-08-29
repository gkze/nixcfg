"""CLI wrapper for shared crate2nix regeneration logic."""

import pathlib  # noqa: TC003
from typing import Annotated

import typer

from lib.cargo_nix_normalizer_cli import normalize_file
from lib.cli import make_main, make_typer_app
from lib.update import crate2nix

app = make_typer_app(
    help_text="Check or refresh checked-in crate2nix artifacts.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def _cli(
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
    raise typer.Exit(code=crate2nix.run(packages=tuple(package or ()), write=write))


@app.command(name="normalize")
def _normalize_cli(
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
    target_spec = crate2nix.TARGETS.get(target)
    if target_spec is None:
        known = ", ".join(sorted(crate2nix.TARGETS))
        msg = f"Unknown crate2nix target {target!r}; expected one of: {known}"
        raise typer.BadParameter(msg, param_hint="TARGET")

    cargo_nix = target_spec.cargo_nix if path is None else path.expanduser()
    if not cargo_nix.is_absolute():
        cargo_nix = crate2nix.REPO_ROOT / cargo_nix
    raise typer.Exit(
        code=normalize_file(
            normalize=crate2nix.load_normalizer(target_spec.normalizer_path),
            path=cargo_nix,
        )
    )


main = make_main(app, prog_name="pipeline crate2nix")


if __name__ == "__main__":  # pragma: no cover -- CLI entrypoint
    raise SystemExit(main())


__all__ = ["app", "main"]
