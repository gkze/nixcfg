"""Updater for JetBrains DataGrip releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes

from lib.update.net import fetch_json
from lib.update.updaters.base import ChecksumProvidedUpdater, VersionInfo


class DataGripUpdater(ChecksumProvidedUpdater):
    """Resolve latest DataGrip release and published checksums."""

    name = "datagrip"

    API_URL = "https://data.services.jetbrains.com/products/releases?code=DG&latest=true&type=release"

    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "macM1",
        "aarch64-linux": "linuxARM64",
        "x86_64-linux": "linux",
    }

    @staticmethod
    def _release_payload(info: VersionInfo) -> dict[str, object]:
        release = info.metadata.get("release")
        if isinstance(release, dict):
            normalized: dict[str, object] = {}
            for key, value in release.items():
                if not isinstance(key, str):
                    msg = f"Invalid DataGrip release field key: {key!r}"
                    raise TypeError(msg)
                normalized[key] = value
            return normalized
        msg = f"Missing or invalid DataGrip release metadata: {release!r}"
        raise RuntimeError(msg)

    @staticmethod
    def _release_downloads(release: dict[str, object]) -> dict[str, object]:
        downloads = release.get("downloads")
        if isinstance(downloads, dict):
            normalized: dict[str, object] = {}
            for key, value in downloads.items():
                if not isinstance(key, str):
                    msg = f"Invalid DataGrip download key: {key!r}"
                    raise TypeError(msg)
                normalized[key] = value
            return normalized
        msg = f"Missing or invalid DataGrip downloads metadata: {release!r}"
        raise RuntimeError(msg)

    @staticmethod
    def _release_download_field(
        downloads: dict[str, object],
        platform_key: str,
        field: str,
    ) -> str:
        payload = downloads.get(platform_key)
        if not isinstance(payload, dict):
            msg = f"Missing DataGrip platform payload for {platform_key}: {downloads!r}"
            raise TypeError(msg)
        platform_payload: dict[str, object] = {}
        for key, raw_value in payload.items():
            if not isinstance(key, str):
                msg = f"Invalid DataGrip platform field key: {key!r}"
                raise TypeError(msg)
            platform_payload[key] = raw_value
        value = platform_payload.get(field)
        if isinstance(value, str) and value:
            return value
        msg = (
            "Missing DataGrip download field "
            f"{field!r} for {platform_key}: {platform_payload!r}"
        )
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
        return VersionInfo(version=version, metadata={"release": release})

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
