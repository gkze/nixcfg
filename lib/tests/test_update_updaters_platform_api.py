"""Tests for PlatformAPIUpdater field extraction and validation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, ClassVar

import aiohttp
import pytest

from lib.tests._assertions import expect_instance
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.metadata import PlatformAPIMetadata
from lib.update.updaters.platform_api import (
    DownloadingPlatformAPIUpdater,
    PlatformAPIUpdater,
)

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


class _NoChecksumUpdater(_DummyPlatformUpdater):
    CHECKSUM_KEY = None


class _NoCommitUpdater(_DummyPlatformUpdater):
    COMMIT_METADATA_KEY = None


class _DownloadingPlatformUpdater(DownloadingPlatformAPIUpdater, _DummyPlatformUpdater):
    pass


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
    assert latest_info.version == "1.110.0-insider"
    assert latest_info.metadata["build"] == "2026-02-24"
    assert latest_info.metadata["commit"] == "67c59a1440590a328f6fd0f15c37383c7576a236"

    platform_info = expect_instance(latest_info.metadata["platform_info"], dict)
    linux_info = expect_instance(platform_info["x86_64-linux"], dict)
    assert linux_info["supportsFastUpdate"] is True
    assert linux_info["timestamp"] == EXPECTED_TIMESTAMP


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
    assert payload == {
        "x86_64-linux": "hash-linux-x64",
        "aarch64-linux": "hash-linux-arm64",
    }


def test_downloading_platform_fetch_hashes_forwards_hash_progress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Download-backed platform APIs should preserve compute_url_hashes progress events."""
    updater = _DownloadingPlatformUpdater()
    info = VersionInfo(
        version="1.110.0-insider",
        metadata=PlatformAPIMetadata(
            platform_info={
                "x86_64-linux": {"downloadUrl": "unused"},
                "aarch64-linux": {"downloadUrl": "unused"},
            },
            equality_fields={"build": "2026-02-24"},
            commit="67c59a1440590a328f6fd0f15c37383c7576a236",
        ),
    )

    async def _compute_url_hashes(name: str, urls: object) -> object:
        url_list = list(urls)  # type: ignore[arg-type]
        assert name == updater.name
        assert url_list == [
            "https://download.example/1.110.0-insider/linux-x64",
            "https://download.example/1.110.0-insider/linux-arm64",
        ]
        yield UpdateEvent.status(name, "prefetching artifacts")
        yield UpdateEvent.value(
            name,
            {
                url_list[0]: "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                url_list[1]: "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            },
        )

    async def _convert_hash(name: str, hash_value: str) -> object:
        yield UpdateEvent.status(name, f"converting {hash_value}")
        yield UpdateEvent.value(name, hash_value)

    monkeypatch.setattr(
        "lib.update.updaters.base.compute_url_hashes",
        _compute_url_hashes,
    )
    monkeypatch.setattr(
        "lib.update.updaters.base.convert_nix_hash_to_sri",
        _convert_hash,
    )

    async def _collect() -> list[UpdateEvent]:
        async with aiohttp.ClientSession() as session:
            return [
                event
                async for event in updater.fetch_hashes(
                    info,
                    session,
                )
            ]

    events = asyncio.run(_collect())

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[0].message == "prefetching artifacts"
    assert events[-1].payload == {
        "x86_64-linux": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "aarch64-linux": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
    }


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


def test_fetch_checksums_error_paths_and_build_result_commit_normalization() -> None:
    """Raise typed errors for malformed metadata and normalize commit payload."""
    updater = _DummyPlatformUpdater()

    with pytest.raises(TypeError, match="Expected platform_info mapping"):
        _run_with_session(
            lambda session: updater.fetch_checksums(
                VersionInfo(version="1", metadata={"platform_info": "oops"}),
                session,
            )
        )

    with pytest.raises(TypeError, match="Malformed platform payload"):
        _run_with_session(
            lambda session: updater.fetch_checksums(
                VersionInfo(
                    version="1",
                    metadata={
                        "platform_info": {
                            "x86_64-linux": "bad",
                            "aarch64-linux": {},
                        }
                    },
                ),
                session,
            )
        )

    with pytest.raises(TypeError, match="'aarch64-linux'"):
        _run_with_session(
            lambda session: updater.fetch_checksums(
                VersionInfo(
                    version="1",
                    metadata={"platform_info": {"x86_64-linux": {"sha256hash": "a"}}},
                ),
                session,
            )
        )

    entry = updater.build_result(
        VersionInfo(version="9.9.9", metadata={"platform_info": {}, "commit": 123}),
        {"x86_64-linux": "sha256-x", "aarch64-linux": "sha256-y"},
    )
    assert entry.version == "9.9.9"
    assert entry.commit is None


def test_missing_checksum_key_and_abstract_methods_raise() -> None:
    """Fail fast for missing checksum key and unimplemented URL builders."""
    updater = _NoChecksumUpdater()
    with pytest.raises(NotImplementedError, match="No CHECKSUM_KEY"):
        _run_with_session(
            lambda session: updater.fetch_checksums(
                VersionInfo(
                    version="1",
                    metadata={
                        "platform_info": {
                            "x86_64-linux": {},
                            "aarch64-linux": {},
                        }
                    },
                ),
                session,
            )
        )

    base = PlatformAPIUpdater()
    with pytest.raises(NotImplementedError):
        _ = base._api_url("linux")
    with pytest.raises(NotImplementedError):
        _ = base._download_url("linux", VersionInfo(version="1", metadata={}))


def test_fetch_latest_payload_shape_and_optional_commit_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject non-object API payloads and allow missing commit metadata key."""
    updater = _DummyPlatformUpdater()

    async def _fetch_bad(_session: object, _url: str, **_kwargs: object) -> object:
        return ["not", "an", "object"]

    monkeypatch.setattr("lib.update.updaters.platform_api.fetch_json", _fetch_bad)
    with pytest.raises(TypeError, match="Expected JSON object"):
        _run_with_session(updater.fetch_latest)

    no_commit = _NoCommitUpdater()

    async def _fetch_good(_session: object, _url: str, **_kwargs: object) -> object:
        return {
            "productVersion": "1.2.3",
            "sha256hash": "sha256-demo",
            "build": "2026-03-01",
        }

    monkeypatch.setattr("lib.update.updaters.platform_api.fetch_json", _fetch_good)
    latest = _run_with_session(no_commit.fetch_latest)
    info = expect_instance(latest, VersionInfo)
    assert "commit" not in info.metadata


def test_metadata_helper_accepts_typed_metadata_and_rejects_invalid_payload() -> None:
    """Round-trip typed platform metadata and reject unsupported payload types."""
    updater = _DummyPlatformUpdater()
    metadata = PlatformAPIMetadata(
        platform_info={
            "x86_64-linux": {"sha256hash": "x"},
            "aarch64-linux": {"sha256hash": "y"},
        },
        equality_fields={"build": "2026-03-01"},
    )
    info = VersionInfo(version="1", metadata=metadata)
    assert updater._metadata(info) is metadata

    with pytest.raises(TypeError, match="Expected platform_info mapping"):
        updater._metadata(VersionInfo(version="1", metadata=object()))
