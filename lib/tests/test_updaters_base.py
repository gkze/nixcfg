"""Tests for shared updater base behavior."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import aiohttp

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update.events import EventStream, UpdateEvent, UpdateEventKind
from lib.update.updaters.base import (
    FlakeInputHashUpdater,
    HashEntryUpdater,
    VersionInfo,
)


class _FakeHashEntryUpdater(HashEntryUpdater):
    name = "fake-hash-updater"

    def __init__(self, *, version: str) -> None:
        super().__init__()
        self._version = version
        self.fetch_hashes_called = False

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Run this test case."""
        _ = session
        return VersionInfo(
            version=object.__getattribute__(self, "_version"), metadata={}
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        """Run this test case."""
        _ = info
        _ = session
        self.fetch_hashes_called = True
        entries: list[HashEntry] = [
            HashEntry.create(
                hash_type="sha256",
                hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
            )
        ]
        yield UpdateEvent.value(
            self.name,
            entries,
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

    assert result.version == "v1.2.3"


def test_hash_entry_updater_recomputes_before_confirming_equivalence() -> None:
    """Generic hash-entry updaters must recompute before declaring no change."""

    async def _collect_events() -> list[UpdateEvent]:
        updater = _FakeHashEntryUpdater(version="v9.9.9")
        current = SourceEntry(
            version="v9.9.9",
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

        assert updater.fetch_hashes_called is True
        return events

    events = asyncio.run(_collect_events())
    status_messages = [
        event.message for event in events if event.kind == UpdateEventKind.STATUS
    ]

    assert "Up to date" in status_messages


# ---------------------------------------------------------------------------
# FlakeInputHashUpdater fingerprint-based staleness tests
# ---------------------------------------------------------------------------


class _FakeFlakeInputUpdater(FlakeInputHashUpdater):
    """Concrete FlakeInputHashUpdater for testing fingerprint logic."""

    name = "fake-flake-input"
    input_name = "fake-flake-input"
    hash_type = "sha256"

    def __init__(self, *, version: str = "v1.0.0") -> None:
        super().__init__()
        self._version = version
        self.fetch_hashes_called = False

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Run this test case."""
        _ = session
        return VersionInfo(
            version=object.__getattribute__(self, "_version"), metadata={}
        )

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        _ = info
        self.fetch_hashes_called = True
        return object.__getattribute__(self, "_yield_fake_hash")()

    async def _yield_fake_hash(self) -> EventStream:
        yield UpdateEvent.value(
            self.name,
            "sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
        )


def test_flake_input_updater_recomputes_when_no_drv_hash() -> None:
    """Missing drvHash in sources.json forces recomputation."""

    async def _run() -> bool:
        updater = _FakeFlakeInputUpdater()
        current = SourceEntry(
            version="v1.0.0",
            hashes=HashCollection(
                entries=[
                    HashEntry.create(
                        hash_type="sha256",
                        hash_value="sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                    ),
                ],
            ),
            # No drv_hash — simulates pre-fingerprinting sources.json
        )
        info = VersionInfo(version="v1.0.0", metadata={})
        return await object.__getattribute__(updater, "_is_latest")(current, info)

    result = asyncio.run(_run())
    assert result is False


def test_flake_input_updater_recomputes_when_version_differs() -> None:
    """Version mismatch must force recomputation before fingerprint checks."""

    async def _run() -> bool:
        updater = _FakeFlakeInputUpdater(version="v9.9.9")
        current = SourceEntry.model_validate(
            {
                "version": "v1.0.0",
                "hashes": [
                    {
                        "hashType": "sha256",
                        "hash": "sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                    },
                ],
                "drvHash": "abc123deadbeef",
            },
        )
        info = VersionInfo(version="v9.9.9", metadata={})
        with patch(
            "lib.update.updaters.base.compute_drv_fingerprint",
            new_callable=AsyncMock,
            return_value="abc123deadbeef",
        ) as compute_drv_fingerprint:
            result = await object.__getattribute__(updater, "_is_latest")(current, info)
            compute_drv_fingerprint.assert_not_awaited()
            return result

    result = asyncio.run(_run())
    assert result is False


def test_flake_input_updater_skips_when_fingerprint_matches() -> None:
    """Matching drvHash means nothing in the build closure changed."""

    async def _run() -> bool:
        updater = _FakeFlakeInputUpdater()
        current = SourceEntry.model_validate({
            "version": "v1.0.0",
            "hashes": [
                {
                    "hashType": "sha256",
                    "hash": "sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                }
            ],
            "drvHash": "abc123deadbeef",
        })
        info = VersionInfo(version="v1.0.0", metadata={})
        with patch(
            "lib.update.updaters.base.compute_drv_fingerprint",
            new_callable=AsyncMock,
            return_value="abc123deadbeef",
        ):
            return await object.__getattribute__(updater, "_is_latest")(current, info)

    result = asyncio.run(_run())
    assert result is True


def test_flake_input_updater_recomputes_when_fingerprint_differs() -> None:
    """Different drvHash means a build input changed — must recompute."""

    async def _run() -> bool:
        updater = _FakeFlakeInputUpdater()
        current = SourceEntry.model_validate({
            "version": "v1.0.0",
            "hashes": [
                {
                    "hashType": "sha256",
                    "hash": "sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                }
            ],
            "drvHash": "abc123deadbeef",
        })
        info = VersionInfo(version="v1.0.0", metadata={})
        with patch(
            "lib.update.updaters.base.compute_drv_fingerprint",
            new_callable=AsyncMock,
            return_value="different_fingerprint",
        ):
            return await object.__getattribute__(updater, "_is_latest")(current, info)

    result = asyncio.run(_run())
    assert result is False


def test_flake_input_updater_recomputes_when_fingerprint_fails() -> None:
    """Fingerprint computation failure conservatively triggers recomputation."""

    async def _run() -> bool:
        updater = _FakeFlakeInputUpdater()
        current = SourceEntry.model_validate({
            "version": "v1.0.0",
            "hashes": [
                {
                    "hashType": "sha256",
                    "hash": "sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo=",
                }
            ],
            "drvHash": "abc123deadbeef",
        })
        info = VersionInfo(version="v1.0.0", metadata={})
        with patch(
            "lib.update.updaters.base.compute_drv_fingerprint",
            new_callable=AsyncMock,
            side_effect=RuntimeError("nix eval failed"),
        ):
            return await object.__getattribute__(updater, "_is_latest")(current, info)

    result = asyncio.run(_run())
    assert result is False
