"""Updater for anthropic-cli Go vendor hash."""

from lib.update.updaters.base import go_vendor_updater

AnthropicCliUpdater = go_vendor_updater("anthropic-cli", module=__name__)
