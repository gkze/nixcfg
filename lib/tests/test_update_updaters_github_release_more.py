"""Additional tests for the shared GitHub release updater base."""

import asyncio
from typing import TYPE_CHECKING, ClassVar

import pytest

from lib.update.events import EventStream, UpdateEvent
from lib.update.updaters.github_release import (
    GitHubReleaseAssetURLsUpdater,
    GitHubReleaseUpdater,
)
from lib.update.updaters.metadata import VersionInfo

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


class _DemoAssetReleaseUpdater(GitHubReleaseAssetURLsUpdater):
    name = "demo-asset-release"
    GITHUB_OWNER = "owner"
    GITHUB_REPO = "repo"
    PLATFORMS: ClassVar[dict[str, str]] = {"x86_64-linux": "linux-x64"}

    def _asset_name(self, version: str, platform_value: str) -> str:
        return f"demo-{version}-{platform_value}.tar.gz"


class _DemoCommitReleaseUpdater(_DemoReleaseUpdater):
    RESOLVE_TAG_COMMIT = True
    RELEASE_DISPLAY_NAME = "Demo"


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


def test_fetch_latest_can_resolve_release_tag_to_immutable_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Commit-backed releases should persist the exact resolved tag target."""
    calls: list[str] = []
    responses: list[object] = [
        {"tag_name": "v1.2.3/rc1"},
        {"sha": "a" * 40},
    ]

    async def fetch(_session: object, path: str, **_kwargs: object) -> object:
        calls.append(path)
        return responses.pop(0)

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        fetch,
    )

    info = asyncio.run(_DemoCommitReleaseUpdater().fetch_latest(object()))

    assert info == VersionInfo(
        version="1.2.3/rc1",
        metadata={"commit": "a" * 40, "tag": "v1.2.3/rc1"},
    )
    assert calls == [
        "repos/owner/repo/releases/latest",
        "repos/owner/repo/commits/v1.2.3%2Frc1",
    ]


@pytest.mark.parametrize(
    ("payload", "error_type"),
    [
        ([], TypeError),
        ({}, RuntimeError),
        ({"sha": "main"}, RuntimeError),
        ({"sha": "A" * 40}, RuntimeError),
    ],
)
def test_commit_backed_release_rejects_nonimmutable_tag_targets(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    error_type: type[Exception],
) -> None:
    """Malformed or mutable tag targets must fail before entering metadata."""
    responses: list[object] = [{"tag_name": "v1.2.3"}, payload]

    async def fetch(*_args: object, **_kwargs: object) -> object:
        return responses.pop(0)

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        fetch,
    )

    with pytest.raises(error_type, match="has no immutable source commit"):
        asyncio.run(_DemoCommitReleaseUpdater().fetch_latest(object()))


@pytest.mark.parametrize("commit", [None, "main", "A" * 40])
def test_release_commit_metadata_requires_full_lowercase_sha(
    commit: str | None,
) -> None:
    """Consumers must reject hand-written metadata that bypasses tag resolution."""
    updater = _DemoCommitReleaseUpdater()
    info = VersionInfo(version="1.2.3", metadata={"commit": commit})

    with pytest.raises(RuntimeError, match="missing an immutable source commit"):
        updater._require_commit(info)

    assert (
        updater._require_commit(
            VersionInfo(version="1.2.3", metadata={"commit": "b" * 40})
        )
        == "b" * 40
    )


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
