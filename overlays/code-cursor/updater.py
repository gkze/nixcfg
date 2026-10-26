"""Updater for Cursor editor release metadata and downloads."""

import asyncio
import re
from typing import TYPE_CHECKING, ClassVar

from packaging.version import Version

from lib.update.net import decode_json, request
from lib.update.updaters import VersionInfo, register_updater
from lib.update.updaters.metadata import PlatformAPIMetadata
from lib.update.updaters.platform_api import DownloadingPlatformAPIUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.json_utils import JsonObject
    from lib.update.updaters.core import UpdateContext

_HTTP_NO_CONTENT = 204
_CURSOR_PRODUCTION_COMMIT_RE = re.compile(r"/production/(?P<commit>[0-9a-f]{40})/")
_CURSOR_APPIMAGE_VERSION_RE = re.compile(r"Cursor-(?P<version>\d+\.\d+\.\d+)-")
# The channel serves the in-app updater archives. Package the same artifacts the
# download page publishes: a disk image on Darwin, the plain AppImage on Linux.
_CURSOR_DARWIN_ARCHIVE_SUFFIX = ".zip"
_CURSOR_DARWIN_ARTIFACT_SUFFIX = ".dmg"
_CURSOR_ZSYNC_SUFFIX = ".zsync"


@register_updater
class CodeCursorUpdater(DownloadingPlatformAPIUpdater):
    """Resolve Cursor versions and platform downloads from a release track."""

    name = "code-cursor"
    API_BASE = "https://api2.cursor.sh/updates/api/update"
    # Cursor stages builds per track. These hosts track dev because the installed
    # app follows the dev track and self-updates in place: a production pin cannot
    # clear `preventDowngrade` against a dev build the app already runs.
    TRACK = "dev"
    # The channel answers 204 when the version asked about is current or newer,
    # and only names a build for an older version. Discovery therefore always
    # asks about a floor version, so the answer is the track's newest build
    # instead of "you are up to date".
    DISCOVERY_VERSION = "0.0.0"
    DISCOVERY_HASH = "0" * 64
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
        return (
            f"{self.API_BASE}/{_api_platform}/cursor/"
            f"{self.DISCOVERY_VERSION}/{self.DISCOVERY_HASH}/{self.TRACK}"
        )

    @staticmethod
    def _uniform_str_value(values: dict[str, str]) -> str | None:
        unique = set(values.values())
        if len(unique) != 1:
            return None
        return unique.pop()

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

    @staticmethod
    def _artifact_url(api_platform: str, url: str) -> str:
        """Map the channel's updater archive onto the packaged artifact."""
        if api_platform.startswith("darwin"):
            if not url.endswith(_CURSOR_DARWIN_ARCHIVE_SUFFIX):
                msg = f"Unexpected Cursor {api_platform} artifact: {url}"
                raise RuntimeError(msg)
            return (
                url[: -len(_CURSOR_DARWIN_ARCHIVE_SUFFIX)]
                + _CURSOR_DARWIN_ARTIFACT_SUFFIX
            )
        if not url.endswith(_CURSOR_ZSYNC_SUFFIX):
            msg = f"Unexpected Cursor {api_platform} artifact: {url}"
            raise RuntimeError(msg)
        return url[: -len(_CURSOR_ZSYNC_SUFFIX)]

    @staticmethod
    def _payload_version(
        api_platform: str,
        payload: JsonObject,
        url: str,
    ) -> str:
        named = next(
            (
                payload[key]
                for key in ("productVersion", "version", "name")
                if isinstance(payload.get(key), str)
            ),
            None,
        )
        if not isinstance(named, str):
            msg = f"Cursor {api_platform} channel entry named no version"
            raise TypeError(msg)
        # Linux AppImage names carry the version, so a staged roll-out shows up
        # as a payload the filename contradicts.
        if api_platform.startswith("linux"):
            match = _CURSOR_APPIMAGE_VERSION_RE.search(url)
            if match is None or match.group("version") != named:
                msg = f"Cursor {api_platform} channel named {named} but published {url}"
                raise RuntimeError(msg)
        return named

    async def _fetch_track_entry(
        self,
        session: aiohttp.ClientSession,
        api_platform: str,
    ) -> tuple[str, str]:
        url = self._api_url(api_platform)
        response = await request(session, url, config=self.config)
        if response.status == _HTTP_NO_CONTENT:
            msg = (
                f"Cursor {self.TRACK} channel reported no build newer than "
                f"{self.DISCOVERY_VERSION} for {api_platform}"
            )
            raise RuntimeError(msg)
        payload = decode_json(
            response.body,
            context=f"Cursor {self.TRACK} channel for {api_platform}",
        )
        if not isinstance(payload, dict):
            msg = (
                f"Expected a JSON object from the Cursor {self.TRACK} channel "
                f"for {api_platform}"
            )
            raise TypeError(msg)
        artifact = payload.get("url")
        if not isinstance(artifact, str):
            msg = f"Cursor {self.TRACK} channel named no artifact for {api_platform}"
            raise TypeError(msg)
        resolved = self._artifact_url(api_platform, artifact)
        return self._payload_version(api_platform, payload, resolved), resolved

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
        """Fetch the newest build on the tracked channel for every platform."""
        entries = await asyncio.gather(
            *(
                self._fetch_track_entry(session, api_platform)
                for api_platform in self.PLATFORMS.values()
            )
        )
        resolved = dict(zip(self.PLATFORMS, entries, strict=True))
        versions = {platform: version for platform, (version, _url) in resolved.items()}
        urls = {platform: url for platform, (_version, url) in resolved.items()}
        version = self._uniform_str_value(versions)
        commit = self._uniform_regex_value(
            urls,
            _CURSOR_PRODUCTION_COMMIT_RE,
            "commit",
        )
        # Cursor stages platforms independently: keep the complete pin until
        # every platform names one version and commit.
        if version is None or commit is None:
            return self._retain_verified_pin(context, resolved_urls=urls)
        # A channel can lag a build already pinned; keep the verified source pin
        # instead of stepping backwards.
        if (
            context.current is not None
            and context.current.version is not None
            and Version(version) < Version(context.current.version)
        ):
            return self._retain_verified_pin(context, resolved_urls=urls)
        return VersionInfo(
            version=version,
            metadata=PlatformAPIMetadata(
                platform_info={
                    platform: {"downloadUrl": url} for platform, url in urls.items()
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
