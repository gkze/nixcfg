"""Tests for PlatformAPIUpdater field extraction and validation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

import aiohttp
import pytest

from lib.nix.tests._assertions import check, expect_instance
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.platform_api import PlatformAPIUpdater

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


EXPECTED_TIMESTAMP = 1_771_952_644_637


class _DummyPlatformUpdater(PlatformAPIUpdater):
    name = "dummy-platform"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "x86_64-linux": "linux-x64",
        "aarch64-linux": "linux-arm64",
    }
    VERSION_KEY = "productVersion"
    CHECKSUM_KEY = "sha256hash"
    EXTRA_EQUALITY_KEYS = ("build",)
    COMMIT_METADATA_KEY = "version"

    def _api_url(self, _api_platform: str) -> str:
        return f"https://example.com/{_api_platform}"

    def _download_url(self, _api_platform: str, info: VersionInfo) -> str:
        return f"https://download.example/{info.version}/{_api_platform}"


def _run_with_session[T](
    run: Callable[[aiohttp.ClientSession], Awaitable[T]],
) -> T:
    async def _runner() -> T:
        async with aiohttp.ClientSession() as session:
            return await run(session)

    return asyncio.run(_runner())


def test_fetch_latest_accepts_mixed_platform_payload_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    updater = _DummyPlatformUpdater()

    payloads = {
        "linux-x64": {
            "productVersion": "1.110.0-insider",
            "sha256hash": "hash-linux-x64",
            "version": "67c59a1440590a328f6fd0f15c37383c7576a236",
            "build": "2026-02-24",
            "timestamp": EXPECTED_TIMESTAMP,
            "supportsFastUpdate": True,
        },
        "linux-arm64": {
            "productVersion": "1.110.0-insider",
            "sha256hash": "hash-linux-arm64",
            "version": "67c59a1440590a328f6fd0f15c37383c7576a236",
            "build": "2026-02-24",
            "timestamp": EXPECTED_TIMESTAMP,
            "supportsFastUpdate": True,
        },
    }

    async def _fetch_json(_session: object, url: str, **_kwargs: object) -> object:
        return payloads[url.rsplit("/", maxsplit=1)[-1]]

    monkeypatch.setattr(
        "lib.update.updaters.platform_api.fetch_json",
        _fetch_json,
    )

    latest = _run_with_session(updater.fetch_latest)
    latest_info = expect_instance(latest, VersionInfo)
    check(latest_info.version == "1.110.0-insider")
    check(latest_info.metadata["build"] == "2026-02-24")
    check(latest_info.metadata["commit"] == "67c59a1440590a328f6fd0f15c37383c7576a236")

    platform_info = expect_instance(latest_info.metadata["platform_info"], dict)
    linux_info = expect_instance(platform_info["x86_64-linux"], dict)
    check(linux_info["supportsFastUpdate"] is True)
    check(linux_info["timestamp"] == EXPECTED_TIMESTAMP)


def test_fetch_checksums_uses_checksum_field_only() -> None:
    """Run this test case."""
    updater = _DummyPlatformUpdater()
    info = VersionInfo(
        version="1.110.0-insider",
        metadata={
            "platform_info": {
                "x86_64-linux": {
                    "sha256hash": "hash-linux-x64",
                    "timestamp": EXPECTED_TIMESTAMP,
                    "supportsFastUpdate": True,
                },
                "aarch64-linux": {
                    "sha256hash": "hash-linux-arm64",
                    "timestamp": EXPECTED_TIMESTAMP,
                    "supportsFastUpdate": True,
                },
            }
        },
    )

    checksums = _run_with_session(
        lambda session: updater.fetch_checksums(info, session)
    )
    payload = expect_instance(checksums, dict)
    check(
        payload
        == {
            "x86_64-linux": "hash-linux-x64",
            "aarch64-linux": "hash-linux-arm64",
        }
    )


def test_fetch_latest_requires_string_required_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    updater = _DummyPlatformUpdater()

    async def _fetch_json(_session: object, _url: str, **_kwargs: object) -> object:
        return {
            "productVersion": 110,
            "sha256hash": "hash",
            "version": "abcdef",
            "build": "2026-02-24",
        }

    monkeypatch.setattr(
        "lib.update.updaters.platform_api.fetch_json",
        _fetch_json,
    )

    with pytest.raises(TypeError, match="Expected string field 'productVersion'"):
        _run_with_session(updater.fetch_latest)


def test_fetch_checksums_requires_string_checksum_field() -> None:
    """Run this test case."""
    updater = _DummyPlatformUpdater()
    info = VersionInfo(
        version="1.110.0-insider",
        metadata={
            "platform_info": {
                "x86_64-linux": {"sha256hash": "hash-linux-x64"},
                "aarch64-linux": {"sha256hash": True},
            }
        },
    )

    with pytest.raises(TypeError, match="Expected string field 'sha256hash'"):
        _run_with_session(
            lambda session: updater.fetch_checksums(info, session),
        )
