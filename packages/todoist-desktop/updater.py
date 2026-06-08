"""Updater for the pinned Todoist desktop macOS app archive."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater

TodoistDesktopUpdater = pinned_source_download_updater(
    "todoist-desktop",
    platforms={"aarch64-darwin": "arm64"},
    download_url=(
        "https://electron-dl.todoist.net/mac/"
        "Todoist-darwin-{version}-{platform_value}-latest.dmg"
    ),
    module=__name__,
)
