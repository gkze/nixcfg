"""Updater for Comet browser macOS releases."""

from __future__ import annotations

from lib.update.updaters.base import json_field_download_updater

CometUpdater = json_field_download_updater(
    "comet",
    json_url=(
        "https://www.perplexity.ai/rest/browser/update?"
        "browser=1.1.1.1&channel=stable&machine=0&platform=mac_arm64"
    ),
    version_path=("body", "browser_version"),
    platforms={
        "aarch64-darwin": "https://www.perplexity.ai/rest/browser/download?channel=stable&platform=mac_arm64",
        "x86_64-darwin": "https://www.perplexity.ai/rest/browser/download?channel=stable&platform=mac_x64",
    },
    display_name="Comet",
    module=__name__,
)
