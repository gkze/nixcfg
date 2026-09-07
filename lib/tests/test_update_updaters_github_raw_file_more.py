"""Tests for GitHub raw-file updater helpers."""

import asyncio

import aiohttp
import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._updater_helpers import collect_events
from lib.update.events import (
    UpdateEvent,
    ignore_event,
)
from lib.update.updaters import VersionInfo
from lib.update.updaters.core import UpdateContext
from lib.update.updaters.github_raw_file import (
    GitHubRawFileMetadata,
    GitHubRawFileUpdater,
)


class _DemoRawFileUpdater(GitHubRawFileUpdater):
    name = "demo"
    owner = "owner"
    repo = "repo"
    path = "path/to/file.txt"


def _collect(operation):
    return asyncio.run(collect_events(operation))


def test_fetch_latest_uses_default_branch_and_latest_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve latest revision metadata from GitHub APIs."""

    async def _default_branch(
        _session: aiohttp.ClientSession,
        owner: str,
        repo: str,
        *,
        config: object,
    ) -> str:
        _ = (owner, repo, config)
        return "main"

    async def _latest_commit(
        _session: aiohttp.ClientSession,
        repo_info: tuple[str, str],
        *,
        file_path: str,
        branch: str,
        config: object,
    ) -> str:
        _ = (repo_info, file_path, branch, config)
        return "deadbeef"

    monkeypatch.setattr(
        "lib.update.updaters.github_raw_file.fetch_github_default_branch",
        _default_branch,
    )
    monkeypatch.setattr(
        "lib.update.updaters.github_raw_file.fetch_github_latest_commit",
        _latest_commit,
    )

    updater = _DemoRawFileUpdater()

    async def _run() -> VersionInfo:
        async with aiohttp.ClientSession() as session:
            return await updater.fetch_latest(
                session, context=UpdateContext(current=None)
            )

    info = asyncio.run(_run())
    assert info.version == "deadbeef"
    assert info.metadata["rev"] == "deadbeef"
    assert info.metadata["branch"] == "main"


def test_fetch_hashes_returns_entries_and_validates_metadata(monkeypatch) -> None:
    """Hash the immutable URL, forward progress, and reject missing revision."""
    updater = _DemoRawFileUpdater()
    context = UpdateContext(current=None)
    with pytest.raises(TypeError, match="Expected string revision metadata"):
        asyncio.run(
            updater.fetch_hashes(
                VersionInfo(version="v1", metadata={}), object(), context=context
            )
        )

    async def hashes(source, urls, *, config, emit=ignore_event):
        assert urls == [
            "https://raw.githubusercontent.com/owner/repo/deadbeef/path/to/file.txt"
        ]
        await emit(UpdateEvent.status(source, "running"))
        return {urls[0]: "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}

    monkeypatch.setattr(
        "lib.update.updaters.github_raw_file.compute_url_hashes", hashes
    )
    captured = _collect(
        lambda emit: updater.fetch_hashes(
            VersionInfo(version="deadbeef", metadata={"rev": "deadbeef"}),
            object(),
            context=context,
            emit=emit,
        )
    )
    assert [event.message for event in captured] == ["running"]
    assert captured.result == [
        HashEntry.create(
            "sha256",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            url="https://raw.githubusercontent.com/owner/repo/deadbeef/path/to/file.txt",
        )
    ]


def test_fetch_hashes_accepts_typed_metadata(monkeypatch) -> None:
    """Pretyped revision metadata uses the same typed hashing boundary."""
    updater = _DemoRawFileUpdater()

    async def hashes(_source, urls, **_kwargs):
        return {urls[0]: "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}

    monkeypatch.setattr(
        "lib.update.updaters.github_raw_file.compute_url_hashes", hashes
    )
    captured = _collect(
        lambda emit: updater.fetch_hashes(
            VersionInfo(
                version="deadbeef",
                metadata=GitHubRawFileMetadata(rev="deadbeef", branch="main"),
            ),
            object(),
            context=UpdateContext(current=None),
            emit=emit,
        )
    )
    assert len(captured.result) == 1
