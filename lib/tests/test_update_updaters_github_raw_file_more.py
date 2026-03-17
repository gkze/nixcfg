"""Tests for GitHub raw-file updater helpers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import aiohttp
import pytest

from lib.tests._assertions import check
from lib.update.events import CapturedValue, UpdateEvent, UpdateEventKind
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.github_raw_file import (
    GitHubRawFileMetadata,
    GitHubRawFileUpdater,
    github_raw_file_updater,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _collect(stream: AsyncIterator[UpdateEvent]) -> list[UpdateEvent]:
    async def _run() -> list[UpdateEvent]:
        items: list[UpdateEvent] = []
        async for item in stream:
            items.append(item)
        return items

    return asyncio.run(_run())


def test_github_raw_file_updater_factory_sets_class_attributes() -> None:
    """Create a concrete updater class with fixed repo metadata."""
    updater_cls = github_raw_file_updater(
        "demo",
        owner="owner",
        repo="repo",
        path="path/to/file.txt",
    )
    check(issubclass(updater_cls, GitHubRawFileUpdater))
    check(updater_cls.name == "demo")
    check(updater_cls.owner == "owner")
    check(updater_cls.repo == "repo")
    check(updater_cls.path == "path/to/file.txt")


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

    updater_cls = github_raw_file_updater(
        "demo",
        owner="owner",
        repo="repo",
        path="path/to/file.txt",
    )
    updater = updater_cls()

    async def _run() -> VersionInfo:
        async with aiohttp.ClientSession() as session:
            return await updater.fetch_latest(session)

    info = asyncio.run(_run())
    check(info.version == "deadbeef")
    check(info.metadata["rev"] == "deadbeef")
    check(info.metadata["branch"] == "main")


def test_fetch_hashes_emits_entries_and_validates_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compute URL hashes and return a sha256 hash entry list."""
    updater_cls = github_raw_file_updater(
        "demo",
        owner="owner",
        repo="repo",
        path="path/to/file.txt",
    )
    updater = updater_cls()

    with pytest.raises(TypeError, match="Expected string revision metadata"):
        _collect(
            updater.fetch_hashes(
                VersionInfo(version="v1", metadata={}),
                session=object(),  # type: ignore[arg-type]
            )
        )

    async def _capture(
        _stream: object,
        *,
        error: str,
    ) -> AsyncIterator[UpdateEvent | CapturedValue[object]]:
        _ = error
        captured_url = (
            "https://raw.githubusercontent.com/owner/repo/deadbeef/path/to/file.txt"
        )
        captured_hash = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        yield UpdateEvent.status("demo", "running")
        yield CapturedValue({captured_url: captured_hash})

    monkeypatch.setattr(
        "lib.update.updaters.github_raw_file.capture_stream_value", _capture
    )
    monkeypatch.setattr(
        "lib.update.updaters.github_raw_file.compute_url_hashes",
        lambda *_args, **_kwargs: iter(()),
    )

    events = _collect(
        updater.fetch_hashes(
            VersionInfo(version="deadbeef", metadata={"rev": "deadbeef"}),
            session=object(),  # type: ignore[arg-type]
        )
    )
    check(events[0].kind == UpdateEventKind.STATUS)
    check(events[-1].kind == UpdateEventKind.VALUE)
    payload = events[-1].payload
    check(isinstance(payload, list))
    check(payload[0].hash == "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=")


def test_fetch_hashes_accepts_typed_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept pre-typed metadata without dict coercion."""
    updater = github_raw_file_updater(
        "typed-demo",
        owner="owner",
        repo="repo",
        path="path/to/file.txt",
    )()

    async def _capture(
        _stream: object,
        *,
        error: str,
    ) -> AsyncIterator[UpdateEvent | CapturedValue[object]]:
        _ = error
        url = "https://raw.githubusercontent.com/owner/repo/deadbeef/path/to/file.txt"
        yield CapturedValue({
            url: "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
        })

    monkeypatch.setattr(
        "lib.update.updaters.github_raw_file.capture_stream_value",
        _capture,
    )
    monkeypatch.setattr(
        "lib.update.updaters.github_raw_file.compute_url_hashes",
        lambda *_args, **_kwargs: iter(()),
    )

    events = _collect(
        updater.fetch_hashes(
            VersionInfo(
                version="deadbeef",
                metadata=GitHubRawFileMetadata(rev="deadbeef", branch="main"),
            ),
            session=object(),  # type: ignore[arg-type]
        )
    )
    check(events[-1].kind == UpdateEventKind.VALUE)
