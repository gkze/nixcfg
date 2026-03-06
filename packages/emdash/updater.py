"""Updater for emdash flake input hash."""

from lib.update.updaters.base import flake_input_hash_updater

flake_input_hash_updater("emdash", "npmDepsHash", platform_specific=True)
