"""Updater for goose-v8 source hash."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.update.updaters.base import (
    FlakeInputHashUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry


@register_updater
class GooseV8Updater(FlakeInputHashUpdater):
    """Track the pinned goose-v8 source without rehashing unchanged revisions.

    The goose V8 fork is pinned to an exact commit in ``flake.nix`` and fetched
    recursively with Chromium submodules. Recomputing the same ``srcHash`` on
    every unrelated flake refresh needlessly re-hits upstream submodule hosts,
    so treat an unchanged pinned revision with an existing ``srcHash`` as
    current.
    """

    name = "goose-v8"
    hash_type = "srcHash"

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        current = getattr(context, "current", context)
        if current is None or getattr(current, "version", None) != info.version:
            return False

        hashes = getattr(current, "hashes", None)
        entries = getattr(hashes, "entries", None)
        if entries is None:
            return False
        return any(
            getattr(entry, "hash_type", None) == self.hash_type for entry in entries
        )
