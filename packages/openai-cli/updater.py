"""Updater for the openai-cli Go vendor hash."""

from lib.update.updaters import GoVendorHashUpdater, register_updater


@register_updater
class OpenaiCliUpdater(GoVendorHashUpdater):
    """Go vendor hash updater for openai-cli."""

    name = "openai-cli"
