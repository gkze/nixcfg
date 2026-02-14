"""Build packages with platform-specific FOD hashes to populate the Nix cache.

After ``compute-hashes`` discovers correct hashes and writes them to
``sources.json``, this step builds each package that contains a
platform-specific fixed-output derivation (FOD) on the *same* runner.
The Cachix daemon automatically pushes the result, so downstream jobs
(e.g. ``darwin-shared``) get a cache hit instead of rebuilding the FOD
— which would produce a different hash due to non-determinism (e.g.
Deno's SQLite cache, HTTP metadata, timestamps).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform as platform_mod
import time
from typing import TYPE_CHECKING

from lib.nix.commands.base import NixCommandError
from lib.nix.commands.build import nix_build
from lib.update.nix import _build_overlay_expr
from lib.update.paths import package_file_map

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lib.nix.models.sources import SourceEntry

log = logging.getLogger(__name__)

# Generous timeout — FODs may download large dependency trees.
BUILD_TIMEOUT = 3600.0

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


def _format_duration(seconds: float) -> str:
    """Format seconds as a human-readable duration."""
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds:.1f}s"
    if seconds < SECONDS_PER_HOUR:
        return (
            f"{int(seconds // SECONDS_PER_MINUTE)}m "
            f"{int(seconds % SECONDS_PER_MINUTE)}s"
        )
    hours = int(seconds // SECONDS_PER_HOUR)
    minutes = int((seconds % SECONDS_PER_HOUR) // SECONDS_PER_MINUTE)
    return f"{hours}h {minutes}m"


def _detect_system() -> str:
    """Return the current Nix system identifier (e.g. ``aarch64-darwin``)."""
    machine = platform_mod.machine()
    # Normalize x86_64 / arm64 to Nix conventions.
    arch_map = {"x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
    arch = arch_map.get(machine, machine)

    os_name = platform_mod.system().lower()
    os_map = {"darwin": "darwin", "linux": "linux"}
    nix_os = os_map.get(os_name, os_name)

    return f"{arch}-{nix_os}"


def _has_platform_fod(entry: SourceEntry, system: str) -> bool:
    """Return ``True`` if *entry* contains a FOD hash for *system*."""
    if entry.hashes.entries is None:
        return False
    return any(
        h.platform == system for h in entry.hashes.entries if h.platform is not None
    )


def _find_fod_packages(system: str) -> list[str]:
    """Return package names whose ``sources.json`` has a platform-specific FOD for *system*."""
    from lib.update.sources import load_source_entry

    packages: list[str] = []
    for name, path in sorted(package_file_map("sources.json").items()):
        try:
            entry = load_source_entry(path)
        except Exception:
            log.warning("Skipping %s: failed to load sources.json", name, exc_info=True)
            continue
        if _has_platform_fod(entry, system):
            packages.append(name)
    return packages


async def _build_one(name: str, system: str) -> bool:
    """Build a single overlay package, returning ``True`` on success."""
    expr = _build_overlay_expr(name, system=system)
    log.info("Building %s for %s ...", name, system)
    start = time.monotonic()
    try:
        await nix_build(
            expr=expr,
            impure=True,
            no_link=True,
            json_output=False,
            timeout=BUILD_TIMEOUT,
        )
    except NixCommandError:
        elapsed = time.monotonic() - start
        log.exception(
            "FAILED %s after %s",
            name,
            _format_duration(elapsed),
        )
        return False
    elapsed = time.monotonic() - start
    log.info("OK     %s in %s", name, _format_duration(elapsed))
    return True


async def _async_main(args: argparse.Namespace) -> int:
    system = args.system or _detect_system()
    log.info("System: %s", system)

    packages = _find_fod_packages(system)
    if not packages:
        log.info("No packages with platform-specific FOD hashes for %s", system)
        return 0

    log.info(
        "Found %d package(s) with platform-specific FODs: %s",
        len(packages),
        ", ".join(packages),
    )

    if args.dry_run:
        log.info("DRY RUN — skipping builds")
        return 0

    # Build sequentially to avoid overwhelming the runner and to get
    # clear per-package log output.
    failed: list[str] = []
    for name in packages:
        ok = await _build_one(name, system)
        if not ok:
            failed.append(name)

    if failed:
        log.error(
            "%d/%d package(s) failed to build: %s",
            len(failed),
            len(packages),
            ", ".join(failed),
        )
        return 1

    log.info("All %d package(s) built successfully", len(packages))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI entrypoint."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--system",
        help="Nix system identifier (default: auto-detect)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List packages that would be built without building",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
        level=logging.DEBUG if args.verbose else logging.INFO,
    )

    return asyncio.run(_async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
