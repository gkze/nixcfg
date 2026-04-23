"""Updater for opencode-desktop-electron's Bun node_modules hash."""

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
class OpencodeDesktopElectronUpdater(FlakeInputHashUpdater):
    """Track platform-specific node_modules hashes for every supported runtime."""

    SUPPORTED_PLATFORMS = (
        "aarch64-darwin",
        "x86_64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    )

    name = "opencode-desktop-electron"
    input_name = "opencode"
    hash_type = "nodeModulesHash"
    platform_specific = True
    native_only = False

    def _platform_targets(self, current_platform: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys((current_platform, *self.SUPPORTED_PLATFORMS)))

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
