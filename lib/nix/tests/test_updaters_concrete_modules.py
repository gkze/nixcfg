"""Tests for concrete updater modules in overlays/packages."""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
from types import ModuleType, SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from lib.nix.models.sources import HashEntry
from lib.nix.tests._assertions import check, expect_instance, expect_not_none
from lib.update.events import EventStream, UpdateEvent, UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine, Iterable

HASH_A = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
HASH_B = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="


def _load_module(path: str, name: str) -> ModuleType:
    loader = importlib.machinery.SourceFileLoader(name, str(REPO_ROOT / path))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    if spec is None or spec.loader is None:
        msg = f"failed to load module at {path}"
        raise RuntimeError(msg)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _run[T](coro: Coroutine[object, object, T]) -> T:
    return asyncio.run(coro)


async def _collect(stream: EventStream) -> list[UpdateEvent]:
    return [event async for event in stream]


def _require_hash_entries(payload: object) -> list[HashEntry]:
    raw = expect_instance(payload, list)
    return [expect_instance(entry, HashEntry) for entry in raw]


@pytest.fixture(scope="module")
def chatgpt_module() -> ModuleType:
    """Run this test case."""
    return _load_module("overlays/chatgpt/updater.py", "chatgpt_updater_test")


@pytest.fixture(scope="module")
def code_cursor_module() -> ModuleType:
    """Run this test case."""
    return _load_module("overlays/code-cursor/updater.py", "code_cursor_updater_test")


@pytest.fixture(scope="module")
def datagrip_module() -> ModuleType:
    """Run this test case."""
    return _load_module("overlays/datagrip/updater.py", "datagrip_updater_test")


@pytest.fixture(scope="module")
def google_chrome_module() -> ModuleType:
    """Run this test case."""
    return _load_module(
        "overlays/google-chrome/updater.py", "google_chrome_updater_test"
    )


@pytest.fixture(scope="module")
def sentry_cli_module() -> ModuleType:
    """Run this test case."""
    return _load_module("overlays/sentry-cli/updater.py", "sentry_cli_updater_test")


@pytest.fixture(scope="module")
def conductor_module() -> ModuleType:
    """Run this test case."""
    return _load_module("packages/conductor/updater.py", "conductor_updater_test")


@pytest.fixture(scope="module")
def droid_module() -> ModuleType:
    """Run this test case."""
    return _load_module("packages/droid/updater.py", "droid_updater_test")


@pytest.fixture(scope="module")
def scratch_module() -> ModuleType:
    """Run this test case."""
    return _load_module("packages/scratch/updater.py", "scratch_updater_test")


@pytest.fixture(scope="module")
def sculptor_module() -> ModuleType:
    """Run this test case."""
    return _load_module("packages/sculptor/updater.py", "sculptor_updater_test")


def test_chatgpt_updater_paths(
    chatgpt_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run this test case."""
    updater = chatgpt_module.ChatGPTUpdater()
    xml = (
        b'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
        b"<channel><item>"
        b"<sparkle:shortVersionString>1.2.3</sparkle:shortVersionString>"
        b'<enclosure url="https://example.com/app.dmg" />'
        b"</item></channel></rss>"
    )

    monkeypatch.setattr(
        chatgpt_module, "fetch_url", lambda *_a, **_k: asyncio.sleep(0, result=xml)
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "1.2.3")
    check(latest.metadata["url"] == "https://example.com/app.dmg")

    check(
        updater.get_download_url("x86_64-darwin", latest)
        == "https://example.com/app.dmg"
    )

    monkeypatch.setattr(
        chatgpt_module, "fetch_url", lambda *_a, **_k: asyncio.sleep(0, result=b"<")
    )
    with pytest.raises(RuntimeError, match="Invalid appcast XML"):
        _run(updater.fetch_latest(object()))

    no_item = b"<rss><channel></channel></rss>"
    monkeypatch.setattr(
        chatgpt_module, "fetch_url", lambda *_a, **_k: asyncio.sleep(0, result=no_item)
    )
    with pytest.raises(RuntimeError, match="No items found"):
        _run(updater.fetch_latest(object()))

    no_version = b"<rss><channel><item><enclosure url='x'/></item></channel></rss>"
    monkeypatch.setattr(
        chatgpt_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=no_version),
    )
    with pytest.raises(RuntimeError, match="No version found"):
        _run(updater.fetch_latest(object()))

    no_enclosure = (
        b'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
        b"<channel><item><sparkle:shortVersionString>1</sparkle:shortVersionString>"
        b"</item></channel></rss>"
    )
    monkeypatch.setattr(
        chatgpt_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=no_enclosure),
    )
    with pytest.raises(RuntimeError, match="No enclosure found"):
        _run(updater.fetch_latest(object()))

    no_url = (
        b'<rss xmlns:sparkle="http://www.andymatuschak.org/xml-namespaces/sparkle">'
        b"<channel><item><sparkle:shortVersionString>1</sparkle:shortVersionString>"
        b"<enclosure /></item></channel></rss>"
    )
    monkeypatch.setattr(
        chatgpt_module, "fetch_url", lambda *_a, **_k: asyncio.sleep(0, result=no_url)
    )
    with pytest.raises(RuntimeError, match="No URL found"):
        _run(updater.fetch_latest(object()))


def test_code_cursor_updater_paths(
    code_cursor_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    updater = code_cursor_module.CodeCursorUpdater()
    check(
        object.__getattribute__(updater, "_api_url")("darwin-arm64").endswith(
            "platform=darwin-arm64&releaseTrack=stable"
        )
    )

    platform_info = {
        nix_plat: {"downloadUrl": f"https://example.com/{api_plat}.zip"}
        for nix_plat, api_plat in updater.PLATFORMS.items()
    }
    info = VersionInfo(version="1.0.0", metadata={"platform_info": platform_info})

    check(
        object.__getattribute__(updater, "_download_url")("darwin-arm64", info)
        == "https://example.com/darwin-arm64.zip"
    )

    async def _hashes(_name: str, urls: Iterable[str]) -> EventStream:
        url_map = dict.fromkeys(urls, HASH_A)
        yield UpdateEvent.status("code-cursor", "hashing")
        yield UpdateEvent.value("code-cursor", url_map)

    monkeypatch.setattr("lib.update.process.compute_url_hashes", _hashes)
    checksums = _run(updater.fetch_checksums(info, object()))
    check(set(checksums) == set(updater.PLATFORMS))
    check(all(v == HASH_A for v in checksums.values()))


def test_datagrip_updater_paths(
    datagrip_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run this test case."""
    updater = datagrip_module.DataGripUpdater()
    payload = {
        "DG": [
            {
                "version": "2025.1",
                "downloads": {
                    "macM1": {"checksumLink": "https://c/m1", "link": "https://d/m1"},
                    "linuxARM64": {
                        "checksumLink": "https://c/a64",
                        "link": "https://d/a64",
                    },
                    "linux": {"checksumLink": "https://c/x64", "link": "https://d/x64"},
                },
            }
        ]
    }

    monkeypatch.setattr(
        datagrip_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=payload),
    )
    info = _run(updater.fetch_latest(object()))
    check(info.version == "2025.1")

    monkeypatch.setattr(
        datagrip_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result={"DG": []}),
    )
    with pytest.raises(RuntimeError, match="No DataGrip releases"):
        _run(updater.fetch_latest(object()))

    monkeypatch.setattr(
        datagrip_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result={"DG": [{"downloads": {}}]}),
    )
    with pytest.raises(RuntimeError, match="Missing DataGrip version"):
        _run(updater.fetch_latest(object()))

    urls_seen: dict[str, str] = {}

    async def _fetch_checksums(
        _session: object,
        urls: dict[str, str],
        parser: Callable[[bytes, str], str] | None = None,
    ) -> dict[str, str]:
        for platform, url in urls.items():
            urls_seen[platform] = parser(b"abcd  file", url) if parser else ""
        return dict.fromkeys(urls, "abcd")

    monkeypatch.setattr(updater, "_fetch_checksums_from_urls", _fetch_checksums)
    parsed_checksums = _run(updater.fetch_checksums(info, object()))
    check(parsed_checksums == dict.fromkeys(updater.PLATFORMS, "abcd"))
    check(set(urls_seen) == set(updater.PLATFORMS))

    result = updater.build_result(info, {"x86_64-linux": HASH_A})
    urls = expect_not_none(result.urls)
    check(urls["x86_64-linux"] == "https://d/x64")


def test_google_chrome_updater_paths(
    google_chrome_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    updater = google_chrome_module.GoogleChromeUpdater()
    monkeypatch.setattr(
        google_chrome_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=[{"version": "133.0.1"}]),
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "133.0.1")

    monkeypatch.setattr(
        google_chrome_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=[]),
    )
    with pytest.raises(RuntimeError, match="No Chrome releases"):
        _run(updater.fetch_latest(object()))

    monkeypatch.setattr(
        google_chrome_module,
        "fetch_json",
        lambda *_a, **_k: asyncio.sleep(0, result=[{}]),
    )
    with pytest.raises(RuntimeError, match="Missing version"):
        _run(updater.fetch_latest(object()))


def test_sentry_cli_updater_paths(
    sentry_cli_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    updater = sentry_cli_module.SentryCliUpdater()
    monkeypatch.setattr(
        sentry_cli_module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(0, result={"tag_name": "v2.0.0"}),
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "v2.0.0")

    src_expr = object.__getattribute__(updater, "_src_nix_expr")("v2.0.0")
    cargo_expr = object.__getattribute__(updater, "_cargo_nix_expr")("v2.0.0", HASH_A)
    check("fetchFromGitHub" in src_expr)
    check("fetchCargoVendor" in cargo_expr)

    monkeypatch.setattr(sentry_cli_module, "_build_nix_expr", lambda expr: expr)

    call_count = 0

    async def _fixed_hash(_name: str, expr: str) -> EventStream:
        nonlocal call_count
        call_count += 1
        yield UpdateEvent.status("sentry-cli", f"build {expr[:5]}")
        yield UpdateEvent.value("sentry-cli", HASH_A if call_count == 1 else HASH_B)

    monkeypatch.setattr(sentry_cli_module, "compute_fixed_output_hash", _fixed_hash)
    events = _run(_collect(updater.fetch_hashes(latest, object())))
    values = [e for e in events if e.kind == UpdateEventKind.VALUE]
    payload = _require_hash_entries(values[-1].payload)
    check(payload[0].hash_type == "srcHash")
    check(payload[1].hash_type == "cargoHash")

    async def _no_hash(_name: str, _expr: str) -> EventStream:
        if False:
            yield UpdateEvent.status("x", "y")

    monkeypatch.setattr(sentry_cli_module, "compute_fixed_output_hash", _no_hash)
    with pytest.raises(RuntimeError, match="Missing srcHash output"):
        _run(_collect(updater.fetch_hashes(latest, object())))


def test_conductor_updater_paths(
    conductor_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run this test case."""
    updater = conductor_module.ConductorUpdater()
    monkeypatch.setattr(
        conductor_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "Content-Disposition": 'attachment; filename="Conductor_1.2.3_arm64.dmg"'
            },
        ),
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "1.2.3")

    monkeypatch.setattr(
        conductor_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(
            0, result={"Content-Disposition": "attachment; filename=oops"}
        ),
    )
    with pytest.raises(RuntimeError, match="Could not parse version"):
        _run(updater.fetch_latest(object()))


def test_droid_updater_paths(
    droid_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run this test case."""
    updater = droid_module.DroidUpdater()
    check(
        object.__getattribute__(updater, "_download_url")(
            "x86_64-linux", "1.0.0"
        ).endswith("/1.0.0/linux/x64/droid")
    )

    monkeypatch.setattr(
        droid_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=b'#!/bin/sh\nVER="2.3.4"\n'),
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "2.3.4")

    monkeypatch.setattr(
        droid_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=b"no version"),
    )
    with pytest.raises(RuntimeError, match="Could not parse version"):
        _run(updater.fetch_latest(object()))

    captured: dict[str, str] = {}

    async def _fetch_checksums(
        _session: object, urls: dict[str, str], **_kwargs: object
    ) -> dict[str, str]:
        captured.update(urls)
        return dict.fromkeys(urls, "sum")

    monkeypatch.setattr(updater, "_fetch_checksums_from_urls", _fetch_checksums)
    checksums = _run(
        updater.fetch_checksums(VersionInfo(version="2.3.4", metadata={}), object())
    )
    check(checksums["x86_64-linux"] == "sum")
    check(captured["x86_64-linux"].endswith("/2.3.4/linux/x64/droid.sha256"))

    built = updater.build_result(
        VersionInfo(version="2.3.4", metadata={}), {"x86_64-linux": HASH_A}
    )
    built_urls = expect_not_none(built.urls)
    check(built_urls["x86_64-linux"].endswith("/2.3.4/linux/x64/droid"))


def test_scratch_updater_paths(
    scratch_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run this test case."""
    updater = scratch_module.ScratchUpdater()

    monkeypatch.setattr(
        scratch_module,
        "get_flake_input_node",
        lambda _name: SimpleNamespace(locked=SimpleNamespace(rev="f" * 40)),
    )
    monkeypatch.setattr(
        scratch_module, "get_flake_input_version", lambda _node: "9.9.9"
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "9.9.9")
    check(latest.metadata["commit"] == "f" * 40)

    npm_expr = object.__getattribute__(updater, "_expr_for_npm_deps")()
    cargo_expr = object.__getattribute__(updater, "_expr_for_cargo_vendor")()
    check("fetchNpmDeps" in npm_expr)
    check("fetchCargoVendor" in cargo_expr)

    async def _fixed_hash(_name: str, expr: str, **_kwargs: object) -> EventStream:
        if "fetchNpmDeps" in expr:
            yield UpdateEvent.value("scratch", HASH_A)
        else:
            yield UpdateEvent.value("scratch", HASH_B)

    monkeypatch.setattr(scratch_module, "compute_fixed_output_hash", _fixed_hash)
    events = _run(_collect(updater.fetch_hashes(latest, object())))
    payload = _require_hash_entries(
        [e for e in events if e.kind == UpdateEventKind.VALUE][-1].payload
    )
    check([entry.hash_type for entry in payload] == ["npmDepsHash", "cargoHash"])

    async def _no_hash(_name: str, _expr: str, **_kwargs: object) -> EventStream:
        if False:
            yield UpdateEvent.status("scratch", "none")

    monkeypatch.setattr(scratch_module, "compute_fixed_output_hash", _no_hash)
    with pytest.raises(RuntimeError, match="Missing npmDepsHash output"):
        _run(_collect(updater.fetch_hashes(latest, object())))

    built = updater.build_result(
        latest,
        [scratch_module.HashEntry.create("npmDepsHash", HASH_A)],
    )
    check(built.input == "scratch")
    check(built.commit == "f" * 40)


def test_sculptor_updater_paths(
    sculptor_module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run this test case."""
    updater = sculptor_module.SculptorUpdater()

    monkeypatch.setattr(
        sculptor_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(
            0, result={"Last-Modified": "Tue, 20 Feb 2024 12:34:56 GMT"}
        ),
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "2024-02-20")

    monkeypatch.setattr(
        sculptor_module,
        "parsedate_to_datetime",
        lambda _value: (_ for _ in ()).throw(ValueError("bad date")),
    )
    monkeypatch.setattr(
        sculptor_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"Last-Modified": "invalid-date"}),
    )
    fallback = _run(updater.fetch_latest(object()))
    check(fallback.version == "invalid-da")

    monkeypatch.setattr(
        sculptor_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"Last-Modified": ""}),
    )
    with pytest.raises(RuntimeError, match="No Last-Modified header"):
        _run(updater.fetch_latest(object()))
