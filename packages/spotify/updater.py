"""Updater for Spotify."""

from __future__ import annotations

from lib.update.updaters.base import head_artifact_download_updater

DOWNLOAD_URL = "https://download.scdn.co/SpotifyARM64.dmg"

SpotifyUpdater = head_artifact_download_updater(
    "spotify",
    download_url=DOWNLOAD_URL,
    platforms={"aarch64-darwin": DOWNLOAD_URL},
    module=__name__,
)
