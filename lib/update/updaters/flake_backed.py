"""Flake-backed updater implementations and materializers."""

from __future__ import annotations

import asyncio
import importlib
import os
import shutil
import sys
import tempfile
from contextlib import suppress
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, Protocol, cast

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import (
    HashCollection,
    HashEntry,
    HashMapping,
    HashType,
    SourceEntry,
    SourceHashes,
)
from lib.update import deno_lock
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import (
    CommandResult,
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_command_result,
    require_value,
)
from lib.update.flake import flake_fetch_expr
from lib.update.updaters.core import (
    UpdateContext,
    Updater,
    _coerce_context,
    _emit_single_hash_entry,
)
from lib.update.updaters.metadata import FlakeInputMetadata, VersionInfo

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import aiohttp

    from lib.update.config import UpdateConfig
    from lib.update.process import RunCommandOptions


class _UpdateProcessModule(Protocol):
    RunCommandOptions: type[RunCommandOptions]

    def run_command(
        self, args: list[str], *, options: RunCommandOptions
    ) -> EventStream: ...


class _BaseModule(Protocol):
    package_dir_for: Callable[[str], Path | None]
    update_process: _UpdateProcessModule

    def compute_deno_deps_hash(
        self,
        source: str,
        input_name: str,
        *,
        native_only: bool = False,
        config: UpdateConfig | None = None,
    ) -> EventStream: ...

    def compute_drv_fingerprint(
        self,
        source: str,
        *,
        system: str | None = None,
        config: UpdateConfig | None = None,
    ) -> Awaitable[str]: ...

    def compute_overlay_hash(
        self,
        source: str,
        *,
        system: str | None = None,
        config: UpdateConfig | None = None,
    ) -> EventStream: ...

    def expect_hash_mapping(self, payload: object) -> HashMapping: ...

    def expect_str(self, payload: object) -> str: ...

    def fetch_url(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        user_agent: str | None = None,
        request_timeout: float | None = None,
        config: UpdateConfig | None = None,
        **kwargs: object,
    ) -> Awaitable[bytes]: ...

    def get_current_nix_platform(self) -> str: ...

    def get_flake_input_node(self, input_name: str) -> FlakeLockNode: ...

    def get_flake_input_version(self, node: FlakeLockNode) -> str: ...


def _base_module() -> _BaseModule:
    return cast("_BaseModule", importlib.import_module("lib.update.updaters.base"))


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
        metadata = info.metadata
        if isinstance(metadata, FlakeInputMetadata):
            return metadata.node
        if isinstance(metadata, dict):
            metadata_map = cast("dict[str, object]", metadata)
            node = metadata_map.get("node")
            if node is None:
                return _base_module().get_flake_input_node(self._input)
            if isinstance(node, FlakeLockNode):
                return node
            msg = f"Expected flake lock node in metadata, got {type(node)}"
            raise TypeError(msg)
        return _base_module().get_flake_input_node(self._input)

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Resolve the latest version from the flake lock node."""
        _ = session
        node = _base_module().get_flake_input_node(self._input)
        version = _base_module().get_flake_input_version(node)
        commit = node.locked.rev if node.locked is not None else None
        return VersionInfo(
            version=version,
            metadata=FlakeInputMetadata(node=node, commit=commit),
        )


class FlakeInputHashUpdater(FlakeInputUpdater):
    """Base updater for hash-only sources backed by flake inputs."""

    hash_type: HashType
    platform_specific: bool = False
    native_only: bool = False
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
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        context = _coerce_context(context)
        current = context.current
        if (
            current is None
            or current.version != info.version
            or current.drv_hash is None
        ):
            return False
        try:
            new_fingerprint = await _base_module().compute_drv_fingerprint(
                self.name,
                config=self.config,
            )
        except RuntimeError:
            return False
        context.drv_fingerprint = new_fingerprint
        return current.drv_hash == new_fingerprint

    async def _finalize_result(
        self,
        result: SourceEntry,
        *,
        info: VersionInfo | None = None,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        _ = info
        context = _coerce_context(context)
        yield UpdateEvent.status(
            self.name,
            "Computing derivation fingerprint...",
            operation="compute_hash",
            status="computing_hash",
            detail="derivation fingerprint",
        )
        try:
            drv_hash = context.drv_fingerprint
            if drv_hash is None:
                drv_hash = await _base_module().compute_drv_fingerprint(
                    self.name,
                    config=self.config,
                )
            result = result.model_copy(update={"drv_hash": drv_hash})
        except RuntimeError as exc:
            yield UpdateEvent.status(
                self.name,
                f"Warning: derivation fingerprint unavailable ({exc})",
                operation="compute_hash",
            )
        yield UpdateEvent.value(self.name, result)

    def _platform_targets(self, current_platform: str) -> tuple[str, ...]:
        if self.native_only:
            return (current_platform,)

        targets = [current_platform]
        for platform in self.config.hash_build_platforms:
            if platform not in targets:
                targets.append(platform)
        return tuple(targets)

    def _existing_platform_hashes(
        self,
        context: UpdateContext | SourceEntry | None = None,
    ) -> dict[str, str]:
        context = _coerce_context(context)
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

    def _compute_hash_for_system(
        self,
        info: VersionInfo,
        *,
        system: str | None,
    ) -> EventStream:
        _ = info
        return _base_module().compute_overlay_hash(
            self.name, system=system, config=self.config
        )

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        system = (
            _base_module().get_current_nix_platform()
            if self.platform_specific
            else None
        )
        return self._compute_hash_for_system(info, system=system)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute flake-backed hashes for one or more target platforms."""
        context = _coerce_context(context)
        _ = session
        if self.platform_specific:
            current_platform = _base_module().get_current_nix_platform()
            error = f"Missing {self.hash_type} output"
            platform_hashes: dict[str, str] = {}
            existing_hashes = self._existing_platform_hashes(context)
            failed_platforms: list[str] = []

            for platform in self._platform_targets(current_platform):
                hash_drain = ValueDrain[str]()
                try:
                    async for event in drain_value_events(
                        self._compute_hash_for_system(info, system=platform),
                        hash_drain,
                        parse=_base_module().expect_str,
                    ):
                        yield event
                except RuntimeError:
                    if platform == current_platform:
                        raise
                    failed_platforms.append(platform)
                    existing = existing_hashes.get(platform)
                    if existing is None:
                        yield UpdateEvent.status(
                            self.name,
                            f"Build failed for {platform}, no existing hash to preserve",
                            operation="compute_hash",
                        )
                        continue
                    platform_hashes[platform] = existing
                    yield UpdateEvent.status(
                        self.name,
                        f"Build failed for {platform}, preserving existing hash",
                        operation="compute_hash",
                    )
                    continue

                hash_value = require_value(hash_drain, error)
                platform_hashes[platform] = hash_value

            if failed_platforms:
                yield UpdateEvent.status(
                    self.name,
                    f"Warning: {len(failed_platforms)} platform(s) failed, "
                    f"preserved existing hashes: {', '.join(failed_platforms)}",
                    operation="compute_hash",
                )

            entries = [
                HashEntry.create(self.hash_type, hash_val, platform=platform)
                for platform, hash_val in sorted(platform_hashes.items())
            ]
            yield UpdateEvent.value(self.name, entries)
        else:
            async for event in _emit_single_hash_entry(
                self.name,
                self._compute_hash(info),
                error=f"Missing {self.hash_type} output",
                hash_type=self.hash_type,
            ):
                yield event


class DenoDepsHashUpdater(FlakeInputHashUpdater):
    """Hash updater for per-platform Deno dependency derivations."""

    hash_type = "denoDepsHash"
    native_only: bool = False

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        _ = info
        return _base_module().compute_deno_deps_hash(
            self.name,
            self._input,
            native_only=self.native_only,
            config=self.config,
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute structured Deno dependency hashes for all target platforms."""
        _ = (session, _coerce_context(context))

        def _expect_platform_hashes(payload: object) -> HashMapping:
            if isinstance(payload, dict):
                return _base_module().expect_hash_mapping(payload)
            msg = f"Expected dict of platform hashes, got {type(payload)}"
            raise TypeError(msg)

        error = f"Missing {self.hash_type} output"
        hash_drain = ValueDrain[HashMapping]()
        async for event in drain_value_events(
            self._compute_hash(info),
            hash_drain,
            parse=_expect_platform_hashes,
        ):
            yield event
        platform_hashes = require_value(hash_drain, error)
        if not isinstance(platform_hashes, dict):
            msg = f"Expected dict of platform hashes, got {type(platform_hashes)}"
            raise TypeError(msg)

        entries = [
            HashEntry.create(self.hash_type, hash_val, platform=platform)
            for platform, hash_val in sorted(platform_hashes.items())
        ]
        yield UpdateEvent.value(self.name, entries)


class DenoManifestUpdater(FlakeInputUpdater):
    """Updater for Deno packages built with ``mkDenoApplication``."""

    lock_file: str = "deno.lock"
    manifest_file: str = "deno-deps.json"
    required_tools: ClassVar[tuple[str, ...]] = ()
    materialize_when_current: ClassVar[bool] = True

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Resolve ``deno.lock`` and emit the generated manifest artifact."""
        _ = _coerce_context(context)
        node = self._resolve_flake_node(info)
        locked = node.locked
        if locked is None or not locked.owner or not locked.repo or not locked.rev:
            msg = f"Cannot resolve source for {self._input}: incomplete lock"
            raise RuntimeError(msg)

        lock_url = (
            f"https://raw.githubusercontent.com/"
            f"{locked.owner}/{locked.repo}/{locked.rev}/{self.lock_file}"
        )
        yield UpdateEvent.status(
            self.name,
            f"Fetching {self.lock_file} from {locked.owner}/{locked.repo}...",
            operation="compute_hash",
            status="computing_hash",
            detail=self.lock_file,
        )
        lock_bytes = await _base_module().fetch_url(
            session,
            lock_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )

        with tempfile.NamedTemporaryFile(mode="w", suffix=".lock", delete=False) as tmp:
            tmp.write(lock_bytes.decode())
            tmp_name = tmp.name

        try:
            yield UpdateEvent.status(
                self.name,
                "Resolving Deno dependencies...",
                operation="compute_hash",
            )
            manifest = await deno_lock.resolve_deno_deps(Path(tmp_name))
        finally:
            with suppress(OSError):
                await asyncio.to_thread(Path(tmp_name).unlink, missing_ok=True)

        pkg_dir = _base_module().package_dir_for(self.name)
        if pkg_dir is None:
            msg = f"Package directory not found for {self.name}"
            raise RuntimeError(msg)
        manifest_path = pkg_dir / self.manifest_file
        yield UpdateEvent.artifact(
            self.name,
            GeneratedArtifact.json(manifest_path, manifest.to_dict()),
        )

        total_files = sum(len(p.files) for p in manifest.jsr_packages)
        yield UpdateEvent.status(
            self.name,
            f"Prepared {manifest_path.name}: "
            f"{len(manifest.jsr_packages)} JSR ({total_files} files) + "
            f"{len(manifest.npm_packages)} npm packages",
            operation="compute_hash",
        )

        empty_entries: list[HashEntry] = []
        yield UpdateEvent.value(self.name, empty_entries)


class UvLockUpdater(FlakeInputUpdater):
    """Updater for checked-in ``uv.lock`` artifacts consumed by ``mkUv2nixPackage``."""

    lock_file: str = "uv.lock"
    lock_env: ClassVar[dict[str, str]] = {}
    required_tools: ClassVar[tuple[str, ...]] = ("nix", "uv")
    materialize_when_current: ClassVar[bool] = True

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a source entry tied to the updater's flake input."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self._input,
        )

    def _render_lock_env(self, info: VersionInfo) -> dict[str, str]:
        return {
            key: value.format(version=info.version)
            for key, value in self.lock_env.items()
        }

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Materialize ``uv.lock`` and emit it as a generated artifact."""
        _ = (session, _coerce_context(context))
        node = self._resolve_flake_node(info)
        locked = node.locked
        if locked is None or not locked.owner or not locked.repo or not locked.rev:
            msg = f"Cannot resolve source for {self._input}: incomplete lock"
            raise RuntimeError(msg)

        yield UpdateEvent.status(
            self.name,
            f"Resolving source tree for {locked.owner}/{locked.repo}...",
            operation="compute_hash",
            status="computing_hash",
            detail=self.lock_file,
        )
        source_path_expr = (
            f"let src = {flake_fetch_expr(node)}; "
            "in if builtins.isAttrs src then src.outPath else src"
        )
        source_path_drain = ValueDrain[CommandResult]()
        async for event in drain_value_events(
            _base_module().update_process.run_command(
                ["nix", "eval", "--impure", "--raw", "--expr", source_path_expr],
                options=_base_module().update_process.RunCommandOptions(
                    source=self.name,
                    error="nix eval did not return output",
                    config=self.config,
                ),
            ),
            source_path_drain,
            parse=expect_command_result,
        ):
            yield event
        source_path_result = require_value(
            source_path_drain,
            "Missing nix eval result for source path",
        )
        source_path = Path(source_path_result.stdout.strip())
        if not source_path_result.stdout.strip():
            msg = f"Failed to resolve source path for {self._input}"
            raise RuntimeError(msg)

        pkg_dir = _base_module().package_dir_for(self.name)
        if pkg_dir is None:
            msg = f"Package directory not found for {self.name}"
            raise RuntimeError(msg)
        lock_path = pkg_dir / self.lock_file

        with tempfile.TemporaryDirectory(prefix=f"{self.name}-uv-lock-") as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            home_dir = tmpdir / ".home"
            workspace_dir = tmpdir / "workspace"
            home_dir.mkdir()

            yield UpdateEvent.status(
                self.name,
                "Copying source tree for lock resolution...",
                operation="compute_hash",
            )
            await asyncio.to_thread(
                shutil.copytree,
                source_path,
                workspace_dir,
                symlinks=True,
            )
            await asyncio.to_thread(_ensure_user_writable_tree, workspace_dir)

            uv_result_drain = ValueDrain[CommandResult]()
            async for event in drain_value_events(
                _base_module().update_process.run_command(
                    ["uv", "-q", "lock", "--directory", str(workspace_dir)],
                    options=_base_module().update_process.RunCommandOptions(
                        source=self.name,
                        error="uv lock did not return output",
                        env={
                            "HOME": str(home_dir),
                            "UV_PYTHON": sys.executable,
                            **self._render_lock_env(info),
                        },
                        config=self.config,
                    ),
                ),
                uv_result_drain,
                parse=expect_command_result,
            ):
                yield event
            require_value(uv_result_drain, "Missing uv lock result")

            lock_text = await asyncio.to_thread(
                (workspace_dir / self.lock_file).read_text,
                encoding="utf-8",
            )

        yield UpdateEvent.artifact(
            self.name,
            GeneratedArtifact.text(lock_path, lock_text),
        )
        yield UpdateEvent.status(
            self.name,
            f"Prepared {lock_path.name}",
            operation="compute_hash",
        )

        empty_entries: list[HashEntry] = []
        yield UpdateEvent.value(self.name, empty_entries)


__all__ = [
    "DenoDepsHashUpdater",
    "DenoManifestUpdater",
    "FlakeInputHashUpdater",
    "FlakeInputUpdater",
    "UvLockUpdater",
]
