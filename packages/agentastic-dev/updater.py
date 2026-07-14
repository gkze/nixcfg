"""Updater for the pinned Agentastic.dev macOS app archive."""

from __future__ import annotations

from typing import ClassVar

from lib.update.updaters import PinnedSourceDownloadUpdater, register_updater


@register_updater
class AgentasticDevUpdater(PinnedSourceDownloadUpdater):
    """Pinned download updater for the Agentastic.dev macOS app archive."""

    name = "agentastic-dev"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
    DOWNLOAD_URL_TEMPLATE = (
        "https://releases.agentastic.ai/agentasticdev/Agentastic.dev-{version}.dmg"
    )
