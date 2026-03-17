"""Shared helpers for GitHub latest-release updaters."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.update.net import fetch_github_api
from lib.update.updaters.base import Updater
from lib.update.updaters.metadata import GitHubReleaseMetadata, VersionInfo

if TYPE_CHECKING:
    import aiohttp


class GitHubReleaseUpdater(Updater):
    """Base updater for packages resolved from GitHub latest releases."""

    GITHUB_OWNER: str
    GITHUB_REPO: str
    TAG_PREFIX = "v"

    def _normalize_release_version(self, tag_name: str) -> str:
        if self.TAG_PREFIX and not tag_name.startswith(self.TAG_PREFIX):
            msg = f"Unexpected release tag format for {self.name}: {tag_name}"
            raise RuntimeError(msg)
        version = tag_name.removeprefix(self.TAG_PREFIX)
        if not version:
            msg = f"Missing version segment in release tag for {self.name}: {tag_name}"
            raise RuntimeError(msg)
        return version

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Resolve version metadata from the latest GitHub release tag."""
        payload = await fetch_github_api(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest",
            config=self.config,
        )
        if not isinstance(payload, dict):
            msg = f"Unexpected release payload type: {type(payload).__name__}"
            raise TypeError(msg)
        tag_name = payload.get("tag_name")
        if not isinstance(tag_name, str) or not tag_name:
            msg = f"Missing tag_name in release payload: {payload!r}"
            raise RuntimeError(msg)
        return VersionInfo(
            version=self._normalize_release_version(tag_name),
            metadata=GitHubReleaseMetadata(tag=tag_name),
        )


__all__ = ["GitHubReleaseUpdater"]
