"""Updater for official Grok Bot desktop releases."""

import asyncio
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar
from urllib.parse import urlsplit

from lib import json_utils
from lib.update.net import fetch_json
from lib.update.updaters import VersionInfo, register_updater
from lib.update.updaters.metadata import PlatformAPIMetadata
from lib.update.updaters.platform_api import DownloadingPlatformAPIUpdater

if TYPE_CHECKING:
    from collections.abc import Mapping

    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.updaters import UpdateContext

_DISCOVERY_VERSION = "0.0.0"
_MACHINE_ID = "00000000-0000-0000-0000-000000000000"
_ARTIFACT_HOST = "downloads.cursor.com"
_VERSION_PATTERN = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
)
_ARTIFACT_PATH_PATTERN = re.compile(
    r"/grokbot/stable/(?P<api_platform>darwin-(?:arm64|x64))/"
    rf"(?P<version>{_VERSION_PATTERN.pattern})/(?P<filename>[^/]+)"
)


@dataclass(frozen=True, slots=True)
class _ResolvedRelease:
    version: str
    url: str


@register_updater
class GrokBotUpdater(DownloadingPlatformAPIUpdater):
    """Resolve stable Grok Bot ZIPs from Cursor's desktop update service."""

    name = "grok-bot"
    API_BASE = "https://api2.cursor.sh/updates/api/update"
    required_tools = ("nix", "nix-prefetch-url")
    supported_platforms = ("aarch64-darwin", "x86_64-darwin")
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-x64",
    }

    def _api_url(self, _api_platform: str) -> str:
        return (
            f"{self.API_BASE}/{_api_platform}/sand/{_DISCOVERY_VERSION}/"
            f"{_MACHINE_ID}/stable"
        )

    @classmethod
    def _parse_artifact_url(cls, platform: str, url: str) -> tuple[str, str]:
        parsed = urlsplit(url)
        match = _ARTIFACT_PATH_PATTERN.fullmatch(parsed.path)
        expected_api_platform = cls.PLATFORMS[platform]
        version = match.group("version") if match is not None else None
        filename_suffix = "_x64" if expected_api_platform == "darwin-x64" else ""
        expected_filename = (
            f"Grok_Bot_{version}{filename_suffix}.zip" if version is not None else None
        )
        if (
            parsed.scheme != "https"
            or parsed.netloc != _ARTIFACT_HOST
            or "?" in url
            or "#" in url
            or match is None
            or match.group("api_platform") != expected_api_platform
            or match.group("filename") != expected_filename
        ):
            msg = f"Grok Bot feed returned an invalid {platform} artifact URL: {url}"
            raise RuntimeError(msg)
        return match.group("version"), url

    @classmethod
    def _parse_feed_payload(
        cls,
        platform: str,
        payload: Mapping[str, object],
    ) -> _ResolvedRelease:
        payload_dict = dict(payload)
        fields = set(payload_dict)
        if fields != {"name", "url"}:
            msg = (
                f"Grok Bot feed for {platform} returned unexpected fields: "
                f"{sorted(fields)}"
            )
            raise RuntimeError(msg)
        version = json_utils.get_required_str(
            payload_dict,
            "name",
            context=f"Grok Bot feed for {platform}",
        )
        if _VERSION_PATTERN.fullmatch(version) is None:
            msg = f"Grok Bot feed returned invalid version {version!r} for {platform}"
            raise RuntimeError(msg)
        url = json_utils.get_required_str(
            payload_dict,
            "url",
            context=f"Grok Bot feed for {platform}",
        )
        artifact_version, validated_url = cls._parse_artifact_url(platform, url)
        if artifact_version != version:
            msg = (
                f"Grok Bot {platform} artifact version {artifact_version!r} "
                f"does not match feed version {version!r}"
            )
            raise RuntimeError(msg)
        return _ResolvedRelease(
            version=version,
            url=validated_url,
        )

    @classmethod
    def _nix_platform(cls, api_platform: str) -> str:
        for nix_platform, candidate in cls.PLATFORMS.items():
            if candidate == api_platform:
                return nix_platform
        msg = f"Unknown Grok Bot API platform: {api_platform}"
        raise RuntimeError(msg)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve one stable release and its validated per-architecture ZIPs."""

        async def _fetch_one(
            platform: str,
            api_platform: str,
        ) -> tuple[str, json_utils.JsonObject, _ResolvedRelease]:
            payload_value = await fetch_json(
                session,
                self._api_url(api_platform),
                config=self.config,
            )
            payload = json_utils.coerce_json_object(
                payload_value,
                context=f"Grok Bot feed for {platform}",
            )
            return platform, payload, self._parse_feed_payload(platform, payload)

        results = await asyncio.gather(
            *(
                _fetch_one(platform, api_platform)
                for platform, api_platform in self.PLATFORMS.items()
            )
        )
        releases = {platform: release for platform, _payload, release in results}
        versions = {platform: release.version for platform, release in releases.items()}
        if len(set(versions.values())) != 1:
            msg = f"Grok Bot feed returned mismatched versions: {versions}"
            raise RuntimeError(msg)
        version = next(iter(versions.values()))
        return VersionInfo(
            version=version,
            metadata=PlatformAPIMetadata(
                platform_info={
                    platform: payload for platform, payload, _release in results
                },
                equality_fields={},
            ),
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Rehash because the vendor may replace a versioned artifact in place."""
        _ = (context, info)
        return False

    def _download_url(self, _api_platform: str, info: VersionInfo) -> str:
        nix_platform = self._nix_platform(_api_platform)
        metadata = self._metadata(info)
        payload = metadata.platform_info.get(nix_platform)
        if not isinstance(payload, dict):
            msg = f"Expected Grok Bot platform payload for {nix_platform}"
            raise TypeError(msg)
        release = self._parse_feed_payload(nix_platform, payload)
        if release.version != info.version:
            msg = (
                f"Grok Bot {nix_platform} artifact version {release.version!r} "
                f"does not match release version {info.version!r}"
            )
            raise RuntimeError(msg)
        return release.url
