"""Core updater abstractions and shared non-flake implementations."""

from __future__ import annotations

import asyncio
import inspect
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import (
    HashCollection,
    HashEntry,
    HashMapping,
    HashType,
    SourceEntry,
    SourceHashes,
)
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

if TYPE_CHECKING:
    from collections.abc import Callable

    import aiohttp

    from lib.update.events import EventStream
    from lib.update.updaters.metadata import VersionInfo

from lib.update.updaters._base_proxy import base_module as _base_module


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
    signature = inspect.signature(func)
    bound_kwargs = dict(kwargs)
    if "context" in signature.parameters:
        bound_kwargs["context"] = context
    if "info" not in signature.parameters:
        bound_kwargs.pop("info", None)
    return func(*args, **bound_kwargs)


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
    shows_materialize_artifacts_phase: ClassVar[bool] = False
    generated_artifact_files: ClassVar[tuple[str, ...]] = ()
    # Optional tuple of Nix system strings (for example ``"aarch64-darwin"``)
    # this updater may run on. ``None`` means "all platforms" (the default).
    # When set, ``update_stream`` short-circuits on other platforms before
    # hitting the network or the Nix store; per-platform compute-hashes CI
    # runners can use this to skip packages whose system constraints in
    # ``packages/registry.nix`` exclude them, or whose remote source registry
    # rejects the matrix runner's IP (as crates.io currently does for some
    # GitHub Actions Linux runners).
    supported_platforms: ClassVar[tuple[str, ...] | None] = None

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
        """Attach additional metadata to *result* before the equality check."""
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
        if self.supported_platforms is not None:
            current_platform = _base_module().get_current_nix_platform()
            if current_platform not in self.supported_platforms:
                yield UpdateEvent.status(
                    self.name,
                    f"Unsupported platform {current_platform}, skipping update",
                    operation="check_version",
                    status="unsupported_platform",
                    detail=current_platform,
                )
                yield UpdateEvent.result(self.name)
                return
        context = UpdateContext(current=current)
        if pinned_version is not None:
            yield UpdateEvent.status(
                self.name,
                f"Using pinned version: {pinned_version.version}",
                operation="check_version",
                status="pinned_version",
                detail=pinned_version.version,
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
                status="latest_version",
                detail=info.version,
            )
        is_latest = await self._is_latest(context, info)
        if is_latest and not self.materialize_when_current:
            yield UpdateEvent.status(
                self.name,
                f"Up to date (version: {info.version})",
                operation="check_version",
                status="up_to_date",
                detail={"scope": "version", "value": info.version},
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
            status="fetching_hashes",
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
                status="up_to_date",
                detail={"scope": "hash"},
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
            platform: _base_module().convert_nix_hash_to_sri(self.name, hex_hash)
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
            payload = await _base_module().fetch_url(
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
            _base_module().compute_url_hashes(self.name, platform_urls.values()),
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


__all__ = [
    "CargoLockGitDep",
    "ChecksumProvidedUpdater",
    "DownloadHashUpdater",
    "HashEntryUpdater",
    "UpdateContext",
    "Updater",
    "_call_with_optional_context",
    "_coerce_context",
    "_emit_single_hash_entry",
    "_ensure_str_mapping",
    "_verify_platform_versions",
]
