"""Reusable base updater for APIs that expose per-platform version/checksum fields."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

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
            if not isinstance(payload, dict):
                msg = (
                    "Expected JSON object from platform API for "
                    f"{self.name}:{api_plat}, got {type(payload).__name__}"
                )
                raise TypeError(msg)
            data: dict[str, object] = {}
            for key, value in payload.items():
                if not isinstance(key, str):
                    msg = (
                        "Expected string-keyed platform payload for "
                        f"{self.name}:{api_plat}, got {payload!r}"
                    )
                    raise TypeError(msg)
                data[key] = value
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
        metadata: dict[str, object] = {
            "platform_info": platform_info,
        }
        for key in self.EXTRA_EQUALITY_KEYS:
            values = self._require_platform_str_field(platform_info, field=key)
            metadata[key] = _verify_platform_versions(values, f"{self.name} {key}")
        if self.COMMIT_METADATA_KEY:
            commits = self._require_platform_str_field(
                platform_info,
                field=self.COMMIT_METADATA_KEY,
            )
            metadata["commit"] = _verify_platform_versions(
                commits, f"{self.name} commit"
            )
        return VersionInfo(version=version, metadata=metadata)

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
        platform_info_obj = info.metadata.get("platform_info")
        if not isinstance(platform_info_obj, dict):
            msg = f"Expected platform_info mapping in {self.name} metadata"
            raise TypeError(msg)
        platform_info: dict[str, dict[str, object]] = {}
        for platform_obj, payload_obj in platform_info_obj.items():
            if not isinstance(platform_obj, str) or not isinstance(payload_obj, dict):
                msg = f"Malformed platform payload for {self.name}: {platform_obj!r}"
                raise TypeError(msg)
            normalized_payload: dict[str, object] = {}
            for key, value in payload_obj.items():
                if not isinstance(key, str):
                    msg = (
                        f"Malformed platform payload for {self.name}: {platform_obj!r}"
                    )
                    raise TypeError(msg)
                normalized_payload[key] = value
            platform_info[platform_obj] = normalized_payload
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
        commit_payload = info.metadata.get("commit")
        commit = commit_payload if isinstance(commit_payload, str) else None
        return self._build_result_with_urls(info, hashes, urls, commit=commit)
