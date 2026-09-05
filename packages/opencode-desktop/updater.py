"""Updater for opencode-desktop's Bun node_modules hash."""

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib import json_utils
from lib.update import nix as update_nix
from lib.update.bun_lock import parse_bun_lock_text
from lib.update.electron_manifest import (
    ElectronManifestMetadata,
    locked_github_source,
)
from lib.update.locked_source import resolve_locked_source
from lib.update.npm_semver import require_npm_version_matches_spec
from lib.update.updaters import (
    FlakeInputHashUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes

_LOCK_PATH = "bun.lock"
_MAX_LOCK_BYTES = 32 * 1024 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_DESKTOP_PACKAGE_NAME = "@opencode-ai/desktop"
_DESKTOP_WORKSPACE_PIN = "desktopWorkspace"
_EXACT_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)$"
)


@dataclass(frozen=True, slots=True)
class _ElectronLockContract:
    manifest_path: str
    package_name: str
    package_version: str
    electron_spec: str
    electron_version: str


def _manifest_contract(payload: object) -> tuple[str, str, str]:
    manifest = json_utils.as_object_dict(
        payload,
        context="OpenCode Desktop package manifest",
    )
    name = json_utils.get_required_str(
        manifest,
        "name",
        context="OpenCode Desktop package manifest",
    )
    version = json_utils.get_required_str(
        manifest,
        "version",
        context="OpenCode Desktop package manifest",
    )
    dependencies = json_utils.as_object_dict(
        manifest.get("devDependencies"),
        context="OpenCode Desktop package manifest devDependencies",
    )
    electron_spec = json_utils.get_required_str(
        dependencies,
        "electron",
        context="OpenCode Desktop package manifest devDependencies",
    )
    return name, version, electron_spec


def _lock_contract(payload: bytes) -> _ElectronLockContract:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        msg = "OpenCode Desktop bun.lock is not valid UTF-8"
        raise RuntimeError(msg) from exc

    lock = parse_bun_lock_text(text, context="OpenCode Desktop bun.lock")
    workspaces = json_utils.as_object_dict(
        lock.get("workspaces"),
        context="OpenCode Desktop bun.lock workspaces",
    )
    matching_workspaces: list[tuple[str, dict[str, object]]] = []
    for workspace_path, raw_workspace in workspaces.items():
        workspace = json_utils.as_object_dict(
            raw_workspace,
            context=f"OpenCode Desktop bun.lock workspace {workspace_path}",
        )
        if workspace.get("name") == _DESKTOP_PACKAGE_NAME:
            matching_workspaces.append((workspace_path, workspace))
    if len(matching_workspaces) != 1:
        msg = (
            "OpenCode Desktop bun.lock must contain exactly one "
            f"{_DESKTOP_PACKAGE_NAME} workspace, found {len(matching_workspaces)}"
        )
        raise RuntimeError(msg)
    workspace_path, workspace = matching_workspaces[0]
    name = json_utils.get_required_str(
        workspace,
        "name",
        context=f"OpenCode Desktop bun.lock workspace {workspace_path}",
    )
    version = json_utils.get_required_str(
        workspace,
        "version",
        context=f"OpenCode Desktop bun.lock workspace {workspace_path}",
    )
    dependencies = json_utils.as_object_dict(
        workspace.get("devDependencies"),
        context=f"OpenCode Desktop bun.lock workspace {workspace_path} devDependencies",
    )
    electron_spec = json_utils.get_required_str(
        dependencies,
        "electron",
        context=f"OpenCode Desktop bun.lock workspace {workspace_path} devDependencies",
    )

    packages = json_utils.as_object_dict(
        lock.get("packages"),
        context="OpenCode Desktop bun.lock packages",
    )
    electron_entry = json_utils.as_object_list(
        packages.get("electron"),
        context="OpenCode Desktop bun.lock Electron package",
    )
    if not electron_entry or not isinstance(electron_entry[0], str):
        msg = "OpenCode Desktop bun.lock Electron package has no string resolution"
        raise TypeError(msg)
    resolution = electron_entry[0]
    prefix = "electron@"
    if not resolution.startswith(prefix):
        msg = (
            "OpenCode Desktop bun.lock Electron resolution is malformed: "
            f"{resolution!r}"
        )
        raise RuntimeError(msg)
    electron_version = resolution.removeprefix(prefix)
    if _EXACT_VERSION_PATTERN.fullmatch(electron_version) is None:
        msg = (
            "OpenCode Desktop bun.lock Electron resolution must be an exact "
            f"semantic version, got {electron_version!r}"
        )
        raise RuntimeError(msg)
    return _ElectronLockContract(
        manifest_path=f"{workspace_path}/package.json",
        package_name=name,
        package_version=version,
        electron_spec=electron_spec,
        electron_version=electron_version,
    )


def _validate_manifest_contract(
    manifest: object,
    lock: _ElectronLockContract,
) -> None:
    manifest_name, manifest_version, manifest_spec = _manifest_contract(manifest)
    if lock.package_name != manifest_name:
        msg = (
            f"OpenCode Desktop bun.lock workspace name {lock.package_name!r} does not "
            f"match package manifest name {manifest_name!r}"
        )
        raise RuntimeError(msg)
    if lock.package_version != manifest_version:
        msg = (
            f"OpenCode Desktop bun.lock workspace version {lock.package_version!r} "
            f"does not match package manifest version {manifest_version!r}"
        )
        raise RuntimeError(msg)
    if lock.electron_spec != manifest_spec:
        msg = (
            f"OpenCode Desktop Electron spec mismatch: manifest declares "
            f"{manifest_spec!r}, bun.lock records {lock.electron_spec!r}"
        )
        raise RuntimeError(msg)
    require_npm_version_matches_spec(
        lock.electron_version,
        manifest_spec,
        context="OpenCode Desktop Electron",
    )


@register_updater
class OpencodeDesktopUpdater(FlakeInputHashUpdater):
    """Track platform-specific node_modules hashes for every supported runtime."""

    name = "opencode-desktop"
    aggregate_into = ("electron-runtimes",)
    input_name = "opencode"
    hash_type = "nodeModulesHash"
    platform_specific = True
    native_only = False

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve Electron from bun.lock at the immutable input commit."""
        info = await super().fetch_latest(session)
        node = self._resolve_flake_node(info)
        _, _, commit = locked_github_source(
            node,
            context="OpenCode Desktop flake input",
        )
        source = await resolve_locked_source(
            node,
            context="OpenCode Desktop flake input",
            command_timeout=self.config.default_subprocess_timeout,
        )
        lock_payload = await source.read_bytes(
            _LOCK_PATH,
            max_bytes=_MAX_LOCK_BYTES,
            description="bun.lock",
        )
        lock = _lock_contract(lock_payload)
        manifest = await source.read_json(
            lock.manifest_path,
            max_bytes=_MAX_MANIFEST_BYTES,
            description="package manifest",
        )
        _validate_manifest_contract(
            manifest,
            lock,
        )
        metadata = ElectronManifestMetadata(
            node=node,
            commit=commit,
            electron_version=lock.electron_version,
            manifest_path=lock.manifest_path,
            manifest_version=lock.package_version,
        )
        return VersionInfo(version=info.version, metadata=metadata)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the source hash matrix and manifest-selected Electron runtime."""
        if not isinstance(info.metadata, ElectronManifestMetadata):
            msg = "OpenCode Desktop metadata is missing its resolved Electron manifest"
            raise TypeError(msg)
        manifest_suffix = "/package.json"
        if not info.metadata.manifest_path.endswith(manifest_suffix):
            msg = (
                "OpenCode Desktop metadata has an invalid manifest path: "
                f"{info.metadata.manifest_path!r}"
            )
            raise RuntimeError(msg)
        desktop_workspace = info.metadata.manifest_path.removesuffix(manifest_suffix)
        result = super().build_result(info, hashes)
        return result.model_copy(
            update={
                "electron_version": info.metadata.electron_version,
                "pins": {
                    **(result.pins or {}),
                    _DESKTOP_WORKSPACE_PIN: desktop_workspace,
                },
            }
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        if not await super()._is_latest(context, info):
            return False

        entry = context.current if isinstance(context, UpdateContext) else context
        if entry is None:
            return False

        hashes = entry.hashes
        platforms = (
            {
                hash_entry.platform
                for hash_entry in hashes.entries
                if hash_entry.platform is not None
                and hash_entry.hash_type == self.hash_type
            }
            if hashes.entries
            else set(hashes.mapping or {})
        )
        expected_platforms = set(
            self._platform_targets(update_nix.get_current_nix_platform())
        )
        return platforms == expected_platforms
