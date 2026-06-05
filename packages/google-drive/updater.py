"""Updater for Google Drive for desktop."""

from __future__ import annotations

from lib.update.updaters.base import head_artifact_download_updater

DOWNLOAD_URL = "https://dl.google.com/drive-file-stream/GoogleDrive.dmg"

GoogleDriveUpdater = head_artifact_download_updater(
    "google-drive",
    download_url=DOWNLOAD_URL,
    platforms={"aarch64-darwin": DOWNLOAD_URL},
    module=__name__,
)
