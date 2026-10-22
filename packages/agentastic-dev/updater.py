"""Updater for the Agentastic.dev signed release channel."""

from typing import ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, register_updater


@register_updater
class AgentasticDevUpdater(SparkleAppcastUrlUpdater):
    """Track the release feed used by the installed Agentastic.dev app."""

    name = "agentastic-dev"
    APPCAST_URL = "https://releases.agentastic.ai/agentasticdev/appcast.xml"
    VERSION_FIELD = "short_version"
    PLATFORMS: ClassVar[dict[str, str]] = {"aarch64-darwin": "arm64"}
