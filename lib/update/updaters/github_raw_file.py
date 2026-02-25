"""Updater factory for hashing files fetched from GitHub raw content."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiohttp

from lib.nix.models.sources import HashEntry
from lib.update.events import (
    CapturedValue,
    EventStream,
    UpdateEvent,
    capture_stream_value,
    expect_hash_mapping,
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
            (self.owner, self.repo),
            file_path=self.path,
            branch=branch,
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
        rev = info.metadata.get("rev")
        if not isinstance(rev, str):
            msg = f"Expected string revision metadata for {self.name}"
            raise TypeError(msg)
        url = github_raw_url(self.owner, self.repo, rev, self.path)
        async for item in capture_stream_value(
            compute_url_hashes(self.name, [url]),
            error="Missing hash output",
        ):
            if isinstance(item, CapturedValue):
                hash_mapping = expect_hash_mapping(item.captured)
                hash_value = hash_mapping[url]
                entries: list[HashEntry] = [
                    HashEntry.create("sha256", hash_value, url=url)
                ]
                yield UpdateEvent.value(
                    self.name,
                    entries,
                )
            else:
                yield item
