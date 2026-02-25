"""Updater for Cursor editor release metadata and downloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update import process as update_process
from lib.update.events import (
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    require_value,
)
from lib.update.updaters.platform_api import PlatformAPIUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.base import VersionInfo


class CodeCursorUpdater(PlatformAPIUpdater):
    """Resolve Cursor versions and platform-specific download URLs.

    The Cursor API does not expose a checksum field, so we override
    ``fetch_checksums`` to compute hashes by downloading the artifacts.
    """

    name = "code-cursor"
    API_BASE = "https://www.cursor.com/api/download"
    VERSION_KEY = "version"
    EXTRA_EQUALITY_KEYS = ("commitSha",)
    COMMIT_METADATA_KEY = "commitSha"
    required_tools = ("nix", "nix-prefetch-url")
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-x64",
        "aarch64-linux": "linux-arm64",
        "x86_64-linux": "linux-x64",
    }

    def _api_url(self, _api_platform: str) -> str:
        return f"{self.API_BASE}?platform={_api_platform}&releaseTrack=stable"

    def _download_url(self, _api_platform: str, info: VersionInfo) -> str:
        # platform_info is keyed by nix platform (aarch64-darwin, etc.);
        # reverse-lookup the nix key for the given API platform name.
        nix_plat = next(n for n, a in self.PLATFORMS.items() if a == _api_platform)
        platform_info = info.metadata.get("platform_info")
        if not isinstance(platform_info, dict):
            msg = "Expected platform_info mapping in Cursor metadata"
            raise TypeError(msg)
        platform_info_map: dict[str, object] = {}
        for key, value in platform_info.items():
            if not isinstance(key, str):
                msg = "Expected string keys in platform_info metadata"
                raise TypeError(msg)
            platform_info_map[key] = value
        payload = platform_info_map.get(nix_plat)
        if not isinstance(payload, dict):
            msg = f"Expected platform payload for {nix_plat}"
            raise TypeError(msg)
        payload_map: dict[str, object] = {}
        for key, value in payload.items():
            if not isinstance(key, str):
                msg = f"Expected string keys in platform payload for {nix_plat}"
                raise TypeError(msg)
            payload_map[key] = value
        download_url = payload_map.get("downloadUrl")
        if not isinstance(download_url, str):
            msg = f"Expected downloadUrl string for {nix_plat}"
            raise TypeError(msg)
        return download_url

    async def fetch_checksums(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> dict[str, str]:
        """Compute hashes by downloading platform artifacts.

        The Cursor API provides download URLs but no checksums, so we
        prefetch each artifact and derive the SRI hash.
        """
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
