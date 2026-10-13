"""Updater for opencode's platform-specific Bun offline cache hashes."""

from lib.system_policy import supported_systems
from lib.update.updaters import (
    BunNodeModulesHashUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.core import hashes_by_platform


@register_updater
class OpencodeUpdater(BunNodeModulesHashUpdater):
    """Bun node_modules hash updater for opencode."""

    SUPPORTED_PLATFORMS = supported_systems()

    name = "opencode"
    supported_platforms = SUPPORTED_PLATFORMS

    async def _is_latest(
        self,
        context: UpdateContext,
        info: VersionInfo,
    ) -> bool:
        if not await super()._is_latest(context, info):
            return False

        entry = context.current
        if entry is None:
            return False

        platforms = set(hashes_by_platform(entry.hashes, hash_type=self.hash_type))
        return platforms == set(self.SUPPORTED_PLATFORMS)
