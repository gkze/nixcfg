"""Updater for toad checked-in uv.lock."""

from lib.update.updaters import UvLockUpdater, register_updater


@register_updater
class ToadUpdater(UvLockUpdater):
    """Uv lock updater for toad."""

    name = "toad"
