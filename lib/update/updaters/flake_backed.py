"""Flake-backed updater implementations and materializers."""

import asyncio
import os
import shutil
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import (
    HashCollection,
    HashEntry,
    HashType,
    SourceEntry,
    SourceHashes,
)
from lib.update import deno_lock
from lib.update import events as update_events
from lib.update import flake as update_flake
from lib.update import net as update_net
from lib.update import nix as update_nix
from lib.update import nix_deno as update_nix_deno
from lib.update import paths as update_paths
from lib.update import process as update_process
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import (
    EventSink,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ignore_event,
)
from lib.update.flake import flake_source_path_expr
from lib.update.nix import _build_package_path_attr_expr
from lib.update.platform_hashes import (
    PlatformHashResult,
    PreservedPlatformHash,
    preserve_existing_platform_hash,
    preserved_platform_hash_status,
    preserved_platform_hash_warning,
)
from lib.update.updaters.core import (
    UpdateContext,
    Updater,
)
from lib.update.updaters.metadata import (
    FlakeInputMetadata,
    VersionInfo,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.flake_lock import FlakeLockNode
    from lib.update.config import UpdateConfig

_raise_failed_command = update_events.raise_failed_command


def _ensure_user_writable_tree(root: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(root):
        dir_path = Path(dirpath)
        dir_path.chmod(dir_path.stat().st_mode | 0o700)
        for filename in filenames:
            file_path = dir_path / filename
            if file_path.is_symlink():
                continue
            file_path.chmod(file_path.stat().st_mode | 0o200)


class FlakeInputUpdater(Updater):
    """Base updater for sources backed by a flake.lock input."""

    input_name: str | None = None

    def __init__(self, *, config: UpdateConfig | None = None) -> None:
        """Initialize a flake-input-backed updater."""
        super().__init__(config=config)
        if self.input_name is None:
            self.input_name = self.name

    @property
    def _input(self) -> str:
        if self.input_name is None:
            msg = "Missing input name"
            raise RuntimeError(msg)
        return self.input_name

    def _resolve_flake_node(self, info: VersionInfo) -> FlakeLockNode:
        metadata = FlakeInputMetadata.from_metadata(
            info.metadata,
            context=f"{self.name} metadata",
        )
        if metadata is not None:
            return metadata.node
        return update_flake.get_flake_input_node(self._input)

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve the latest version from the flake lock node."""
        _ = context
        _ = session
        node = update_flake.get_flake_input_node(self._input)
        version = update_flake.get_flake_input_version(node)
        commit = node.locked.rev if node.locked is not None else None
        return VersionInfo(
            version=version,
            metadata=FlakeInputMetadata(node=node, commit=commit),
        )


class FlakeInputMetadataUpdater(FlakeInputUpdater):
    """Persist flake input version/commit metadata without extra hashes."""

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a source entry tied to this updater's flake input."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self._input,
            commit=info.commit,
            pins=self.source_pins_for(info),
        )

    async def _is_latest(
        self,
        context: UpdateContext,
        info: VersionInfo,
    ) -> bool:
        current = context.current
        if current is None:
            return False
        expected = self.build_result(info, [])
        return current.equivalent_to(expected)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Emit an empty hash set for metadata-only flake input tracking."""
        _ = (info, session, context, emit)
        empty_entries: list[HashEntry] = []
        return empty_entries


class FlakeInputHashUpdater(FlakeInputUpdater):
    """Base updater for hash-only sources backed by flake inputs."""

    hash_type: HashType
    hash_attr_path: ClassVar[str] = ""
    platform_specific: bool = False
    native_only: bool = False
    # Tuple of Nix system strings (e.g. ``"aarch64-darwin"``) this updater may
    # evaluate against. ``None`` means "all platforms" (the default). When set,
    # ``fetch_hashes`` short-circuits on unsupported platforms and preserves
    # any existing sources.json hashes. Mirror whatever system constraint the
    # companion package has in ``packages/registry.nix`` so local updates skip
    # Darwin-only or Linux-only packages on unsupported hosts.
    supported_platforms: ClassVar[tuple[str, ...] | None] = None
    required_tools: ClassVar[tuple[str, ...]] = ("nix",)

    def build_result(
        self,
        info: VersionInfo,
        hashes: SourceHashes,
    ) -> SourceEntry:
        """Build a source entry tied to this updater's flake input."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self._input,
            pins=self.source_pins_for(info),
        )

    async def _is_latest(
        self,
        context: UpdateContext,
        info: VersionInfo,
    ) -> bool:
        current = context.current
        expected = self.build_result(info, [])
        if (
            current is None
            or current.version != info.version
            or current.drv_hash is None
            or current.pins != expected.pins
            or current.electron_version != expected.electron_version
        ):
            return False
        try:
            new_fingerprint = await self._compute_drv_fingerprint(expected)
        except RuntimeError:
            return False
        context.drv_fingerprint = new_fingerprint
        return current.drv_hash == new_fingerprint

    async def _finalize_result(
        self,
        result: SourceEntry,
        *,
        info: VersionInfo | None = None,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceEntry:
        _ = info
        if not context.hashes_fully_computed:
            current_drv_hash = context.current.drv_hash if context.current else None
            if current_drv_hash is None:
                msg = (
                    f"Cannot preserve derivation fingerprint for {self.name}: "
                    "this run cannot certify all fingerprint inputs and the current "
                    "source entry has no drvHash"
                )
                raise RuntimeError(msg)
            result = result.model_copy(update={"drv_hash": current_drv_hash})
            await emit(
                UpdateEvent.status(
                    self.name,
                    "Preserving previous derivation fingerprint because this run cannot "
                    "certify all fingerprint inputs",
                    operation="compute_hash",
                    status=StatusInfo(
                        kind=StatusKind.PRESERVED_DRV_HASH,
                        value=current_drv_hash,
                    ),
                )
            )
            return result
        await emit(
            UpdateEvent.status(
                self.name,
                "Computing derivation fingerprint...",
                operation="compute_hash",
                status=StatusInfo(
                    kind=StatusKind.COMPUTING_HASH,
                    value="derivation fingerprint",
                ),
            )
        )
        try:
            drv_hash = context.drv_fingerprint
            if drv_hash is None:
                drv_hash = await self._compute_drv_fingerprint(result)
            result = result.model_copy(update={"drv_hash": drv_hash})
        except RuntimeError as exc:
            await emit(
                UpdateEvent.status(
                    self.name,
                    f"Warning: derivation fingerprint unavailable ({exc})",
                    operation="compute_hash",
                )
            )
        return result

    def _platform_targets(self, current_platform: str) -> tuple[str, ...]:
        if self.native_only:
            return (current_platform,)

        targets = [current_platform]
        for platform in self.config.hash_build_platforms:
            if platform not in targets:
                targets.append(platform)

        if self.supported_platforms is not None:
            supported = set(self.supported_platforms)
            targets = [platform for platform in targets if platform in supported]

        return tuple(targets)

    def _existing_platform_hashes(
        self,
        context: UpdateContext,
    ) -> dict[str, str]:
        entry = context.current
        if entry is None:
            legacy_entry = getattr(self, "_current_entry", None)
            if isinstance(legacy_entry, SourceEntry):
                entry = legacy_entry
        if entry is None:
            return {}

        hashes = entry.hashes
        if hashes.entries:
            return {
                hash_entry.platform: hash_entry.hash
                for hash_entry in hashes.entries
                if hash_entry.platform is not None
                and hash_entry.hash_type == self.hash_type
            }
        if hashes.mapping:
            return dict(hashes.mapping)
        return {}

    async def _compute_hash_for_system(
        self, info: VersionInfo, *, system: str | None, emit: EventSink = ignore_event
    ) -> str:
        candidate = self.build_result(info, [])
        source_override = (
            candidate
            if candidate.pins is not None or candidate.electron_version is not None
            else None
        )
        package_expr = self._package_hash_expr(
            system=system,
            source_override=source_override,
        )
        if package_expr is not None:
            return await update_nix.compute_fixed_output_hash(
                self.name, package_expr, config=self.config, emit=emit
            )
        if source_override is None:
            return await update_nix.compute_overlay_hash(
                self.name, system=system, config=self.config, emit=emit
            )
        return await update_nix.compute_overlay_hash(
            self.name,
            system=system,
            config=self.config,
            source_overrides={self.name: source_override},
            fake_hashes=True,
            emit=emit,
        )

    def _package_hash_expr(
        self,
        *,
        system: str | None,
        source_override: SourceEntry | None = None,
    ) -> str | None:
        updater_path = update_paths.package_file_for(
            self.name,
            update_paths.UPDATER_FILE_NAME,
        )
        if updater_path is None or not updater_path.is_relative_to(
            update_paths.get_repo_file("packages")
        ):
            return None
        return _build_package_path_attr_expr(
            self.name,
            self.hash_attr_path,
            system=system,
            source_overrides=(
                {self.name: source_override} if source_override is not None else None
            ),
            fake_hashes=True if source_override is not None else None,
        )

    async def _compute_drv_fingerprint(
        self,
        source_override: SourceEntry | None = None,
    ) -> str:
        if source_override is None or (
            source_override.pins is None and source_override.electron_version is None
        ):
            source_override = None
        fingerprint_override = (
            source_override.model_copy(
                update={
                    "drv_hash": None,
                    "hashes": HashCollection(
                        entries=[
                            entry
                            for entry in source_override.hashes.entries or ()
                            if entry.hash_type != self.hash_type
                        ],
                    ),
                }
            )
            if source_override is not None
            else None
        )
        package_expr = self._package_hash_expr(
            system=None,
            source_override=fingerprint_override,
        )
        if package_expr is None:
            return await update_nix.compute_drv_fingerprint(
                self.name,
                config=self.config,
                source_overrides=(
                    {self.name: fingerprint_override}
                    if fingerprint_override is not None
                    else None
                ),
                fake_hashes=True if fingerprint_override is not None else None,
            )
        return await update_nix.compute_expr_drv_fingerprint(
            self.name,
            package_expr,
            config=self.config,
        )

    async def _compute_hash(
        self, info: VersionInfo, *, emit: EventSink = ignore_event
    ) -> str:
        system = (
            update_nix.get_current_nix_platform() if self.platform_specific else None
        )
        return await self._compute_hash_for_system(info, system=system, emit=emit)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Compute flake-backed hashes for one or more target platforms."""
        _ = session
        current_platform = update_nix.get_current_nix_platform()
        if (
            self.supported_platforms is not None
            and current_platform not in self.supported_platforms
        ):
            context.hashes_fully_computed = False
            existing_hashes = self._existing_platform_hashes(context)
            entries = [
                HashEntry.create(self.hash_type, hash_val, platform=platform)
                for platform, hash_val in sorted(existing_hashes.items())
            ]
            await emit(
                UpdateEvent.status(
                    self.name,
                    f"Unsupported platform {current_platform}, preserving existing hashes",
                    operation="compute_hash",
                    status=StatusInfo(
                        kind=StatusKind.UNSUPPORTED_PLATFORM,
                        value=current_platform,
                    ),
                )
            )
            return entries
        if self.platform_specific:
            if self.native_only and (
                self.supported_platforms is None
                or set(self.supported_platforms) != {current_platform}
            ):
                context.hashes_fully_computed = False
            platform_hashes: dict[str, str] = {}
            existing_hashes = self._existing_platform_hashes(context)
            failed_platforms: list[PreservedPlatformHash] = []

            for platform in self._platform_targets(current_platform):
                try:
                    hash_value = await self._compute_hash_for_system(
                        info, system=platform, emit=emit
                    )
                except RuntimeError as exc:
                    if platform == current_platform:
                        raise
                    context.hashes_fully_computed = False
                    preserved = preserve_existing_platform_hash(
                        platform,
                        existing_hashes,
                        exc,
                    )
                    failed_platforms.append(preserved)
                    platform_hashes[platform] = preserved.hash
                    await emit(preserved_platform_hash_status(self.name, preserved))
                    continue
                platform_hashes[platform] = hash_value

            if failed_platforms:
                await emit(preserved_platform_hash_warning(self.name, failed_platforms))

            return [
                HashEntry.create(self.hash_type, hash_val, platform=platform)
                for platform, hash_val in sorted(platform_hashes.items())
            ]
        return [
            HashEntry.create(self.hash_type, await self._compute_hash(info, emit=emit))
        ]


class DenoDepsHashUpdater(FlakeInputHashUpdater):
    """Hash updater for per-platform Deno dependency derivations."""

    hash_type: HashType = "denoDepsHash"
    native_only: bool = False

    async def _compute_platform_hashes(
        self,
        info: VersionInfo,
        *,
        source_override: SourceEntry | None = None,
        emit: EventSink = ignore_event,
    ) -> PlatformHashResult:
        _ = info
        if source_override is None:
            return await update_nix_deno.compute_deno_deps_hash(
                self.name,
                self._input,
                native_only=self.native_only,
                config=self.config,
                emit=emit,
            )
        return await update_nix_deno.compute_deno_deps_hash(
            self.name,
            self._input,
            native_only=self.native_only,
            config=self.config,
            source_override=source_override,
            emit=emit,
        )

    def _candidate_source_override(
        self,
        info: VersionInfo,
        current: SourceEntry | None,
    ) -> SourceEntry | None:
        if self.source_pins_for(info) is None:
            return None
        hashes: SourceHashes
        if current is None:
            hashes = []
        elif current.hashes.entries is not None:
            hashes = list(current.hashes.entries)
        else:
            hashes = dict(current.hashes.mapping or {})
        return self.build_result(info, hashes)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Compute structured Deno dependency hashes for all target platforms."""
        _ = session
        source_override = self._candidate_source_override(info, context.current)
        if self.native_only:
            current_platform = update_nix.get_current_nix_platform()
            if set(self.config.hash_build_platforms) != {current_platform}:
                context.hashes_fully_computed = False

        computed = await self._compute_platform_hashes(
            info,
            source_override=source_override,
            emit=emit,
        )
        if not computed.fully_computed:
            context.hashes_fully_computed = False
        platform_hashes = computed.hashes
        return [
            HashEntry.create(self.hash_type, hash_val, platform=platform)
            for platform, hash_val in sorted(platform_hashes.items())
        ]


class DenoManifestUpdater(FlakeInputUpdater):
    """Updater for Deno packages built with ``mkDenoApplication``."""

    lock_file: str = "deno.lock"
    manifest_file: str = "deno-deps.json"
    required_tools: ClassVar[tuple[str, ...]] = ()
    materialize_when_current: ClassVar[bool] = True

    @classmethod
    def get_generated_artifact_files(cls) -> tuple[str, ...]:
        """Return this updater's configurable Deno manifest path."""
        return (cls.manifest_file,)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a source entry carrying the backing input identity."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self._input,
            commit=info.commit,
            pins=self.source_pins_for(info),
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Resolve ``deno.lock`` and emit the generated manifest artifact."""
        _ = context
        node = self._resolve_flake_node(info)
        locked = node.locked
        if locked is None or not locked.owner or not locked.repo or not locked.rev:
            msg = f"Cannot resolve source for {self._input}: incomplete lock"
            raise RuntimeError(msg)

        lock_url = (
            f"https://raw.githubusercontent.com/"
            f"{locked.owner}/{locked.repo}/{locked.rev}/{self.lock_file}"
        )
        await emit(
            UpdateEvent.status(
                self.name,
                f"Fetching {self.lock_file} from {locked.owner}/{locked.repo}...",
                operation="compute_hash",
                status=StatusInfo(kind=StatusKind.COMPUTING_HASH, value=self.lock_file),
            )
        )
        lock_bytes = await update_net.fetch_url(
            session,
            lock_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".lock", delete=False) as tmp:
            tmp.write(lock_bytes.decode())
            tmp_name = tmp.name

        try:
            await emit(
                UpdateEvent.status(
                    self.name,
                    "Resolving Deno dependencies...",
                    operation="compute_hash",
                )
            )
            manifest = await deno_lock.resolve_deno_deps(Path(tmp_name))
        finally:
            with suppress(OSError):
                await asyncio.to_thread(Path(tmp_name).unlink, missing_ok=True)

        pkg_dir = update_paths.updater_dir_for(self.name)
        if pkg_dir is None:
            msg = f"Package directory not found for {self.name}"
            raise RuntimeError(msg)
        manifest_path = pkg_dir / self.manifest_file
        await emit(
            UpdateEvent.artifact(
                self.name,
                GeneratedArtifact.json(manifest_path, manifest.to_dict()),
            )
        )

        total_files = sum(len(p.files) for p in manifest.jsr_packages)
        await emit(
            UpdateEvent.status(
                self.name,
                f"Prepared {manifest_path.name}: "
                f"{len(manifest.jsr_packages)} JSR ({total_files} files) + "
                f"{len(manifest.npm_packages)} npm packages",
                operation="compute_hash",
            )
        )

        empty_entries: list[HashEntry] = []
        return empty_entries


class UvLockUpdater(FlakeInputUpdater):
    """Updater for checked-in ``uv.lock`` artifacts consumed by ``mkUv2nixPackage``."""

    lock_file: str = "uv.lock"
    lock_env: ClassVar[dict[str, str]] = {}
    required_tools: ClassVar[tuple[str, ...]] = ("nix", "uv")
    materialize_when_current: ClassVar[bool] = True

    @classmethod
    def get_generated_artifact_files(cls) -> tuple[str, ...]:
        """Return this updater's configurable uv lock path."""
        return (cls.lock_file,)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a source entry tied to the updater's flake input."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self._input,
            commit=info.commit,
            pins=self.source_pins_for(info),
        )

    def _render_lock_env(self, info: VersionInfo) -> dict[str, str]:
        return {
            key: value.format(version=info.version)
            for key, value in self.lock_env.items()
        }

    async def _resolve_source_path(
        self, node: FlakeLockNode, *, emit: EventSink = ignore_event
    ) -> Path:
        source_path_expr = flake_source_path_expr(node)
        source_path_result = await update_process.run_command(
            ["nix", "eval", "--impure", "--raw", "--expr", source_path_expr],
            options=update_process.RunCommandOptions(
                source=self.name,
                config=self.config,
            ),
            emit=emit,
        )
        _raise_failed_command("nix eval", source_path_result)
        resolved_path = source_path_result.stdout.strip()
        if not resolved_path:
            msg = f"Failed to resolve source path for {self._input}"
            raise RuntimeError(msg)
        return Path(resolved_path)

    async def _copy_workspace(self, source_path: Path, workspace_dir: Path) -> None:
        await asyncio.to_thread(
            shutil.copytree,
            source_path,
            workspace_dir,
            symlinks=True,
        )
        await asyncio.to_thread(_ensure_user_writable_tree, workspace_dir)

    async def _run_uv_lock(
        self,
        *,
        info: VersionInfo,
        home_dir: Path,
        workspace_dir: Path,
        emit: EventSink = ignore_event,
    ) -> Path:
        uv_result = await update_process.run_command(
            ["uv", "-q", "lock", "--directory", str(workspace_dir)],
            options=update_process.RunCommandOptions(
                source=self.name,
                env={
                    "HOME": str(home_dir),
                    "UV_PYTHON": sys.executable,
                    **self._render_lock_env(info),
                },
                config=self.config,
            ),
            emit=emit,
        )
        _raise_failed_command("uv lock", uv_result)
        return workspace_dir / self.lock_file

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Materialize ``uv.lock`` and emit it as a generated artifact."""
        _ = (session, context)
        node = self._resolve_flake_node(info)
        locked = node.locked
        if locked is None or not locked.owner or not locked.repo or not locked.rev:
            msg = f"Cannot resolve source for {self._input}: incomplete lock"
            raise RuntimeError(msg)

        await emit(
            UpdateEvent.status(
                self.name,
                f"Resolving source tree for {locked.owner}/{locked.repo}...",
                operation="compute_hash",
                status=StatusInfo(kind=StatusKind.COMPUTING_HASH, value=self.lock_file),
            )
        )
        source_path = await self._resolve_source_path(node, emit=emit)

        pkg_dir = update_paths.updater_dir_for(self.name)
        if pkg_dir is None:
            msg = f"Package directory not found for {self.name}"
            raise RuntimeError(msg)
        lock_path = pkg_dir / self.lock_file

        with tempfile.TemporaryDirectory(prefix=f"{self.name}-uv-lock-") as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            home_dir = tmpdir / ".home"
            workspace_dir = tmpdir / "workspace"
            home_dir.mkdir()

            await emit(
                UpdateEvent.status(
                    self.name,
                    "Copying source tree for lock resolution...",
                    operation="compute_hash",
                )
            )
            await self._copy_workspace(source_path, workspace_dir)
            resolved_lock_path = await self._run_uv_lock(
                info=info, home_dir=home_dir, workspace_dir=workspace_dir, emit=emit
            )
            lock_text = await asyncio.to_thread(
                resolved_lock_path.read_text,
                encoding="utf-8",
            )

        await emit(
            UpdateEvent.artifact(
                self.name,
                GeneratedArtifact.text(lock_path, lock_text),
            )
        )
        await emit(
            UpdateEvent.status(
                self.name,
                f"Prepared {lock_path.name}",
                operation="compute_hash",
            )
        )

        empty_entries: list[HashEntry] = []
        return empty_entries


class GoVendorHashUpdater(FlakeInputHashUpdater):
    """Flake-input updater refreshing a Go ``vendorHash``."""

    hash_type: HashType = "vendorHash"


class CargoVendorHashUpdater(FlakeInputHashUpdater):
    """Flake-input updater refreshing a Rust ``cargoHash``."""

    hash_type: HashType = "cargoHash"


class NpmDepsHashUpdater(FlakeInputHashUpdater):
    """Flake-input updater refreshing an npm ``npmDepsHash``."""

    hash_type: HashType = "npmDepsHash"


class BunNodeModulesHashUpdater(FlakeInputHashUpdater):
    """Flake-input updater refreshing a per-platform Bun ``nodeModulesHash``."""

    hash_type: HashType = "nodeModulesHash"
    platform_specific: bool = True


__all__ = [
    "BunNodeModulesHashUpdater",
    "CargoVendorHashUpdater",
    "DenoDepsHashUpdater",
    "DenoManifestUpdater",
    "FlakeInputHashUpdater",
    "FlakeInputMetadataUpdater",
    "FlakeInputUpdater",
    "GoVendorHashUpdater",
    "NpmDepsHashUpdater",
    "UvLockUpdater",
]
