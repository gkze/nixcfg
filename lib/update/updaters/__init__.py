"""Updater registry with automatic per-package discovery.

Scans ``packages/*/updater.py`` and ``overlays/*/updater.py`` for
updater modules. Importing each module
triggers ``Updater.__init_subclass__`` which registers the concrete
class into :data:`UPDATERS`.
"""

import importlib.util
import sys

from lib.update.paths import package_file_map
from lib.update.updaters.base import (
    UPDATERS,
    CargoLockGitDep,
    ChecksumProvidedUpdater,
    DownloadHashUpdater,
    HashEntryUpdater,
    Updater,
    VersionInfo,
    bun_node_modules_updater,
    cargo_vendor_updater,
    deno_deps_updater,
    go_vendor_updater,
    npm_deps_updater,
)
from lib.update.updaters.github_raw_file import (
    GitHubRawFileUpdater,
    github_raw_file_updater,
)
from lib.update.updaters.platform_api import PlatformAPIUpdater


def _discover_updaters() -> None:
    """Import every per-package ``updater.py`` to trigger registration."""
    for name, updater_file in package_file_map("updater.py").items():
        # Use a stable module name so re-imports are safe.
        mod_name = f"_updater_pkg.{name}"
        if mod_name in sys.modules:
            continue
        if (
            spec := importlib.util.spec_from_file_location(mod_name, updater_file)
        ) is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = mod
        spec.loader.exec_module(mod)


_discover_updaters()

__all__ = [
    "UPDATERS",
    "CargoLockGitDep",
    "ChecksumProvidedUpdater",
    "DownloadHashUpdater",
    "GitHubRawFileUpdater",
    "HashEntryUpdater",
    "PlatformAPIUpdater",
    "Updater",
    "VersionInfo",
    "bun_node_modules_updater",
    "cargo_vendor_updater",
    "deno_deps_updater",
    "github_raw_file_updater",
    "go_vendor_updater",
    "npm_deps_updater",
]
