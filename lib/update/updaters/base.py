"""Base updater abstractions and shared updater implementations."""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, cast

if TYPE_CHECKING:
    from collections.abc import Callable

    import aiohttp

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
    gather_event_streams,
    require_value,
)
from lib.update.flake import get_flake_input_node, get_flake_input_version
from lib.update.net import fetch_url
from lib.update.nix import (
    compute_bun_node_modules_hash,
    compute_cargo_vendor_hash,
    compute_drv_fingerprint,
    compute_go_vendor_hash,
    compute_npm_deps_hash,
    get_current_nix_platform,
)
from lib.update.nix_deno import compute_deno_deps_hash
from lib.update.process import compute_url_hashes, convert_nix_hash_to_sri


@dataclass
class VersionInfo:
    """Latest upstream version metadata fetched by an updater."""

    version: str
    metadata: dict[str, Any]


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


UPDATERS: dict[str, type[Updater]] = {}


class Updater(ABC):
    """Abstract base class for all update sources."""

    name: str
    config: UpdateConfig
    required_tools: tuple[str, ...] = ("nix",)

    def __init_subclass__(cls, **kwargs: object) -> None:
        """Register concrete updater subclasses in :data:`UPDATERS`."""
        super().__init_subclass__(**kwargs)
        name = getattr(cls, "name", None)
        if name is not None and not getattr(cls, "__abstractmethods__", None):
            UPDATERS[name] = cls

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

    async def _is_latest(self, current: SourceEntry | None, info: VersionInfo) -> bool:
        if current is None:
            return False
        if current.version != info.version:
            return False
        upstream_commit = info.metadata.get("commit")
        if upstream_commit and current.commit:
            return current.commit == upstream_commit
        return True

    async def _finalize_result(self, result: SourceEntry) -> EventStream:
        """Attach additional metadata to *result* before the equality check.

        Subclasses can override this to attach additional metadata (e.g. a
        derivation fingerprint) to the result entry.  The implementation
        **must** yield the (possibly updated) result as a
        :class:`UpdateEvent.value` event so the caller can retrieve it.
        """
        yield UpdateEvent.value(self.name, result)

    async def update_stream(
        self,
        current: SourceEntry | None,
        session: aiohttp.ClientSession,
        *,
        pinned_version: VersionInfo | None = None,
    ) -> EventStream:
        """Run fetch/check/hash/update flow and emit update events."""
        if pinned_version is not None:
            yield UpdateEvent.status(
                self.name,
                f"Using pinned version: {pinned_version.version}",
            )
            info = pinned_version
        else:
            yield UpdateEvent.status(
                self.name, f"Fetching latest {self.name} version..."
            )
            info = await self.fetch_latest(session)

        yield UpdateEvent.status(self.name, f"Latest version: {info.version}")
        if await self._is_latest(current, info):
            yield UpdateEvent.status(self.name, f"Up to date (version: {info.version})")
            yield UpdateEvent.result(self.name)
            return

        yield UpdateEvent.status(self.name, "Fetching hashes for all platforms...")
        hashes_drain = ValueDrain[SourceHashes]()
        async for event in drain_value_events(
            self.fetch_hashes(info, session),
            hashes_drain,
        ):
            yield event
        hashes = require_value(hashes_drain, "Missing hash output")
        result = self.build_result(info, hashes)

        result_drain = ValueDrain[SourceEntry]()
        async for event in drain_value_events(
            self._finalize_result(result),
            result_drain,
        ):
            yield event
        result = require_value(result_drain, "Missing finalized result")

        if current is not None and result == current:
            yield UpdateEvent.status(self.name, "Up to date")
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
    ) -> EventStream:
        """Convert fetched hex checksums to SRI hashes."""
        checksums = await self.fetch_checksums(info, session)
        streams = {
            platform: convert_nix_hash_to_sri(self.name, hex_hash)
            for platform, hex_hash in checksums.items()
        }
        async for item in gather_event_streams(streams):
            if isinstance(item, GatheredValues):
                yield UpdateEvent.value(self.name, cast("dict[str, str]", item.values))
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
    BASE_URL: str = ""
    required_tools = ("nix", "nix-prefetch-url")

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return artifact URL for ``platform``."""
        _ = info
        if self.BASE_URL:
            return f"{self.BASE_URL}/{self.PLATFORMS[platform]}"
        return self.PLATFORMS[platform]

    def _platform_urls(self, info: VersionInfo) -> dict[str, str]:
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
    ) -> EventStream:
        """Compute platform hashes from prefetched artifact URLs."""
        _ = session
        platform_urls = self._platform_urls(info)
        hashes_drain = ValueDrain[HashMapping]()
        async for event in drain_value_events(
            compute_url_hashes(self.name, platform_urls.values()),
            hashes_drain,
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
    required_tools = ("nix", "nix-prefetch-url")

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
        hash_drain = ValueDrain[str]()
        async for event in drain_value_events(events, hash_drain):
            yield event
        hash_value = require_value(hash_drain, error)
        yield UpdateEvent.value(self.name, [HashEntry.create(hash_type, hash_value)])


class FlakeInputHashUpdater(HashEntryUpdater):
    """Base updater for hash-only sources backed by flake inputs.

    Uses derivation fingerprinting for maximally precise staleness detection.
    Instead of comparing version strings (which miss nixpkgs bumps, toolchain
    changes, and build-script edits), we evaluate the package with a sentinel
    hash and compare the resulting ``.drv`` path.  Since the sentinel is
    constant, the ``.drv`` hash is a pure function of the entire transitive
    build-input closure — equivalent to Nix's own rebuild detection.
    """

    input_name: str | None = None
    hash_type: HashType
    required_tools = ("nix",)

    def __init__(self, *, config: UpdateConfig | None = None) -> None:
        super().__init__(config=config)
        if self.input_name is None:
            self.input_name = self.name

    @property
    def _input(self) -> str:
        if self.input_name is None:
            msg = "Missing input name"
            raise RuntimeError(msg)
        return self.input_name

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        _ = session
        node = get_flake_input_node(self._input)
        version = get_flake_input_version(node)
        return VersionInfo(version=version, metadata={"node": node})

    async def _is_latest(self, current: SourceEntry | None, info: VersionInfo) -> bool:  # noqa: ARG002
        """Check staleness via derivation fingerprint comparison.

        Computes the ``.drv`` hash with ``FAKE_HASHES=1`` and compares it to
        the stored ``drvHash`` in ``sources.json``.  Returns ``True`` only
        when the fingerprint matches exactly — meaning no build input in the
        entire transitive closure has changed.
        """
        if current is None or current.drv_hash is None:
            return False
        try:
            new_fingerprint = await compute_drv_fingerprint(
                self.name, config=self.config
            )
        except RuntimeError:
            # If fingerprint computation fails, conservatively recompute.
            return False
        # Cache the fingerprint so _finalize_result can reuse it.
        self._cached_fingerprint = new_fingerprint
        return current.drv_hash == new_fingerprint

    async def _finalize_result(self, result: SourceEntry) -> EventStream:
        """Attach a derivation fingerprint to the result entry."""
        yield UpdateEvent.status(self.name, "Computing derivation fingerprint...")
        try:
            # Reuse cached fingerprint from _is_latest when available.
            drv_hash = getattr(self, "_cached_fingerprint", None)
            if drv_hash is None:
                drv_hash = await compute_drv_fingerprint(self.name, config=self.config)
            result = result.model_copy(update={"drv_hash": drv_hash})
        except RuntimeError:
            pass  # Store without fingerprint; will recompute next run
        yield UpdateEvent.value(self.name, result)

    @abstractmethod
    def _compute_hash(self, info: VersionInfo) -> EventStream:
        """Return an event stream that yields the computed hash value."""
        raise NotImplementedError

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        _ = session
        async for event in self._emit_single_hash_entry(
            self._compute_hash(info),
            error=f"Missing {self.hash_type} output",
            hash_type=self.hash_type,
        ):
            yield event


class GoVendorHashUpdater(FlakeInputHashUpdater):
    """Hash updater for Go vendoring outputs."""

    hash_type = "vendorHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        _ = info
        return compute_go_vendor_hash(self.name, config=self.config)


class CargoVendorHashUpdater(FlakeInputHashUpdater):
    """Hash updater for Cargo vendoring outputs."""

    hash_type = "cargoHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        _ = info
        return compute_cargo_vendor_hash(self.name, config=self.config)


class NpmDepsHashUpdater(FlakeInputHashUpdater):
    """Hash updater for npm dependency derivations."""

    hash_type = "npmDepsHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        _ = info
        return compute_npm_deps_hash(self.name, config=self.config)


class BunNodeModulesHashUpdater(FlakeInputHashUpdater):
    """Hash updater for Bun node_modules derivations."""

    hash_type = "nodeModulesHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        _ = info
        return compute_bun_node_modules_hash(self.name, config=self.config)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        """Compute hash for current platform only, emit platform-specific entry."""
        _ = session
        error = f"Missing {self.hash_type} output"
        hash_drain = ValueDrain[str]()
        async for event in drain_value_events(self._compute_hash(info), hash_drain):
            yield event
        hash_value = require_value(hash_drain, error)
        platform = get_current_nix_platform()
        entries = [HashEntry.create(self.hash_type, hash_value, platform=platform)]
        yield UpdateEvent.value(self.name, entries)


class DenoDepsHashUpdater(FlakeInputHashUpdater):
    """Hash updater for per-platform Deno dependency derivations."""

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
    ) -> EventStream:
        _ = session
        error = f"Missing {self.hash_type} output"
        hash_drain = ValueDrain[HashMapping]()
        async for event in drain_value_events(
            self._compute_hash(info),
            hash_drain,
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


def go_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    **_kwargs: object,
) -> type[GoVendorHashUpdater]:
    """Create a :class:`GoVendorHashUpdater` subclass for *name*."""
    attrs: dict[str, Any] = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (GoVendorHashUpdater,), attrs)


def cargo_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    **_kwargs: object,
) -> type[CargoVendorHashUpdater]:
    """Create a :class:`CargoVendorHashUpdater` subclass for *name*."""
    attrs: dict[str, Any] = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (CargoVendorHashUpdater,), attrs)


def npm_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[NpmDepsHashUpdater]:
    """Create an :class:`NpmDepsHashUpdater` subclass for ``name``."""
    attrs = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (NpmDepsHashUpdater,), attrs)


def bun_node_modules_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[BunNodeModulesHashUpdater]:
    """Create a :class:`BunNodeModulesHashUpdater` subclass for ``name``."""
    attrs = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (BunNodeModulesHashUpdater,), attrs)


def deno_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[DenoDepsHashUpdater]:
    """Create a :class:`DenoDepsHashUpdater` subclass for ``name``."""
    attrs = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (DenoDepsHashUpdater,), attrs)


__all__ = [
    "UPDATERS",
    "CargoLockGitDep",
    "ChecksumProvidedUpdater",
    "DownloadHashUpdater",
    "HashEntryUpdater",
    "UpdateConfig",
    "Updater",
    "VersionInfo",
    "_verify_platform_versions",
    "bun_node_modules_updater",
    "cargo_vendor_updater",
    "deno_deps_updater",
    "go_vendor_updater",
    "npm_deps_updater",
]
