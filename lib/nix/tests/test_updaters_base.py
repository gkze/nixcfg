"""Tests for shared updater base behavior."""

from __future__ import annotations

import asyncio

import aiohttp

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.events import EventStream, UpdateEvent, UpdateEventKind
from lib.update.updaters.base import HashEntryUpdater, VersionInfo


class _FakeHashEntryUpdater(HashEntryUpdater):
    name = "fake-hash-updater"

    def __init__(self, *, version: str) -> None:
        super().__init__()
        self._version = version
        self.fetch_hashes_called = False

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        _ = session
        return VersionInfo(version=self._version, metadata={})

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        _ = info
        _ = session
        self.fetch_hashes_called = True
        yield UpdateEvent.value(
            self.name,
            [
                HashEntry.create(
                    hash_type="sha256",
                    hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                ),
            ],
        )


def test_hash_entry_updater_build_result_preserves_version() -> None:
    """Hash-only updaters should persist version for latest checks."""
    updater = _FakeHashEntryUpdater(version="v1.2.3")
    info = VersionInfo(version="v1.2.3", metadata={})
    hashes = [
        HashEntry.create(
            hash_type="sha256",
            hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
        ),
    ]

    result = updater.build_result(info, hashes)

    assert result.version == "v1.2.3"  # noqa: S101


def test_hash_entry_updater_skips_hash_fetch_when_version_matches() -> None:
    """Matching versions should short-circuit before recomputing hashes."""

    async def _collect_events() -> list[UpdateEvent]:
        updater = _FakeHashEntryUpdater(version="v2.0.0")
        current = SourceEntry(
            version="v2.0.0",
            hashes=HashCollection(
                entries=[
                    HashEntry.create(
                        hash_type="sha256",
                        hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                    ),
                ],
            ),
        )

        async with aiohttp.ClientSession() as session:
            events = [event async for event in updater.update_stream(current, session)]

        assert updater.fetch_hashes_called is False  # noqa: S101
        return events

    events = asyncio.run(_collect_events())
    status_messages = [
        event.message for event in events if event.kind == UpdateEventKind.STATUS
    ]

    assert "Up to date (version: v2.0.0)" in status_messages  # noqa: S101
