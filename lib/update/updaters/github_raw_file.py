"""Updater factory for hashing files fetched from GitHub raw content."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

from lib.nix.models.sources import HashEntry, HashMapping
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    _require_value,
    drain_value_events,
)
from lib.update.net import (
    fetch_github_default_branch,
    fetch_github_latest_commit,
    github_raw_url,
)
from lib.update.process import compute_url_hashes
from lib.update.updaters.base import HashEntryUpdater, VersionInfo


def github_raw_file_updater(
    name: str,
    *,
    owner: str,
    repo: str,
    path: str,
) -> type[GitHubRawFileUpdater]:
    """Create a ``GitHubRawFileUpdater`` subclass with fixed repo/path settings."""
    attrs = {"name": name, "owner": owner, "repo": repo, "path": path}
    return type(f"{name}Updater", (GitHubRawFileUpdater,), attrs)


class GitHubRawFileUpdater(HashEntryUpdater):
    """Updater that pins the latest commit hash for a specific raw file."""

    owner: str
    repo: str
    path: str

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch default branch and latest file commit SHA from GitHub."""
        branch = await fetch_github_default_branch(
            session,
            self.owner,
            self.repo,
            config=self.config,
        )
        rev = await fetch_github_latest_commit(
            session,
            self.owner,
            self.repo,
            self.path,
            branch,
            config=self.config,
        )
        return VersionInfo(version=rev, metadata={"rev": rev, "branch": branch})

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        """Compute a sha256 hash entry for the resolved raw file URL."""
        _ = session
        url = github_raw_url(self.owner, self.repo, info.metadata["rev"], self.path)
        hash_drain = ValueDrain[HashMapping]()
        async for event in drain_value_events(
            compute_url_hashes(self.name, [url]),
            hash_drain,
        ):
            yield event
        hashes_by_url = _require_value(hash_drain, "Missing hash output")
        hash_value = hashes_by_url[url]
        yield UpdateEvent.value(
            self.name,
            [HashEntry.create("sha256", hash_value, url=url)],
        )
