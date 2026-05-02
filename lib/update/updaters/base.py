"""Base updater facade and monkeypatch-friendly shared dependencies."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from lib.update import paths as update_paths
from lib.update import process as update_process
from lib.update import sources as update_sources
from lib.update.config import UpdateConfig
from lib.update.events import CommandResult, expect_hash_mapping, expect_str
from lib.update.flake import (
    flake_fetch_expr,
    get_flake_input_node,
    get_flake_input_version,
)
from lib.update.net import fetch_url
from lib.update.nix import (
    compute_drv_fingerprint,
    compute_fixed_output_hash,
    compute_overlay_hash,
    get_current_nix_platform,
)
from lib.update.nix_deno import compute_deno_deps_hash
from lib.update.updaters._sourcefile import resolve_sourcefile
from lib.update.updaters.core import (
    CargoLockGitDep,
    ChecksumProvidedUpdater,
    DownloadHashUpdater,
    FixedOutputHashStep,
    HashEntryUpdater,
    UpdateContext,
    Updater,
    _call_with_optional_context,
    _coerce_context,
    _emit_single_hash_entry,
    _ensure_str_mapping,
    _verify_platform_versions,
    source_override_env,
    stream_fixed_output_hashes,
    stream_source_then_overlay_hashes,
    stream_url_hash_mapping,
)
from lib.update.updaters.flake_backed import (
    DenoDepsHashUpdater,
    DenoManifestUpdater,
    FlakeInputHashUpdater,
    FlakeInputMetadataUpdater,
    FlakeInputUpdater,
    UvLockUpdater,
)
from lib.update.updaters.materialization import (
    Crate2NixArtifactsMixin,
    Crate2NixMetadataUpdater,
    MaterializesArtifactsMixin,
)
from lib.update.updaters.metadata import VersionInfo
from lib.update.updaters.registry import UPDATERS, register_updater
from lib.update.updaters.single_url import (
    SingleURLHashEntryUpdater,
    stream_single_url_hash_entry,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable
    from pathlib import Path

    from lib.update.events import EventStream


def _package_dir_for(name: str) -> Path | None:
    return update_paths.package_dir_for(name)


def _compute_url_hashes(source_name: str, urls: Iterable[str]) -> EventStream:
    return update_process.compute_url_hashes(source_name, urls)


def _convert_nix_hash_to_sri(source_name: str, nix_hash: str) -> EventStream:
    return update_process.convert_nix_hash_to_sri(source_name, nix_hash)


package_dir_for: Callable[[str], Path | None] = _package_dir_for
compute_url_hashes: Callable[[str, Iterable[str]], EventStream] = _compute_url_hashes
convert_nix_hash_to_sri: Callable[[str, str], EventStream] = _convert_nix_hash_to_sri


def read_pinned_source_version(name: str) -> str:
    """Read the pinned version from one updater-managed ``sources.json`` file."""
    pkg_dir = package_dir_for(name)
    if pkg_dir is None:
        msg = f"Package directory not found for {name}"
        raise RuntimeError(msg)
    entry = update_sources.load_source_entry(pkg_dir / "sources.json")
    version = entry.version
    if not isinstance(version, str) or not version:
        msg = f"{name} sources.json is missing a pinned version"
        raise RuntimeError(msg)
    return version


def _updater_sourcefile(cls: type[Updater]) -> str | None:
    return resolve_sourcefile(cls, inspect_module=inspect)


from lib.update.updaters.factories import (  # noqa: E402
    bun_node_modules_updater,
    cargo_vendor_updater,
    deno_deps_updater,
    deno_manifest_updater,
    flake_input_hash_updater,
    go_vendor_updater,
    npm_deps_updater,
    uv_lock_hash_updater,
    uv_lock_updater,
)

__all__ = [
    "UPDATERS",
    "CargoLockGitDep",
    "ChecksumProvidedUpdater",
    "CommandResult",
    "Crate2NixArtifactsMixin",
    "Crate2NixMetadataUpdater",
    "DenoDepsHashUpdater",
    "DenoManifestUpdater",
    "DownloadHashUpdater",
    "FixedOutputHashStep",
    "FlakeInputHashUpdater",
    "FlakeInputMetadataUpdater",
    "FlakeInputUpdater",
    "HashEntryUpdater",
    "MaterializesArtifactsMixin",
    "SingleURLHashEntryUpdater",
    "UpdateConfig",
    "UpdateContext",
    "Updater",
    "UvLockUpdater",
    "VersionInfo",
    "_call_with_optional_context",
    "_coerce_context",
    "_emit_single_hash_entry",
    "_ensure_str_mapping",
    "_verify_platform_versions",
    "bun_node_modules_updater",
    "cargo_vendor_updater",
    "compute_deno_deps_hash",
    "compute_drv_fingerprint",
    "compute_fixed_output_hash",
    "compute_overlay_hash",
    "compute_url_hashes",
    "convert_nix_hash_to_sri",
    "deno_deps_updater",
    "deno_manifest_updater",
    "expect_hash_mapping",
    "expect_str",
    "fetch_url",
    "flake_fetch_expr",
    "flake_input_hash_updater",
    "get_current_nix_platform",
    "get_flake_input_node",
    "get_flake_input_version",
    "go_vendor_updater",
    "npm_deps_updater",
    "package_dir_for",
    "read_pinned_source_version",
    "register_updater",
    "source_override_env",
    "stream_fixed_output_hashes",
    "stream_single_url_hash_entry",
    "stream_source_then_overlay_hashes",
    "stream_url_hash_mapping",
    "uv_lock_hash_updater",
    "uv_lock_updater",
]
