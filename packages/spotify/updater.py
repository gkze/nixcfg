"""Updater for Spotify."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import HeadArtifactDownloadUpdater, register_updater

DOWNLOAD_URL = "https://download.scdn.co/SpotifyARM64.dmg"


@register_updater
class SpotifyUpdater(HeadArtifactDownloadUpdater):
    """Version Spotify by the mutable DMG's response headers."""

    name = "spotify"
    HEAD_URL = DOWNLOAD_URL
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": DOWNLOAD_URL}
