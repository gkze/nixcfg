"""Updater registry with automatic per-package discovery.

Loads updater modules from an explicit manifest. Importing each module runs
explicit ``register_updater(...)`` calls or factory helpers, which populate
:data:`UPDATERS`.
"""

import importlib.util
import sys
from threading import Lock
from typing import TYPE_CHECKING, Any

from lib.update.paths import REPO_ROOT
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
    UvLockUpdater,
    VersionInfo,
    bun_node_modules_updater,
    cargo_vendor_updater,
    deno_deps_updater,
    flake_input_hash_updater,
    go_vendor_updater,
    npm_deps_updater,
    register_updater,
    uv_lock_hash_updater,
    uv_lock_updater,
)
from lib.update.updaters.github_raw_file import (
    GitHubRawFileUpdater,
    github_raw_file_updater,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater
from lib.update.updaters.module_manifest import UPDATER_MODULE_PATHS
from lib.update.updaters.platform_api import (
    DownloadingPlatformAPIUpdater,
    PlatformAPIUpdater,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_DISCOVERY_LOCK = Lock()
_DISCOVERY_STATE = {"complete": False}


def _updater_module_paths() -> dict[str, Path]:
    """Return the explicit updater module path manifest."""
    return {
        name: REPO_ROOT / rel_path for name, rel_path in UPDATER_MODULE_PATHS.items()
    }


def _discover_updaters() -> None:
    """Import every manifest-declared updater module to trigger registration."""
    for name, updater_file in sorted(_updater_module_paths().items()):
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


def resolve_registry_alias(
    registry_alias: dict[str, type[Any]],
    loader: Callable[[], dict[str, type[Updater]]] | None = None,
) -> dict[str, type[Any]]:
    """Return a registry alias or lazily load the shared updater registry."""
    if registry_alias is not UPDATERS:
        return registry_alias
    effective_loader = ensure_updaters_loaded if loader is None else loader
    return registry_alias or effective_loader()


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
    "UvLockUpdater",
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
    "resolve_registry_alias",
    "uv_lock_hash_updater",
    "uv_lock_updater",
]
