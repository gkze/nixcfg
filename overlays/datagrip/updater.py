"""Updater for JetBrains DataGrip releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters.base import (
    ChecksumProvidedUpdater,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import ReleasePayloadMetadata

type JsonObject = json_utils.JsonObject


@register_updater
class DataGripUpdater(ChecksumProvidedUpdater):
    """Resolve latest DataGrip release and published checksums."""

    name = "datagrip"

    API_URL = "https://data.services.jetbrains.com/products/releases?code=DG&latest=true&type=release"

    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "macM1",
        "x86_64-darwin": "mac",
        "aarch64-linux": "linuxARM64",
        "x86_64-linux": "linux",
    }

    @staticmethod
    def _release_payload(info: VersionInfo) -> JsonObject:
        metadata = info.metadata
        if isinstance(metadata, ReleasePayloadMetadata):
            return metadata.release
        msg = f"Missing or invalid DataGrip release metadata: {metadata!r}"
        raise RuntimeError(msg)

    @staticmethod
    def _release_downloads(release: JsonObject) -> JsonObject:
        downloads = release.get("downloads")
        if isinstance(downloads, dict):
            return downloads
        msg = f"Missing or invalid DataGrip downloads metadata: {release!r}"
        raise RuntimeError(msg)

    @staticmethod
    def _release_download_field(
        downloads: JsonObject,
        platform_key: str,
        field: str,
    ) -> str:
        payload = downloads.get(platform_key)
        if not isinstance(payload, dict):
            msg = f"Missing DataGrip platform payload for {platform_key}: {downloads!r}"
            raise TypeError(msg)
        platform_payload = payload
        value = platform_payload.get(field)
        if isinstance(value, str) and value:
            return value
        msg = f"Missing DataGrip download field {field!r} for {platform_key}: {platform_payload!r}"
        raise RuntimeError(msg)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch latest DataGrip version metadata from JetBrains."""
        payload = await fetch_json(session, self.API_URL, config=self.config)
        if not isinstance(payload, dict):
            msg = f"Unexpected DataGrip payload type: {type(payload).__name__}"
            raise TypeError(msg)
        releases_payload = payload.get("DG")
        if not isinstance(releases_payload, list):
            msg = f"No DataGrip releases found in response: {payload}"
            raise TypeError(msg)
        releases = releases_payload
        if not releases:
            msg = f"No DataGrip releases found in response: {payload}"
            raise RuntimeError(msg)
        release = releases[0]
        if not isinstance(release, dict):
            msg = f"Unexpected DataGrip release payload: {release!r}"
            raise TypeError(msg)
        version = release.get("version")
        if not isinstance(version, str) or not version:
            msg = f"Missing DataGrip version in release payload: {release}"
            raise RuntimeError(msg)
        return VersionInfo(
            version=version,
            metadata=ReleasePayloadMetadata(
                release=json_utils.coerce_json_object(
                    release,
                    context="DataGrip release payload",
                )
            ),
        )

    async def fetch_checksums(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> dict[str, str]:
        """Fetch upstream checksum files for each supported platform."""
        release = self._release_payload(info)
        downloads = self._release_downloads(release)
        checksum_urls = {
            nix_platform: self._release_download_field(
                downloads,
                jetbrains_key,
                "checksumLink",
            )
            for nix_platform, jetbrains_key in self.PLATFORMS.items()
        }

        def _parse_checksum(payload: bytes, _url: str) -> str:
            parts = payload.decode().split()
            return parts[0] if parts else ""

        return await self._fetch_checksums_from_urls(
            session,
            checksum_urls,
            parser=_parse_checksum,
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build a source entry from JetBrains release URLs and hashes."""
        release = self._release_payload(info)
        downloads = self._release_downloads(release)
        urls = {
            nix_platform: self._release_download_field(downloads, jetbrains_key, "link")
            for nix_platform, jetbrains_key in self.PLATFORMS.items()
        }
        return self._build_result_with_urls(info, hashes, urls)
