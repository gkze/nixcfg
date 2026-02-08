from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import aiohttp

from libnix.models.sources import (
    HashCollection,
    HashEntry,
    HashMapping,
    HashType,
    SourceEntry,
    SourceHashes,
)
from libnix.update.events import (
    EventStream,
    GatheredValues,
    UpdateEvent,
    ValueDrain,
    _require_value,
    drain_value_events,
    gather_event_streams,
)
from update.config import UpdateConfig, _resolve_active_config
from update.nix import (
    compute_bun_node_modules_hash,
    compute_cargo_vendor_hash,
    compute_deno_deps_hash,
    compute_go_vendor_hash,
    compute_npm_deps_hash,
    get_current_nix_platform,
)
from update.process import compute_url_hashes, convert_nix_hash_to_sri


@dataclass
class VersionInfo:
    version: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class CargoLockGitDep:
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
    name: str
    config: UpdateConfig
    required_tools: tuple[str, ...] = ("nix",)

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        name = getattr(cls, "name", None)
        if name is not None and not getattr(cls, "__abstractmethods__", None):
            UPDATERS[name] = cls

    def __init__(self, *, config: UpdateConfig | None = None) -> None:
        self.config = _resolve_active_config(config)

    @abstractmethod
    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo: ...

    @abstractmethod
    def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream: ...

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return SourceEntry(
            version=info.version, hashes=HashCollection.from_value(hashes)
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

    def _is_latest(self, current: SourceEntry | None, info: VersionInfo) -> bool:
        if current is None:
            return False
        if current.version != info.version:
            return False
        upstream_commit = info.metadata.get("commit")
        if upstream_commit and current.commit:
            return current.commit == upstream_commit
        return True

    async def update_stream(
        self, current: SourceEntry | None, session: aiohttp.ClientSession
    ) -> EventStream:
        yield UpdateEvent.status(self.name, f"Fetching latest {self.name} version...")
        info = await self.fetch_latest(session)

        yield UpdateEvent.status(self.name, f"Latest version: {info.version}")
        if self._is_latest(current, info):
            yield UpdateEvent.status(self.name, f"Up to date (version: {info.version})")
            yield UpdateEvent.result(self.name)
            return

        yield UpdateEvent.status(self.name, "Fetching hashes for all platforms...")
        hashes_drain = ValueDrain[SourceHashes]()
        async for event in drain_value_events(
            self.fetch_hashes(info, session), hashes_drain
        ):
            yield event
        hashes = _require_value(hashes_drain, "Missing hash output")
        result = self.build_result(info, hashes)
        if current is not None and result == current:
            yield UpdateEvent.status(self.name, "Up to date")
            yield UpdateEvent.result(self.name)
            return
        yield UpdateEvent.result(self.name, result)


class ChecksumProvidedUpdater(Updater):
    PLATFORMS: dict[str, str]

    @abstractmethod
    async def fetch_checksums(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> dict[str, str]: ...

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
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


class DownloadHashUpdater(Updater):
    PLATFORMS: dict[str, str]
    BASE_URL: str = ""
    required_tools = ("nix", "nix-prefetch-url")

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        if self.BASE_URL:
            return f"{self.BASE_URL}/{self.PLATFORMS[platform]}"
        return self.PLATFORMS[platform]

    def _platform_urls(self, info: VersionInfo) -> dict[str, str]:
        return {
            platform: self.get_download_url(platform, info)
            for platform in self.PLATFORMS
        }

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = self._platform_urls(info)
        return self._build_result_with_urls(info, hashes, urls)

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
        platform_urls = self._platform_urls(info)
        hashes_drain = ValueDrain[HashMapping]()
        async for event in drain_value_events(
            compute_url_hashes(self.name, platform_urls.values()), hashes_drain
        ):
            yield event
        hashes_by_url = _require_value(hashes_drain, "Missing hash output")

        hashes: dict[str, str] = {
            platform: hashes_by_url[platform_urls[platform]]
            for platform in self.PLATFORMS
        }
        yield UpdateEvent.value(self.name, hashes)


class HashEntryUpdater(Updater):
    input_name: str | None = None
    required_tools = ("nix", "nix-prefetch-url")

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return SourceEntry(
            hashes=HashCollection.from_value(hashes), input=self.input_name
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
        hash_value = _require_value(hash_drain, error)
        yield UpdateEvent.value(self.name, [HashEntry.create(hash_type, hash_value)])


class FlakeInputHashUpdater(HashEntryUpdater):
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
            raise RuntimeError("Missing input name")
        return self.input_name

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        from update.flake import get_flake_input_node, get_flake_input_version

        node = get_flake_input_node(self._input)
        version = get_flake_input_version(node)
        return VersionInfo(version=version, metadata={"node": node})

    @abstractmethod
    def _compute_hash(self, info: VersionInfo) -> EventStream: ...

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
        async for event in self._emit_single_hash_entry(
            self._compute_hash(info),
            error=f"Missing {self.hash_type} output",
            hash_type=self.hash_type,
        ):
            yield event


class GoVendorHashUpdater(FlakeInputHashUpdater):
    hash_type = "vendorHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_go_vendor_hash(self.name, config=self.config)


class CargoVendorHashUpdater(FlakeInputHashUpdater):
    hash_type = "cargoHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_cargo_vendor_hash(self.name, config=self.config)


class NpmDepsHashUpdater(FlakeInputHashUpdater):
    hash_type = "npmDepsHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_npm_deps_hash(self.name, config=self.config)


class BunNodeModulesHashUpdater(FlakeInputHashUpdater):
    hash_type = "nodeModulesHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_bun_node_modules_hash(self.name, config=self.config)

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
        """Compute hash for current platform only, emit platform-specific entry."""
        hash_drain = ValueDrain[str]()
        async for event in drain_value_events(self._compute_hash(info), hash_drain):
            yield event

        hash_value = _require_value(hash_drain, f"Missing {self.hash_type} output")
        platform = get_current_nix_platform()
        entries = [HashEntry.create(self.hash_type, hash_value, platform=platform)]
        yield UpdateEvent.value(self.name, entries)


class DenoDepsHashUpdater(FlakeInputHashUpdater):
    hash_type = "denoDepsHash"
    native_only: bool = False

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_deno_deps_hash(
            self.name,
            self._input,
            native_only=self.native_only,
            config=self.config,
        )

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
        hash_drain = ValueDrain[HashMapping]()
        async for event in drain_value_events(self._compute_hash(info), hash_drain):
            yield event

        platform_hashes = _require_value(hash_drain, f"Missing {self.hash_type} output")
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
    **_kwargs: Any,
) -> type[GoVendorHashUpdater]:
    """Create a :class:`GoVendorHashUpdater` subclass for *name*.

    Hash computation is driven by the overlay via ``FAKE_HASHES=1``; all
    build parameters (subpackages, proxy_vendor, go version) live in
    ``overlays.nix`` — the single source of truth.
    """
    attrs: dict[str, Any] = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (GoVendorHashUpdater,), attrs)


def cargo_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    **_kwargs: Any,
) -> type[CargoVendorHashUpdater]:
    """Create a :class:`CargoVendorHashUpdater` subclass for *name*."""
    attrs: dict[str, Any] = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (CargoVendorHashUpdater,), attrs)


def npm_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[NpmDepsHashUpdater]:
    attrs = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (NpmDepsHashUpdater,), attrs)


def bun_node_modules_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[BunNodeModulesHashUpdater]:
    attrs = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (BunNodeModulesHashUpdater,), attrs)


def deno_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[DenoDepsHashUpdater]:
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
