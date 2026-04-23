"""Updater for Zed nightly flake input metadata."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from lib.update.net import fetch_url
from lib.update.updaters.base import (
    Crate2NixMetadataUpdater,
    VersionInfo,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp


@register_updater
class ZedEditorNightlyUpdater(Crate2NixMetadataUpdater):
    """Track the current Zed nightly app version and locked commit."""

    name = "zed-editor-nightly"
    input_name = "zed"
    _MANIFEST_PATH = "crates/zed/Cargo.toml"

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Resolve the current app version from the locked upstream manifest."""
        node = self._resolve_flake_node(VersionInfo(version="ignored"))
        locked = node.locked
        owner = locked.owner if locked is not None else None
        repo = locked.repo if locked is not None else None
        rev = locked.rev if locked is not None else None
        if not all(isinstance(value, str) and value for value in (owner, repo, rev)):
            msg = "zed flake input is missing owner/repo/rev metadata"
            raise RuntimeError(msg)

        manifest_url = f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{self._MANIFEST_PATH}"
        manifest_bytes = await fetch_url(
            session,
            manifest_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
            user_agent=self.config.default_user_agent,
        )
        manifest = tomllib.loads(manifest_bytes.decode(errors="replace"))
        package = manifest.get("package")
        version = package.get("version") if isinstance(package, dict) else None
        if not isinstance(version, str) or not version:
            msg = "Zed manifest is missing package.version"
            raise RuntimeError(msg)

        return VersionInfo(version=version, metadata={"commit": rev})
