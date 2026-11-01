"""Additional tests for the shared GitHub release updater base."""

import asyncio
from typing import TYPE_CHECKING, ClassVar

import pytest

from lib.update.events import EventSink, UpdateEvent, ignore_event
from lib.update.updaters import github_release
from lib.update.updaters.core import UpdateContext
from lib.update.updaters.github_release import (
    GitHubHeadSourceUpdater,
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
        emit: EventSink = ignore_event,
    ) -> object:
        _ = (info, session, context)
        if False:
            await emit(UpdateEvent.status(self.name, "never"))


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


class _DemoHeadUpdater(GitHubHeadSourceUpdater):
    name = "demo-head"
    GITHUB_OWNER = "owner"
    GITHUB_REPO = "repo"


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
    info = asyncio.run(
        updater.fetch_latest(object(), context=UpdateContext(current=None))
    )
    assert info.version == "9.9.9"
    assert info.metadata["tag"] == "v9.9.9"

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=[]),
    )
    with pytest.raises(TypeError, match="Unexpected release payload type"):
        asyncio.run(updater.fetch_latest(object(), context=UpdateContext(current=None)))

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        lambda *_args, **_kwargs: asyncio.sleep(0, result={}),
    )
    with pytest.raises(RuntimeError, match="Missing tag_name"):
        asyncio.run(updater.fetch_latest(object(), context=UpdateContext(current=None)))


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

    info = asyncio.run(
        _DemoCommitReleaseUpdater().fetch_latest(
            object(), context=UpdateContext(current=None)
        )
    )

    assert info == VersionInfo(
        version="1.2.3/rc1",
        metadata={"commit": "a" * 40, "tag": "v1.2.3/rc1"},
    )
    assert calls == [
        "repos/owner/repo/releases/latest",
        "repos/owner/repo/commits/v1.2.3%2Frc1",
    ]
    updater = _DemoCommitReleaseUpdater()
    result = updater.build_result(info, [])
    assert result.commit == "a" * 40
    assert (
        asyncio.run(
            updater._is_latest(
                UpdateContext(current=result.model_copy(update={"commit": None})), info
            )
        )
        is False
    )


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
        asyncio.run(
            _DemoCommitReleaseUpdater().fetch_latest(
                object(), context=UpdateContext(current=None)
            )
        )


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
    result = updater.build_result(
        VersionInfo("1.2.3"),
        {"x86_64-linux": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="},
    )
    assert result.urls == {
        "x86_64-linux": (
            "https://github.com/owner/repo/releases/download/"
            "v1.2.3/demo-1.2.3-linux-x64.tar.gz"
        )
    }
    assert _DemoReleaseUpdater().build_result(VersionInfo("1.2.3"), []).commit is None


def test_resolve_tag_commit_quotes_tag_and_names_the_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any owner/repo tag resolves through one quoted commits lookup."""
    paths: list[str] = []

    async def fetch(_session: object, path: str, **_kwargs: object) -> object:
        paths.append(path)
        return {"sha": "main"}

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        fetch,
    )
    with pytest.raises(
        RuntimeError,
        match="Demo k2-fsa/sherpa-onnx tag v1/2 has no immutable source commit",
    ):
        asyncio.run(
            github_release.resolve_tag_commit(
                object(),
                owner="k2-fsa",
                repo="sherpa-onnx",
                tag="v1/2",
                config=_DemoCommitReleaseUpdater().config,
                context="Demo",
            )
        )
    assert paths == ["repos/k2-fsa/sherpa-onnx/commits/v1%2F2"]


def test_require_immutable_commit_accepts_only_full_lowercase_sha() -> None:
    """Pinned companion commits share the release commit contract."""
    commit = "a" * 40
    assert github_release.require_immutable_commit(commit, context="X") == commit
    with pytest.raises(RuntimeError, match="X must be an immutable commit"):
        github_release.require_immutable_commit("main", context="X")


def test_head_source_updater_derives_a_dated_version_from_the_head_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A repo without releases still pins through the head commit and its date."""
    paths: list[str] = []

    async def fetch(_session: object, path: str, **_kwargs: object) -> object:
        paths.append(path)
        return {
            "sha": "c" * 40,
            "commit": {"committer": {"date": "2026-10-31T22:23:01Z"}},
        }

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        fetch,
    )
    updater = _DemoHeadUpdater()

    info = asyncio.run(updater.fetch_latest(object(), context=UpdateContext(None)))

    assert info == VersionInfo(
        version="0.0.0-unstable-2026-10-31",
        metadata={"commit": "c" * 40},
    )
    assert paths == ["repos/owner/repo/commits/HEAD"]
    assert updater.build_result(info, []).commit == "c" * 40


@pytest.mark.parametrize(
    ("payload", "error_type", "message"),
    [
        ([], TypeError, "has no immutable source commit"),
        ({}, RuntimeError, "has no immutable source commit"),
        ({"sha": "main"}, RuntimeError, "has no immutable source commit"),
        ({"sha": "d" * 40}, TypeError, "has no usable commit date"),
        (
            {"sha": "d" * 40, "commit": {"committer": {"date": 20261031}}},
            TypeError,
            "has no usable commit date",
        ),
        (
            {"sha": "d" * 40, "commit": {"committer": {"date": "31-10-2026"}}},
            RuntimeError,
            "has no usable commit date",
        ),
    ],
)
def test_head_commit_resolution_rejects_unusable_payloads(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    error_type: type[Exception],
    message: str,
) -> None:
    """A head pin needs one immutable sha and one parseable committer date."""

    async def fetch(*_args: object, **_kwargs: object) -> object:
        return payload

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        fetch,
    )

    with pytest.raises(error_type, match=message):
        asyncio.run(
            _DemoHeadUpdater().fetch_latest(object(), context=UpdateContext(None))
        )


def test_head_source_updater_hashes_only_the_immutable_source_tree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Head pins carry exactly one fixed-output source hash, never a tag URL."""
    captured: dict[str, object] = {}

    async def stream(
        name: str,
        *,
        steps: tuple[object, ...],
        config: object,
        emit: object,
    ) -> list[object]:
        captured.update(name=name, steps=steps, config=config, emit=emit)
        return []

    monkeypatch.setattr(
        "lib.update.updaters.github_release.stream_fixed_output_hashes",
        stream,
    )
    updater = _DemoHeadUpdater()
    info = VersionInfo(
        version="0.0.0-unstable-2026-10-31",
        metadata={"commit": "e" * 40},
    )

    hashes = asyncio.run(
        updater.fetch_hashes(info, object(), context=UpdateContext(None))
    )

    assert hashes == []
    assert captured["name"] == "demo-head"
    (step,) = captured["steps"]
    assert step.hash_type == "srcHash"
    assert step.expr({}) == updater._src_expr("e" * 40)
