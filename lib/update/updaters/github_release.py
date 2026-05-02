"""Shared helpers for GitHub latest-release updaters."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from lib.update.net import fetch_github_api
from lib.update.updaters.base import DownloadHashUpdater, Updater
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
        return payload

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

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Resolve version metadata from the latest GitHub release tag."""
        payload = await self._fetch_latest_release_payload(session)
        tag_name = self._release_tag_from_payload(payload)
        return VersionInfo(
            version=self._normalize_release_version(tag_name),
            metadata=GitHubReleaseMetadata(tag=tag_name),
        )


class GitHubReleaseAssetURLsUpdater(GitHubReleaseUpdater, DownloadHashUpdater):
    """Download-hash updater that resolves assets from a GitHub latest release."""

    def _asset_name(self, version: str, platform_value: str) -> str:
        raise NotImplementedError

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
