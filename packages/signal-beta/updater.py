"""Updater for Signal Desktop beta macOS releases."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, cast

from lib.update.net import fetch_github_api_paginated
from lib.update.updaters import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    import aiohttp


@register_updater
class SignalBetaUpdater(DownloadHashUpdater):
    """Resolve the latest Signal Desktop beta tag and per-arch ZIP URLs."""

    name = "signal-beta"
    GITHUB_OWNER = "signalapp"
    GITHUB_REPO = "Signal-Desktop"
    TAG_PREFIX = "v"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
    }

    def _normalize_release_version(self, tag_name: str) -> str:
        if not tag_name.startswith(self.TAG_PREFIX):
            msg = f"Unexpected Signal beta release tag: {tag_name}"
            raise RuntimeError(msg)
        version = tag_name.removeprefix(self.TAG_PREFIX)
        if "-beta." not in version:
            msg = f"Signal release tag is not a beta: {tag_name}"
            raise RuntimeError(msg)
        return version

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the newest prerelease beta tag from GitHub releases."""
        releases = await fetch_github_api_paginated(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases",
            config=self.config,
            per_page=20,
            item_limit=40,
        )
        for release in releases:
            if not isinstance(release, dict):
                msg = f"Unexpected Signal release payload: {release!r}"
                raise TypeError(msg)
            release_payload = cast("dict[str, object]", release)
            if release_payload.get("draft") is True:
                continue
            if release_payload.get("prerelease") is not True:
                continue
            tag_name = release_payload.get("tag_name")
            if not isinstance(tag_name, str) or not tag_name:
                msg = f"Missing Signal release tag in payload: {release_payload!r}"
                raise RuntimeError(msg)
            version = self._normalize_release_version(tag_name)
            return VersionInfo(version=version)
        msg = "No Signal Desktop beta release found"
        raise RuntimeError(msg)

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return Signal's versioned beta ZIP URL for the platform."""
        arch = self.PLATFORMS[platform]
        return (
            "https://updates.signal.org/desktop/"
            f"signal-desktop-beta-mac-{arch}-{info.version}.zip"
        )
