"""Updater for Ghostty tip."""

from __future__ import annotations

import re

from lib.update.updaters.base import sparkle_appcast_updater
from lib.update.updaters.vendor_feeds import (
    SparkleAppcastItem,
    require_url,
    require_version,
)

_APPCAST_URL = "https://tip.files.ghostty.org/appcast.xml"


def _ghostty_tip_version(item: SparkleAppcastItem) -> str:
    build = require_version(item.version, context=_APPCAST_URL)
    url = require_url(item.url, context=_APPCAST_URL)
    match = re.search(r"/([0-9a-f]{40})/Ghostty\.dmg$", url)
    if match is None:
        msg = f"Could not parse Ghostty tip commit from URL: {url}"
        raise RuntimeError(msg)
    return f"{build}-{match.group(1)}"


GhosttyTipUpdater = sparkle_appcast_updater(
    "ghostty-tip",
    appcast_url=_APPCAST_URL,
    platforms={"aarch64-darwin": "darwin"},
    version_transform=_ghostty_tip_version,
    appcast_url_metadata=True,
    url_metadata_context="Ghostty tip metadata",
    module=__name__,
)
