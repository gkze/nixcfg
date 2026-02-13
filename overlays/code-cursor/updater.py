"""Updater for Cursor editor release metadata and downloads."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

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
        platform_info = info.metadata["platform_info"]
        return platform_info[_api_platform]["downloadUrl"]

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
        from lib.update.events import ValueDrain, drain_value_events
        from lib.update.process import compute_url_hashes
        from lib.update.updaters.base import HashMapping, _require_value

        urls = {
            nix_plat: self._download_url(api_plat, info)
            for nix_plat, api_plat in self.PLATFORMS.items()
        }
        hashes_drain = ValueDrain[HashMapping]()
        async for _event in drain_value_events(
            compute_url_hashes(self.name, urls.values()),
            hashes_drain,
        ):
            pass
        hashes_by_url = _require_value(hashes_drain, "Missing hash output")
        return {plat: hashes_by_url[url] for plat, url in urls.items()}
