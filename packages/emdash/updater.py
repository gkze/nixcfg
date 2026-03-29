"""Updater for emdash flake input hash."""

from lib.update.updaters.base import flake_input_hash_updater

EmdashUpdater = flake_input_hash_updater(
    "emdash",
    "npmDepsHash",
    module=__name__,
    platform_specific=True,
)
