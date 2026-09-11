"""Flake-backed updater implementations and materializers."""

import asyncio
import hashlib
import json
import os
import shutil
import sys
import tempfile
from contextlib import suppress
from dataclasses import replace
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
from lib.update.nix import _build_overlay_attr_expr, _build_package_path_attr_expr
from lib.update.platform_hashes import (
    PlatformHashFailure,
    platform_hash_failure_status,
    require_complete_platform_hashes,
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
    """Probe and certify a fixed-output dependency backed by flake inputs.

    ``hash_attr_path`` must select the dependency that owns ``hash_type``;
    selecting a consumer package would make a nested mismatch ambiguous.
    """

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
        if self.platform_specific:
            current_platform = update_nix.get_current_nix_platform()
            if self._has_partial_platform_scope(current_platform):
                return False
            existing_hashes = self._existing_platform_hashes(context)
            for platform in self._platform_targets(current_platform):
                if not self._hash_is_usable(existing_hashes.get(platform)):
                    return False
        elif not any(
            entry.hash_type == self.hash_type
            and entry.platform is None
            and self._hash_is_usable(entry.hash)
            for entry in current.hashes.entries or ()
        ):
            return False
        try:
            await self._prepare_hash_probes(expected, context=context)
        except RuntimeError:
            return False
        return current.drv_hash == context.drv_fingerprint

    def _hash_is_usable(self, value: str | None) -> bool:
        return bool(
            value
            and value != self.config.fake_hash
            and not value.startswith(HashCollection.FAKE_HASH_PREFIX)
        )

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
            result = result.model_copy(
                update={
                    "drv_hash": current_drv_hash,
                    "platform_drv_hashes": context.drv_fingerprints or None,
                }
            )
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
                await self._prepare_hash_probes(result, context=context)
                drv_hash = context.drv_fingerprint
            result = result.model_copy(
                update={
                    "drv_hash": drv_hash,
                    "platform_drv_hashes": context.drv_fingerprints or None,
                }
            )
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
        return self._full_platform_targets(current_platform)

    def _full_platform_targets(self, current_platform: str) -> tuple[str, ...]:
        targets = [current_platform]
        for platform in self.config.hash_build_platforms:
            if platform not in targets:
                targets.append(platform)

        if self.supported_platforms is not None:
            supported = set(self.supported_platforms)
            targets = [platform for platform in targets if platform in supported]

        return tuple(targets)

    def _has_partial_platform_scope(self, current_platform: str) -> bool:
        return self.native_only and set(
            self._full_platform_targets(current_platform)
        ) != {current_platform}

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
        self,
        info: VersionInfo,
        *,
        system: str | None,
        context: UpdateContext | None = None,
        emit: EventSink = ignore_event,
    ) -> str:
        candidate = self.build_result(info, [])
        if context is not None:
            await self._prepare_hash_probes(candidate, context=context, emit=emit)
            return await update_nix.compute_fixed_output_hash(
                self.name,
                context.prepared_probes[system or ""],
                config=self.config,
                emit=emit,
            )
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
        return await self._prepare_hash_probes(
            source_override or SourceEntry(hashes={}),
            context=UpdateContext(current=None),
        )

    def _fingerprint_source(self, source: SourceEntry) -> SourceEntry | None:
        if source.pins is None and source.electron_version is None:
            return None
        return source.model_copy(
            update={
                "drv_hash": None,
                "platform_drv_hashes": None,
                "hashes": HashCollection(
                    entries=[
                        entry
                        for entry in source.hashes.entries or ()
                        if entry.hash_type != self.hash_type
                    ]
                ),
            }
        )

    def _probe_expressions(self, source: SourceEntry) -> dict[str, str]:
        override = self._fingerprint_source(source)
        targets = (
            self._platform_targets(update_nix.get_current_nix_platform())
            if self.platform_specific
            else ("",)
        )
        expressions = {}
        for target in targets:
            expression = self._package_hash_expr(
                system=target or None, source_override=override
            )
            if expression is None:
                expression = _build_overlay_attr_expr(
                    self.name,
                    self.hash_attr_path,
                    system=target or None,
                    source_overrides={self.name: override}
                    if override is not None
                    else None,
                    fake_hashes=True if override is not None else None,
                )
            expressions[target] = expression
        return expressions

    async def _prepare_hash_probes(
        self,
        source: SourceEntry,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> str:
        expressions = self._probe_expressions(source)
        if {
            key: probe.expression for key, probe in context.prepared_probes.items()
        } != expressions:
            context.prepared_probes = await update_nix.prepare_fixed_output_probes(
                self.name, expressions, config=self.config, emit=emit
            )
        fingerprints = {
            key: probe.fingerprint for key, probe in context.prepared_probes.items()
        }
        context.drv_fingerprints = fingerprints if self.platform_specific else {}
        if len(fingerprints) == 1:
            context.drv_fingerprint = next(iter(fingerprints.values()))
        else:
            # Scope is part of the certificate; a native-only snapshot cannot
            # certify the full platform matrix.
            encoded = json.dumps(fingerprints, sort_keys=True, separators=(",", ":"))
            context.drv_fingerprint = (
                f"platforms-v1:{hashlib.sha256(encoded.encode()).hexdigest()}"
            )
        return context.drv_fingerprint

    async def _compute_hash(
        self,
        info: VersionInfo,
        *,
        context: UpdateContext | None = None,
        emit: EventSink = ignore_event,
    ) -> str:
        system = (
            update_nix.get_current_nix_platform() if self.platform_specific else None
        )
        return await self._compute_hash_for_system(
            info, system=system, context=context, emit=emit
        )

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
            if self._has_partial_platform_scope(current_platform):
                context.hashes_fully_computed = False
            platform_hashes: dict[str, str] = {}
            failed_platforms: list[PlatformHashFailure] = []
            await self._prepare_hash_probes(
                self.build_result(info, []), context=context, emit=emit
            )
            existing_hashes = self._existing_platform_hashes(context)
            certificates = (
                context.current.platform_drv_hashes or {} if context.current else {}
            )

            async def probe(platform: str) -> str:
                existing_hash = existing_hashes.get(platform)
                if (
                    existing_hash is not None
                    and certificates.get(platform)
                    == context.drv_fingerprints.get(platform)
                    and self._hash_is_usable(existing_hash)
                ):
                    return existing_hash
                return await update_nix.compute_fixed_output_hash(
                    self.name,
                    context.prepared_probes[platform],
                    config=self.config,
                    emit=emit,
                )

            async def foreign_probe(platform: str) -> str | PlatformHashFailure:
                try:
                    return await probe(platform)
                except RuntimeError as exc:
                    failure = PlatformHashFailure(platform, str(exc))
                    await emit(platform_hash_failure_status(self.name, failure))
                    return failure

            # Retain the native failure boundary before starting remote builds.
            # Every child uses an already prepared store path, so it never reads
            # temporary workspace artifacts or acquires the parent's workspace lock.
            platform_hashes[current_platform] = await probe(current_platform)
            async with asyncio.TaskGroup() as group:
                tasks = {
                    platform: group.create_task(foreign_probe(platform))
                    for platform in self._platform_targets(current_platform)
                    if platform != current_platform
                }
            for platform, task in tasks.items():
                result = task.result()
                if isinstance(result, PlatformHashFailure):
                    failed_platforms.append(result)
                else:
                    platform_hashes[platform] = result

            require_complete_platform_hashes(self.name, failed_platforms)

            return [
                HashEntry.create(self.hash_type, hash_val, platform=platform)
                for platform, hash_val in sorted(platform_hashes.items())
            ]
        return [
            HashEntry.create(
                self.hash_type,
                await self._compute_hash(info, context=context, emit=emit),
            )
        ]


class DenoDepsHashUpdater(FlakeInputHashUpdater):
    """Hash updater for per-platform Deno dependency derivations."""

    hash_type: HashType = "denoDepsHash"
    platform_specific: bool = True
    native_only: bool = False

    async def _compute_platform_hashes(
        self,
        info: VersionInfo,
        *,
        source_override: SourceEntry | None = None,
        emit: EventSink = ignore_event,
    ) -> dict[str, str]:
        _ = info
        config = replace(
            self.config,
            hash_build_platforms=self._full_platform_targets(
                update_nix.get_current_nix_platform()
            ),
        )
        if source_override is None:
            return await update_nix_deno.compute_deno_deps_hash(
                self.name,
                self._input,
                native_only=self.native_only,
                config=config,
                emit=emit,
            )
        return await update_nix_deno.compute_deno_deps_hash(
            self.name,
            self._input,
            native_only=self.native_only,
            config=config,
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
            if self._has_partial_platform_scope(current_platform):
                context.hashes_fully_computed = False

        platform_hashes = await self._compute_platform_hashes(
            info,
            source_override=source_override,
            emit=emit,
        )
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
    hash_attr_path = ".goModules"


class CargoVendorHashUpdater(FlakeInputHashUpdater):
    """Flake-input updater refreshing a Rust ``cargoHash``."""

    hash_type: HashType = "cargoHash"
    hash_attr_path = ".cargoDeps"


class NpmDepsHashUpdater(FlakeInputHashUpdater):
    """Flake-input updater refreshing an npm ``npmDepsHash``."""

    hash_type: HashType = "npmDepsHash"
    hash_attr_path = ".npmDeps"


class BunNodeModulesHashUpdater(FlakeInputHashUpdater):
    """Flake-input updater refreshing a per-platform Bun ``nodeModulesHash``."""

    hash_type: HashType = "nodeModulesHash"
    hash_attr_path = ".node_modules"
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
