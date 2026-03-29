"""Updater for goose-v8 source hash."""

from lib.update.updaters.base import flake_input_hash_updater

GooseV8Updater = flake_input_hash_updater("goose-v8", "srcHash", module=__name__)
