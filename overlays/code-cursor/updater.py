"""Updater for Cursor editor release metadata and downloads."""

from __future__ import annotations

import asyncio
import re
from typing import ClassVar
from urllib.parse import urljoin

import aiohttp

from lib.update.net import fetch_url
from lib.update.updaters.base import VersionInfo, register_updater
from lib.update.updaters.metadata import PlatformAPIMetadata
from lib.update.updaters.platform_api import DownloadingPlatformAPIUpdater

HTTP_REDIRECT_MIN = 300
HTTP_BAD_REQUEST = 400

_CURSOR_API_URL_RE = re.compile(
    r"https://api2\.cursor\.sh/updates/download/golden/"
    r"(?P<api_platform>[^/\\\"']+)/cursor/(?P<version>[^\\\"']+)"
)
_CURSOR_PRODUCTION_COMMIT_RE = re.compile(r"/production/(?P<commit>[0-9a-f]{40})/")
_CURSOR_APPIMAGE_VERSION_RE = re.compile(r"Cursor-(?P<version>\d+\.\d+\.\d+)-")


@register_updater
class CodeCursorUpdater(DownloadingPlatformAPIUpdater):
    """Resolve Cursor versions and platform-specific download URLs."""

    name = "code-cursor"
    DOWNLOAD_PAGE = "https://cursor.com/download"
    API_BASE = "https://api2.cursor.sh/updates/download/golden"
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

    def _api_url(self, _api_platform: str, version: str = "3.9") -> str:
        return f"{self.API_BASE}/{_api_platform}/cursor/{version}"

    def _extract_download_api_urls(self, payload: str) -> dict[str, str]:
        expected = set(self.PLATFORMS.values())
        urls: dict[str, str] = {}
        for match in _CURSOR_API_URL_RE.finditer(payload):
            api_platform = match.group("api_platform")
            if api_platform in expected:
                urls.setdefault(api_platform, match.group(0))
        missing = expected - set(urls)
        if missing:
            msg = f"Cursor download page missing platform links: {sorted(missing)}"
            raise RuntimeError(msg)
        return urls

    async def _resolve_download_url(
        self,
        session: aiohttp.ClientSession,
        api_url: str,
    ) -> str:
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.head(
            api_url,
            allow_redirects=False,
            timeout=timeout,
        ) as response:
            if not HTTP_REDIRECT_MIN <= response.status < HTTP_BAD_REQUEST:
                msg = (
                    f"Expected Cursor download redirect from {api_url}, "
                    f"got HTTP {response.status}"
                )
                raise RuntimeError(msg)
            location = response.headers.get("Location")
            if not location:
                msg = (
                    f"Cursor download redirect from {api_url} did not include Location"
                )
                raise RuntimeError(msg)
            return urljoin(api_url, location)

    @staticmethod
    def _require_uniform_regex_value(
        urls: dict[str, str],
        pattern: re.Pattern[str],
        group: str,
        *,
        context: str,
    ) -> str:
        values = {
            match.group(group)
            for url in urls.values()
            if (match := pattern.search(url)) is not None
        }
        if len(values) != 1:
            msg = f"Unable to resolve one Cursor {context} from URLs: {urls}"
            raise RuntimeError(msg)
        return values.pop()

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch Cursor release metadata from the download page redirect targets."""
        page = (
            await fetch_url(session, self.DOWNLOAD_PAGE, config=self.config)
        ).decode(errors="replace")
        api_urls = self._extract_download_api_urls(page)
        resolved_by_api_platform = dict(
            zip(
                api_urls,
                await asyncio.gather(
                    *(
                        self._resolve_download_url(session, url)
                        for url in api_urls.values()
                    )
                ),
                strict=True,
            )
        )
        resolved_urls = {
            nix_platform: resolved_by_api_platform[api_platform]
            for nix_platform, api_platform in self.PLATFORMS.items()
        }
        version = self._require_uniform_regex_value(
            resolved_urls,
            _CURSOR_APPIMAGE_VERSION_RE,
            "version",
            context="version",
        )
        commit = self._require_uniform_regex_value(
            resolved_urls,
            _CURSOR_PRODUCTION_COMMIT_RE,
            "commit",
            context="commit",
        )
        return VersionInfo(
            version=version,
            metadata=PlatformAPIMetadata(
                platform_info={
                    platform: {"downloadUrl": url}
                    for platform, url in resolved_urls.items()
                },
                equality_fields={"commitSha": commit},
                commit=commit,
            ),
        )

    def _download_url(self, _api_platform: str, info: VersionInfo) -> str:
        # platform_info is keyed by nix platform (aarch64-darwin, etc.);
        # reverse-lookup the nix key for the given API platform name.
        nix_plat = next(n for n, a in self.PLATFORMS.items() if a == _api_platform)
        metadata = self._metadata(info)
        payload = metadata.platform_info.get(nix_plat)
        if not isinstance(payload, dict):
            msg = f"Expected platform payload for {nix_plat}"
            raise TypeError(msg)
        download_url = payload.get("downloadUrl")
        if not isinstance(download_url, str):
            msg = f"Expected downloadUrl string for {nix_plat}"
            raise TypeError(msg)
        return download_url
