"""Build the union of derivations needed by multiple Nix flake outputs.

Evaluates each target with ``nix build --dry-run``, collects every
derivation that needs building, then realises them all.  Subsequent
parallel builds of individual targets will hit the Nix store cache.
"""

import asyncio
import logging
import time
from typing import Annotated

import typer

from lib.nix.commands.base import NixCommandError
from lib.nix.commands.build import nix_build_dry_run
from lib.nix.commands.store import nix_store_realise
from lib.update.ci._cli import make_typer_app, run_main
from lib.update.ci._time import format_duration

log = logging.getLogger(__name__)

# Timeout for nix evaluation (3 hours — large configs can take significantly longer)
EVAL_TIMEOUT = 10800.0

# Timeout for nix-store builds (6 hours — Zed can take up to 1 hour from source)
BUILD_TIMEOUT = 21600.0

# Maximum derivations per batch to avoid ARG_MAX limits
MAX_BATCH_SIZE = 500

_format_duration = format_duration


async def _eval_one(ref: str) -> set[str]:
    """Evaluate a single flake ref and return its derivations."""
    log.info("Evaluating %s ...", ref)
    start = time.monotonic()
    drvs = await nix_build_dry_run(ref, timeout=EVAL_TIMEOUT)
    elapsed = time.monotonic() - start
    log.info("  %s: %d derivation(s) in %s", ref, len(drvs), _format_duration(elapsed))
    return drvs


async def _collect_derivations(flake_refs: list[str]) -> set[str]:
    """Evaluate all flake refs concurrently and return the union of derivations."""
    results = await asyncio.gather(*(_eval_one(ref) for ref in flake_refs))
    all_drvs: set[str] = set()
    for drvs in results:
        all_drvs |= drvs
    return all_drvs


async def _build_derivations(derivations: set[str], *, dry_run: bool = False) -> bool:
    """Build derivations in batches, continuing past failures."""
    if not derivations:
        log.info("Nothing to build.")
        return True

    drv_list = sorted(derivations)
    batches = [
        drv_list[i : i + MAX_BATCH_SIZE]
        for i in range(0, len(drv_list), MAX_BATCH_SIZE)
    ]
    total = len(batches)
    log.info("Building %d derivation(s) in %d batch(es)", len(drv_list), total)

    if dry_run:
        log.info("DRY RUN — skipping actual build")
        return True

    success = True
    overall_start = time.monotonic()

    for idx, batch in enumerate(batches, 1):
        tag = f"[{idx}/{total}] " if total > 1 else ""
        log.info("%sBuilding %d derivation(s) ...", tag, len(batch))

        batch_start = time.monotonic()
        try:
            # capture=False inherits stdout/stderr for CI visibility.
            # check=False so we can continue with remaining batches on failure.
            result = await nix_store_realise(
                batch,
                check=False,
                capture=False,
                timeout=BUILD_TIMEOUT,
            )
        except NixCommandError:
            # run_nix raises on timeout regardless of check=
            log.exception(
                "%sBuild timed out after %s",
                tag,
                _format_duration(BUILD_TIMEOUT),
            )
            success = False
            continue

        elapsed = time.monotonic() - batch_start
        if result.returncode != 0:
            log.error("%sBuild failed (exit code %d)", tag, result.returncode)
            success = False
        else:
            log.info("%sCompleted in %s", tag, _format_duration(elapsed))

    log.info(
        "Build completed in %s",
        _format_duration(time.monotonic() - overall_start),
    )
    return success


async def _async_main(
    *,
    flake_refs: list[str],
    dry_run: bool = False,
) -> int:
    all_drvs = await _collect_derivations(flake_refs)
    ok = await _build_derivations(all_drvs, dry_run=dry_run)
    return 0 if ok else 1


def run(*, flake_refs: list[str], dry_run: bool = False, verbose: bool = False) -> int:
    """Run the shared-closure workflow with explicit options."""
    logging.basicConfig(
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
        level=logging.DEBUG if verbose else logging.INFO,
    )
    return asyncio.run(_async_main(flake_refs=flake_refs, dry_run=dry_run))


app = make_typer_app(
    help_text="Build the union of derivations needed by multiple flake outputs.",
    no_args_is_help=False,
)


_standalone_app = make_typer_app(
    help_text="Build the union of derivations needed by multiple flake outputs.",
    no_args_is_help=False,
)


@_standalone_app.command()
@app.callback(invoke_without_command=True)
def cli(
    flake_refs: Annotated[
        list[str],
        typer.Argument(help="Flake references to build."),
    ],
    *,
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be built."),
    ] = False,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Enable debug logging."),
    ] = False,
) -> None:
    """Run the shared-closure build command."""
    raise typer.Exit(code=run(flake_refs=flake_refs, dry_run=dry_run, verbose=verbose))


def main(argv: list[str] | None = None) -> int:
    """Run the CLI entrypoint."""
    return run_main(_standalone_app, argv=argv, prog_name="build-shared-closure")


if __name__ == "__main__":
    raise SystemExit(main())
