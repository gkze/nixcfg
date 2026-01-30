"""Source updater infrastructure.

This package contains:
- Base classes for building updaters (base.py)
- Concrete updater implementations (various modules)
- Registry for auto-discovery of updaters
"""

from updaters.base import (
    UPDATERS,
    Updater,
    ChecksumProvidedUpdater,
    DownloadHashUpdater,
    HashEntryUpdater,
    FlakeInputHashUpdater,
    GoVendorHashUpdater,
    CargoVendorHashUpdater,
    NpmDepsHashUpdater,
    DenoDepsHashUpdater,
    go_vendor_updater,
    cargo_vendor_updater,
    npm_deps_updater,
    deno_deps_updater,
    github_raw_file_updater,
)

__all__ = [
    "UPDATERS",
    "Updater",
    "ChecksumProvidedUpdater",
    "DownloadHashUpdater",
    "HashEntryUpdater",
    "FlakeInputHashUpdater",
    "GoVendorHashUpdater",
    "CargoVendorHashUpdater",
    "NpmDepsHashUpdater",
    "DenoDepsHashUpdater",
    "go_vendor_updater",
    "cargo_vendor_updater",
    "npm_deps_updater",
    "deno_deps_updater",
    "github_raw_file_updater",
]

# Import concrete updaters to trigger registration
from updaters import sources  # noqa: F401, E402
