"""Updater for the pinned Todoist desktop macOS app archive."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import PinnedSourceDownloadUpdater, register_updater


@register_updater
class TodoistDesktopUpdater(PinnedSourceDownloadUpdater):
    """Pinned download updater for the Todoist desktop macOS app archive."""

    name = "todoist-desktop"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://electron-dl.todoist.net/mac/"
        "Todoist-darwin-{version}-{platform_value}-latest.dmg"
    )
