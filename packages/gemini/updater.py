"""Updater for Gemini for macOS releases."""

import asyncio
import json
import re
from typing import TYPE_CHECKING, ClassVar

import aiohttp

from lib import json_utils
from lib.update.net import HTTP_BAD_REQUEST, fetch_url
from lib.update.updaters import (
    DownloadUrlMetadataUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.metadata import DownloadUrlMetadata

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry

_APP_ID = "com.google.geminimacos"
_CHANNEL = "m1-prod"
_ANTI_XSSI_PREFIX = b")]}'\n"
_EMPTY_VERSION = "0.0.0.0"  # noqa: S104 - Omaha sentinel, not a bind address.
_MAX_VERSION_COMPONENT = (1 << 32) - 1
_VERSION_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+){0,3}")
_DOWNLOAD_PATTERN = re.compile(
    rb"https://dl\.google\.com/release2/[A-Za-z0-9_-]+/release/Gemini\.dmg"
)


def _version_key(version: str) -> tuple[int, ...]:
    if _VERSION_PATTERN.fullmatch(version) is None:
        raise ValueError(version)
    components = tuple(int(component) for component in version.split("."))
    if any(component > _MAX_VERSION_COMPONENT for component in components):
        raise ValueError(version)
    return (*components, *(0 for _ in range(4 - len(components))))


def _effective_version_info(
    context: UpdateContext | SourceEntry | None,
    upstream: VersionInfo,
) -> VersionInfo:
    current = context.current if isinstance(context, UpdateContext) else context
    if (
        current is None
        or current.version is None
        or _version_key(upstream.version) >= _version_key(current.version)
    ):
        return upstream

    current_url = (current.urls or {}).get("aarch64-darwin")
    if not current_url:
        msg = "Cannot safely refresh a newer Gemini pin without its current DMG URL"
        raise RuntimeError(msg)
    return VersionInfo(
        version=current.version,
        metadata=DownloadUrlMetadata(url=current_url),
    )


@register_updater
class GeminiUpdater(DownloadUrlMetadataUpdater):
    """Cross-check Gemini's Google Updater version and official download page."""

    name = "gemini"
    materialize_when_current = True
    supported_platforms = ("aarch64-darwin",)
    UPDATE_URL = "https://update.googleapis.com/service/update2/json"
    DOWNLOAD_PAGE_URL = "https://gemini.google/mac/"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
    }
    URL_METADATA_CONTEXT = "Gemini metadata"

    @staticmethod
    def _request_body() -> dict[str, object]:
        return {
            "request": {
                "@os": "mac",
                "@updater": "nixcfg",
                "acceptformat": "crx3,download,puff,run,xz,zucc",
                "apps": [
                    {
                        "ap": _CHANNEL,
                        "appid": _APP_ID,
                        "enabled": True,
                        "updatecheck": {},
                        "version": _EMPTY_VERSION,
                    }
                ],
                "arch": "arm64",
                "dedup": "cr",
                "domainjoined": False,
                "ismachine": False,
                "os": {
                    "arch": "arm64",
                    "platform": "Mac OS X",
                    "version": "15.0",
                },
                "prodversion": "0",
                "protocol": "4.0",
                "testsource": "nixcfg-updater",
                "updaterversion": "0",
            }
        }

    @staticmethod
    def _parse_version(payload_bytes: bytes) -> str:
        if not payload_bytes.startswith(_ANTI_XSSI_PREFIX):
            msg = "Gemini Omaha response omitted its anti-XSSI prefix"
            raise RuntimeError(msg)
        try:
            payload_value = json.loads(payload_bytes.removeprefix(_ANTI_XSSI_PREFIX))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            msg = "Gemini Omaha response was not valid JSON"
            raise RuntimeError(msg) from exc

        payload = json_utils.as_object_dict(
            payload_value,
            context="Gemini Omaha response",
        )
        response = json_utils.as_object_dict(
            payload.get("response"),
            context="Gemini Omaha response.response",
        )
        apps = json_utils.as_object_list(
            response.get("apps"),
            context="Gemini Omaha response.response.apps",
        )
        parsed_apps = [
            json_utils.as_object_dict(
                app,
                context="Gemini Omaha response app",
            )
            for app in apps
        ]
        matching_apps = [app for app in parsed_apps if app.get("appid") == _APP_ID]
        if len(matching_apps) != 1:
            msg = f"Gemini Omaha response contained {len(matching_apps)} matching apps"
            raise RuntimeError(msg)
        app = matching_apps[0]
        app_status = json_utils.get_required_str(
            app,
            "status",
            context="Gemini Omaha app",
        )
        if app_status != "ok":
            msg = f"Gemini Omaha app returned status {app_status!r}"
            raise RuntimeError(msg)
        updatecheck = json_utils.as_object_dict(
            app.get("updatecheck"),
            context="Gemini Omaha updatecheck",
        )
        update_status = json_utils.get_required_str(
            updatecheck,
            "status",
            context="Gemini Omaha updatecheck",
        )
        if update_status != "ok":
            msg = f"Gemini Omaha updatecheck returned status {update_status!r}"
            raise RuntimeError(msg)
        version = json_utils.get_required_str(
            updatecheck,
            "nextversion",
            context="Gemini Omaha updatecheck",
        )
        try:
            _version_key(version)
        except ValueError as exc:
            msg = f"Gemini Omaha returned invalid version {version!r}"
            raise RuntimeError(msg) from exc

        return version

    @staticmethod
    def _parse_download_url(page_bytes: bytes) -> str:
        urls = {
            match.group(0).decode("ascii")
            for match in _DOWNLOAD_PATTERN.finditer(page_bytes)
        }
        if len(urls) != 1:
            msg = f"Gemini download page contained {len(urls)} official DMG URLs"
            raise RuntimeError(msg)
        return urls.pop()

    async def _fetch_version(self, session: aiohttp.ClientSession) -> str:
        headers = {
            "User-Agent": self.config.default_user_agent,
            "Content-Type": "application/json",
            "X-Goog-Update-Interactivity": "fg",
            "X-Goog-Update-AppId": _APP_ID,
            "X-Goog-Update-Updater": "nixcfg-0",
        }
        timeout = aiohttp.ClientTimeout(total=self.config.default_timeout)
        async with session.request(
            "POST",
            self.UPDATE_URL,
            headers=headers,
            json=self._request_body(),
            allow_redirects=True,
            timeout=timeout,
        ) as response:
            payload_bytes = await response.read()
            if response.status >= HTTP_BAD_REQUEST:
                msg = (
                    "Gemini Omaha request failed with "
                    f"HTTP {response.status} {response.reason}"
                )
                raise RuntimeError(msg)
        return self._parse_version(payload_bytes)

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> VersionInfo:
        """Resolve one release from Google's updater and public download page."""
        version, page_bytes = await asyncio.gather(
            self._fetch_version(session),
            fetch_url(
                session,
                self.DOWNLOAD_PAGE_URL,
                request_timeout=self.config.default_timeout,
                config=self.config,
            ),
        )
        return _effective_version_info(
            context,
            VersionInfo(
                version=version,
                metadata=DownloadUrlMetadata(url=self._parse_download_url(page_bytes)),
            ),
        )
