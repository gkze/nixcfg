"""Updater for Claude Code."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import VersionEndpointDownloadUpdater, register_updater


@register_updater
class ClaudeCodeUpdater(VersionEndpointDownloadUpdater):
    """Resolve Claude Code versions from the stable release channel endpoint."""

    name = "claude-code"
    VERSION_URL = (
        "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/"
        "claude-code-releases/stable"
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/"
        "claude-code-releases/{version}/{platform_value}/claude"
    )
