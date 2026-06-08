"""Updater for openai-cli Go vendor hash."""

from lib.update.updaters.base import go_vendor_updater

OpenAICliUpdater = go_vendor_updater("openai-cli", module=__name__)
