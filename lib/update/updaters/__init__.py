"""Updater registry with automatic per-package discovery.

Loads updater modules by scanning package/overlay updater files in the repo.
Importing each module runs explicit ``register_updater(...)`` calls, which
populate :data:`UPDATERS`.
"""

import importlib.util
import sys
from pathlib import Path
from threading import Lock
from typing import TYPE_CHECKING

from lib.update.paths import REPO_ROOT, package_file_map
from lib.update.sources import read_pinned_source_version
from lib.update.updaters.core import (
    AssetURLsMetadataUpdater,
    CargoLockGitDep,
    ChecksumProvidedUpdater,
    DownloadHashUpdater,
    DownloadUrlMetadataUpdater,
    FixedOutputHashStep,
    HashEntryUpdater,
    SourceThenOverlayHashMixin,
    UpdateContext,
    Updater,
    stream_fixed_output_hashes,
    stream_url_hash_mapping,
)
from lib.update.updaters.flake_backed import (
    BunNodeModulesHashUpdater,
    CargoVendorHashUpdater,
    DenoDepsHashUpdater,
    DenoManifestUpdater,
    FlakeInputHashUpdater,
    FlakeInputMetadataUpdater,
    FlakeInputUpdater,
    GoVendorHashUpdater,
    NpmDepsHashUpdater,
    UvLockUpdater,
)
from lib.update.updaters.github_raw_file import GitHubRawFileUpdater
from lib.update.updaters.github_release import (
    GitHubReleaseAssetURLsUpdater,
    GitHubReleaseUpdater,
)
from lib.update.updaters.materialization import (
    Crate2NixArtifactsMixin,
    Crate2NixMetadataUpdater,
    MaterializesArtifactsMixin,
)
from lib.update.updaters.metadata import VersionInfo
from lib.update.updaters.platform_api import (
    DownloadingPlatformAPIUpdater,
    PlatformAPIUpdater,
)
from lib.update.updaters.registry import UPDATERS, UpdaterClass, register_updater
from lib.update.updaters.single_url import SingleURLHashEntryUpdater
from lib.update.updaters.strategies import (
    ElectronBuilderAssetURLsUpdater,
    HeadArtifactDownloadUpdater,
    JsonFieldDownloadUpdater,
    PinnedSourceDownloadUpdater,
    SparkleAppcastUpdater,
    SparkleAppcastUrlUpdater,
    VersionEndpointDownloadUpdater,
)
from lib.update.updaters.vendor_feeds import SparkleAppcastItem

if TYPE_CHECKING:
    from collections.abc import Callable

_DISCOVERY_LOCK = Lock()
_DISCOVERY_STATE = {"complete": False}


def _updater_module_paths() -> dict[str, Path]:
    """Return discovered updater module paths from the repository layout."""
    return package_file_map("updater.py")


def _prepend_package_path(module_name: str, package_path: Path) -> None:
    """Prefer a repo-local package path for dynamically imported updaters."""
    module = sys.modules.get(module_name)
    if module is None or not hasattr(module, "__path__"):
        return

    search_path = module.__path__
    repo_path = str(package_path)
    entries = list(search_path)
    if entries[:1] == [repo_path]:
        return

    search_path[:] = [repo_path, *[entry for entry in entries if entry != repo_path]]


def _prefer_repo_lib_paths() -> None:
    """Allow current-worktree helper modules to satisfy updater imports."""
    root = Path(REPO_ROOT)
    _prepend_package_path("lib", root / "lib")
    _prepend_package_path("lib.update", root / "lib" / "update")
    _prepend_package_path(
        "lib.update.updaters",
        root / "lib" / "update" / "updaters",
    )


def _discover_updaters() -> None:
    """Import every discovered updater module to trigger registration."""
    _prefer_repo_lib_paths()
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
    "AssetURLsMetadataUpdater",
    "BunNodeModulesHashUpdater",
    "CargoLockGitDep",
    "CargoVendorHashUpdater",
    "ChecksumProvidedUpdater",
    "Crate2NixArtifactsMixin",
    "Crate2NixMetadataUpdater",
    "DenoDepsHashUpdater",
    "DenoManifestUpdater",
    "DownloadHashUpdater",
    "DownloadUrlMetadataUpdater",
    "DownloadingPlatformAPIUpdater",
    "ElectronBuilderAssetURLsUpdater",
    "FixedOutputHashStep",
    "FlakeInputHashUpdater",
    "FlakeInputMetadataUpdater",
    "FlakeInputUpdater",
    "GitHubRawFileUpdater",
    "GitHubReleaseAssetURLsUpdater",
    "GitHubReleaseUpdater",
    "GoVendorHashUpdater",
    "HashEntryUpdater",
    "HeadArtifactDownloadUpdater",
    "JsonFieldDownloadUpdater",
    "MaterializesArtifactsMixin",
    "NpmDepsHashUpdater",
    "PinnedSourceDownloadUpdater",
    "PlatformAPIUpdater",
    "SingleURLHashEntryUpdater",
    "SourceThenOverlayHashMixin",
    "SparkleAppcastItem",
    "SparkleAppcastUpdater",
    "SparkleAppcastUrlUpdater",
    "UpdateContext",
    "Updater",
    "UpdaterClass",
    "UvLockUpdater",
    "VersionEndpointDownloadUpdater",
    "VersionInfo",
    "ensure_updaters_loaded",
    "read_pinned_source_version",
    "register_updater",
    "resolve_registry_alias",
    "stream_fixed_output_hashes",
    "stream_url_hash_mapping",
]
