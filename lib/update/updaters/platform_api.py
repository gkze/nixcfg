"""Reusable base updater for APIs that expose per-platform version/checksum fields."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes

from lib.update.net import fetch_json
from lib.update.updaters.base import (
    ChecksumProvidedUpdater,
    VersionInfo,
    _verify_platform_versions,
)


class PlatformAPIUpdater(ChecksumProvidedUpdater):
    """Base updater for APIs that expose per-platform version/checksum fields."""

    VERSION_KEY: str = "version"
    CHECKSUM_KEY: str | None = None
    EXTRA_EQUALITY_KEYS: tuple[str, ...] = ()
    COMMIT_METADATA_KEY: str | None = None

    def _api_url(self, _api_platform: str) -> str:
        raise NotImplementedError

    def _download_url(self, _api_platform: str, info: VersionInfo) -> str:
        raise NotImplementedError

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch platform metadata and verify versions match across platforms."""

        async def _fetch_one(
            nix_plat: str,
            api_plat: str,
        ) -> tuple[str, dict[str, str]]:
            data = await fetch_json(
                session,
                self._api_url(api_plat),
                config=self.config,
            )
            return nix_plat, cast("dict[str, str]", data)

        results = await asyncio.gather(
            *(_fetch_one(p, k) for p, k in self.PLATFORMS.items()),
        )
        platform_info = dict(results)
        versions = {p: info[self.VERSION_KEY] for p, info in platform_info.items()}
        version = _verify_platform_versions(versions, self.name)
        metadata: dict[str, str | dict[str, dict[str, str]]] = {
            "platform_info": platform_info,
        }
        for key in self.EXTRA_EQUALITY_KEYS:
            values = {p: info[key] for p, info in platform_info.items()}
            metadata[key] = _verify_platform_versions(values, f"{self.name} {key}")
        return VersionInfo(version=version, metadata=metadata)

    async def fetch_checksums(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> dict[str, str]:
        """Extract per-platform checksums from fetched metadata."""
        _ = session
        if not self.CHECKSUM_KEY:
            msg = "No CHECKSUM_KEY defined"
            raise NotImplementedError(msg)
        platform_info = info.metadata["platform_info"]
        return {p: platform_info[p][self.CHECKSUM_KEY] for p in self.PLATFORMS}

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build result with platform download URLs and computed hashes."""
        urls = {
            nix_plat: self._download_url(api_plat, info)
            for nix_plat, api_plat in self.PLATFORMS.items()
        }
        commit = None
        if self.COMMIT_METADATA_KEY:
            commit = cast("str | None", info.metadata.get(self.COMMIT_METADATA_KEY))
        return self._build_result_with_urls(info, hashes, urls, commit=commit)
