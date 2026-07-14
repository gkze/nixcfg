"""Updater for the anthropic-cli Go vendor hash."""

from lib.update.updaters import GoVendorHashUpdater, register_updater


@register_updater
class AnthropicCliUpdater(GoVendorHashUpdater):
    """Go vendor hash updater for anthropic-cli."""

    name = "anthropic-cli"
