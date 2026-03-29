"""Updater for toad checked-in uv.lock."""

from lib.update.updaters.base import uv_lock_updater

ToadUpdater = uv_lock_updater("toad", module=__name__)
