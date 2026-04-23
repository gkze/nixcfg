"""Profile rebuild timings for current nix-darwin and home-manager generations."""

from __future__ import annotations

import asyncio
import logging
import shlex
import time
from dataclasses import dataclass
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
from lib.update.ci._cli import (
    make_dual_typer_apps,
    make_main,
    register_dual_entrypoint,
)
from lib.update.ci._profiling import (
    BuildProfiler,
)
from lib.update.ci._profiling import (
    aggregate_profile_events as _aggregate_profile_events,
)
from lib.update.ci._profiling import (
    emit_stream_line as _emit_stream_line,
)
from lib.update.ci._profiling import (
    log_profile_summary as _log_profile_summary,
)
from lib.update.ci._profiling import (
    write_profile_report as _write_profile_report,
)
from lib.update.ci._time import format_duration

log = logging.getLogger(__name__)

BUILD_TIMEOUT = 21600.0
PUBLIC_NIX_CACHE = "https://cache.nixos.org"
DEFAULT_SYSTEM_PROFILE = "/run/current-system"


@dataclass(frozen=True)
class GenerationTarget:
    """One generation profile target and its top-level derivation."""

    name: Literal["system", "home-manager"]
    profile_path: str
    derivation: str


_format_duration = format_duration


def _python_log_level(verbosity: int) -> int:
    """Map CLI verbosity count to Python logging level."""
    return logging.DEBUG if verbosity > 0 else logging.INFO


def _nix_verbosity_args(nix_verbosity: int) -> list[str]:
    """Return nix verbosity arguments for *nix_verbosity* level."""
    if nix_verbosity <= 0:
        return []
    return ["-" + ("v" * nix_verbosity)]


def _nix_verbosity_from_cli(verbosity: int) -> int:
    """Map CLI verbosity to nix verbosity level.

    ``-v`` enables Python DEBUG logs only; ``-vv`` forwards ``-v`` to Nix.
    """
    return max(0, verbosity - 1)


def _default_home_profile() -> str:
    """Return the canonical Home Manager profile symlink for the current user."""
    return str(Path.home() / ".local/state/nix/profiles/home-manager")


def _path_exists(path: str) -> bool:
    """Return ``True`` when *path* exists (symlinks included)."""
    return Path(path).exists()


async def _query_deriver(profile_path: str) -> str:
    """Resolve the top-level derivation path for *profile_path*."""
    result = await run_nix(
        ["nix-store", "--query", "--deriver", profile_path],
        check=False,
        command_timeout=60.0,
    )
    if result.returncode != 0:
        raise NixCommandError(result)

    deriver = result.stdout.strip()
    if not deriver or deriver == "unknown-deriver":
        msg = f"Could not resolve deriver for profile: {profile_path}"
        raise RuntimeError(msg)
    return deriver


async def _resolve_targets(
    *,
    target: Literal["all", "system", "home-manager"],
    system_profile: str,
    home_profile: str,
) -> list[GenerationTarget]:
    """Resolve generation targets and their derivations from profile paths."""
    wanted_system = target in {"all", "system"}
    wanted_home = target in {"all", "home-manager"}
    optional_home = target == "all"

    candidates: list[tuple[str, str, bool]] = []
    if wanted_system:
        candidates.append(("system", system_profile, False))
    if wanted_home:
        candidates.append(("home-manager", home_profile, optional_home))

    resolved: list[GenerationTarget] = []
    for name, profile_path, optional in candidates:
        if not _path_exists(profile_path):
            message = f"Profile path not found: {profile_path}"
            if optional:
                log.warning("Skipping %s target: %s", name, message)
                continue
            raise RuntimeError(message)

        try:
            derivation = await _query_deriver(profile_path)
        except (NixCommandError, RuntimeError) as exc:
            if optional:
                log.warning("Skipping %s target: %s", name, exc)
                continue
            raise

        kind: Literal["system", "home-manager"] = (
            "system" if name == "system" else "home-manager"
        )

        resolved.append(
            GenerationTarget(
                name=kind,
                profile_path=profile_path,
                derivation=derivation,
            )
        )

    if not resolved:
        msg = "No generation targets resolved."
        raise RuntimeError(msg)
    return resolved


def _build_rebuild_args(
    target: GenerationTarget,
    *,
    nix_verbosity: int,
    public_cache_only: bool,
    substituters: str | None,
    extra_substituters: str | None,
) -> list[str]:
    """Construct ``nix build --rebuild`` arguments for one generation target."""
    args: list[str] = [
        "nix",
        "build",
        *_nix_verbosity_args(nix_verbosity),
        "--rebuild",
        "--no-link",
        "--log-format",
        "internal-json",
    ]

    if public_cache_only:
        args.extend([
            "--option",
            "substituters",
            PUBLIC_NIX_CACHE,
            "--option",
            "extra-substituters",
            "",
        ])
    else:
        if substituters is not None:
            args.extend(["--option", "substituters", substituters])
        if extra_substituters is not None:
            args.extend(["--option", "extra-substituters", extra_substituters])

    args.append(f"{target.derivation}^*")
    return args


async def _profile_target(
    target: GenerationTarget,
    *,
    profiler: BuildProfiler,
    nix_verbosity: int,
    public_cache_only: bool,
    substituters: str | None,
    extra_substituters: str | None,
) -> bool:
    """Profile rebuild/check timing for one target derivation."""
    args = _build_rebuild_args(
        target,
        nix_verbosity=nix_verbosity,
        public_cache_only=public_cache_only,
        substituters=substituters,
        extra_substituters=extra_substituters,
    )
    log.info("Profiling %s generation (%s)", target.name, target.derivation)

    result: CommandResult | None = None
    started = time.monotonic()
    try:
        async for event in stream_process(args, command_timeout=BUILD_TIMEOUT):
            if isinstance(event, ProcessDone):
                result = event.result
                continue

            _emit_stream_line(event)
            if event.stream == "stderr":
                profiler.ingest_line(event.text, now=time.monotonic())
    except TimeoutError:
        log.exception(
            "Profiling timed out for %s after %s",
            target.name,
            _format_duration(BUILD_TIMEOUT),
        )
        return False

    elapsed = time.monotonic() - started
    if result is None:
        log.error("Stream ended without a terminal result for %s", target.name)
        return False
    if result.returncode != 0:
        log.error(
            "%s generation profiling failed (exit code %d) after %s",
            target.name,
            result.returncode,
            _format_duration(elapsed),
        )
        return False

    log.info(
        "%s generation profiling completed in %s",
        target.name,
        _format_duration(elapsed),
    )
    return True


async def _async_main(
    *,
    target: Literal["all", "system", "home-manager"] = "all",
    system_profile: str = DEFAULT_SYSTEM_PROFILE,
    home_profile: str | None = None,
    profile_output: Path = Path("artifacts/current-generation-build-profile.json"),
    public_cache_only: bool = True,
    substituters: str | None = None,
    extra_substituters: str | None = None,
    dry_run: bool = False,
    verbosity: int = 0,
) -> int:
    if public_cache_only and (
        substituters is not None or extra_substituters is not None
    ):
        msg = "--substituters/--extra-substituters cannot be combined with --public-cache-only"
        raise RuntimeError(msg)

    home_profile_path = home_profile or _default_home_profile()
    nix_verbosity = _nix_verbosity_from_cli(verbosity)
    targets = await _resolve_targets(
        target=target,
        system_profile=system_profile,
        home_profile=home_profile_path,
    )
    log.info(
        "Resolved %d generation target(s): %s",
        len(targets),
        ", ".join(f"{item.name}={item.derivation}" for item in targets),
    )

    if dry_run:
        for item in targets:
            cmd = _build_rebuild_args(
                item,
                nix_verbosity=nix_verbosity,
                public_cache_only=public_cache_only,
                substituters=substituters,
                extra_substituters=extra_substituters,
            )
            log.info("DRY RUN: %s", shlex.join(cmd))
        return 0

    start = time.monotonic()
    profiler = BuildProfiler()
    success = True
    for item in targets:
        ok = await _profile_target(
            item,
            profiler=profiler,
            nix_verbosity=nix_verbosity,
            public_cache_only=public_cache_only,
            substituters=substituters,
            extra_substituters=extra_substituters,
        )
        success = success and ok

    profiler.finalize(now=time.monotonic())
    aggregated = _aggregate_profile_events(profiler.events)
    _log_profile_summary(aggregated)
    _write_profile_report(
        output_path=profile_output,
        flake_refs=[item.profile_path for item in targets],
        derivation_count=len(targets),
        profiler=profiler,
    )
    log.info("Wrote profile report to %s", profile_output)
    log.info(
        "Generation profiling finished in %s",
        _format_duration(time.monotonic() - start),
    )
    return 0 if success else 1


def run(
    *,
    target: Literal["all", "system", "home-manager"] = "all",
    system_profile: str = DEFAULT_SYSTEM_PROFILE,
    home_profile: str | None = None,
    profile_output: str | Path = "artifacts/current-generation-build-profile.json",
    public_cache_only: bool = True,
    substituters: str | None = None,
    extra_substituters: str | None = None,
    dry_run: bool = False,
    verbosity: int = 0,
) -> int:
    """Run current-generation rebuild profiling."""
    logging.basicConfig(
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
        level=_python_log_level(verbosity),
    )
    output_path = Path(profile_output)

    try:
        return asyncio.run(
            _async_main(
                target=target,
                system_profile=system_profile,
                home_profile=home_profile,
                profile_output=output_path,
                public_cache_only=public_cache_only,
                substituters=substituters,
                extra_substituters=extra_substituters,
                dry_run=dry_run,
                verbosity=verbosity,
            )
        )
    except (NixCommandError, RuntimeError):
        log.exception("Generation profiling failed")
        return 1


_DUAL_APPS = make_dual_typer_apps(
    help_text="Profile rebuild timings for current system/home-manager generations.",
    no_args_is_help=False,
)
app = _DUAL_APPS.app


@register_dual_entrypoint(_DUAL_APPS)
def cli(
    *,
    dry_run: Annotated[
        bool,
        typer.Option(
            "-n",
            "--dry-run",
            help="Print commands without running rebuild profiling.",
        ),
    ] = False,
    extra_substituters: Annotated[
        str | None,
        typer.Option(
            "-x",
            "--extra-substituters",
            help="Override extra-substituters (used with --respect-configured-substituters).",
        ),
    ] = None,
    home_profile: Annotated[
        str | None,
        typer.Option(
            "-m",
            "--home-profile",
            help="Home Manager profile symlink path.",
        ),
    ] = None,
    profile_output: Annotated[
        Path,
        typer.Option(
            "-p",
            "--profile-output",
            help="Path to write JSON profile report.",
        ),
    ] = Path("artifacts/current-generation-build-profile.json"),
    public_cache_only: Annotated[
        bool,
        typer.Option(
            "-c/-C",
            "--public-cache-only/--respect-configured-substituters",
            help=(
                "Use only cache.nixos.org and clear extra substituters; "
                "disable to keep configured substituters."
            ),
        ),
    ] = True,
    substituters: Annotated[
        str | None,
        typer.Option(
            "-u",
            "--substituters",
            help="Override substituters (used with --respect-configured-substituters).",
        ),
    ] = None,
    system_profile: Annotated[
        str,
        typer.Option(
            "-s",
            "--system-profile",
            help="nix-darwin system profile symlink path.",
        ),
    ] = DEFAULT_SYSTEM_PROFILE,
    target: Annotated[
        Literal["all", "system", "home-manager"],
        typer.Option(
            "-t",
            "--target",
            help="Which generation target(s) to profile.",
        ),
    ] = "all",
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
    """Run rebuild profiling for current nix-darwin/home-manager generations."""
    raise typer.Exit(
        code=run(
            dry_run=dry_run,
            extra_substituters=extra_substituters,
            home_profile=home_profile,
            profile_output=profile_output,
            public_cache_only=public_cache_only,
            substituters=substituters,
            system_profile=system_profile,
            target=target,
            verbosity=verbosity,
        )
    )


main = make_main(_DUAL_APPS.standalone_app, prog_name="cache generations")


if __name__ == "__main__":
    raise SystemExit(main())
