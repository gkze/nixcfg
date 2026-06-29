"""Updater for the Town Assistant nightly macOS appcast."""

from __future__ import annotations

from lib.update.updaters.base import sparkle_appcast_updater
from lib.update.updaters.vendor_feeds import SparkleAppcastItem, require_version

_APPCAST_URL = (
    "https://town-macos-app.s3.us-east-1.amazonaws.com/desktop/nightly/appcast.xml"
)


def _nightly_version(item: SparkleAppcastItem) -> str:
    short_version = require_version(item.short_version, context=_APPCAST_URL)
    build_version = require_version(item.version, context=_APPCAST_URL)
    return f"{short_version}-{build_version}"


TownAssistantNightlyUpdater = sparkle_appcast_updater(
    "town-assistant-nightly",
    appcast_url=_APPCAST_URL,
    platforms={"aarch64-darwin": "darwin-aarch64"},
    version_transform=_nightly_version,
    appcast_url_metadata=True,
    url_metadata_context="Town Assistant nightly metadata",
    module=__name__,
)
