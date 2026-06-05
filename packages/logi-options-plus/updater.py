"""Updater for Logitech Options+."""

from __future__ import annotations

from lib.update.updaters.base import head_artifact_download_updater

DOWNLOAD_URL = (
    "https://download01.logi.com/web/ftp/pub/techsupport/optionsplus/"
    "logioptionsplus_installer.zip"
)

LogiOptionsPlusUpdater = head_artifact_download_updater(
    "logi-options-plus",
    download_url=DOWNLOAD_URL,
    platforms={"aarch64-darwin": DOWNLOAD_URL},
    module=__name__,
)
