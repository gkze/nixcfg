"""Updater registry with automatic per-package discovery.

Scans ``packages/*/updater.py`` and ``overlays/*/updater.py`` for updater
modules. Importing each module runs explicit ``register_updater(...)`` calls
or factory helpers, which populate :data:`UPDATERS`.
"""

import importlib.util
import sys
from threading import Lock

from lib.update.paths import package_file_map
from lib.update.updaters.base import (
    UPDATERS,
    CargoLockGitDep,
    ChecksumProvidedUpdater,
    DenoDepsHashUpdater,
    DownloadHashUpdater,
    FlakeInputHashUpdater,
    FlakeInputUpdater,
    HashEntryUpdater,
    UpdateContext,
    Updater,
    VersionInfo,
    bun_node_modules_updater,
    cargo_vendor_updater,
    deno_deps_updater,
    flake_input_hash_updater,
    go_vendor_updater,
    npm_deps_updater,
    register_updater,
    uv_lock_hash_updater,
)
from lib.update.updaters.github_raw_file import (
    GitHubRawFileUpdater,
    github_raw_file_updater,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater
from lib.update.updaters.platform_api import (
    DownloadingPlatformAPIUpdater,
    PlatformAPIUpdater,
)

_DISCOVERY_LOCK = Lock()
_DISCOVERY_STATE = {"complete": False}


def _discover_updaters() -> None:
    """Import every per-package ``updater.py`` to trigger registration."""
    for name, updater_file in package_file_map("updater.py").items():
        # Use a stable module name so re-imports are safe.
        mod_name = f"_updater_pkg.{name}"
        if mod_name in sys.modules:
            if name in UPDATERS:
                continue
            del sys.modules[mod_name]
        if (
            spec := importlib.util.spec_from_file_location(mod_name, updater_file)
        ) is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)


def ensure_updaters_loaded() -> dict[str, type[Updater]]:
    """Load package updater modules on first use and return the registry."""
    if _DISCOVERY_STATE["complete"] and UPDATERS:
        return UPDATERS
    with _DISCOVERY_LOCK:
        if not _DISCOVERY_STATE["complete"] or not UPDATERS:
            _discover_updaters()
            _DISCOVERY_STATE["complete"] = True
    return UPDATERS


__all__ = [
    "UPDATERS",
    "CargoLockGitDep",
    "ChecksumProvidedUpdater",
    "DenoDepsHashUpdater",
    "DownloadHashUpdater",
    "DownloadingPlatformAPIUpdater",
    "FlakeInputHashUpdater",
    "FlakeInputUpdater",
    "GitHubRawFileUpdater",
    "GitHubReleaseUpdater",
    "HashEntryUpdater",
    "PlatformAPIUpdater",
    "UpdateContext",
    "Updater",
    "VersionInfo",
    "bun_node_modules_updater",
    "cargo_vendor_updater",
    "deno_deps_updater",
    "ensure_updaters_loaded",
    "flake_input_hash_updater",
    "github_raw_file_updater",
    "go_vendor_updater",
    "npm_deps_updater",
    "register_updater",
    "uv_lock_hash_updater",
]
