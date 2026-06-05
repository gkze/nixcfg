"""Updater for Claude Code."""

from __future__ import annotations

from lib.update.updaters.base import version_endpoint_download_updater

ClaudeCodeUpdater = version_endpoint_download_updater(
    "claude-code",
    version_url=(
        "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/"
        "claude-code-releases/stable"
    ),
    platforms={
        "aarch64-darwin": "darwin-arm64",
    },
    download_url=(
        "https://storage.googleapis.com/claude-code-dist-86c565f3-f756-42ad-8dfa-d59b1c096819/"
        "claude-code-releases/{version}/{platform_value}/claude"
    ),
    display_name="Claude Code",
    module=__name__,
)
