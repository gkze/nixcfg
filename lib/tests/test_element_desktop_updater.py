"""Tests for the element-desktop updater overlay surface."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from lib.import_utils import load_module_from_path
from lib.nix.models.sources import HashEntry
from lib.update.events import UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo


def _run[T](coro):
    return asyncio.run(coro)


async def _collect_events(stream):
    return [event async for event in stream]


def _load_module(module_name: str = "element_desktop_updater_test"):
    return load_module_from_path(
        REPO_ROOT / "overlays/element-desktop/updater.py",
        module_name,
    )


def test_element_desktop_fetch_latest_reads_pinned_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the pinned version from the local sources entry."""
    module = _load_module()
    updater = module.ElementDesktopUpdater()

    monkeypatch.setattr(
        module, "package_dir_for", lambda _name: Path("/tmp/element-desktop")
    )
    monkeypatch.setattr(
        module.update_sources,
        "load_source_entry",
        lambda path: type("Entry", (), {"path": path, "version": "1.11.99"})(),
    )

    latest = _run(updater.fetch_latest(object()))

    assert latest.version == "1.11.99"
    assert latest.metadata is module.NO_METADATA


@pytest.mark.parametrize(
    ("pkg_dir", "version", "match"),
    [
        (None, "1.11.99", "Package directory not found for element-desktop"),
        (Path("/tmp/element-desktop"), "", "missing a pinned version"),
        (Path("/tmp/element-desktop"), None, "missing a pinned version"),
    ],
)
def test_element_desktop_fetch_latest_rejects_missing_package_or_version(
    monkeypatch: pytest.MonkeyPatch,
    pkg_dir: Path | None,
    version: str | None,
    match: str,
) -> None:
    """Raise clear errors when the pinned sources entry is unavailable or invalid."""
    module = _load_module("element_desktop_updater_test_fetch_latest_error")
    updater = module.ElementDesktopUpdater()

    monkeypatch.setattr(module, "package_dir_for", lambda _name: pkg_dir)
    monkeypatch.setattr(
        module.update_sources,
        "load_source_entry",
        lambda _path: type("Entry", (), {"version": version})(),
    )

    with pytest.raises(RuntimeError, match=match):
        _run(updater.fetch_latest(object()))


def test_element_desktop_expr_builders_include_expected_structure() -> None:
    """Build GitHub source and fetchYarnDeps expressions for the pinned tag."""
    module = _load_module("element_desktop_updater_test_exprs")

    src_expr = module.ElementDesktopUpdater._src_expr("1.11.99")
    offline_expr = module.ElementDesktopUpdater._offline_expr("1.11.99", "sha256-src")

    assert 'owner = "element-hq"' in src_expr
    assert 'repo = "element-desktop"' in src_expr
    assert 'rev = "v1.11.99"' in src_expr
    assert "fetchFromGitHub" in src_expr

    assert "fetchYarnDeps" in offline_expr
    assert 'owner = "element-hq"' in offline_expr
    assert 'repo = "element-desktop"' in offline_expr
    assert 'rev = "v1.11.99"' in offline_expr
    assert 'hash = "sha256-src"' in offline_expr


def test_element_desktop_is_latest_always_recomputes_hashes() -> None:
    """Pinned releases always force a hash refresh before comparison."""
    module = _load_module("element_desktop_updater_test_is_latest")
    updater = module.ElementDesktopUpdater()

    assert _run(updater._is_latest(object(), VersionInfo(version="1.11.99"))) is False


def test_element_desktop_fetch_hashes_streams_events_and_emits_hash_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compute src and offline hashes in order and forward status events."""
    module = _load_module("element_desktop_updater_test_fetch_hashes")
    updater = module.ElementDesktopUpdater()
    info = VersionInfo(version="1.11.99")
    calls: list[tuple[str, str]] = []

    async def _fixed_hash(name: str, expr: str, *, config=None):
        assert name == updater.name
        assert config == updater.config
        calls.append((name, expr))
        if len(calls) == 1:
            yield module.UpdateEvent.status(name, "building src")
            yield module.UpdateEvent.value(name, "sha256-src")
            return
        yield module.UpdateEvent.status(name, "building offline cache")
        yield module.UpdateEvent.value(name, "sha256-offline")

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(_collect_events(updater.fetch_hashes(info, object())))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert [event.message for event in events[:-1]] == [
        "building src",
        "building offline cache",
    ]
    assert calls == [
        ("element-desktop", updater._src_expr("1.11.99")),
        ("element-desktop", updater._offline_expr("1.11.99", "sha256-src")),
    ]
    assert events[-1].payload == [
        HashEntry.create("srcHash", "sha256-src"),
        HashEntry.create("sha256", "sha256-offline"),
    ]


def test_element_desktop_fetch_hashes_requires_src_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise when the source hash stream never captures a hash value."""
    module = _load_module("element_desktop_updater_test_missing_src")
    updater = module.ElementDesktopUpdater()

    async def _no_src_hash(_name: str, _expr: str, *, config=None):
        _ = config
        if False:
            yield module.UpdateEvent.status("element-desktop", "never")

    monkeypatch.setattr(module, "compute_fixed_output_hash", _no_src_hash)

    with pytest.raises(RuntimeError, match="Missing srcHash output"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.11.99"), object())
            )
        )


def test_element_desktop_fetch_hashes_falls_back_when_src_capture_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise from the updater when the wrapped source capture yields no final value."""
    module = _load_module("element_desktop_updater_test_missing_src_fallback")
    updater = module.ElementDesktopUpdater()

    async def _fixed_hash(_name: str, _expr: str, *, config=None):
        _ = config
        yield module.UpdateEvent.status("element-desktop", "building src")

    async def _capture_without_value(events, *, error: str):
        assert error == "Missing srcHash output"
        async for event in events:
            yield event

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(module, "capture_stream_value", _capture_without_value)

    with pytest.raises(RuntimeError, match="Missing srcHash output"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.11.99"), object())
            )
        )


def test_element_desktop_fetch_hashes_requires_sha256(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise when the offline cache hash stream never captures a hash value."""
    module = _load_module("element_desktop_updater_test_missing_sha")
    updater = module.ElementDesktopUpdater()

    async def _missing_sha(_name: str, expr: str, *, config=None):
        _ = config
        if expr == updater._src_expr("1.11.99"):
            yield module.UpdateEvent.value("element-desktop", "sha256-src")
            return
        if False:
            yield module.UpdateEvent.status("element-desktop", "never")

    monkeypatch.setattr(module, "compute_fixed_output_hash", _missing_sha)

    with pytest.raises(RuntimeError, match="Missing sha256 output"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.11.99"), object())
            )
        )


def test_element_desktop_fetch_hashes_falls_back_when_sha_capture_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise from the updater when the wrapped offline capture yields no final value."""
    module = _load_module("element_desktop_updater_test_missing_sha_fallback")
    updater = module.ElementDesktopUpdater()

    async def _fixed_hash(_name: str, expr: str, *, config=None):
        _ = config
        if expr == updater._src_expr("1.11.99"):
            yield module.UpdateEvent.value("element-desktop", "sha256-src")
            return
        yield module.UpdateEvent.status("element-desktop", "building offline cache")

    async def _capture_selectively(events, *, error: str):
        async for event in events:
            yield event
        if error == "Missing srcHash output":
            yield module.CapturedValue("sha256-src")

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(module, "capture_stream_value", _capture_selectively)

    with pytest.raises(RuntimeError, match="Missing sha256 output"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.11.99"), object())
            )
        )


def test_element_desktop_fetch_hashes_rejects_non_string_src_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail fast when the source hash capture payload has the wrong type."""
    module = _load_module("element_desktop_updater_test_bad_src_type")
    updater = module.ElementDesktopUpdater()

    async def _bad_src_hash(_name: str, _expr: str, *, config=None):
        _ = config
        yield module.UpdateEvent.value("element-desktop", {"hash": "sha256-src"})

    monkeypatch.setattr(module, "compute_fixed_output_hash", _bad_src_hash)

    with pytest.raises(TypeError, match="Expected srcHash string"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.11.99"), object())
            )
        )


def test_element_desktop_fetch_hashes_rejects_non_string_sha256(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail fast when the offline cache hash capture payload has the wrong type."""
    module = _load_module("element_desktop_updater_test_bad_sha_type")
    updater = module.ElementDesktopUpdater()

    async def _bad_sha(_name: str, expr: str, *, config=None):
        _ = config
        if expr == updater._src_expr("1.11.99"):
            yield module.UpdateEvent.value("element-desktop", "sha256-src")
            return
        yield module.UpdateEvent.value("element-desktop", ["sha256-offline"])

    monkeypatch.setattr(module, "compute_fixed_output_hash", _bad_sha)

    with pytest.raises(TypeError, match="Expected sha256 string"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.11.99"), object())
            )
        )
