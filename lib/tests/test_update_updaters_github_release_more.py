"""Additional tests for the shared GitHub release updater base."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from lib.update.events import EventStream, UpdateEvent
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.github_release import GitHubReleaseUpdater

if TYPE_CHECKING:
    import aiohttp


class _DemoReleaseUpdater(GitHubReleaseUpdater):
    name = "demo-release"
    GITHUB_OWNER = "owner"
    GITHUB_REPO = "repo"

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: object | None = None,
    ) -> EventStream:
        _ = (info, session, context)
        if False:
            yield UpdateEvent.status(self.name, "never")


def test_normalize_release_version_paths() -> None:
    """Handle prefix stripping plus malformed tags."""
    updater = _DemoReleaseUpdater()
    assert updater._normalize_release_version("v1.2.3") == "1.2.3"

    with pytest.raises(RuntimeError, match="Unexpected release tag format"):
        updater._normalize_release_version("1.2.3")

    with pytest.raises(RuntimeError, match="Missing version segment"):
        updater._normalize_release_version("v")

    class _NoPrefix(_DemoReleaseUpdater):
        TAG_PREFIX = ""

    assert _NoPrefix()._normalize_release_version("nightly") == "nightly"


def test_fetch_latest_success_and_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Validate payload shape checks for GitHub latest release lookups."""
    updater = _DemoReleaseUpdater()

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"tag_name": "v2.0.0"}),
    )
    info = asyncio.run(updater.fetch_latest(object()))
    assert info.version == "2.0.0"
    assert info.metadata["tag"] == "v2.0.0"

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=[]),
    )
    with pytest.raises(TypeError, match="Unexpected release payload type"):
        asyncio.run(updater.fetch_latest(object()))

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={}),
    )
    with pytest.raises(RuntimeError, match="Missing tag_name"):
        asyncio.run(updater.fetch_latest(object()))
