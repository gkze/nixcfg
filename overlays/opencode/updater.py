"""Updater for opencode's platform-specific Bun offline cache hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.update.updaters import (
    BunNodeModulesHashUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry


@register_updater
class OpencodeUpdater(BunNodeModulesHashUpdater):
    """Bun node_modules hash updater for opencode."""

    SUPPORTED_PLATFORMS = (
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    )

    name = "opencode"
    supported_platforms = SUPPORTED_PLATFORMS

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        if not await super()._is_latest(context, info):
            return False

        entry = context.current if isinstance(context, UpdateContext) else context
        if entry is None:
            return False

        hashes = entry.hashes
        platforms = (
            {
                hash_entry.platform
                for hash_entry in hashes.entries
                if hash_entry.platform is not None
                and hash_entry.hash_type == self.hash_type
            }
            if hashes.entries
            else set(hashes.mapping or {})
        )
        return platforms == set(self.SUPPORTED_PLATFORMS)
