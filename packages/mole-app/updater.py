"""Updater for the pinned Mole helper binary archives."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import PinnedSourceDownloadUpdater, register_updater


@register_updater
class MoleAppUpdater(PinnedSourceDownloadUpdater):
    """Pinned download updater for the Mole helper binary archives."""

    name = "mole-app"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-amd64",
    }
    DOWNLOAD_URL_TEMPLATE = (
        "https://github.com/tw93/Mole/releases/download/"
        "V{version}/binaries-{platform_value}.tar.gz"
    )
