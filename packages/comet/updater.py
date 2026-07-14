"""Updater for Comet browser macOS releases."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import JsonFieldDownloadUpdater, register_updater


@register_updater
class CometUpdater(JsonFieldDownloadUpdater):
    """Resolve Comet versions from Perplexity's browser update endpoint."""

    name = "comet"
    JSON_URL = (
        "https://www.perplexity.ai/rest/browser/update?"
        "browser=1.1.1.1&channel=stable&machine=0&platform=mac_arm64"
    )
    VERSION_PATH = ("body", "browser_version")
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://www.perplexity.ai/rest/browser/download?channel=stable&platform=mac_arm64",
        "x86_64-darwin": "https://www.perplexity.ai/rest/browser/download?channel=stable&platform=mac_x64",
    }
