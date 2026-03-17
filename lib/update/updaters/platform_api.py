"""Reusable base updater for APIs that expose per-platform version/checksum fields."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from pydantic import TypeAdapter, ValidationError

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes

from lib.update import process as update_process
from lib.update.events import (
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    require_value,
)
from lib.update.net import fetch_json
from lib.update.updaters.base import (
    ChecksumProvidedUpdater,
    VersionInfo,
    _verify_platform_versions,
)
from lib.update.updaters.metadata import PlatformAPIMetadata

_OBJECT_MAP_ADAPTER = TypeAdapter(dict[str, object])
_PLATFORM_INFO_ADAPTER = TypeAdapter(dict[str, dict[str, object]])


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

    def _require_platform_str_field(
        self,
        platform_info: dict[str, dict[str, object]],
        *,
        field: str,
    ) -> dict[str, str]:
        values: dict[str, str] = {}
        for platform, info in platform_info.items():
            value = info.get(field)
            if not isinstance(value, str):
                msg = (
                    f"Expected string field {field!r} in "
                    f"{self.name}:{platform}, got {value!r}"
                )
                raise TypeError(msg)
            values[platform] = value
        return values

    def _metadata(self, info: VersionInfo) -> PlatformAPIMetadata:
        metadata = info.metadata
        if isinstance(metadata, PlatformAPIMetadata):
            return metadata
        if isinstance(metadata, dict):
            metadata_map = cast("dict[str, object]", metadata)
            platform_info_obj = metadata_map.get("platform_info")
            if not isinstance(platform_info_obj, dict):
                msg = f"Expected platform_info mapping in {self.name} metadata"
                raise TypeError(msg)
            try:
                platform_info = _PLATFORM_INFO_ADAPTER.validate_python(
                    platform_info_obj,
                    strict=True,
                )
            except ValidationError as exc:
                msg = f"Malformed platform payload for {self.name}"
                raise TypeError(msg) from exc
            equality_fields = {
                key: value
                for key, value in metadata_map.items()
                if isinstance(key, str)
                and key not in {"commit", "platform_info"}
                and isinstance(value, str)
            }
            commit_payload = metadata_map.get("commit")
            commit = commit_payload if isinstance(commit_payload, str) else None
            return PlatformAPIMetadata(
                platform_info=platform_info,
                equality_fields=equality_fields,
                commit=commit,
            )
        msg = f"Expected platform_info mapping in {self.name} metadata"
        raise TypeError(msg)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch platform metadata and verify versions match across platforms."""

        async def _fetch_one(
            nix_plat: str,
            api_plat: str,
        ) -> tuple[str, dict[str, object]]:
            payload = await fetch_json(
                session,
                self._api_url(api_plat),
                config=self.config,
            )
            try:
                data = _OBJECT_MAP_ADAPTER.validate_python(payload, strict=True)
            except ValidationError as exc:
                msg = (
                    f"Expected JSON object from platform API for {self.name}:{api_plat}"
                )
                raise TypeError(msg) from exc
            return nix_plat, data

        results = await asyncio.gather(
            *(_fetch_one(p, k) for p, k in self.PLATFORMS.items()),
        )
        platform_info: dict[str, dict[str, object]] = dict(results)
        versions = self._require_platform_str_field(
            platform_info,
            field=self.VERSION_KEY,
        )
        version = _verify_platform_versions(versions, self.name)
        equality_fields: dict[str, str] = {}
        for key in self.EXTRA_EQUALITY_KEYS:
            values = self._require_platform_str_field(platform_info, field=key)
            equality_fields[key] = _verify_platform_versions(
                values, f"{self.name} {key}"
            )
        commit: str | None = None
        if self.COMMIT_METADATA_KEY:
            commits = self._require_platform_str_field(
                platform_info,
                field=self.COMMIT_METADATA_KEY,
            )
            commit = _verify_platform_versions(commits, f"{self.name} commit")
        return VersionInfo(
            version=version,
            metadata=PlatformAPIMetadata(
                platform_info=platform_info,
                equality_fields=equality_fields,
                commit=commit,
            ),
        )

    async def fetch_checksums(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> dict[str, str]:
        """Extract per-platform checksums from fetched metadata."""
        _ = session
        checksum_key = self.CHECKSUM_KEY
        if not checksum_key:
            msg = "No CHECKSUM_KEY defined"
            raise NotImplementedError(msg)
        metadata = self._metadata(info)
        platform_info = metadata.platform_info
        checksums: dict[str, str] = {}
        for platform in self.PLATFORMS:
            if platform not in platform_info:
                msg = f"Malformed platform payload for {self.name}: {platform!r}"
                raise TypeError(msg)
            payload = platform_info[platform]
            checksum = payload.get(checksum_key)
            if not isinstance(checksum, str):
                msg = (
                    f"Expected string field {checksum_key!r} in "
                    f"{self.name}:{platform}, got {checksum!r}"
                )
                raise TypeError(msg)
            checksums[platform] = checksum
        return checksums

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build result with platform download URLs and computed hashes."""
        urls = {
            nix_plat: self._download_url(api_plat, info)
            for nix_plat, api_plat in self.PLATFORMS.items()
        }
        metadata = self._metadata(info)
        commit = metadata.commit
        return self._build_result_with_urls(info, hashes, urls, commit=commit)


class DownloadingPlatformAPIUpdater(PlatformAPIUpdater):
    """Platform API updater that computes hashes from download URLs."""

    async def fetch_checksums(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> dict[str, str]:
        """Download artifacts and derive per-platform hashes."""
        _ = session
        urls = {
            nix_plat: self._download_url(api_plat, info)
            for nix_plat, api_plat in self.PLATFORMS.items()
        }
        hashes_drain = ValueDrain[dict[str, str]]()
        async for _event in drain_value_events(
            update_process.compute_url_hashes(self.name, urls.values()),
            hashes_drain,
            parse=expect_hash_mapping,
        ):
            pass
        hashes_by_url = require_value(hashes_drain, "Missing hash output")
        return {plat: hashes_by_url[url] for plat, url in urls.items()}


__all__ = ["DownloadingPlatformAPIUpdater", "PlatformAPIUpdater"]
