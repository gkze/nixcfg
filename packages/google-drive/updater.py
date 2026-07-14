"""Updater for Google Drive for desktop."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import HeadArtifactDownloadUpdater, register_updater

DOWNLOAD_URL = "https://dl.google.com/drive-file-stream/GoogleDrive.dmg"


@register_updater
class GoogleDriveUpdater(HeadArtifactDownloadUpdater):
    """Version Google Drive by the mutable DMG's response headers."""

    name = "google-drive"
    HEAD_URL = DOWNLOAD_URL
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": DOWNLOAD_URL}
