"""Shared helpers for GitHub latest-release updaters."""

import re
import urllib.parse
from typing import TYPE_CHECKING, ClassVar, cast

from lib.update.net import fetch_github_api
from lib.update.updaters.core import DownloadHashUpdater, Updater
from lib.update.updaters.metadata import (
    AssetURLsMetadata,
    GitHubReleaseMetadata,
    VersionInfo,
)

if TYPE_CHECKING:
    import aiohttp


class GitHubReleaseUpdater(Updater):
    """Base updater for packages resolved from GitHub latest releases."""

    GITHUB_OWNER: str
    GITHUB_REPO: str
    TAG_PREFIX = "v"
    RESOLVE_TAG_COMMIT: ClassVar[bool] = False
    RELEASE_DISPLAY_NAME: ClassVar[str] = ""

    _COMMIT_PATTERN: ClassVar[re.Pattern[str]] = re.compile(r"^[0-9a-f]{40}$")

    @property
    def _release_display_name(self) -> str:
        return self.RELEASE_DISPLAY_NAME or self.name

    def _require_commit(self, info: VersionInfo) -> str:
        """Return validated immutable release metadata for source consumers."""
        commit = info.commit
        if commit is None or self._COMMIT_PATTERN.fullmatch(commit) is None:
            msg = (
                f"{self._release_display_name} release metadata is missing an "
                "immutable source commit"
            )
            raise RuntimeError(msg)
        return commit

    async def _fetch_latest_release_payload(
        self,
        session: aiohttp.ClientSession,
    ) -> dict[str, object]:
        payload = await fetch_github_api(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest",
            config=self.config,
        )
        if not isinstance(payload, dict):
            msg = f"Unexpected release payload type: {type(payload).__name__}"
            raise TypeError(msg)
        return cast("dict[str, object]", payload)

    def _release_tag_from_payload(self, payload: dict[str, object]) -> str:
        tag_name = payload.get("tag_name")
        if not isinstance(tag_name, str) or not tag_name:
            msg = f"Missing tag_name in release payload: {payload!r}"
            raise RuntimeError(msg)
        return tag_name

    def _normalize_release_version(self, tag_name: str) -> str:
        if self.TAG_PREFIX and not tag_name.startswith(self.TAG_PREFIX):
            msg = f"Unexpected release tag format for {self.name}: {tag_name}"
            raise RuntimeError(msg)
        version = tag_name.removeprefix(self.TAG_PREFIX)
        if not version:
            msg = f"Missing version segment in release tag for {self.name}: {tag_name}"
            raise RuntimeError(msg)
        return version

    async def _resolve_release_tag_commit(
        self,
        session: aiohttp.ClientSession,
        tag_name: str,
    ) -> str:
        tag_path = urllib.parse.quote(tag_name, safe="")
        payload = await fetch_github_api(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/commits/{tag_path}",
            config=self.config,
        )
        message = (
            f"{self._release_display_name} release {tag_name} has no immutable "
            "source commit"
        )
        if not isinstance(payload, dict):
            raise TypeError(message)
        commit = payload.get("sha")
        if (
            not isinstance(commit, str)
            or self._COMMIT_PATTERN.fullmatch(commit) is None
        ):
            raise RuntimeError(message)
        return commit

    async def _fetch_release_version_tag_commit(
        self,
        session: aiohttp.ClientSession,
    ) -> tuple[str, str, str]:
        """Resolve the latest release to a version, tag, and immutable commit."""
        payload = await self._fetch_latest_release_payload(session)
        tag_name = self._release_tag_from_payload(payload)
        version = self._normalize_release_version(tag_name)
        commit = await self._resolve_release_tag_commit(session, tag_name)
        return version, tag_name, commit

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Resolve version metadata from the latest GitHub release tag."""
        if self.RESOLVE_TAG_COMMIT:
            version, tag_name, commit = await self._fetch_release_version_tag_commit(
                session
            )
            return VersionInfo(
                version=version,
                metadata={"commit": commit, "tag": tag_name},
            )

        payload = await self._fetch_latest_release_payload(session)
        tag_name = self._release_tag_from_payload(payload)
        return VersionInfo(
            version=self._normalize_release_version(tag_name),
            metadata=GitHubReleaseMetadata(tag=tag_name),
        )


class GitHubReleaseAssetURLsUpdater(GitHubReleaseUpdater, DownloadHashUpdater):
    """Download-hash updater that resolves assets from a GitHub latest release."""

    # Optional ``str.format`` template with ``{version}`` and
    # ``{platform_value}`` fields; subclasses either set it or override
    # ``_asset_name``.
    ASSET_NAME_TEMPLATE: ClassVar[str] = ""

    def _asset_name(self, version: str, platform_value: str) -> str:
        if not self.ASSET_NAME_TEMPLATE:
            raise NotImplementedError
        return self.ASSET_NAME_TEMPLATE.format(
            version=version,
            platform_value=platform_value,
        )

    def _fallback_url(self, version: str, platform_value: str) -> str:
        return (
            f"https://github.com/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/download/"
            f"{self.TAG_PREFIX}{version}/{self._asset_name(version, platform_value)}"
        )

    def _missing_asset_message(self, expected_name: str, tag_name: str) -> str:
        return f"Could not find {self.name} release asset {expected_name!r} in tag {tag_name}"

    def _asset_urls_from_payload(
        self,
        payload: dict[str, object],
        *,
        version: str,
        tag_name: str,
    ) -> dict[str, str]:
        assets = payload.get("assets")
        if not isinstance(assets, list):
            msg = f"Missing assets in release payload for tag {tag_name}"
            raise TypeError(msg)

        asset_urls: dict[str, str] = {}
        for platform, platform_value in self.PLATFORMS.items():
            expected_name = self._asset_name(version, platform_value)
            download_url: str | None = None
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                asset_payload = cast("dict[str, object]", asset)
                if asset_payload.get("name") != expected_name:
                    continue
                candidate = asset_payload.get("browser_download_url")
                if isinstance(candidate, str) and candidate:
                    download_url = candidate
                    break
            if download_url is None:
                raise RuntimeError(self._missing_asset_message(expected_name, tag_name))
            asset_urls[platform] = download_url
        return asset_urls

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Resolve latest version and matching release asset URLs."""
        payload = await self._fetch_latest_release_payload(session)
        tag_name = self._release_tag_from_payload(payload)
        version = self._normalize_release_version(tag_name)
        return VersionInfo(
            version=version,
            metadata=AssetURLsMetadata(
                self._asset_urls_from_payload(
                    payload,
                    version=version,
                    tag_name=tag_name,
                )
            ),
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return a resolved release asset URL, falling back to the convention."""
        metadata = info.metadata
        asset_urls = (
            metadata.asset_urls if isinstance(metadata, AssetURLsMetadata) else None
        )
        if asset_urls is not None:
            candidate = asset_urls.get(platform)
            if isinstance(candidate, str) and candidate:
                return candidate

        platform_value = self.PLATFORMS.get(platform)
        if platform_value is None:
            msg = f"Unsupported platform for {self.name} updater: {platform}"
            raise RuntimeError(msg)
        return self._fallback_url(info.version, platform_value)


__all__ = ["GitHubReleaseAssetURLsUpdater", "GitHubReleaseUpdater"]
