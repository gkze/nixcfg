"""Updater registry with automatic per-package discovery.

Loads updater modules by scanning package/overlay updater files in the repo.
Importing each module runs explicit ``register_updater(...)`` calls or factory
helpers, which populate :data:`UPDATERS`.
"""

import importlib.util
import sys
from threading import Lock
from typing import TYPE_CHECKING

import lib.update.updaters.base as _updaters_base
import lib.update.updaters.github_raw_file as _github_raw_file_module
import lib.update.updaters.github_release as _github_release_module
import lib.update.updaters.platform_api as _platform_api_module
from lib.update.paths import REPO_ROOT, package_file_map
from lib.update.updaters.registry import UPDATERS, UpdaterClass, register_updater

CargoLockGitDep = _updaters_base.CargoLockGitDep
ChecksumProvidedUpdater = _updaters_base.ChecksumProvidedUpdater
DenoDepsHashUpdater = _updaters_base.DenoDepsHashUpdater
DownloadHashUpdater = _updaters_base.DownloadHashUpdater
FixedOutputHashStep = _updaters_base.FixedOutputHashStep
FlakeInputHashUpdater = _updaters_base.FlakeInputHashUpdater
FlakeInputMetadataUpdater = _updaters_base.FlakeInputMetadataUpdater
FlakeInputUpdater = _updaters_base.FlakeInputUpdater
HashEntryUpdater = _updaters_base.HashEntryUpdater
MaterializesArtifactsMixin = _updaters_base.MaterializesArtifactsMixin
Crate2NixArtifactsMixin = _updaters_base.Crate2NixArtifactsMixin
Crate2NixMetadataUpdater = _updaters_base.Crate2NixMetadataUpdater
UpdateContext = _updaters_base.UpdateContext
Updater = _updaters_base.Updater
UvLockUpdater = _updaters_base.UvLockUpdater
VersionInfo = _updaters_base.VersionInfo
bun_node_modules_updater = _updaters_base.bun_node_modules_updater
cargo_vendor_updater = _updaters_base.cargo_vendor_updater
deno_deps_updater = _updaters_base.deno_deps_updater
flake_input_hash_updater = _updaters_base.flake_input_hash_updater
go_vendor_updater = _updaters_base.go_vendor_updater
npm_deps_updater = _updaters_base.npm_deps_updater
uv_lock_hash_updater = _updaters_base.uv_lock_hash_updater
uv_lock_updater = _updaters_base.uv_lock_updater
stream_fixed_output_hashes = _updaters_base.stream_fixed_output_hashes
stream_url_hash_mapping = _updaters_base.stream_url_hash_mapping
GitHubRawFileUpdater = _github_raw_file_module.GitHubRawFileUpdater
github_raw_file_updater = _github_raw_file_module.github_raw_file_updater
GitHubReleaseUpdater = _github_release_module.GitHubReleaseUpdater
DownloadingPlatformAPIUpdater = _platform_api_module.DownloadingPlatformAPIUpdater
PlatformAPIUpdater = _platform_api_module.PlatformAPIUpdater

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_DISCOVERY_LOCK = Lock()
_DISCOVERY_STATE = {"complete": False}


def _updater_module_paths() -> dict[str, Path]:
    """Return discovered updater module paths from the repository layout."""
    return package_file_map("updater.py")


def _discover_updaters() -> None:
    """Import every discovered updater module to trigger registration."""
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
    registry_alias: dict[str, UpdaterClass],
    loader: Callable[[], dict[str, UpdaterClass]] | None = None,
) -> dict[str, UpdaterClass]:
    """Return a registry alias or lazily load the shared updater registry."""
    if registry_alias is not UPDATERS:
        return registry_alias
    effective_loader = ensure_updaters_loaded if loader is None else loader
    return registry_alias or effective_loader()


__all__ = [
    "REPO_ROOT",
    "UPDATERS",
    "CargoLockGitDep",
    "ChecksumProvidedUpdater",
    "Crate2NixArtifactsMixin",
    "Crate2NixMetadataUpdater",
    "DenoDepsHashUpdater",
    "DownloadHashUpdater",
    "DownloadingPlatformAPIUpdater",
    "FixedOutputHashStep",
    "FlakeInputHashUpdater",
    "FlakeInputMetadataUpdater",
    "FlakeInputUpdater",
    "GitHubRawFileUpdater",
    "GitHubReleaseUpdater",
    "HashEntryUpdater",
    "MaterializesArtifactsMixin",
    "PlatformAPIUpdater",
    "UpdateContext",
    "Updater",
    "UpdaterClass",
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
    "stream_fixed_output_hashes",
    "stream_url_hash_mapping",
    "uv_lock_hash_updater",
    "uv_lock_updater",
]
