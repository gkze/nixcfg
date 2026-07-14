"""Updater for hermes-agent flake input metadata."""

from lib.update.updaters import FlakeInputMetadataUpdater, register_updater


@register_updater
class HermesAgentUpdater(FlakeInputMetadataUpdater):
    """Track the hermes-agent flake input ref and locked commit."""

    name = "hermes-agent"
