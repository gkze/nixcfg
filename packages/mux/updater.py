"""Updater for mux's platform-specific Bun offline cache hashes."""

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, override

from lib import json_utils
from lib.nix.models.sources import HashEntry, SourceHashes
from lib.system_policy import supported_systems
from lib.update import process as update_process
from lib.update.bun_lock import parse_bun_lock_text
from lib.update.bun_toolchain import (
    bun_release_urls,
    bun_runtime_hash_entries,
    require_bun_package_manager,
)
from lib.update.events import (
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    expect_source_hashes,
    require_value,
)
from lib.update.net import fetch_json, fetch_url, github_raw_url
from lib.update.npm_semver import require_npm_version_matches_spec
from lib.update.updaters import (
    BunNodeModulesHashUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.core import _coerce_context
from lib.update.updaters.metadata import MappingMetadata

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.flake_lock import FlakeLockNode
    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_EXACT_VERSION_PATTERN = re.compile(
    r"^(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_MANIFEST_PATH = "package.json"
_LOCK_PATH = "bun.lock"
_SUPPORTED_SYSTEMS = supported_systems()


@dataclass(frozen=True, slots=True)
class MuxSourceMetadata(MappingMetadata):
    """Immutable source identity and Electron closure selected for one release."""

    node: FlakeLockNode
    commit: str
    bun_version: str
    electron_version: str
    bun_runtime_hashes: tuple[HashEntry, ...] = ()

    @override
    def to_dict(self) -> dict[str, object]:
        """Expose fields through the updater metadata compatibility mapping."""
        return {
            "node": self.node,
            "commit": self.commit,
            "bunVersion": self.bun_version,
            "electronVersion": self.electron_version,
        }


def _require_exact_version(value: str, *, context: str) -> str:
    if _EXACT_VERSION_PATTERN.fullmatch(value) is None:
        msg = f"Mux {context} must be an exact semantic version, got {value!r}"
        raise RuntimeError(msg)
    return value


def _release_version(version: str) -> str:
    return _require_exact_version(version.removeprefix("v"), context="flake input ref")


def _locked_github_source(node: FlakeLockNode) -> tuple[str, str, str]:
    locked = node.locked
    if (
        locked is None
        or locked.type != "github"
        or not locked.owner
        or not locked.repo
        or not locked.rev
    ):
        msg = "Mux flake input must resolve to a complete GitHub source"
        raise RuntimeError(msg)
    if _COMMIT_PATTERN.fullmatch(locked.rev) is None:
        msg = (
            f"Mux flake input revision must be an immutable commit, got {locked.rev!r}"
        )
        raise RuntimeError(msg)
    return locked.owner, locked.repo, locked.rev


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


def _require_metadata(info: VersionInfo) -> MuxSourceMetadata:
    metadata = info.metadata
    if not isinstance(metadata, MuxSourceMetadata):
        msg = "Mux version metadata is missing its resolved release source"
        raise TypeError(msg)
    return metadata


def _with_bun_runtime_hashes(
    info: VersionInfo,
    hashes: list[HashEntry],
) -> VersionInfo:
    metadata = _require_metadata(info)
    return VersionInfo(
        version=info.version,
        metadata=replace(metadata, bun_runtime_hashes=tuple(hashes)),
    )


@register_updater
class MuxUpdater(BunNodeModulesHashUpdater):
    """Bun node_modules hash updater for mux."""

    name = "mux"
    aggregate_into = ("electron-runtimes",)
    hash_attr_path = ".offlineCache"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve Electron from manifests at the immutable refreshed input commit."""
        info = await super().fetch_latest(session)
        node = self._resolve_flake_node(info)
        owner, repo, commit = _locked_github_source(node)
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

    def source_pins_for(self, info: VersionInfo) -> dict[str, str]:
        """Persist the source manifest's exact Bun runtime identity."""
        return {"bunVersion": _require_metadata(info).bun_version}

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Fingerprint the package with its persisted exact Bun sources."""
        context = _coerce_context(context)
        current = context.current
        if current is None or current.hashes.entries is None:
            return False
        bun_hashes = [
            entry
            for entry in current.hashes.entries
            if entry.hash_type == "bunRuntimeHash"
        ]
        expected_urls = bun_release_urls(
            _require_metadata(info).bun_version,
            _SUPPORTED_SYSTEMS,
        )
        if {(entry.platform, entry.url) for entry in bun_hashes} != set(
            expected_urls.items()
        ):
            return False
        return await super()._is_latest(
            context,
            _with_bun_runtime_hashes(info, bun_hashes),
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash exact Bun runtimes before probing Bun's node_modules cache."""
        bun_version = _require_metadata(info).bun_version
        bun_urls = bun_release_urls(bun_version, _SUPPORTED_SYSTEMS)
        bun_hash_drain = ValueDrain[dict[str, str]]()
        async for event in drain_value_events(
            update_process.compute_url_hashes(
                self.name,
                bun_urls.values(),
                config=self.config,
            ),
            bun_hash_drain,
            parse=expect_hash_mapping,
        ):
            yield event
        bun_hashes = bun_runtime_hash_entries(
            bun_version,
            _SUPPORTED_SYSTEMS,
            require_value(bun_hash_drain, "Missing Mux Bun runtime hashes"),
        )

        node_hash_drain = ValueDrain[SourceHashes]()
        async for event in drain_value_events(
            super().fetch_hashes(
                _with_bun_runtime_hashes(info, bun_hashes),
                session,
                context=context,
            ),
            node_hash_drain,
            parse=expect_source_hashes,
        ):
            yield event
        node_hashes = require_value(
            node_hash_drain,
            "Missing Mux node_modules hashes",
        )
        if not isinstance(node_hashes, list):
            msg = "Mux node_modules hashes must use structured hash entries"
            raise TypeError(msg)
        yield UpdateEvent.value(self.name, [*bun_hashes, *node_hashes])

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the lockfile-resolved Electron runtime as source identity."""
        metadata = _require_metadata(info)
        if metadata.bun_runtime_hashes:
            if not isinstance(hashes, list):
                msg = "Mux candidate hashes must use structured hash entries"
                raise TypeError(msg)
            if any(entry.hash_type == "bunRuntimeHash" for entry in hashes):
                msg = "Mux candidate contains duplicate Bun runtime hashes"
                raise RuntimeError(msg)
            hashes = [*metadata.bun_runtime_hashes, *hashes]
        return (
            super()
            .build_result(info, hashes)
            .model_copy(update={"electron_version": metadata.electron_version})
        )
