"""Updater for linearis npm dependency hash."""

from lib.update.updaters.base import flake_input_hash_updater

LinearisUpdater = flake_input_hash_updater(
    "linearis",
    "npmDepsHash",
    module=__name__,
    platform_specific=True,
)
