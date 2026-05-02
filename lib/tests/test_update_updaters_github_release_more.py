"""Additional tests for the shared GitHub release updater base."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

import pytest

from lib.update.events import EventStream, UpdateEvent
from lib.update.updaters.github_release import (
    GitHubReleaseAssetURLsUpdater,
    GitHubReleaseUpdater,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.base import VersionInfo


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


class _DemoAssetReleaseUpdater(GitHubReleaseAssetURLsUpdater):
    name = "demo-asset-release"
    GITHUB_OWNER = "owner"
    GITHUB_REPO = "repo"
    PLATFORMS: ClassVar[dict[str, str]] = {"x86_64-linux": "linux-x64"}

    def _asset_name(self, version: str, platform_value: str) -> str:
        return f"demo-{version}-{platform_value}.tar.gz"


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
        lambda *_args, **_kwargs: asyncio.sleep(0, result={"tag_name": "v9.9.9"}),
    )
    info = asyncio.run(updater.fetch_latest(object()))
    assert info.version == "9.9.9"
    assert info.metadata["tag"] == "v9.9.9"

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


def test_github_release_asset_defaults() -> None:
    """Cover default release asset helper behavior."""
    updater = _DemoAssetReleaseUpdater()

    with pytest.raises(NotImplementedError):
        GitHubReleaseAssetURLsUpdater()._asset_name("1.2.3", "linux-x64")

    assert updater._fallback_url("1.2.3", "linux-x64") == (
        "https://github.com/owner/repo/releases/download/"
        "v1.2.3/demo-1.2.3-linux-x64.tar.gz"
    )
    assert updater._missing_asset_message("demo.tar.gz", "v1.2.3") == (
        "Could not find demo-asset-release release asset 'demo.tar.gz' in tag v1.2.3"
    )
