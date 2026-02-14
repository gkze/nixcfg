"""Build platform-specific FOD sub-derivations to populate the Nix cache.

After ``compute-hashes`` discovers correct hashes and writes them to
``sources.json``, this step builds each **FOD sub-derivation** (not the
full package) on the *same* runner.  The Cachix daemon automatically
pushes the result, so downstream jobs (e.g. ``darwin-shared``) get a
cache hit instead of rebuilding the FOD — which would produce a
different hash due to non-determinism (e.g. Deno's SQLite cache, HTTP
metadata, timestamps).

Only the FOD is built because the full package may need resources not
available inside the Nix sandbox (e.g. ``deno compile`` downloads a
runtime binary).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import platform as platform_mod
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.nix.commands.base import NixCommandError
from lib.nix.commands.build import nix_build
from lib.update.nix import _build_overlay_expr
from lib.update.paths import package_file_map

if TYPE_CHECKING:
    from collections.abc import Sequence

    from lib.nix.models.sources import HashEntry, SourceEntry

log = logging.getLogger(__name__)

# Generous timeout — FODs may download large dependency trees.
BUILD_TIMEOUT = 3600.0

SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600

# Map from hash type to the Nix attribute suffix that evaluates to the
# FOD sub-derivation.  Each entry uses dot-separated path components
# appended to the package expression.
_HASH_TYPE_TO_FOD_ATTR: dict[str, str] = {
    "nodeModulesHash": ".node_modules",
}


@dataclass(frozen=True)
class FodTarget:
    """A single FOD sub-derivation to build."""

    package: str
    hash_type: str
    fod_attr: str


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
    arch_map = {"x86_64": "x86_64", "arm64": "aarch64", "aarch64": "aarch64"}
    arch = arch_map.get(machine, machine)

    os_name = platform_mod.system().lower()
    os_map = {"darwin": "darwin", "linux": "linux"}
    nix_os = os_map.get(os_name, os_name)

    return f"{arch}-{nix_os}"


def _platform_fod_entries(entry: SourceEntry, system: str) -> list[HashEntry]:
    """Return hash entries that are platform-specific FODs for *system*."""
    if entry.hashes.entries is None:
        return []
    return [
        h
        for h in entry.hashes.entries
        if h.platform == system and h.platform is not None
    ]


def _find_fod_targets(system: str) -> list[FodTarget]:
    """Discover FOD sub-derivations that need building for *system*."""
    from lib.update.sources import load_source_entry

    targets: list[FodTarget] = []
    for name, path in sorted(package_file_map("sources.json").items()):
        try:
            entry = load_source_entry(path)
        except Exception:
            log.warning("Skipping %s: failed to load sources.json", name, exc_info=True)
            continue
        for h in _platform_fod_entries(entry, system):
            fod_attr = _HASH_TYPE_TO_FOD_ATTR.get(h.hash_type)
            if fod_attr is None:
                log.warning(
                    "No FOD attribute mapping for hash type %s in %s — skipping",
                    h.hash_type,
                    name,
                )
                continue
            targets.append(
                FodTarget(package=name, hash_type=h.hash_type, fod_attr=fod_attr)
            )
    return targets


def _build_fod_expr(package: str, fod_attr: str, *, system: str | None = None) -> str:
    """Build a Nix expression that evaluates to a package's FOD sub-derivation."""
    base_expr = _build_overlay_expr(package, system=system)
    # _build_overlay_expr returns: let ... in applied."<package>"
    # We append the FOD attribute path: let ... in (applied."<package>").passthru.denoDeps
    # Wrap in parens to ensure the attribute access binds to the full let expression.
    return f"({base_expr}){fod_attr}"


async def _build_one(target: FodTarget, system: str) -> bool:
    """Build a single FOD sub-derivation, returning ``True`` on success."""
    expr = _build_fod_expr(target.package, target.fod_attr, system=system)
    label = f"{target.package}{target.fod_attr}"
    log.info("Building %s for %s ...", label, system)
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
            label,
            _format_duration(elapsed),
        )
        return False
    elapsed = time.monotonic() - start
    log.info("OK     %s in %s", label, _format_duration(elapsed))
    return True


async def _async_main(args: argparse.Namespace) -> int:
    system = args.system or _detect_system()
    log.info("System: %s", system)

    targets = _find_fod_targets(system)
    if not targets:
        log.info("No FOD sub-derivations to build for %s", system)
        return 0

    log.info(
        "Found %d FOD target(s): %s",
        len(targets),
        ", ".join(f"{t.package}{t.fod_attr}" for t in targets),
    )

    if args.dry_run:
        log.info("DRY RUN — skipping builds")
        return 0

    # Build sequentially for clear per-target log output.
    failed: list[str] = []
    for target in targets:
        ok = await _build_one(target, system)
        if not ok:
            failed.append(f"{target.package}{target.fod_attr}")

    if failed:
        log.error(
            "%d/%d target(s) failed to build: %s",
            len(failed),
            len(targets),
            ", ".join(failed),
        )
        return 1

    log.info("All %d target(s) built successfully", len(targets))
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
        help="List targets that would be built without building",
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
