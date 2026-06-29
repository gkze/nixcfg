"""Updater for ChatGPT desktop app releases."""

from __future__ import annotations

from lib.update.updaters.base import sparkle_appcast_updater

ChatGPTUpdater = sparkle_appcast_updater(
    "chatgpt",
    appcast_url=(
        "https://persistent.oaistatic.com/sidekick/public/sparkle_public_appcast.xml"
    ),
    platforms={
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    },
    version_field="short_version",
    appcast_url_metadata=True,
    url_metadata_context="ChatGPT metadata",
    module=__name__,
)
