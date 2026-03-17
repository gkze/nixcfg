"""Build platform-specific FOD sub-derivations and push them to Cachix.

After ``compute-hashes`` discovers correct hashes and writes them to
``sources.json``, this step builds each **FOD sub-derivation** (not the
full package) on the *same* runner and then **explicitly pushes** the
resulting store path to Cachix.  Downstream jobs (e.g. ``darwin-shared``)
then get a cache hit instead of rebuilding the FOD — which would produce
a different hash due to non-determinism (e.g. Bun's ``node_modules``
layout, Deno's SQLite cache, HTTP metadata, timestamps).

The explicit ``cachix push`` is necessary because the Cachix daemon's
automatic store-path detection may miss FODs built inside nested ``nix
build`` invocations (e.g. ``nix run .#nixcfg -- ci cache fod``
spawns its own ``nix build --impure``).

Only the FOD is built because the full package may need resources not
available inside the Nix sandbox (e.g. ``deno compile`` downloads a
runtime binary).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

import typer

from lib.nix.commands import base as nix_base
from lib.nix.commands.build import nix_build
from lib.update import sources as update_sources
from lib.update.ci._cli import make_main, make_typer_app
from lib.update.ci._time import format_duration
from lib.update.nix import _build_overlay_attr_expr, normalize_nix_platform
from lib.update.paths import SOURCES_FILE_NAME, package_file_map

if TYPE_CHECKING:
    from lib.nix.models.sources import HashEntry, SourceEntry

log = logging.getLogger(__name__)

# Generous timeout — FODs may download large dependency trees.
BUILD_TIMEOUT = 3600.0

# Default map from hash type to the Nix attribute suffix that evaluates to the
# FOD sub-derivation. Each entry uses dot-separated path components appended to
# the package expression.
_HASH_TYPE_TO_FOD_ATTR: dict[str, str] = {
    "nodeModulesHash": ".node_modules",
}

# Package-specific FOD attribute overrides.
#
# Most Bun-based packages expose the fixed-output dependency derivation at
# ``.node_modules``. ``mux`` is an exception: it tracks the same
# ``nodeModulesHash`` value but exposes it as ``.offlineCache``.
_PACKAGE_HASH_TYPE_TO_FOD_ATTR: dict[tuple[str, str], str] = {
    ("mux", "nodeModulesHash"): ".offlineCache",
}


@dataclass(frozen=True)
class FodTarget:
    """A single FOD sub-derivation to build."""

    package: str
    hash_type: str
    fod_attr: str


_format_duration = format_duration


def _detect_system() -> str:
    """Return the current Nix system identifier (e.g. ``aarch64-darwin``)."""
    return normalize_nix_platform(platform.machine(), platform.system())


def _platform_fod_entries(entry: SourceEntry, system: str) -> list[HashEntry]:
    """Return hash entries that are platform-specific FODs for *system*."""
    if entry.hashes.entries is None:
        return []
    return [
        h
        for h in entry.hashes.entries
        if h.platform == system and h.platform is not None
    ]


def _resolve_fod_attr(package: str, hash_type: str) -> str | None:
    """Resolve the FOD sub-derivation attribute path for a package/hash type."""
    return _PACKAGE_HASH_TYPE_TO_FOD_ATTR.get(
        (package, hash_type),
        _HASH_TYPE_TO_FOD_ATTR.get(hash_type),
    )


def _find_fod_targets(system: str) -> list[FodTarget]:
    """Discover FOD sub-derivations that need building for *system*."""
    targets: list[FodTarget] = []
    for name, path in sorted(package_file_map(SOURCES_FILE_NAME).items()):
        try:
            entry = update_sources.load_source_entry(path)
        except Exception:
            log.warning("Skipping %s: failed to load sources.json", name, exc_info=True)
            continue
        for h in _platform_fod_entries(entry, system):
            fod_attr = _resolve_fod_attr(name, h.hash_type)
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
    return _build_overlay_attr_expr(package, fod_attr, system=system)


async def _resolve_output_paths(expr: str) -> list[str]:
    """Resolve the output store paths for a Nix expression.

    Runs ``nix build --json --dry-run`` which evaluates the expression
    and returns output paths without performing a build (the derivation
    must already be realised).  The JSON format is::

        [{"drvPath": "/nix/store/...", "outputs": {"out": "/nix/store/..."}}]

    This is intentionally separate from the main build step so that the
    build itself can use ``json_output=False`` (avoiding Pydantic model
    validation against the simpler ``nix build --json`` schema).
    """
    args = ["nix", "build", "--expr", expr, "--impure", "--no-link", "--json"]
    result = await nix_base.run_nix(args, check=False, timeout=60)
    if result.returncode != 0:
        log.debug(
            "nix build --json (dry path resolution) failed: %s", result.stderr[:200]
        )
        return []

    try:
        entries = json.loads(result.stdout)
    except json.JSONDecodeError:
        log.debug("Failed to parse nix build --json output")
        return []

    paths: list[str] = []
    for entry in entries:
        if isinstance(entry, dict):
            outputs = entry.get("outputs", {})
            paths.extend(
                path
                for path in outputs.values()
                if isinstance(path, str) and path.startswith("/nix/store/")
            )
    return paths


async def _push_to_cachix(store_paths: list[str], cache_name: str) -> bool:
    """Push store paths to Cachix, returning ``True`` on success.

    Falls back gracefully: if ``cachix`` is not installed or the push
    fails, a warning is logged but the build is still considered
    successful (the FOD exists locally and may still be served by
    the Cachix daemon).
    """
    if not store_paths:
        log.debug("No store paths to push")
        return True

    cachix_bin = shutil.which("cachix")
    if cachix_bin is None:
        log.warning("cachix not found on PATH — skipping explicit push")
        return False

    cmd = [cachix_bin, "push", cache_name, *store_paths]
    log.info("Pushing %d path(s) to cachix cache %r ...", len(store_paths), cache_name)
    log.debug("cachix push command: %s", cmd)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_bytes, stderr_bytes = await proc.communicate()
    stdout = (stdout_bytes or b"").decode(errors="replace").strip()
    stderr = (stderr_bytes or b"").decode(errors="replace").strip()

    if proc.returncode != 0:
        log.warning(
            "cachix push exited %d:\n  stdout: %s\n  stderr: %s",
            proc.returncode,
            stdout or "(empty)",
            stderr or "(empty)",
        )
        return False

    if stdout:
        log.debug("cachix push stdout: %s", stdout)
    log.info("Successfully pushed %d path(s) to cachix", len(store_paths))
    return True


async def _build_one(target: FodTarget, system: str, *, cache_name: str | None) -> bool:
    """Build a single FOD sub-derivation, returning ``True`` on success.

    When *cache_name* is set, the built store path is explicitly pushed
    to the named Cachix cache after a successful build.
    """
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
    except nix_base.NixCommandError:
        elapsed = time.monotonic() - start
        log.exception(
            "FAILED %s after %s",
            label,
            _format_duration(elapsed),
        )
        return False
    elapsed = time.monotonic() - start
    log.info("OK     %s in %s", label, _format_duration(elapsed))

    # Explicitly push the FOD to Cachix so downstream jobs get a cache hit.
    # We resolve the output path separately (the derivation is already built,
    # so this is essentially instant).
    if cache_name:
        store_paths = await _resolve_output_paths(expr)
        if store_paths:
            await _push_to_cachix(store_paths, cache_name)
        else:
            log.warning(
                "Could not resolve store path for %s — "
                "skipping cachix push (daemon may still pick it up)",
                label,
            )

    return True


async def _async_main(
    *,
    system: str | None = None,
    dry_run: bool = False,
    cachix_cache: str | None = None,
) -> int:
    system = system or _detect_system()
    log.info("System: %s", system)

    # Resolve the Cachix cache name for explicit pushes.
    cache_name = cachix_cache or os.environ.get("CACHIX_NAME")
    if cache_name:
        log.info("Cachix cache: %s (will push FODs after build)", cache_name)
    else:
        log.info("No Cachix cache configured — relying on daemon for pushes")

    targets = _find_fod_targets(system)
    if not targets:
        log.info("No FOD sub-derivations to build for %s", system)
        return 0

    log.info(
        "Found %d FOD target(s): %s",
        len(targets),
        ", ".join(f"{t.package}{t.fod_attr}" for t in targets),
    )

    if dry_run:
        log.info("DRY RUN — skipping builds")
        return 0

    # Build sequentially for clear per-target log output.
    failed: list[str] = []
    for target in targets:
        ok = await _build_one(target, system, cache_name=cache_name)
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


def run(
    *,
    system: str | None = None,
    dry_run: bool = False,
    cachix_cache: str | None = None,
    verbose: bool = False,
) -> int:
    """Build cache-warming fixed-output derivations."""
    logging.basicConfig(
        format="[%(asctime)s] %(message)s",
        datefmt="%H:%M:%S",
        level=logging.DEBUG if verbose else logging.INFO,
    )
    return asyncio.run(
        _async_main(system=system, dry_run=dry_run, cachix_cache=cachix_cache)
    )


app = make_typer_app(
    help_text="Build platform-specific FODs to populate binary caches.",
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def cli(
    *,
    cachix_cache: Annotated[
        str | None,
        typer.Option(
            "-c",
            "--cachix-cache",
            help=(
                "Cachix cache name for explicit pushes after build. "
                "Falls back to $CACHIX_NAME if not set."
            ),
        ),
    ] = None,
    dry_run: Annotated[
        bool,
        typer.Option(
            "-n",
            "--dry-run",
            help="List targets that would be built without building.",
        ),
    ] = False,
    system: Annotated[
        str | None,
        typer.Option(
            "-s",
            "--system",
            help="Nix system identifier (default: auto-detect).",
        ),
    ] = None,
    verbose: Annotated[
        bool,
        typer.Option("-v", "--verbose", help="Enable debug logging."),
    ] = False,
) -> None:
    """Run FOD cache warming for the selected system."""
    raise typer.Exit(
        code=run(
            cachix_cache=cachix_cache,
            dry_run=dry_run,
            system=system,
            verbose=verbose,
        )
    )


main = make_main(app, prog_name="cache fod")


if __name__ == "__main__":
    raise SystemExit(main())
