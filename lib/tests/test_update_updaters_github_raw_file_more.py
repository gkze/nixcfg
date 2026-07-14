"""Tests for GitHub raw-file updater helpers."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import aiohttp
import pytest

from lib.update.events import CapturedValue, UpdateEvent, UpdateEventKind
from lib.update.updaters import VersionInfo
from lib.update.updaters.github_raw_file import (
    GitHubRawFileMetadata,
    GitHubRawFileUpdater,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _DemoRawFileUpdater(GitHubRawFileUpdater):
    name = "demo"
    owner = "owner"
    repo = "repo"
    path = "path/to/file.txt"


def _collect(stream: AsyncIterator[UpdateEvent]) -> list[UpdateEvent]:
    async def _run() -> list[UpdateEvent]:
        items: list[UpdateEvent] = []
        async for item in stream:
            items.append(item)
        return items

    return asyncio.run(_run())


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
            return await updater.fetch_latest(session)

    info = asyncio.run(_run())
    assert info.version == "deadbeef"
    assert info.metadata["rev"] == "deadbeef"
    assert info.metadata["branch"] == "main"


def test_fetch_hashes_emits_entries_and_validates_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compute URL hashes and return a sha256 hash entry list."""
    updater = _DemoRawFileUpdater()

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
    assert events[0].kind == UpdateEventKind.STATUS
    assert events[-1].kind == UpdateEventKind.VALUE
    payload = events[-1].payload
    assert isinstance(payload, list)
    assert payload[0].hash == "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def test_fetch_hashes_accepts_typed_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Accept pre-typed metadata without dict coercion."""

    class _TypedDemoRawFileUpdater(GitHubRawFileUpdater):
        name = "typed-demo"
        owner = "owner"
        repo = "repo"
        path = "path/to/file.txt"

    updater = _TypedDemoRawFileUpdater()

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
    assert events[-1].kind == UpdateEventKind.VALUE
