"""Tests for the element-desktop updater overlay surface."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import install_fixed_hash_stream, load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEventKind
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_fetch_pnpm_deps_expr,
)
from lib.update.updaters import base as updater_base
from lib.update.updaters.base import VersionInfo


def _load_module(module_name: str = "element_desktop_updater_test"):
    return load_repo_module("overlays/element-desktop/updater.py", module_name)


def test_element_desktop_fetch_latest_reads_pinned_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return the pinned version from the local sources entry."""
    module = _load_module()
    updater = module.ElementDesktopUpdater()

    monkeypatch.setattr(
        updater_base, "package_dir_for", lambda _name: Path("/tmp/element-desktop")
    )
    monkeypatch.setattr(
        updater_base.update_sources,
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

    monkeypatch.setattr(updater_base, "package_dir_for", lambda _name: pkg_dir)
    monkeypatch.setattr(
        updater_base.update_sources,
        "load_source_entry",
        lambda _path: type("Entry", (), {"version": version})(),
    )

    with pytest.raises(RuntimeError, match=match):
        _run(updater.fetch_latest(object()))


def test_element_desktop_expr_builders_include_expected_structure() -> None:
    """Build GitHub source and fetchPnpmDeps expressions for the pinned tag."""
    module = _load_module("element_desktop_updater_test_exprs")

    src_expr = module.ElementDesktopUpdater._src_expr("1.11.99")
    offline_expr = module.ElementDesktopUpdater._offline_expr("1.11.99", "sha256-src")

    assert_nix_ast_equal(
        src_expr,
        _build_fetch_from_github_call(
            "element-hq",
            "element-web",
            tag="v1.11.99",
        ),
    )
    assert_nix_ast_equal(
        offline_expr,
        _build_fetch_pnpm_deps_expr(
            _build_fetch_from_github_call(
                "element-hq",
                "element-web",
                tag="v1.11.99",
                hash_value="sha256-src",
            ),
            pname="element",
            version="1.11.99",
            fetcher_version=3,
        ),
    )


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
    calls = install_fixed_hash_stream(
        monkeypatch,
        (("building src", "sha256-src"), ("building offline cache", "sha256-offline")),
    )

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
        {
            "name": "element-desktop",
            "expr": updater._src_expr("1.11.99"),
            "env": None,
            "config": updater.config,
        },
        {
            "name": "element-desktop",
            "expr": updater._offline_expr("1.11.99", "sha256-src"),
            "env": None,
            "config": updater.config,
        },
    ]
    assert events[-1].payload == [
        HashEntry.create("srcHash", "sha256-src"),
        HashEntry.create("sha256", "sha256-offline"),
    ]
