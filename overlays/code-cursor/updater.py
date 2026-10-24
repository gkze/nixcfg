"""Updater for Cursor editor release metadata and downloads."""

import asyncio
import re
from typing import TYPE_CHECKING, ClassVar

from packaging.version import Version

from lib.update.net import fetch_redirect_location, fetch_url
from lib.update.updaters import VersionInfo, register_updater
from lib.update.updaters.metadata import PlatformAPIMetadata
from lib.update.updaters.platform_api import DownloadingPlatformAPIUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.core import UpdateContext

_CURSOR_API_URL_RE = re.compile(
    r"https://api2\.cursor\.sh/updates/download/golden/"
    r"(?P<api_platform>[^/\\\"']+)/cursor/(?P<version>[^\\\"']+)"
)
# Match the complete production path segment; never truncate a malformed commit.
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

    @staticmethod
    def _uniform_regex_value(
        urls: dict[str, str],
        pattern: re.Pattern[str],
        group: str,
    ) -> str | None:
        values = set()
        for url in urls.values():
            match = pattern.search(url)
            if match is None:
                return None
            values.add(match.group(group))
        if len(values) != 1:
            return None
        return values.pop()

    @classmethod
    def _require_uniform_regex_value(
        cls,
        urls: dict[str, str],
        pattern: re.Pattern[str],
        group: str,
        *,
        context: str,
    ) -> str:
        value = cls._uniform_regex_value(urls, pattern, group)
        if value is None:
            msg = f"Unable to resolve one Cursor {context} from URLs: {urls}"
            raise RuntimeError(msg)
        return value

    def _retain_verified_pin(
        self,
        context: UpdateContext,
        *,
        resolved_urls: dict[str, str],
    ) -> VersionInfo:
        """Keep a coherent pin while platforms stage or a channel offers an older build."""
        current = context.current
        pinned_urls = None if current is None else current.urls
        if (
            current is None
            or current.version is None
            or current.commit is None
            or not pinned_urls
            or set(pinned_urls) != set(self.PLATFORMS)
            or any(
                (match := _CURSOR_PRODUCTION_COMMIT_RE.search(url)) is None
                or match.group("commit") != current.commit
                for url in pinned_urls.values()
            )
            or any(
                (match := _CURSOR_APPIMAGE_VERSION_RE.search(url)) is None
                or match.group("version") != current.version
                for platform, url in pinned_urls.items()
                if platform.endswith("-linux")
            )
        ):
            msg = (
                "Unable to resolve one Cursor version/commit from URLs: "
                f"{resolved_urls}"
            )
            raise RuntimeError(msg)
        return VersionInfo(
            version=current.version,
            metadata=PlatformAPIMetadata(
                platform_info={
                    platform: {"downloadUrl": url}
                    for platform, url in pinned_urls.items()
                },
                equality_fields={"commitSha": current.commit},
                commit=current.commit,
            ),
        )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Fetch Cursor release metadata from the download page redirect targets."""
        page = (
            await fetch_url(session, self.DOWNLOAD_PAGE, config=self.config)
        ).decode(errors="replace")
        api_urls = self._extract_download_api_urls(page)
        # The download page can lag an already deployed stable series. Keep
        # checking that series rather than reinstalling the page's older one.
        if context.current is not None and context.current.version is not None:
            current_series = Version(context.current.version).release[:2]
            for api_platform, api_url in api_urls.items():
                advertised = api_url.rsplit("/", maxsplit=1)[1]
                if current_series > Version(advertised).release[:2]:
                    series = ".".join(map(str, current_series))
                    api_urls[api_platform] = self._api_url(api_platform, series)
        resolved_by_api_platform = dict(
            zip(
                api_urls,
                await asyncio.gather(
                    *(
                        fetch_redirect_location(session, url, config=self.config)
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
        version = self._uniform_regex_value(
            {
                platform: url
                for platform, url in resolved_urls.items()
                if platform.endswith("-linux")
            },
            _CURSOR_APPIMAGE_VERSION_RE,
            "version",
        )
        commit = self._uniform_regex_value(
            resolved_urls,
            _CURSOR_PRODUCTION_COMMIT_RE,
            "commit",
        )
        # Cursor stages Linux and Darwin independently. Update 37415412232
        # failed prepare because Linux was 3.22.15 / 983d341a while Darwin
        # was still 3.22.12 / 3a929743. Keep the complete pin until every
        # platform names one version and commit.
        if version is None or commit is None:
            return self._retain_verified_pin(
                context,
                resolved_urls=resolved_urls,
            )
        # Golden can lag a release already delivered through the in-app stable
        # cohort. Keep that verified source pin, not the feed's older artifacts.
        if (
            context.current is not None
            and context.current.version is not None
            and Version(version) < Version(context.current.version)
        ):
            return self._retain_verified_pin(
                context,
                resolved_urls=resolved_urls,
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
