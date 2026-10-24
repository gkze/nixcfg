"""Updater for mux's platform-specific Bun offline cache hashes."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

from lib import json_utils
from lib.update.bun_lock import parse_bun_lock_text
from lib.update.bun_toolchain import require_bun_package_manager
from lib.update.locked_source import locked_github_source
from lib.update.net import fetch_json, fetch_url, github_raw_url
from lib.update.npm_semver import require_npm_version_matches_spec
from lib.update.updaters import (
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.exact_bun import (
    ExactBunMetadata,
    ExactBunNodeModulesHashUpdater,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes

_EXACT_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_MANIFEST_PATH = "package.json"
_LOCK_PATH = "bun.lock"


@dataclass(frozen=True, slots=True, kw_only=True)
class MuxSourceMetadata(ExactBunMetadata):
    """Immutable source identity and Electron closure selected for one release."""

    electron_version: str

    @override
    def to_dict(self) -> dict[str, object]:
        """Expose fields through the updater metadata compatibility mapping."""
        return {
            **ExactBunMetadata.to_dict(self),
            "electronVersion": self.electron_version,
        }


def _require_exact_version(value: str, *, context: str) -> str:
    if _EXACT_VERSION_PATTERN.fullmatch(value) is None:
        msg = f"Mux {context} must be an exact semantic version, got {value!r}"
        raise RuntimeError(msg)
    return value


def _release_version(version: str) -> str:
    return _require_exact_version(version.removeprefix("v"), context="flake input ref")


def _manifest_contract(payload: object) -> tuple[str, str, str, str]:
    manifest = json_utils.as_object_dict(payload, context="Mux package manifest")
    name = json_utils.get_required_str(manifest, "name", context="Mux package manifest")
    version = json_utils.get_required_str(
        manifest,
        "version",
        context="Mux package manifest",
    )
    optional_dependencies = json_utils.as_object_dict(
        manifest.get("optionalDependencies"),
        context="Mux package manifest optionalDependencies",
    )
    electron_spec = json_utils.get_required_str(
        optional_dependencies,
        "electron",
        context="Mux package manifest optionalDependencies",
    )
    bun_version = require_bun_package_manager(
        manifest,
        context="Mux package manifest",
    )
    return name, version, electron_spec, bun_version


def _bun_lock_contract(payload: bytes) -> tuple[str, str, str]:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = "Mux bun lock is not valid UTF-8"
        raise RuntimeError(msg) from exc
    lock = parse_bun_lock_text(text, context="Mux bun.lock")
    workspaces = json_utils.as_object_dict(
        lock.get("workspaces"),
        context="Mux bun lock workspaces",
    )
    root_workspace = json_utils.as_object_dict(
        workspaces.get(""),
        context="Mux bun lock root workspace",
    )
    name = json_utils.get_required_str(
        root_workspace,
        "name",
        context="Mux bun lock root workspace",
    )
    optional_dependencies = json_utils.as_object_dict(
        root_workspace.get("optionalDependencies"),
        context="Mux bun lock root optionalDependencies",
    )
    electron_spec = json_utils.get_required_str(
        optional_dependencies,
        "electron",
        context="Mux bun lock root optionalDependencies",
    )
    packages = json_utils.as_object_dict(
        lock.get("packages"),
        context="Mux bun lock packages",
    )
    electron_entry = json_utils.as_object_list(
        packages.get("electron"),
        context="Mux bun lock Electron package",
    )
    if not electron_entry or not isinstance(electron_entry[0], str):
        msg = "Mux bun lock Electron package has no string resolution"
        raise TypeError(msg)
    resolution = electron_entry[0]
    prefix = "electron@"
    if not resolution.startswith(prefix):
        msg = f"Mux bun lock Electron resolution is malformed: {resolution!r}"
        raise RuntimeError(msg)
    electron_version = _require_exact_version(
        resolution.removeprefix(prefix),
        context="locked Electron version",
    )
    return name, electron_spec, electron_version


def _resolve_release_contract(
    *,
    release_version: str,
    manifest: object,
    lock_payload: bytes,
) -> tuple[str, str]:
    manifest_name, manifest_version, manifest_spec, bun_version = _manifest_contract(
        manifest
    )
    if manifest_version != release_version:
        msg = (
            f"Mux package manifest version {manifest_version!r} does not match "
            f"flake input version {release_version!r}"
        )
        raise RuntimeError(msg)

    workspace_name, lock_spec, electron_version = _bun_lock_contract(lock_payload)
    if workspace_name != manifest_name:
        msg = (
            f"Mux Bun workspace name {workspace_name!r} does not match "
            f"package manifest name {manifest_name!r}"
        )
        raise RuntimeError(msg)
    if lock_spec != manifest_spec:
        msg = (
            f"Mux Electron spec mismatch: manifest declares {manifest_spec!r}, "
            f"lock workspace records {lock_spec!r}"
        )
        raise RuntimeError(msg)
    return (
        require_npm_version_matches_spec(
            electron_version,
            manifest_spec,
            context="Mux Electron",
        ),
        bun_version,
    )


@register_updater
class MuxUpdater(ExactBunNodeModulesHashUpdater):
    """Bun node_modules hash updater for mux."""

    name = "mux"
    aggregate_into = ("electron-runtimes",)
    hash_attr_path = ".offlineCache"

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve Electron from manifests at the immutable refreshed input commit."""
        info = await super().fetch_latest(session, context=context)
        node = self._resolve_flake_node(info)
        owner, repo, commit = locked_github_source(node, context="Mux flake input")
        manifest = await fetch_json(
            session,
            github_raw_url(owner, repo, commit, _MANIFEST_PATH),
            config=self.config,
        )
        lock_payload = await fetch_url(
            session,
            github_raw_url(owner, repo, commit, _LOCK_PATH),
            config=self.config,
        )
        electron_version, bun_version = _resolve_release_contract(
            release_version=_release_version(info.version),
            manifest=manifest,
            lock_payload=lock_payload,
        )
        return VersionInfo(
            version=info.version,
            metadata=MuxSourceMetadata(
                node=node,
                commit=commit,
                bun_version=bun_version,
                electron_version=electron_version,
            ),
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the lockfile-resolved Electron runtime as source identity."""
        metadata = self.exact_bun_metadata(info)
        if not isinstance(metadata, MuxSourceMetadata):
            msg = "mux version metadata is missing its resolved release source"
            raise TypeError(msg)
        return (
            super()
            .build_result(info, hashes)
            .model_copy(update={"electron_version": metadata.electron_version})
        )
