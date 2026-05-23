"""Updater for Claude Code."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.update.net import fetch_url
from lib.update.updaters.base import DownloadHashUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    import aiohttp


@register_updater
class ClaudeCodeUpdater(DownloadHashUpdater):
    """Resolve Claude Code from Anthropic's stable binary pointer."""

    name = "claude-code"
    STABLE_VERSION_URL = (
        "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/"
        "claude-code-releases/stable"
    )
    BASE_URL = (
        "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/"
        "claude-code-releases"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the current stable Claude Code binary version."""
        payload = await fetch_url(
            session,
            self.STABLE_VERSION_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        version = payload.decode().strip()
        if not version:
            msg = f"Missing Claude Code version in {self.STABLE_VERSION_URL}"
            raise RuntimeError(msg)
        return VersionInfo(version=version)

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return Anthropic's versioned Claude Code binary URL."""
        return f"{self.BASE_URL}/{info.version}/{self.PLATFORMS[platform]}/claude"
