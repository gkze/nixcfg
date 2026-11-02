"""Updater for the anthropic-cli Go vendor hash."""

from lib.update.updaters import register_updater
from lib.update.updaters.go_compatibility import GoModCompatibilityUpdater


@register_updater
class AnthropicCliUpdater(GoModCompatibilityUpdater):
    """Go vendor hash updater for anthropic-cli."""

    name = "anthropic-cli"
    GITHUB_OWNER = "anthropics"
    GITHUB_REPO = "anthropic-cli"
