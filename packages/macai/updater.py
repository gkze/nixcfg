"""Updater for macai macOS releases."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, register_updater


@register_updater
class MacAIUpdater(SparkleAppcastUrlUpdater):
    """Resolve macai versions and download URLs from its Sparkle feed."""

    name = "macai"
    APPCAST_URL = "https://renset.dev/macai/appcast.xml"
    VERSION_FIELD = "short_or_version"
    URL_METADATA_CONTEXT: ClassVar[str | None] = "macai metadata"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }
