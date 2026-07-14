"""Updater for Logitech Options+."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import HeadArtifactDownloadUpdater, register_updater

DOWNLOAD_URL = (
    "https://download01.logi.com/web/ftp/pub/techsupport/optionsplus/"
    "logioptionsplus_installer.zip"
)


@register_updater
class LogiOptionsPlusUpdater(HeadArtifactDownloadUpdater):
    """Version Logitech Options+ by the mutable installer's response headers."""

    name = "logi-options-plus"
    HEAD_URL = DOWNLOAD_URL
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": DOWNLOAD_URL}
