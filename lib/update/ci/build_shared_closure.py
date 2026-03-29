"""Build derivations needed by multiple Nix flake outputs.

Evaluates each target with ``nix build --dry-run``, collects every
derivation that needs building, then realises them all.  Subsequent
parallel builds of individual targets will hit the Nix store cache.

When ``--profile-output`` is provided, build activity is captured via
``--log-format internal-json`` and written as a JSON report for CI triage.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from pathlib import Path
from typing import Annotated, Literal

import typer

from lib.nix.commands.base import (
    CommandResult,
    NixCommandError,
    ProcessDone,
    run_nix,
    stream_process,
)
from lib.nix.commands.build import nix_build_dry_run
from lib.nix.commands.store import nix_store_realise
from lib.update.ci._cli import (
    make_dual_typer_apps,
    make_main,
    register_dual_entrypoint,
)
from lib.update.ci._profiling import (
    AggregatedProfileRow as _AggregatedProfileRow,
)
from lib.update.ci._profiling import (
    BuildProfileEvent as _BuildProfileEvent,
)
from lib.update.ci._profiling import (
    BuildProfiler,
    aggregate_profile_events,
    emit_stream_line,
    log_profile_summary,
    parse_internal_json_line,
    write_profile_report,
)
from lib.update.ci._time import format_duration

log = logging.getLogger(__name__)

# Timeout for nix evaluation (3 hours — large configs can take significantly longer)
EVAL_TIMEOUT = 10800.0

# Timeout for nix-store builds (6 hours — Zed can take up to 1 hour from source)
BUILD_TIMEOUT = 21600.0

# Maximum derivations per batch to avoid ARG_MAX limits
MAX_BATCH_SIZE = 500

DEFAULT_DERIVATION_SET_MODE: Literal["union", "intersection"] = "union"

_format_duration = format_duration

_DRY_RUN_DRV_RE = re.compile(r"(/nix/store/[a-z0-9]{32}-[^\s]+\.drv)\b")

_aggregate_profile_events = aggregate_profile_events
_emit_stream_line = emit_stream_line
_parse_internal_json_line = parse_internal_json_line
_write_profile_report = write_profile_report
BuildProfileEvent = _BuildProfileEvent
AggregatedProfileRow = _AggregatedProfileRow


def _log_profile_summary(events: list[AggregatedProfileRow]) -> None:
    """Log profile summaries through this module's logger for test patching."""
    log_profile_summary(events, logger=log)


def _python_log_level(verbosity: int) -> int:
    """Map CLI verbosity count to Python logging level."""
    return logging.DEBUG if verbosity > 0 else logging.INFO


def _nix_verbosity_args(nix_verbosity: int) -> list[str]:
    """Return nix/nix-store verbosity arguments for *nix_verbosity* level."""
    if nix_verbosity <= 0:
        return []
    return ["-" + ("v" * nix_verbosity)]


def _nix_verbosity_from_cli(verbosity: int) -> int:
    """Map CLI verbosity to nix verbosity level.

    ``-v`` enables Python DEBUG logs only; ``-vv`` starts forwarding ``-v`` to Nix.
    """
    return max(0, verbosity - 1)


def _parse_dry_run_derivations(combined_output: str) -> set[str]:
    """Extract derivation paths from ``nix build --dry-run`` output."""
    drvs: set[str] = set()
    in_build_section = False
    for line in combined_output.splitlines():
        if "will be built:" in line:
            in_build_section = True
            continue

        if in_build_section:
            match = _DRY_RUN_DRV_RE.search(line)
            if match:
                drvs.add(match.group(1))
            elif line.strip() == "" or not line.startswith(" "):
                in_build_section = False

    return drvs


async def _stream_nix_build_dry_run(
    installable: str,
    *,
    nix_verbosity: int,
) -> set[str]:
    """Run ``nix build --dry-run`` while streaming output to the terminal."""
    args = [
        "nix",
        "build",
        installable,
        "--dry-run",
        "--impure",
        *_nix_verbosity_args(nix_verbosity),
    ]
    result: CommandResult | None = None

    try:
        async for event in stream_process(args, command_timeout=EVAL_TIMEOUT):
            if isinstance(event, ProcessDone):
                result = event.result
                continue
            _emit_stream_line(event)
    except TimeoutError as exc:
        raise NixCommandError(
            CommandResult(args=args, returncode=-1, stdout="", stderr=""),
            message=f"command timed out after {EVAL_TIMEOUT}s",
        ) from exc

    if result is None:
        msg = "nix build --dry-run stream ended without a terminal result"
        raise RuntimeError(msg)
    if result.returncode != 0:
        raise NixCommandError(result)

    return _parse_dry_run_derivations(result.stdout + result.stderr)


async def _realise_batch_with_profiling(
    batch: list[str],
    *,
    profiler: BuildProfiler,
    nix_verbosity: int = 0,
) -> CommandResult:
    """Realise one batch while streaming/parsing internal-json build events."""
    result: CommandResult | None = None
    args = [
        "nix-store",
        *_nix_verbosity_args(nix_verbosity),
        "--realise",
        "--log-format",
        "internal-json",
        *batch,
    ]
    async for event in stream_process(args, command_timeout=BUILD_TIMEOUT):
        if isinstance(event, ProcessDone):
            result = event.result
            continue

        _emit_stream_line(event)
        if event.stream == "stderr":
            profiler.ingest_line(event.text, now=time.monotonic())

    if result is None:
        msg = "nix-store stream ended without a terminal result"
        raise RuntimeError(msg)
    return result


async def _eval_one(ref: str, *, nix_verbosity: int = 0) -> set[str]:
    """Evaluate a single flake ref and return its derivations."""
    log.info("Evaluating %s ...", ref)
    start = time.monotonic()
    if nix_verbosity > 0:
        drvs = await _stream_nix_build_dry_run(ref, nix_verbosity=nix_verbosity)
    else:
        drvs = await nix_build_dry_run(ref, timeout=EVAL_TIMEOUT)
    elapsed = time.monotonic() - start
    log.info("  %s: %d derivation(s) in %s", ref, len(drvs), _format_duration(elapsed))
    return drvs


def _combine_derivation_sets(
    results: list[set[str]],
    *,
    mode: Literal["union", "intersection"],
) -> set[str]:
    """Combine derivation sets according to *mode*."""
    if not results:
        return set()

    if mode == "intersection":
        shared_drvs = set(results[0])
        for drvs in results[1:]:
            shared_drvs &= drvs
        return shared_drvs

    all_drvs: set[str] = set()
    for drvs in results:
        all_drvs |= drvs
    return all_drvs


async def _collect_derivations(
    flake_refs: list[str],
    *,
    mode: Literal["union", "intersection"] = DEFAULT_DERIVATION_SET_MODE,
    nix_verbosity: int = 0,
) -> set[str]:
    """Evaluate all refs concurrently and combine derivations per *mode*."""
    results = await asyncio.gather(
        *(_eval_one(ref, nix_verbosity=nix_verbosity) for ref in flake_refs)
    )
    return _combine_derivation_sets(results, mode=mode)


async def _build_derivations(
    derivations: set[str],
    *,
    dry_run: bool = False,
    nix_verbosity: int = 0,
    profiler: BuildProfiler | None = None,
) -> bool:
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
            if profiler is None:
                if nix_verbosity > 0:
                    result = await run_nix(
                        [
                            "nix-store",
                            *_nix_verbosity_args(nix_verbosity),
                            "--realise",
                            *batch,
                        ],
                        check=False,
                        capture=False,
                        command_timeout=BUILD_TIMEOUT,
                    )
                else:
                    # capture=False inherits stdout/stderr for CI visibility.
                    # check=False so we can continue with remaining batches on failure.
                    result = await nix_store_realise(
                        batch,
                        check=False,
                        capture=False,
                        timeout=BUILD_TIMEOUT,
                    )
            else:
                result = await _realise_batch_with_profiling(
                    batch,
                    profiler=profiler,
                    nix_verbosity=nix_verbosity,
                )
        except NixCommandError, TimeoutError:
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
    exclude_refs: list[str] | None = None,
    dry_run: bool = False,
    mode: Literal["union", "intersection"] = DEFAULT_DERIVATION_SET_MODE,
    verbosity: int = 0,
    profile_output: Path | None = None,
) -> int:
    nix_verbosity = _nix_verbosity_from_cli(verbosity)
    all_drvs = await _collect_derivations(
        flake_refs,
        mode=mode,
        nix_verbosity=nix_verbosity,
    )
    normalized_exclude_refs = [ref for ref in (exclude_refs or []) if ref]
    if normalized_exclude_refs:
        excluded_drvs = await _collect_derivations(
            normalized_exclude_refs,
            mode="union",
            nix_verbosity=nix_verbosity,
        )
        before_exclusion = len(all_drvs)
        all_drvs -= excluded_drvs
        removed = before_exclusion - len(all_drvs)
        log.info(
            "Excluded %d derivation(s) from %d exclude ref(s)",
            removed,
            len(normalized_exclude_refs),
        )
    log.info("Collected %d derivation(s) using %s mode", len(all_drvs), mode)
    profiler = BuildProfiler() if profile_output is not None else None
    ok = await _build_derivations(
        all_drvs,
        dry_run=dry_run,
        nix_verbosity=nix_verbosity,
        profiler=profiler,
    )
    if profiler is not None and profile_output is not None:
        profile_path = profile_output
        profiler.finalize(now=time.monotonic())
        aggregated = _aggregate_profile_events(profiler.events)
        _log_profile_summary(aggregated)
        _write_profile_report(
            output_path=profile_path,
            flake_refs=flake_refs,
            derivation_count=len(all_drvs),
            profiler=profiler,
        )
        log.info("Wrote profile report to %s", profile_path)
    return 0 if ok else 1


def run(
    *,
    flake_refs: list[str],
    exclude_refs: list[str] | None = None,
    dry_run: bool = False,
    mode: Literal["union", "intersection"] = DEFAULT_DERIVATION_SET_MODE,
    profile_output: str | Path | None = None,
    verbosity: int = 0,
) -> int:
    """Run the shared-closure workflow with explicit options."""
    logging.basicConfig(
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
        level=_python_log_level(verbosity),
    )
    profile_path = None if profile_output is None else Path(profile_output)

    return asyncio.run(
        _async_main(
            flake_refs=flake_refs,
            exclude_refs=exclude_refs,
            dry_run=dry_run,
            mode=mode,
            verbosity=verbosity,
            profile_output=profile_path,
        )
    )


_DUAL_APPS = make_dual_typer_apps(
    help_text="Build derivations needed by multiple flake outputs.",
    no_args_is_help=False,
)
app = _DUAL_APPS.app


@register_dual_entrypoint(_DUAL_APPS)
def cli(
    flake_refs: Annotated[
        list[str],
        typer.Argument(help="Flake references to build."),
    ],
    exclude_refs: Annotated[
        list[str] | None,
        typer.Option(
            "-x",
            "--exclude-ref",
            help=("Exclude derivations reachable from this flake ref. Repeatable."),
        ),
    ] = None,
    *,
    dry_run: Annotated[
        bool,
        typer.Option("-n", "--dry-run", help="Show what would be built."),
    ] = False,
    mode: Annotated[
        Literal["union", "intersection"],
        typer.Option(
            "-m",
            "--mode",
            help="Combine derivations across refs via union or intersection.",
        ),
    ] = DEFAULT_DERIVATION_SET_MODE,
    profile_output: Annotated[
        Path | None,
        typer.Option(
            "-p",
            "--profile-output",
            help="Write a JSON timing report from Nix internal-json logs.",
        ),
    ] = None,
    verbosity: Annotated[
        int,
        typer.Option(
            "-v",
            "--verbose",
            count=True,
            help="Increase verbosity (-v for debug, -vv for Nix logs).",
        ),
    ] = 0,
) -> None:
    """Run the shared-closure build command."""
    raise typer.Exit(
        code=run(
            flake_refs=flake_refs,
            exclude_refs=exclude_refs,
            dry_run=dry_run,
            mode=mode,
            profile_output=profile_output,
            verbosity=verbosity,
        )
    )


main = make_main(_DUAL_APPS.standalone_app, prog_name="cache closure")


if __name__ == "__main__":
    raise SystemExit(main())
