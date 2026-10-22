"""Updater for Cursor editor release metadata and downloads."""

import asyncio
import re
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urljoin

import aiohttp
from packaging.version import Version

from lib.http_utils import HTTP_BAD_REQUEST
from lib.update.net import fetch_url
from lib.update.updaters import VersionInfo, register_updater
from lib.update.updaters.metadata import PlatformAPIMetadata
from lib.update.updaters.platform_api import DownloadingPlatformAPIUpdater

if TYPE_CHECKING:
    from lib.update.updaters.core import UpdateContext

HTTP_REDIRECT_MIN = 300

_CURSOR_API_URL_RE = re.compile(
    r"https://api2\.cursor\.sh/updates/download/golden/"
    r"(?P<api_platform>[^/\\\"']+)/cursor/(?P<version>[^\\\"']+)"
)
# Cursor's CDN has served production paths whose commit segment is longer than
# 40 characters (the 3.27.6 DMG lives under a 41-character path). Only the
# leading 40 hex characters identify the build, so the segment length must not
# be part of the match. Requiring an exact 40-character segment made those
# releases invisible: the commit never resolved, so the updater fell back to
# the older build that the golden channel still advertises.
_CURSOR_PRODUCTION_COMMIT_RE = re.compile(r"/production/(?P<commit>[0-9a-f]{40})")
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
        attempts = max(1, self.config.default_retries)
        last_error: BaseException | None = None
        for attempt in range(attempts):
            try:
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
                            f"Cursor download redirect from {api_url} "
                            "did not include Location"
                        )
                        raise RuntimeError(msg)
                    return urljoin(api_url, location)
            except (TimeoutError, aiohttp.ClientError) as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(
                        self.config.default_retry_backoff * (attempt + 1)
                    )
        msg = (
            f"Cursor download redirect from {api_url} failed after {attempts} attempts"
        )
        raise RuntimeError(msg) from last_error

    @staticmethod
    def _uniform_regex_value(
        urls: dict[str, str],
        pattern: re.Pattern[str],
        group: str,
    ) -> str | None:
        values = {
            match.group(group)
            for url in urls.values()
            if (match := pattern.search(url)) is not None
        }
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

    def _keep_current_pin(
        self,
        context: UpdateContext,
        *,
        resolved_urls: dict[str, str],
    ) -> VersionInfo:
        """Return the existing pin instead of adopting ``resolved_urls``.

        Two situations keep the pin. Cursor has not staged one version and
        commit for every platform yet. And the golden channel sometimes
        resolves to a build *older* than the pin, either because Cursor rolled
        a release back or because the channel still lags an already deployed
        series. Rewriting the pin backwards would then fail activation on hosts
        that already run the newer bundle, so the pin is preserved.
        """
        current = context.current
        pinned_urls = None if current is None else current.urls
        if (
            current is None
            or current.version is None
            or current.commit is None
            or not pinned_urls
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
        version = self._uniform_regex_value(
            resolved_urls,
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
            return self._keep_current_pin(context, resolved_urls=resolved_urls)
        if (
            context.current is not None
            and context.current.version is not None
            and Version(version) < Version(context.current.version)
        ):
            # The golden channel keeps serving a rolled-back (or lagging)
            # build, so a fully resolved version can still be older than the
            # pin. Never rewrite the pin downwards: hosts that already run the
            # newer bundle would then refuse to activate.
            return self._keep_current_pin(context, resolved_urls=resolved_urls)
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
