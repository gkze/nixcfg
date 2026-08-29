"""Tests for shared updater base behavior."""

import asyncio
from typing import ClassVar
from unittest.mock import patch

import aiohttp

from lib.nix.commands.base import CommandResult as NixCommandResultData
from lib.nix.commands.base import NixCommandError
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.update.config import resolve_config
from lib.update.events import EventStream, UpdateEvent, UpdateEventKind
from lib.update.nix import _build_package_path_attr_expr
from lib.update.updaters import (
    DownloadHashUpdater,
    FlakeInputHashUpdater,
    HashEntryUpdater,
    VersionInfo,
)


class _ConfiguredDownloadUpdater(DownloadHashUpdater):
    name = "configured-download"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "https://example.com/archive.tar.gz"
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        _ = session
        return VersionInfo(version="1.0.0")


def test_download_updater_applies_explicit_retry_and_timeout_config() -> None:
    """The config bound at the updater seam governs URL prefetch subprocesses."""
    calls: list[float] = []

    async def _prefetch(
        _url: str,
        *,
        name: str | None = None,
        command_timeout: float,
    ) -> str:
        _ = name
        calls.append(command_timeout)
        if len(calls) == 1:
            raise NixCommandError(
                NixCommandResultData(
                    args=["nix-prefetch-url"],
                    returncode=1,
                    stdout="",
                    stderr="HTTP error 503",
                ),
                "transient prefetch failure",
            )
        return "sha256-4TE4PIBEUDUalSRf8yPdc8fM7E7fRJsODG+1DgxhDEo="

    async def _run() -> list[UpdateEvent]:
        config = resolve_config(
            retries=2,
            retry_backoff=0,
            subprocess_timeout=17,
        )
        updater = _ConfiguredDownloadUpdater(config=config)
        with patch("lib.update.process.libnix_prefetch_url", _prefetch):
            async with aiohttp.ClientSession() as session:
                return [event async for event in updater.update_stream(None, session)]

    events = asyncio.run(_run())

    assert calls == [17, 17]
    assert events[-1].kind is UpdateEventKind.RESULT


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


def test_package_flake_input_updater_hashes_discovered_package_expression() -> None:
    """Package-owned hash updaters should not require an overlay export."""

    class _PackageUpdater(FlakeInputHashUpdater):
        name = "anthropic-cli"
        hash_type = "vendorHash"

    captured: dict[str, object] = {}

    async def _compute_fixed_output_hash(
        source: str,
        expr: str,
        *,
        config: object,
    ) -> EventStream:
        captured.update({"source": source, "expr": expr, "config": config})
        yield UpdateEvent.value(source, "sha256-package")

    async def _collect() -> list[UpdateEvent]:
        return [
            event
            async for event in updater._compute_hash_for_system(
                VersionInfo(version="1.0.0"),
                system="aarch64-darwin",
            )
        ]

    updater = _PackageUpdater()
    with (
        patch(
            "lib.update.nix.compute_fixed_output_hash",
            _compute_fixed_output_hash,
        ),
        patch(
            "lib.update.nix.compute_overlay_hash",
            side_effect=AssertionError("package updater used overlay route"),
        ),
    ):
        events = asyncio.run(_collect())

    assert events == [UpdateEvent.value("anthropic-cli", "sha256-package")]
    assert captured["source"] == "anthropic-cli"
    assert_nix_ast_equal(
        str(captured["expr"]),
        _build_package_path_attr_expr(
            "anthropic-cli",
            "",
            system="aarch64-darwin",
        ),
    )


def test_package_flake_input_updater_fingerprints_discovered_package() -> None:
    """Package-owned staleness checks should use the package derivation."""

    class _PackageUpdater(FlakeInputHashUpdater):
        name = "anthropic-cli"
        hash_type = "vendorHash"

    captured: dict[str, object] = {}

    async def _compute_expr_drv_fingerprint(
        source: str,
        expr: str,
        *,
        config: object,
    ) -> str:
        captured.update({"source": source, "expr": expr, "config": config})
        return "package-drv"

    current = SourceEntry.model_validate({
        "version": "1.0.0",
        "drvHash": "package-drv",
        "hashes": [],
    })
    updater = _PackageUpdater()
    with (
        patch(
            "lib.update.nix.compute_expr_drv_fingerprint",
            _compute_expr_drv_fingerprint,
        ),
        patch(
            "lib.update.nix.compute_drv_fingerprint",
            side_effect=AssertionError("package updater used overlay fingerprint"),
        ),
    ):
        is_latest = asyncio.run(
            updater._is_latest(current, VersionInfo(version="1.0.0"))
        )

    assert is_latest is True
    assert captured["source"] == "anthropic-cli"
    assert_nix_ast_equal(
        str(captured["expr"]),
        _build_package_path_attr_expr("anthropic-cli", ""),
    )
