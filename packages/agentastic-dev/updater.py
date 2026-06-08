"""Updater for the pinned Agentastic.dev macOS app archive."""

from __future__ import annotations

from lib.update.updaters.base import pinned_source_download_updater

AgentasticDevUpdater = pinned_source_download_updater(
    "agentastic-dev",
    platforms={"aarch64-darwin": "arm64"},
    download_url="https://releases.agentastic.ai/agentasticdev/Agentastic.dev-{version}.dmg",
    module=__name__,
)
