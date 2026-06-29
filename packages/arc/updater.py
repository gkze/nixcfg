"""Updater for Arc macOS releases."""

from __future__ import annotations

import re

from lib.update.updaters.base import sparkle_appcast_updater
from lib.update.updaters.vendor_feeds import SparkleAppcastItem, require_version

_APPCAST_URL = "https://releases.arc.net/updates.xml"


def _arc_version(item: SparkleAppcastItem) -> str:
    raw_version = require_version(
        item.short_version or item.version, context=_APPCAST_URL
    )
    version_match = re.match(r"([0-9]+(?:\.[0-9]+)+)", raw_version)
    if version_match is None:
        msg = f"Could not parse Arc version from {raw_version!r}"
        raise RuntimeError(msg)
    return version_match.group(1)


ArcUpdater = sparkle_appcast_updater(
    "arc",
    appcast_url=_APPCAST_URL,
    platforms={
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    },
    version_transform=_arc_version,
    appcast_url_metadata=True,
    url_metadata_context="Arc metadata",
    module=__name__,
)
