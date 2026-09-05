"""Updater for emdash's platform-specific npm dependency hashes."""

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, override

import yaml

from lib import json_utils
from lib.system_policy import supported_systems
from lib.update.locked_source import resolve_locked_source
from lib.update.npm_semver import (
    require_exact_semantic_version,
    require_npm_version_matches_spec,
    require_valid_npm_range,
)
from lib.update.updaters import NpmDepsHashUpdater, VersionInfo, register_updater
from lib.update.updaters.metadata import MappingMetadata
from lib.update.updaters.node_compatibility import (
    require_supported_node_engine,
    resolve_nixpkgs_nodejs_for_engine,
    resolve_nixpkgs_package_version,
)

if TYPE_CHECKING:
    from pathlib import Path

    import aiohttp

    from lib.nix.models.flake_lock import FlakeLockNode
    from lib.nix.models.sources import SourceEntry, SourceHashes

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_PNPM_PACKAGE_MANAGER_PATTERN = re.compile(
    r"^pnpm@(?P<version>[^+]+)"
    r"(?:\+sha512\.[A-Za-z0-9_+/=-]+)?$"
)
_ROOT_MANIFEST_PATH = "package.json"
_DESKTOP_MANIFEST_PATH = "apps/emdash-desktop/package.json"
_LOCK_PATH = "pnpm-lock.yaml"
_MAX_LOCK_BYTES = 32 * 1024 * 1024
_MAX_MANIFEST_BYTES = 1024 * 1024
_MAX_SHELL_ENV_CANDIDATE_BYTES = 1024 * 1024
_MAX_SHELL_ENV_CANDIDATES = 8192
_INTERACTIVE_SHELL_ENV_PROBE = "spawnSync(shell, ['-ilc', 'env'], shellEnvProbeOptions)"
_SOURCE_FILE_SUFFIX = ".ts"


@dataclass(frozen=True, slots=True)
class EmdashToolchain:
    """Validated toolchain selection owned by Emdash's root manifest."""

    node_engine: str
    nodejs_attr: str
    nodejs_version: str
    package_manager: str
    pnpm_engine: str
    pnpm_attr: str
    pnpm_version: str

    def to_pins(self) -> dict[str, str]:
        """Return the source-derived toolchain contract persisted for Nix."""
        return {
            "nodeEngine": self.node_engine,
            "nodejsAttr": self.nodejs_attr,
            "nodejsVersion": self.nodejs_version,
            "packageManager": self.package_manager,
            "pnpmEngine": self.pnpm_engine,
            "pnpmAttr": self.pnpm_attr,
            "pnpmVersion": self.pnpm_version,
        }


@dataclass(frozen=True, slots=True)
class EmdashSourceMetadata(MappingMetadata):
    """Immutable source and build contracts selected for one release."""

    node: FlakeLockNode
    commit: str
    electron_version: str
    shell_env_capture_path: str
    toolchain: EmdashToolchain

    @override
    def to_dict(self) -> dict[str, object]:
        """Expose fields through the updater metadata compatibility mapping."""
        return {
            "node": self.node,
            "commit": self.commit,
            "electronVersion": self.electron_version,
            "shellEnvCapturePath": self.shell_env_capture_path,
            **self.toolchain.to_pins(),
        }


def _require_exact_version(value: str, *, context: str) -> str:
    return require_exact_semantic_version(value, context=f"Emdash {context}")


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
        msg = "Emdash flake input must resolve to a complete GitHub source"
        raise RuntimeError(msg)
    if _COMMIT_PATTERN.fullmatch(locked.rev) is None:
        msg = f"Emdash flake input revision must be an immutable commit, got {locked.rev!r}"
        raise RuntimeError(msg)
    return locked.owner, locked.repo, locked.rev


def _manifest_contract(payload: object) -> tuple[str, str]:
    manifest = json_utils.as_object_dict(payload, context="Emdash desktop manifest")
    version = json_utils.get_required_str(
        manifest,
        "version",
        context="Emdash desktop manifest",
    )
    dev_dependencies = json_utils.as_object_dict(
        manifest.get("devDependencies"),
        context="Emdash desktop manifest devDependencies",
    )
    electron_spec = json_utils.get_required_str(
        dev_dependencies,
        "electron",
        context="Emdash desktop manifest devDependencies",
    )
    return version, electron_spec


def _pnpm_lock_contract(payload: bytes) -> tuple[str, str]:
    try:
        decoded = yaml.safe_load(payload.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        msg = "Emdash pnpm lock is not valid UTF-8 YAML"
        raise RuntimeError(msg) from exc

    lock = json_utils.as_object_dict(decoded, context="Emdash pnpm lock")
    importers = json_utils.as_object_dict(
        lock.get("importers"),
        context="Emdash pnpm lock importers",
    )
    desktop = json_utils.as_object_dict(
        importers.get("apps/emdash-desktop"),
        context="Emdash desktop lock importer",
    )
    dev_dependencies = json_utils.as_object_dict(
        desktop.get("devDependencies"),
        context="Emdash desktop lock devDependencies",
    )
    electron = json_utils.as_object_dict(
        dev_dependencies.get("electron"),
        context="Emdash desktop lock Electron dependency",
    )
    specifier = json_utils.get_required_str(
        electron,
        "specifier",
        context="Emdash desktop lock Electron dependency",
    )
    version = _require_exact_version(
        json_utils.get_required_str(
            electron,
            "version",
            context="Emdash desktop lock Electron dependency",
        ),
        context="locked Electron version",
    )
    return specifier, version


def _resolve_electron_version(
    *,
    release_version: str,
    manifest: object,
    lock_payload: bytes,
) -> str:
    manifest_version, manifest_spec = _manifest_contract(manifest)
    if manifest_version != release_version:
        msg = (
            f"Emdash desktop manifest version {manifest_version!r} does not match "
            f"flake input version {release_version!r}"
        )
        raise RuntimeError(msg)

    lock_spec, electron_version = _pnpm_lock_contract(lock_payload)
    if lock_spec != manifest_spec:
        msg = (
            f"Emdash Electron spec mismatch: manifest declares {manifest_spec!r}, "
            f"lock importer records {lock_spec!r}"
        )
        raise RuntimeError(msg)
    return require_npm_version_matches_spec(
        electron_version,
        manifest_spec,
        context="Emdash Electron",
    )


async def _resolve_toolchain_contract(
    manifest_payload: object,
    *,
    command_timeout: float,
) -> EmdashToolchain:
    """Derive and validate Nix toolchain selection from Emdash's root manifest."""
    manifest = json_utils.as_object_dict(
        manifest_payload,
        context="Emdash root manifest",
    )
    engines = json_utils.as_object_dict(
        manifest.get("engines"),
        context="Emdash root manifest engines",
    )
    node_engine = json_utils.get_required_str(
        engines,
        "node",
        context="Emdash root manifest engines",
    )
    pnpm_engine = json_utils.get_required_str(
        engines,
        "pnpm",
        context="Emdash root manifest engines",
    )
    package_manager = json_utils.get_required_str(
        manifest,
        "packageManager",
        context="Emdash root manifest",
    )
    pnpm_match = _PNPM_PACKAGE_MANAGER_PATTERN.fullmatch(package_manager)
    if pnpm_match is None:
        msg = (
            "Emdash root manifest must select an exact pnpm@<version>, "
            f"got {package_manager!r}"
        )
        raise RuntimeError(msg)

    pnpm_required_version = _require_exact_version(
        pnpm_match.group("version"),
        context="root packageManager pnpm",
    )
    node_engine = require_valid_npm_range(
        node_engine,
        context="Emdash root manifest Node engine",
    )
    pnpm_engine = require_valid_npm_range(
        pnpm_engine,
        context="Emdash root manifest pnpm engine",
    )
    pnpm_attr = f"pnpm_{pnpm_required_version.partition('.')[0]}"
    nodejs, pnpm_version = await asyncio.gather(
        resolve_nixpkgs_nodejs_for_engine(
            node_engine,
            command_timeout=command_timeout,
            source_name="Emdash",
        ),
        resolve_nixpkgs_package_version(
            pnpm_attr,
            command_timeout=command_timeout,
            source_name="Emdash",
        ),
    )
    node_engine = require_supported_node_engine(
        node_engine,
        selected_attr=nodejs.attribute,
        selected_version=nodejs.version,
        source_name="Emdash",
    )
    require_npm_version_matches_spec(
        pnpm_required_version,
        pnpm_engine,
        context="Emdash packageManager pnpm",
    )
    require_npm_version_matches_spec(
        pnpm_version,
        f"^{pnpm_required_version}",
        context="Emdash nixpkgs pnpm",
    )
    require_npm_version_matches_spec(
        pnpm_version,
        pnpm_engine,
        context="Emdash nixpkgs pnpm engine",
    )
    return EmdashToolchain(
        node_engine=node_engine,
        nodejs_attr=nodejs.attribute,
        nodejs_version=nodejs.version,
        package_manager=package_manager,
        pnpm_engine=pnpm_engine,
        pnpm_attr=pnpm_attr,
        pnpm_version=pnpm_version,
    )


def _shell_env_candidate_paths(source_root: Path) -> tuple[str, ...]:
    """Find the bounded TypeScript universe searched by the Nix patch."""
    candidates: list[str] = []
    for source_dir_name in ("apps", "packages"):
        source_dir = source_root / source_dir_name
        if not source_dir.is_dir():
            continue
        for source_file in source_dir.rglob(f"*{_SOURCE_FILE_SUFFIX}"):
            if not source_file.is_file():
                continue
            path = source_file.relative_to(source_root).as_posix()
            candidates.append(path)
            if len(candidates) > _MAX_SHELL_ENV_CANDIDATES:
                msg = "Emdash source has too many shell environment probe candidates"
                raise RuntimeError(msg)

    if not candidates:
        msg = "Emdash source tree has no shell environment probe candidates"
        raise RuntimeError(msg)
    return tuple(sorted(candidates))


def _shell_env_patch_target(payloads: dict[str, bytes]) -> str:
    """Require one immutable source file to satisfy the Nix patch contract."""
    matching_paths: list[str] = []
    occurrence_count = 0
    for path, payload in payloads.items():
        try:
            source = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = f"Emdash shell environment probe candidate {path} is not UTF-8"
            raise RuntimeError(msg) from exc
        occurrences = source.count(_INTERACTIVE_SHELL_ENV_PROBE)
        occurrence_count += occurrences
        if occurrences:
            matching_paths.append(path)

    if occurrence_count != 1:
        msg = (
            "Emdash source must contain exactly one interactive shell environment "
            f"probe for the Nix compatibility patch, found {occurrence_count}"
        )
        raise RuntimeError(msg)
    return matching_paths[0]


@register_updater
class EmdashUpdater(NpmDepsHashUpdater):
    """Npm deps hash updater for emdash."""

    name = "emdash"
    aggregate_into = ("electron-runtimes",)
    hash_attr_path = ".pnpmDeps"
    platform_specific = True
    supported_platforms = supported_systems()

    def source_pins_for(self, info: VersionInfo) -> dict[str, str]:
        """Persist the complete manifest-derived toolchain contract."""
        if not isinstance(info.metadata, EmdashSourceMetadata):
            msg = "Emdash version metadata is missing its resolved source contracts"
            raise TypeError(msg)
        return info.metadata.toolchain.to_pins()

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve Electron from manifests at the immutable refreshed input commit."""
        info = await super().fetch_latest(session)
        node = self._resolve_flake_node(info)
        _, _, commit = _locked_github_source(node)

        source = await resolve_locked_source(
            node,
            context="Emdash flake input",
            command_timeout=self.config.default_subprocess_timeout,
        )
        candidate_paths = await asyncio.to_thread(
            _shell_env_candidate_paths,
            source.root,
        )
        root_manifest, manifest, lock_payload = await asyncio.gather(
            source.read_json(
                _ROOT_MANIFEST_PATH,
                max_bytes=_MAX_MANIFEST_BYTES,
                description="root package manifest",
            ),
            source.read_json(
                _DESKTOP_MANIFEST_PATH,
                max_bytes=_MAX_MANIFEST_BYTES,
                description="desktop package manifest",
            ),
            source.read_bytes(
                _LOCK_PATH,
                max_bytes=_MAX_LOCK_BYTES,
                description="pnpm lock",
            ),
        )
        candidate_payloads = await asyncio.gather(
            *(
                source.read_bytes(
                    path,
                    max_bytes=_MAX_SHELL_ENV_CANDIDATE_BYTES,
                    description=f"shell environment probe candidate {path}",
                )
                for path in candidate_paths
            )
        )
        shell_env_capture_path = _shell_env_patch_target(
            dict(zip(candidate_paths, candidate_payloads, strict=True))
        )
        electron_version = _resolve_electron_version(
            release_version=_release_version(info.version),
            manifest=manifest,
            lock_payload=lock_payload,
        )
        toolchain = await _resolve_toolchain_contract(
            root_manifest,
            command_timeout=self.config.default_subprocess_timeout,
        )
        return VersionInfo(
            version=info.version,
            metadata=EmdashSourceMetadata(
                node=node,
                commit=commit,
                electron_version=electron_version,
                shell_env_capture_path=shell_env_capture_path,
                toolchain=toolchain,
            ),
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the lockfile-resolved Electron runtime as source identity."""
        if not isinstance(info.metadata, EmdashSourceMetadata):
            msg = "Emdash version metadata is missing its resolved source contracts"
            raise TypeError(msg)
        return (
            super()
            .build_result(info, hashes)
            .model_copy(update={"electron_version": info.metadata.electron_version})
        )
