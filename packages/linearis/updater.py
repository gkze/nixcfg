"""Updater for linearis npm dependency hash."""

from lib.update.updaters.base import npm_deps_updater

LinearisUpdater = npm_deps_updater("linearis", module=__name__)
