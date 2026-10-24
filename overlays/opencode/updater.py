"""Updater for opencode's exact Bun runtime and platform node_modules hashes."""

from typing import TYPE_CHECKING, override

from lib.system_policy import supported_systems
from lib.update.bun_toolchain import require_bun_package_manager
from lib.update.locked_source import locked_github_source
from lib.update.net import fetch_json, github_raw_url
from lib.update.updaters import UpdateContext, VersionInfo, register_updater
from lib.update.updaters.core import hashes_by_platform
from lib.update.updaters.exact_bun import (
    ExactBunMetadata,
    ExactBunNodeModulesHashUpdater,
)

if TYPE_CHECKING:
    import aiohttp

_MANIFEST_PATH = "package.json"


@register_updater
class OpencodeUpdater(ExactBunNodeModulesHashUpdater):
    """Bun node_modules hash updater for opencode."""

    SUPPORTED_PLATFORMS = supported_systems()

    name = "opencode"
    supported_platforms = SUPPORTED_PLATFORMS

    @override
    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve the exact Bun pinned by the immutable refreshed input commit."""
        info = await super().fetch_latest(session, context=context)
        node = self._resolve_flake_node(info)
        owner, repo, commit = locked_github_source(node, context="OpenCode flake input")
        manifest = await fetch_json(
            session,
            github_raw_url(owner, repo, commit, _MANIFEST_PATH),
            config=self.config,
        )
        return VersionInfo(
            version=info.version,
            metadata=ExactBunMetadata(
                node=node,
                commit=commit,
                bun_version=require_bun_package_manager(
                    manifest, context="OpenCode package manifest"
                ),
            ),
        )

    @override
    async def _is_latest(
        self,
        context: UpdateContext,
        info: VersionInfo,
    ) -> bool:
        current = context.current
        if current is None or not await super()._is_latest(context, info):
            return False
        platforms = set(hashes_by_platform(current.hashes, hash_type=self.hash_type))
        return platforms == set(self.SUPPORTED_PLATFORMS)
