"""Base updater abstractions and shared updater implementations."""

from __future__ import annotations

import asyncio
import inspect
import tempfile
from abc import ABC, abstractmethod
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    import aiohttp

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
from lib.update import paths as update_paths
from lib.update import process as update_process
from lib.update.artifacts import GeneratedArtifact
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.events import (
    EventStream,
    GatheredValues,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    expect_source_entry,
    expect_source_hashes,
    expect_str,
    gather_event_streams,
    require_value,
)
from lib.update.flake import get_flake_input_node, get_flake_input_version
from lib.update.net import fetch_url
from lib.update.nix import (
    compute_drv_fingerprint,
    compute_overlay_hash,
    get_current_nix_platform,
)
from lib.update.nix_deno import compute_deno_deps_hash
from lib.update.updaters.metadata import FlakeInputMetadata, VersionInfo
from lib.update.updaters.registry import UPDATERS, register_updater


def _package_dir_for(name: str) -> Path | None:
    return update_paths.package_dir_for(name)


def _compute_url_hashes(source_name: str, urls: Iterable[str]) -> EventStream:
    return update_process.compute_url_hashes(source_name, urls)


def _convert_nix_hash_to_sri(source_name: str, nix_hash: str) -> EventStream:
    return update_process.convert_nix_hash_to_sri(source_name, nix_hash)


package_dir_for: Callable[[str], Path | None] = _package_dir_for
compute_url_hashes: Callable[[str, Iterable[str]], EventStream] = _compute_url_hashes
convert_nix_hash_to_sri: Callable[[str, str], EventStream] = _convert_nix_hash_to_sri


def _updater_sourcefile(cls: type[Updater]) -> str | None:
    try:
        return inspect.getsourcefile(cls)
    except OSError, TypeError:
        module = inspect.getmodule(cls)
        module_file = getattr(module, "__file__", None)
        return module_file if isinstance(module_file, str) else None


@dataclass(frozen=True)
class CargoLockGitDep:
    """Cargo git dependency descriptor used for output hash collection."""

    git_dep: str
    hash_type: HashType
    match_name: str


def _verify_platform_versions(versions: dict[str, str], source_name: str) -> str:
    unique = set(versions.values())
    if len(unique) != 1:
        msg = f"{source_name} version mismatch across platforms: {versions}"
        raise RuntimeError(msg)
    return unique.pop()


def _ensure_str_mapping(values: object) -> dict[str, str]:
    """Return ``values`` as ``dict[str, str]`` or raise ``TypeError``."""
    if not isinstance(values, dict):
        msg = f"Expected dict for platform/hash mapping, got {type(values)}"
        raise TypeError(msg)

    result: dict[str, str] = {}
    for key, value in values.items():
        if not isinstance(key, str) or not isinstance(value, str):
            msg = "Expected platform/hash string mapping"
            raise TypeError(msg)
        result[key] = value
    return result


@dataclass(slots=True)
class UpdateContext:
    """Explicit per-run state shared across updater phases."""

    current: SourceEntry | None
    drv_fingerprint: str | None = None


def _coerce_context(context: UpdateContext | SourceEntry | None) -> UpdateContext:
    if isinstance(context, UpdateContext):
        return context
    return UpdateContext(current=context)


def _call_with_optional_context[T](
    func: Callable[..., T],
    *args: object,
    context: UpdateContext,
    **kwargs: object,
) -> T:
    try:
        return func(*args, context=context, **kwargs)
    except TypeError as exc:
        if "context" not in str(exc):
            raise
        try:
            return func(*args, **kwargs)
        except TypeError as inner_exc:
            if "info" not in str(inner_exc):
                raise
            trimmed = dict(kwargs)
            trimmed.pop("info", None)
            return func(*args, **trimmed)


async def _emit_single_hash_entry(
    source_name: str,
    events: EventStream,
    *,
    error: str,
    hash_type: HashType,
) -> EventStream:
    hash_drain = ValueDrain[str]()
    async for event in drain_value_events(events, hash_drain, parse=expect_str):
        yield event
    hash_value = require_value(hash_drain, error)
    yield UpdateEvent.value(
        source_name,
        [HashEntry.create(hash_type, hash_value)],
    )


class Updater(ABC):
    """Abstract base class for all update sources."""

    name: str
    config: UpdateConfig
    required_tools: ClassVar[tuple[str, ...]] = ("nix",)
    materialize_when_current: ClassVar[bool] = False

    def __init__(self, *, config: UpdateConfig | None = None) -> None:
        """Create an updater bound to active config values."""
        self.config = resolve_active_config(config)

    @abstractmethod
    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch latest upstream version details."""
        raise NotImplementedError

    @abstractmethod
    def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute source hashes for the fetched version."""
        raise NotImplementedError

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a :class:`SourceEntry` from version and hashes."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
        )

    def _build_result_with_urls(
        self,
        info: VersionInfo,
        hashes: SourceHashes,
        urls: dict[str, str],
        *,
        commit: str | None = None,
    ) -> SourceEntry:
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            urls=urls,
            commit=commit,
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        context = _coerce_context(context)
        current = context.current
        if current is None:
            return False
        if current.version != info.version:
            return False
        upstream_commit = info.commit
        if isinstance(upstream_commit, str) and current.commit:
            return current.commit == upstream_commit
        return True

    async def _finalize_result(
        self,
        result: SourceEntry,
        *,
        info: VersionInfo | None = None,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Attach additional metadata to *result* before the equality check.

        Subclasses can override this to attach additional metadata (e.g. a
        derivation fingerprint) to the result entry.  The implementation
        **must** yield the (possibly updated) result as a
        :class:`UpdateEvent.value` event so the caller can retrieve it.
        """
        _ = (info, context)
        yield UpdateEvent.value(self.name, result)

    async def update_stream(
        self,
        current: SourceEntry | None,
        session: aiohttp.ClientSession,
        *,
        pinned_version: VersionInfo | None = None,
    ) -> EventStream:
        """Run fetch/check/hash/update flow and emit update events."""
        context = UpdateContext(current=current)
        if pinned_version is not None:
            yield UpdateEvent.status(
                self.name,
                f"Using pinned version: {pinned_version.version}",
                operation="check_version",
            )
            info = pinned_version
        else:
            yield UpdateEvent.status(
                self.name,
                f"Fetching latest {self.name} version...",
                operation="check_version",
            )
            info = await self.fetch_latest(session)

        yield UpdateEvent.status(
            self.name,
            f"Latest version: {info.version}",
            operation="check_version",
        )
        is_latest = await self._is_latest(context, info)
        if is_latest and not self.materialize_when_current:
            yield UpdateEvent.status(
                self.name,
                f"Up to date (version: {info.version})",
                operation="check_version",
            )
            yield UpdateEvent.result(self.name)
            return
        if is_latest and self.materialize_when_current:
            yield UpdateEvent.status(
                self.name,
                "Version up to date; refreshing generated artifacts...",
                operation="compute_hash",
            )

        yield UpdateEvent.status(
            self.name,
            "Fetching hashes for all platforms...",
            operation="compute_hash",
        )
        hashes_drain = ValueDrain[SourceHashes]()
        async for event in drain_value_events(
            _call_with_optional_context(
                self.fetch_hashes,
                info,
                session,
                context=context,
            ),
            hashes_drain,
            parse=expect_source_hashes,
        ):
            yield event
        hashes = require_value(hashes_drain, "Missing hash output")
        result = self.build_result(info, hashes)

        result_drain = ValueDrain[SourceEntry]()
        async for event in drain_value_events(
            _call_with_optional_context(
                self._finalize_result,
                result,
                info=info,
                context=context,
            ),
            result_drain,
            parse=expect_source_entry,
        ):
            yield event
        result = require_value(result_drain, "Missing finalized result")

        if context.current is not None and result == context.current:
            unchanged_message = (
                "Source metadata unchanged"
                if self.materialize_when_current
                else "Up to date"
            )
            yield UpdateEvent.status(
                self.name,
                unchanged_message,
                operation="compute_hash",
            )
            yield UpdateEvent.result(self.name)
            return
        yield UpdateEvent.result(self.name, result)


class ChecksumProvidedUpdater(Updater):
    """Updater that receives checksums from an upstream API."""

    PLATFORMS: ClassVar[dict[str, str]]

    @abstractmethod
    async def fetch_checksums(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> dict[str, str]:
        """Fetch hex checksums keyed by platform."""
        raise NotImplementedError

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Convert fetched hex checksums to SRI hashes."""
        _ = _coerce_context(context)
        checksums = await self.fetch_checksums(info, session)
        streams = {
            platform: convert_nix_hash_to_sri(self.name, hex_hash)
            for platform, hex_hash in checksums.items()
        }
        async for item in gather_event_streams(streams):
            if isinstance(item, GatheredValues):
                yield UpdateEvent.value(self.name, _ensure_str_mapping(item.values))
            else:
                yield item

    async def _fetch_checksums_from_urls(
        self,
        session: aiohttp.ClientSession,
        checksum_urls: dict[str, str],
        *,
        parser: Callable[[bytes, str], str] | None = None,
    ) -> dict[str, str]:
        """Fetch per-platform checksums from sidecar URLs."""

        async def _fetch_one(platform: str, checksum_url: str) -> tuple[str, str]:
            payload = await fetch_url(
                session,
                checksum_url,
                request_timeout=self.config.default_timeout,
                config=self.config,
            )
            checksum = (
                parser(payload, checksum_url)
                if parser is not None
                else payload.decode().strip()
            )
            if not checksum:
                msg = f"Empty checksum payload from {checksum_url}"
                raise RuntimeError(msg)
            return platform, checksum

        results = await asyncio.gather(
            *(_fetch_one(platform, url) for platform, url in checksum_urls.items()),
        )
        return dict(results)


class DownloadHashUpdater(Updater):
    """Updater that computes hashes from downloadable platform artifacts."""

    PLATFORMS: ClassVar[dict[str, str]]
    BASE_URL: ClassVar[str] = ""
    required_tools: ClassVar[tuple[str, ...]] = ("nix", "nix-prefetch-url")

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return artifact URL for ``platform``."""
        _ = info
        if self.BASE_URL:
            return f"{self.BASE_URL}/{self.PLATFORMS[platform]}"
        return self.PLATFORMS[platform]

    def _platform_urls(self, info: VersionInfo) -> dict[str, str]:
        """Build per-platform URL mapping."""
        return {
            platform: self.get_download_url(platform, info)
            for platform in self.PLATFORMS
        }

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a result including generated platform URLs."""
        urls = self._platform_urls(info)
        return self._build_result_with_urls(info, hashes, urls)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute platform hashes from prefetched artifact URLs."""
        _ = (session, _coerce_context(context))
        platform_urls = self._platform_urls(info)
        hashes_drain = ValueDrain[HashMapping]()
        async for event in drain_value_events(
            compute_url_hashes(self.name, platform_urls.values()),
            hashes_drain,
            parse=expect_hash_mapping,
        ):
            yield event
        hashes_by_url = require_value(hashes_drain, "Missing hash output")

        hashes: dict[str, str] = {
            platform: hashes_by_url[platform_urls[platform]]
            for platform in self.PLATFORMS
        }
        yield UpdateEvent.value(self.name, hashes)


class HashEntryUpdater(Updater):
    """Updater that emits structured :class:`HashEntry` values."""

    input_name: str | None = None
    required_tools: ClassVar[tuple[str, ...]] = ("nix", "nix-prefetch-url")

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a source entry containing hash entries and optional input."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            input=self.input_name,
        )

    async def _emit_single_hash_entry(
        self,
        events: EventStream,
        *,
        error: str,
        hash_type: HashType,
    ) -> EventStream:
        async for event in _emit_single_hash_entry(
            self.name,
            events,
            error=error,
            hash_type=hash_type,
        ):
            yield event


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
                return get_flake_input_node(self._input)
            if isinstance(node, FlakeLockNode):
                return node
            msg = f"Expected flake lock node in metadata, got {type(node)}"
            raise TypeError(msg)
        return get_flake_input_node(self._input)

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Resolve the latest version from the flake lock node."""
        _ = session
        node = get_flake_input_node(self._input)
        version = get_flake_input_version(node)
        commit = node.locked.rev if node.locked is not None else None
        return VersionInfo(
            version=version,
            metadata=FlakeInputMetadata(node=node, commit=commit),
        )


class FlakeInputHashUpdater(FlakeInputUpdater):
    """Base updater for hash-only sources backed by flake inputs.

    Uses derivation fingerprinting for maximally precise staleness detection.
    Instead of comparing version strings (which miss nixpkgs bumps, toolchain
    changes, and build-script edits), we evaluate the package with a sentinel
    hash and compare the resulting ``.drv`` path.  Since the sentinel is
    constant, the ``.drv`` hash is a pure function of the entire transitive
    build-input closure — equivalent to Nix's own rebuild detection.

    Simple subclasses only need to set ``hash_type`` (and optionally
    ``platform_specific = True`` for platform-keyed hashes like Bun).  The
    default ``_compute_hash`` calls :func:`compute_overlay_hash` directly.
    """

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
        """Check staleness via derivation fingerprint comparison.

        The source version must match first.  Even when derivation inputs are
        unchanged, a stale version field in ``sources.json`` should trigger a
        refresh so merged CI artifacts agree on scalar metadata.

        Computes the ``.drv`` hash with ``FAKE_HASHES=1`` and compares it to
        the stored ``drvHash`` in ``sources.json``.  Returns ``True`` only
        when the fingerprint matches exactly — meaning no build input in the
        entire transitive closure has changed.
        """
        context = _coerce_context(context)
        current = context.current
        if current is None:
            return False
        if current.version != info.version:
            return False
        if current.drv_hash is None:
            return False
        try:
            new_fingerprint = await compute_drv_fingerprint(
                self.name, config=self.config
            )
        except RuntimeError:
            # If fingerprint computation fails, conservatively recompute.
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
        """Attach a derivation fingerprint to the result entry."""
        _ = info
        context = _coerce_context(context)
        yield UpdateEvent.status(
            self.name,
            "Computing derivation fingerprint...",
            operation="compute_hash",
        )
        try:
            drv_hash = context.drv_fingerprint
            if drv_hash is None:
                drv_hash = await compute_drv_fingerprint(self.name, config=self.config)
            result = result.model_copy(update={"drv_hash": drv_hash})
        except RuntimeError as exc:
            yield UpdateEvent.status(
                self.name,
                f"Warning: derivation fingerprint unavailable ({exc})",
                operation="compute_hash",
            )
        yield UpdateEvent.value(self.name, result)

    def _platform_targets(self, current_platform: str) -> tuple[str, ...]:
        """Return platform targets for platform-specific hash computation."""
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
        """Return existing platform hashes for ``self.hash_type`` when present."""
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
        """Compute the FOD hash for ``system`` (or host when ``None``)."""
        _ = info
        return compute_overlay_hash(self.name, system=system, config=self.config)

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        """Return an event stream that yields the computed hash value.

        The default implementation builds the overlay package with
        ``FAKE_HASHES=1`` and extracts the hash from the mismatch error.
        When ``platform_specific`` is ``True``, the build is pinned to the
        current Nix platform.
        """
        system = get_current_nix_platform() if self.platform_specific else None
        return self._compute_hash_for_system(info, system=system)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute FOD hashes for the package overlay build."""
        context = _coerce_context(context)
        _ = session
        if self.platform_specific:
            current_platform = get_current_nix_platform()
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
                        parse=expect_str,
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
    """Hash updater for per-platform Deno dependency derivations.

    .. deprecated::
        Use :class:`DenoManifestUpdater` for packages built with
        ``mkDenoApplication`` (deterministic individual ``fetchurl`` calls).
        This class only applies to the legacy FOD-based Deno builder.
    """

    hash_type = "denoDepsHash"
    native_only: bool = False

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        _ = info
        return compute_deno_deps_hash(
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
        """Compute a single FOD hash entry for the Deno deps overlay."""
        _ = (session, _coerce_context(context))

        def _expect_platform_hashes(payload: object) -> HashMapping:
            if isinstance(payload, dict):
                return expect_hash_mapping(payload)
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
    """Updater for Deno packages built with ``mkDenoApplication``.

    Instead of computing FOD hashes, this updater resolves the ``deno.lock``
    file from the flake input source and writes a deterministic
    ``deno-deps.json`` manifest that ``mkDenoApplication`` consumes to fetch
    each dependency individually via ``fetchurl``.

    No hash entries are stored in ``sources.json`` — only the version and
    flake input name.  The manifest file itself is emitted as a generated
    artifact and persisted by the update CLI alongside ``sources.json``.
    """

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
        """Resolve ``deno.lock`` and emit ``deno-deps.json`` as an artifact."""
        _ = _coerce_context(context)
        node = self._resolve_flake_node(info)

        # Download deno.lock from the GitHub source.
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
        )
        lock_bytes = await fetch_url(
            session,
            lock_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )

        # Write lock content to a temp file for the resolver.
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

        # Emit the manifest as a generated artifact for the CLI to persist.
        pkg_dir = package_dir_for(self.name)
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

        # No hash entries — mkDenoApplication uses the manifest directly.
        empty_entries: list[HashEntry] = []
        yield UpdateEvent.value(self.name, empty_entries)


FlakeInputMixin = FlakeInputUpdater

from lib.update.updaters.factories import (  # noqa: E402
    bun_node_modules_updater,
    cargo_vendor_updater,
    deno_deps_updater,
    deno_manifest_updater,
    flake_input_hash_updater,
    go_vendor_updater,
    npm_deps_updater,
    uv_lock_hash_updater,
)

__all__ = [
    "UPDATERS",
    "CargoLockGitDep",
    "ChecksumProvidedUpdater",
    "DenoDepsHashUpdater",
    "DenoManifestUpdater",
    "DownloadHashUpdater",
    "FlakeInputHashUpdater",
    "FlakeInputMixin",
    "FlakeInputUpdater",
    "HashEntryUpdater",
    "UpdateConfig",
    "UpdateContext",
    "Updater",
    "VersionInfo",
    "_verify_platform_versions",
    "bun_node_modules_updater",
    "cargo_vendor_updater",
    "deno_deps_updater",
    "deno_manifest_updater",
    "flake_input_hash_updater",
    "go_vendor_updater",
    "npm_deps_updater",
    "register_updater",
    "uv_lock_hash_updater",
]
