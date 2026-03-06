"""Additional coverage for updater base classes and helpers."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar

import aiohttp
import pytest

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.tests._assertions import check, expect_instance, expect_not_none
from lib.update.config import resolve_config
from lib.update.events import EventStream, UpdateEvent, UpdateEventKind
from lib.update.updaters import UPDATERS
from lib.update.updaters.base import (
    ChecksumProvidedUpdater,
    DenoDepsHashUpdater,
    DenoManifestUpdater,
    DownloadHashUpdater,
    FlakeInputHashUpdater,
    HashEntryUpdater,
    Updater,
    VersionInfo,
    _verify_platform_versions,
    bun_node_modules_updater,
    cargo_vendor_updater,
    deno_deps_updater,
    deno_manifest_updater,
    flake_input_hash_updater,
    go_vendor_updater,
    npm_deps_updater,
    uv_lock_hash_updater,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from pathlib import Path

HASH_A = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
HASH_B = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


class _DummyUpdater(Updater):
    name = "dummy"

    def __init__(self, *, latest: str = "1.0.0") -> None:
        super().__init__()
        self.latest = latest

    async def fetch_latest(self, session: object) -> VersionInfo:
        """Run this test case."""
        _ = session
        return VersionInfo(version=self.latest, metadata={})

    async def fetch_hashes(self, info: VersionInfo, session: object) -> EventStream:
        """Run this test case."""
        _ = (info, session)
        payload: dict[str, str] = {"x86_64-linux": HASH_A}
        yield UpdateEvent.value(self.name, payload)


class _DummyChecksum(ChecksumProvidedUpdater):
    name = "checksum"
    PLATFORMS: ClassVar[dict[str, str]] = {"x86_64-linux": "linux"}

    async def fetch_latest(self, session: object) -> VersionInfo:
        """Run this test case."""
        _ = session
        return VersionInfo(version="1.0.0", metadata={})

    async def fetch_checksums(
        self, info: VersionInfo, session: object
    ) -> dict[str, str]:
        """Run this test case."""
        _ = (info, session)
        return {"x86_64-linux": "deadbeef"}


class _DummyDownload(DownloadHashUpdater):
    name = "download"
    BASE_URL = "https://example.com"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "x86_64-linux": "linux.tar.gz",
        "aarch64-linux": "arm.tar.gz",
    }

    async def fetch_latest(self, session: object) -> VersionInfo:
        """Run this test case."""
        _ = session
        return VersionInfo(version="1.0.0", metadata={})


class _DummyHashEntry(HashEntryUpdater):
    name = "hash-entry"
    input_name = "input"

    async def fetch_latest(self, session: object) -> VersionInfo:
        """Run this test case."""
        _ = session
        return VersionInfo(version="1.0.0", metadata={})

    async def fetch_hashes(self, info: VersionInfo, session: object) -> EventStream:
        """Run this test case."""
        _ = (info, session)
        entry = HashEntry.create("sha256", HASH_A)
        payload: list[HashEntry] = [entry]
        yield UpdateEvent.value(self.name, payload)


class _DummyFlakeInput(FlakeInputHashUpdater):
    name = "dummy-flake"
    input_name = "dummy-input"
    hash_type = "sha256"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Run this test case."""
        return await super().fetch_latest(session)

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        _ = info

        async def _stream() -> EventStream:
            yield UpdateEvent.value(self.name, HASH_A)

        return _stream()


class _DummyDenoDeps(DenoDepsHashUpdater):
    name = "deno-hash"
    input_name = "deno-input"

    async def fetch_latest(self, session: object) -> VersionInfo:
        """Run this test case."""
        _ = session
        return VersionInfo(version="1.0.0", metadata={})

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        _ = info

        async def _stream() -> EventStream:
            payload: dict[str, str] = {
                "x86_64-linux": HASH_A,
                "aarch64-linux": HASH_B,
            }
            yield UpdateEvent.value(
                self.name,
                payload,
            )

        return _stream()


class _DummyManifest:
    def __init__(self) -> None:
        self.saved: list[Path] = []
        self.jsr_packages = [SimpleNamespace(files=["a", "b"])]
        self.npm_packages = [SimpleNamespace(name="n")]

    def save(self, path: Path) -> None:
        """Run this test case."""
        self.saved.append(path)


class _DummyDenoManifest(DenoManifestUpdater):
    name = "deno-manifest"
    input_name = "deno-manifest"


def _entry(version: str = "1.0.0", *, drv_hash: str | None = None) -> SourceEntry:
    return SourceEntry.model_validate({
        "version": version,
        "hashes": HashCollection(entries=[HashEntry.create("sha256", HASH_A)]),
        "drvHash": drv_hash,
    })


def _require_hash_mapping(payload: object) -> dict[str, str]:
    raw = expect_instance(payload, dict)
    mapping: dict[str, str] = {}
    for raw_key, raw_value in raw.items():
        key = expect_instance(raw_key, str)
        value = expect_instance(raw_value, str)
        mapping[key] = value
    return mapping


def _require_hash_entries(payload: object) -> list[HashEntry]:
    raw = expect_instance(payload, list)
    entries: list[HashEntry] = []
    for raw_entry in raw:
        entry = expect_instance(raw_entry, HashEntry)
        entries.append(entry)
    return entries


async def _with_session[T](
    run: Callable[[aiohttp.ClientSession], Awaitable[T]],
) -> T:
    async with aiohttp.ClientSession() as session:
        return await run(session)


def test_verify_platform_versions() -> None:
    """Run this test case."""
    check(_verify_platform_versions({"a": "1", "b": "1"}, "x") == "1")
    with pytest.raises(RuntimeError, match="version mismatch"):
        _verify_platform_versions({"a": "1", "b": "2"}, "x")


def test_updater_is_latest_and_update_stream_paths() -> None:
    """Run this test case."""
    updater = _DummyUpdater(latest="1.0.0")
    check(
        asyncio.run(
            object.__getattribute__(updater, "_is_latest")(
                None, VersionInfo(version="1", metadata={})
            )
        )
        is False
    )
    check(
        asyncio.run(
            object.__getattribute__(updater, "_is_latest")(
                _entry(version="2"), VersionInfo(version="1", metadata={})
            )
        )
        is False
    )

    current_commit = SourceEntry(
        version="1.0.0",
        hashes=HashCollection(entries=[HashEntry.create("sha256", HASH_A)]),
        commit="0" * 40,
    )
    info_commit = VersionInfo(version="1.0.0", metadata={"commit": "0" * 40})
    check(
        asyncio.run(
            object.__getattribute__(updater, "_is_latest")(current_commit, info_commit)
        )
        is True
    )

    async def _collect(
        current: SourceEntry | None,
        pinned: VersionInfo | None = None,
    ) -> list[UpdateEvent]:
        async with aiohttp.ClientSession() as session:
            return [
                event
                async for event in updater.update_stream(
                    current,
                    session,
                    pinned_version=pinned,
                )
            ]

    pinned_events = asyncio.run(
        _collect(_entry(), pinned=VersionInfo(version="1.0.0", metadata={}))
    )
    check(any(e.message == "Using pinned version: 1.0.0" for e in pinned_events))
    check(any(e.message == "Up to date (version: 1.0.0)" for e in pinned_events))

    changed_events = asyncio.run(_collect(_entry(version="0.9.0")))
    check(
        any(
            e.kind == UpdateEventKind.RESULT and isinstance(e.payload, SourceEntry)
            for e in changed_events
        )
    )


def test_checksum_provided_fetch_hashes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    updater = _DummyChecksum()

    async def _convert(name: str, hex_hash: str) -> EventStream:
        yield UpdateEvent.status(name, f"convert {hex_hash}")
        yield UpdateEvent.value(name, HASH_A)

    monkeypatch.setattr("lib.update.updaters.base.convert_nix_hash_to_sri", _convert)

    async def _collect() -> list[UpdateEvent]:
        info = VersionInfo(version="1.0.0", metadata={})
        async with aiohttp.ClientSession() as session:
            return [event async for event in updater.fetch_hashes(info, session)]

    events = asyncio.run(_collect())
    check(any(e.kind == UpdateEventKind.STATUS for e in events))
    final = [e for e in events if e.kind == UpdateEventKind.VALUE][-1]
    check(final.payload == {"x86_64-linux": HASH_A})


def test_fetch_checksums_from_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    updater = _DummyChecksum()

    async def _fetch_url(_session: object, url: str, **_kwargs: object) -> bytes:
        return b"sum1" if url.endswith("1") else b"sum2"

    monkeypatch.setattr("lib.update.updaters.base.fetch_url", _fetch_url)

    checksums = asyncio.run(
        _with_session(
            lambda session: object.__getattribute__(
                updater, "_fetch_checksums_from_urls"
            )(
                session,
                {"a": "https://x/1", "b": "https://x/2"},
            )
        )
    )
    check(checksums == {"a": "sum1", "b": "sum2"})

    with pytest.raises(RuntimeError, match="Empty checksum payload"):
        asyncio.run(
            _with_session(
                lambda session: object.__getattribute__(
                    updater, "_fetch_checksums_from_urls"
                )(
                    session,
                    {"a": "https://x/1"},
                    parser=lambda _payload, _url: "",
                )
            )
        )


def test_download_hash_updater(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    updater = _DummyDownload(config=resolve_config())
    info = VersionInfo(version="1.0.0", metadata={})

    check(
        updater.get_download_url("x86_64-linux", info)
        == "https://example.com/linux.tar.gz"
    )
    result = updater.build_result(info, {"x86_64-linux": HASH_A})
    urls = expect_not_none(result.urls)
    check(urls["x86_64-linux"].endswith("linux.tar.gz"))

    async def _hashes(_name: str, urls: Iterable[str]) -> EventStream:
        mapping = dict.fromkeys(urls, HASH_A)
        yield UpdateEvent.value("download", mapping)

    monkeypatch.setattr("lib.update.updaters.base.compute_url_hashes", _hashes)

    async def _collect() -> list[UpdateEvent]:
        async with aiohttp.ClientSession() as session:
            return [event async for event in updater.fetch_hashes(info, session)]

    events = asyncio.run(_collect())
    final = [e for e in events if e.kind == UpdateEventKind.VALUE][-1]
    payload = _require_hash_mapping(final.payload)
    check(set(payload) == {"x86_64-linux", "aarch64-linux"})


def test_hash_entry_updater_emit_and_build_result() -> None:
    """Run this test case."""
    updater = _DummyHashEntry()
    info = VersionInfo(version="1.0.0", metadata={})
    built = updater.build_result(info, [HashEntry.create("sha256", HASH_A)])
    check(built.input == "input")

    async def _stream() -> EventStream:
        yield UpdateEvent.value("x", HASH_A)

    events = asyncio.run(
        _collect_events(
            object.__getattribute__(updater, "_emit_single_hash_entry")(
                _stream(),
                error="missing",
                hash_type="sha256",
            )
        )
    )
    final = [e for e in events if e.kind == UpdateEventKind.VALUE][-1]
    payload = _require_hash_entries(final.payload)
    check(payload[0].hash_type == "sha256")

    async def _no_value() -> EventStream:
        if False:
            yield UpdateEvent.status("x", "noop")

    with pytest.raises(RuntimeError, match="missing"):
        asyncio.run(
            _collect_events(
                object.__getattribute__(updater, "_emit_single_hash_entry")(
                    _no_value(),
                    error="missing",
                    hash_type="sha256",
                )
            )
        )


def test_flake_input_helpers_and_hash_updater_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    updater = _DummyFlakeInput()

    class _NoInput(_DummyFlakeInput):
        input_name = None

    no_input = _NoInput()
    no_input.input_name = None
    with pytest.raises(RuntimeError, match="Missing input name"):
        _ = object.__getattribute__(no_input, "_input")

    monkeypatch.setattr(
        "lib.update.updaters.base.get_flake_input_node",
        lambda _name: SimpleNamespace(locked=SimpleNamespace(rev="a" * 40)),
    )
    monkeypatch.setattr(
        "lib.update.updaters.base.get_flake_input_version", lambda _node: "2.0.0"
    )
    latest = asyncio.run(_with_session(updater.fetch_latest))
    check(latest.version == "2.0.0")

    current = _entry(version="1.0.0", drv_hash="drv")
    info = VersionInfo(version="1.0.0", metadata={})
    monkeypatch.setattr(
        "lib.update.updaters.base.compute_drv_fingerprint",
        lambda *_a, **_k: asyncio.sleep(0, result="drv"),
    )
    check(
        asyncio.run(object.__getattribute__(updater, "_is_latest")(current, info))
        is True
    )

    events = asyncio.run(
        _collect_events(object.__getattribute__(updater, "_finalize_result")(_entry()))
    )
    check(any(e.kind == UpdateEventKind.STATUS for e in events))
    check(any(e.kind == UpdateEventKind.VALUE for e in events))

    monkeypatch.setattr(
        "lib.update.updaters.base.compute_drv_fingerprint",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    object.__setattr__(updater, "_cached_fingerprint", None)
    warn_events = asyncio.run(
        _collect_events(object.__getattribute__(updater, "_finalize_result")(_entry()))
    )
    check(
        any(
            e.message and "Warning: derivation fingerprint unavailable" in e.message
            for e in warn_events
        )
    )

    class _PlatformFlake(_DummyFlakeInput):
        platform_specific = True

    plat = _PlatformFlake()
    monkeypatch.setattr(
        "lib.update.updaters.base.get_current_nix_platform", lambda: "x86_64-linux"
    )
    plat_events = asyncio.run(
        _with_session(lambda session: _collect_events(plat.fetch_hashes(info, session)))
    )
    plat_payload = _require_hash_entries(
        [e for e in plat_events if e.kind == UpdateEventKind.VALUE][-1].payload
    )
    check(plat_payload[0].platform == "x86_64-linux")


def test_deno_deps_hash_updater_paths() -> None:
    """Run this test case."""
    updater = _DummyDenoDeps()
    info = VersionInfo(version="1.0.0", metadata={})

    events = asyncio.run(
        _with_session(
            lambda session: _collect_events(updater.fetch_hashes(info, session))
        )
    )
    payload = _require_hash_entries(
        [e for e in events if e.kind == UpdateEventKind.VALUE][-1].payload
    )
    check({e.platform for e in payload} == {"x86_64-linux", "aarch64-linux"})

    class _BadDeno(_DummyDenoDeps):
        def _compute_hash(self, info: VersionInfo) -> EventStream:
            _ = info

            async def _stream() -> EventStream:
                yield UpdateEvent.value(self.name, "not-a-dict")

            return _stream()

    with pytest.raises(TypeError, match="Expected dict of platform hashes"):
        asyncio.run(
            _with_session(
                lambda session: _collect_events(_BadDeno().fetch_hashes(info, session))
            )
        )


def test_deno_manifest_updater_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run this test case."""
    updater = _DummyDenoManifest(config=resolve_config())
    manifest = _DummyManifest()
    node = SimpleNamespace(
        locked=SimpleNamespace(owner="o", repo="r", rev="a" * 40),
    )

    monkeypatch.setattr(
        "lib.update.updaters.base.get_flake_input_node", lambda _name: node
    )
    monkeypatch.setattr(
        "lib.update.deno_lock.resolve_deno_deps",
        lambda _path: asyncio.sleep(0, result=manifest),
    )
    monkeypatch.setattr("lib.update.paths.package_dir_for", lambda _name: tmp_path)
    monkeypatch.setattr(
        "lib.update.updaters.base.fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=b"{}"),
    )

    info = VersionInfo(version="1.0.0", metadata={})
    events = asyncio.run(
        _with_session(
            lambda session: _collect_events(updater.fetch_hashes(info, session))
        )
    )
    saved = expect_not_none(manifest.saved)
    check(saved[0].name == updater.manifest_file)
    check(any(e.message and "Wrote deno-deps.json" in e.message for e in events))
    check([e for e in events if e.kind == UpdateEventKind.VALUE][-1].payload == [])

    monkeypatch.setattr(
        "lib.update.updaters.base.get_flake_input_node",
        lambda _name: SimpleNamespace(locked=None),
    )
    bad_info = VersionInfo(version="1.0.0", metadata={})
    with pytest.raises(RuntimeError, match="incomplete lock"):
        asyncio.run(
            _with_session(
                lambda session: _collect_events(updater.fetch_hashes(bad_info, session))
            )
        )

    monkeypatch.setattr(
        "lib.update.updaters.base.get_flake_input_node", lambda _name: node
    )
    monkeypatch.setattr("lib.update.paths.package_dir_for", lambda _name: None)
    with pytest.raises(RuntimeError, match="Package directory not found"):
        asyncio.run(
            _with_session(
                lambda session: _collect_events(updater.fetch_hashes(info, session))
            )
        )


def test_factory_helpers_return_expected_subclasses() -> None:
    """Run this test case."""
    check(flake_input_hash_updater("x", "vendorHash").hash_type == "vendorHash")
    check(go_vendor_updater("x").hash_type == "vendorHash")
    check(cargo_vendor_updater("x").hash_type == "cargoHash")
    check(npm_deps_updater("x").hash_type == "npmDepsHash")
    check(bun_node_modules_updater("x").platform_specific is True)
    check(uv_lock_hash_updater("x").hash_type == "uvLockHash")
    check(deno_deps_updater("x").__name__.endswith("Updater"))
    check(deno_manifest_updater("x").__name__.endswith("Updater"))


def test_emdash_uses_platform_specific_npm_hashes() -> None:
    """Ensure emdash tracks npmDepsHash per platform in CI."""
    updater = UPDATERS["emdash"]
    check(getattr(updater, "hash_type", None) == "npmDepsHash")
    check(getattr(updater, "platform_specific", False) is True)


async def _collect_events(stream: EventStream) -> list[UpdateEvent]:
    return [event async for event in stream]
