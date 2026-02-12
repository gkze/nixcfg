"""Updater for JetBrains DataGrip releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

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

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch latest DataGrip version metadata from JetBrains."""
        data = cast(
            "dict[str, list[dict[str, str]]]",
            await fetch_json(session, self.API_URL, config=self.config),
        )
        releases = data.get("DG") or []
        if not releases:
            msg = f"No DataGrip releases found in response: {data}"
            raise RuntimeError(msg)
        release = releases[0]
        version = release.get("version")
        if not version:
            msg = f"Missing DataGrip version in release payload: {release}"
            raise RuntimeError(msg)
        return VersionInfo(version=version, metadata={"release": release})

    async def fetch_checksums(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> dict[str, str]:
        """Fetch upstream checksum files for each supported platform."""
        release = info.metadata["release"]
        checksum_urls = {
            nix_platform: release["downloads"][jetbrains_key]["checksumLink"]
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
        release = info.metadata["release"]
        urls = {
            nix_platform: release["downloads"][jetbrains_key]["link"]
            for nix_platform, jetbrains_key in self.PLATFORMS.items()
        }
        return self._build_result_with_urls(info, hashes, urls)
