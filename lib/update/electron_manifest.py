"""Resolve exact Electron versions from immutable flake-input manifests."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from lib import json_utils
from lib.update.locked_source import resolve_locked_source
from lib.update.updaters.metadata import MappingMetadata

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.flake_lock import FlakeLockNode
    from lib.update.config import UpdateConfig

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_EXACT_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)
_MAX_MANIFEST_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ElectronManifestMetadata(MappingMetadata):
    """One flake source plus its exact manifest-selected Electron runtime."""

    node: FlakeLockNode
    commit: str
    electron_version: str
    manifest_path: str
    manifest_version: str

    @override
    def to_dict(self) -> dict[str, object]:
        """Expose fields through the updater metadata compatibility mapping."""
        return {
            "node": self.node,
            "commit": self.commit,
            "electronVersion": self.electron_version,
            "manifestPath": self.manifest_path,
            "manifestVersion": self.manifest_version,
        }


def locked_github_source(
    node: FlakeLockNode,
    *,
    context: str,
) -> tuple[str, str, str]:
    """Return immutable GitHub coordinates from a complete flake lock node."""
    locked = node.locked
    if (
        locked is None
        or locked.type != "github"
        or not locked.owner
        or not locked.repo
        or not locked.rev
    ):
        msg = f"{context} must resolve to a complete GitHub source"
        raise RuntimeError(msg)
    if _COMMIT_PATTERN.fullmatch(locked.rev) is None:
        msg = f"{context} revision must be an immutable commit, got {locked.rev!r}"
        raise RuntimeError(msg)
    return locked.owner, locked.repo, locked.rev


def electron_manifest_contract(
    payload: object,
    *,
    context: str,
    dependency_group: str,
) -> tuple[str, str]:
    """Return manifest version and exact Electron dependency version."""
    manifest = json_utils.as_object_dict(payload, context=context)
    version = json_utils.get_required_str(manifest, "version", context=context)
    dependencies = json_utils.as_object_dict(
        manifest.get(dependency_group),
        context=f"{context} {dependency_group}",
    )
    spec = json_utils.get_required_str(
        dependencies,
        "electron",
        context=f"{context} {dependency_group}",
    )
    if _EXACT_VERSION_PATTERN.fullmatch(spec) is None:
        msg = f"{context} Electron dependency must resolve exactly, got {spec!r}"
        raise RuntimeError(msg)
    return version, spec


async def fetch_flake_electron_manifest(
    _session: aiohttp.ClientSession,
    *,
    node: FlakeLockNode,
    manifest_path: str,
    dependency_group: str,
    context: str,
    config: UpdateConfig,
) -> ElectronManifestMetadata:
    """Read and validate one manifest from its realized locked flake source."""
    _, _, commit = locked_github_source(node, context=context)
    source = await resolve_locked_source(
        node,
        context=context,
        command_timeout=config.default_subprocess_timeout,
    )
    manifest = await source.read_json(
        manifest_path,
        max_bytes=_MAX_MANIFEST_BYTES,
        description="manifest",
    )
    manifest_version, electron_version = electron_manifest_contract(
        manifest,
        context=f"{context} manifest",
        dependency_group=dependency_group,
    )
    return ElectronManifestMetadata(
        node=node,
        commit=commit,
        electron_version=electron_version,
        manifest_path=manifest_path,
        manifest_version=manifest_version,
    )


__all__ = [
    "ElectronManifestMetadata",
    "electron_manifest_contract",
    "fetch_flake_electron_manifest",
    "locked_github_source",
]
