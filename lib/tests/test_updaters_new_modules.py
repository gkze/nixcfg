"""Tests for newer updater modules added to the registry."""

from __future__ import annotations

import asyncio
import importlib.machinery
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any

import pytest

from lib.nix.models.sources import SourceEntry
from lib.tests._assertions import check, expect_instance
from lib.update.events import EventStream, UpdateEvent, UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo

if TYPE_CHECKING:
    from collections.abc import Coroutine


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


@pytest.fixture(scope="module")
def commander_module() -> ModuleType:
    """Load the commander updater module."""
    return _load_module("packages/commander/updater.py", "commander_updater_test")


@pytest.fixture(scope="module")
def codex_desktop_module() -> ModuleType:
    """Load the codex-desktop updater module."""
    return _load_module(
        "packages/codex-desktop/updater.py", "codex_desktop_updater_test"
    )


@pytest.fixture(scope="module")
def opencode_desktop_module() -> ModuleType:
    """Load the opencode-desktop updater module."""
    return _load_module(
        "packages/opencode-desktop/updater.py", "opencode_desktop_updater_test"
    )


@pytest.fixture(scope="module")
def element_desktop_module() -> ModuleType:
    """Load the element-desktop updater module."""
    return _load_module(
        "overlays/element-desktop/updater.py", "element_desktop_updater_test"
    )


@pytest.fixture(scope="module")
def superset_module() -> ModuleType:
    """Load the superset updater module."""
    return _load_module("packages/superset/updater.py", "superset_updater_test")


@pytest.fixture(scope="module")
def mux_module() -> ModuleType:
    """Load the mux updater module."""
    return _load_module("packages/mux/updater.py", "mux_updater_test")


def test_mux_uses_platform_specific_node_modules_hashes(
    mux_module: ModuleType,
) -> None:
    """Mux node_modules hashes should be tracked separately per platform."""
    updater_cls = mux_module.MuxUpdater
    check(updater_cls.platform_specific is True)
    check(updater_cls.hash_type == "nodeModulesHash")


def test_commander_fetches_latest_version_from_changelog(
    commander_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse the latest version from a markdown changelog heading."""
    updater = commander_module.CommanderUpdater()
    monkeypatch.setattr(
        commander_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=b"# Changelog\n\n## 0.7.875 - 2026-03-16\n\n- Fix stuff\n",
        ),
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "0.7.875")


def test_commander_fetches_latest_version_from_html_changelog(
    commander_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Parse the latest version from the live HTML changelog heading."""
    updater = commander_module.CommanderUpdater()
    monkeypatch.setattr(
        commander_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result=(
                b"<!DOCTYPE html><html><body><h1>Changelog</h1>"
                b"<h2>0.7.875 - 2026-03-16</h2>"
                b"<h3>Fix</h3></body></html>"
            ),
        ),
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "0.7.875")


def test_commander_uses_versioned_download_urls(
    commander_module: ModuleType,
) -> None:
    """Commander should pin each release to a versioned DMG URL."""
    updater = commander_module.CommanderUpdater()
    latest = VersionInfo(version="0.7.890")

    check(
        updater.get_download_url("aarch64-darwin", latest)
        == "https://download.thecommander.app/release/Commander-0.7.890.dmg"
    )
    check(
        updater.get_download_url("x86_64-darwin", latest)
        == "https://download.thecommander.app/release/Commander-0.7.890.dmg"
    )


def test_commander_rejects_changelog_without_release_heading(
    commander_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail when the changelog page lacks a release heading."""
    updater = commander_module.CommanderUpdater()
    monkeypatch.setattr(
        commander_module,
        "fetch_url",
        lambda *_a, **_k: asyncio.sleep(0, result=b"# Changelog\n\nNo releases yet\n"),
    )
    with pytest.raises(RuntimeError, match="Could not parse latest Commander version"):
        _run(updater.fetch_latest(object()))


def test_codex_desktop_version_header_priority(
    codex_desktop_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prefer Content-MD5, then ETag, then Last-Modified."""
    updater = codex_desktop_module.CodexDesktopUpdater()

    monkeypatch.setattr(
        codex_desktop_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={"Content-MD5": "dSDx9z9xMK/8IITfW12Edg=="},
        ),
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "md5.7520f1f73f7130affc2084df5b5d8476")

    monkeypatch.setattr(
        codex_desktop_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"ETag": '"0xABCDEF"'}),
    )
    etag_latest = _run(updater.fetch_latest(object()))
    check(etag_latest.version == "etag.abcdef")

    monkeypatch.setattr(
        codex_desktop_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={"Last-Modified": "Wed, 04 Mar 2026 00:25:01 GMT"},
        ),
    )
    lm_latest = _run(updater.fetch_latest(object()))
    check(lm_latest.version == "modified.20260304002501")


def test_codex_desktop_invalid_content_md5_raises(
    codex_desktop_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed Content-MD5 metadata."""
    updater = codex_desktop_module.CodexDesktopUpdater()
    monkeypatch.setattr(
        codex_desktop_module,
        "fetch_headers",
        lambda *_a, **_k: asyncio.sleep(0, result={"Content-MD5": "not-base64"}),
    )
    with pytest.raises(RuntimeError, match="Invalid Content-MD5"):
        _run(updater.fetch_latest(object()))


def test_opencode_dep_key_resolution_is_exact(
    opencode_desktop_module: ModuleType,
) -> None:
    """Resolve exact crate names and ignore similarly prefixed crates."""
    lockfile = "\n".join([
        "[[package]]",
        'name = "specta-macros"',
        'version = "2.0.0-rc.18"',
        'source = "git+https://github.com/specta-rs/specta?branch=main#111111"',
        "[[package]]",
        'name = "specta"',
        'version = "2.0.0-rc.22"',
        'source = "git+https://github.com/specta-rs/specta?branch=main#222222"',
        "[[package]]",
        'name = "tauri"',
        'version = "2.9.5"',
        'source = "git+https://github.com/tauri-apps/tauri?branch=dev#333333"',
        "[[package]]",
        'name = "tauri-specta"',
        'version = "2.0.0-rc.21"',
        'source = "git+https://github.com/specta-rs/tauri-specta?branch=main#444444"',
    ])
    keys = opencode_desktop_module.OpencodeDesktopUpdater._resolve_git_dep_keys(
        lockfile
    )
    check(keys["specta"] == "specta-2.0.0-rc.22")
    check(keys["tauri"] == "tauri-2.9.5")
    check(keys["tauri-specta"] == "tauri-specta-2.0.0-rc.21")


def test_opencode_hash_fetch_passes_direct_match_names(
    opencode_desktop_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pass direct git dep keys to cargo hash helper."""
    updater = opencode_desktop_module.OpencodeDesktopUpdater()
    lockfile = "\n".join([
        "[[package]]",
        'name = "specta"',
        'version = "2.0.0-rc.22"',
        'source = "git+https://github.com/specta-rs/specta?branch=main#222222"',
        "[[package]]",
        'name = "tauri"',
        'version = "2.9.5"',
        'source = "git+https://github.com/tauri-apps/tauri?branch=dev#333333"',
        "[[package]]",
        'name = "tauri-specta"',
        'version = "2.0.0-rc.21"',
        'source = "git+https://github.com/specta-rs/tauri-specta?branch=main#444444"',
    ])

    monkeypatch.setattr(
        updater,
        "_fetch_lockfile_content",
        lambda *_a, **_k: asyncio.sleep(0, result=lockfile),
    )

    captured: dict[str, object] = {}

    async def _compute_hashes(
        source: str,
        input_name: str,
        *,
        lockfile_path: str,
        git_deps: list[Any],
        lockfile_content: str | None = None,
        config: object,
    ):
        _ = (source, input_name, lockfile_path, lockfile_content, config)
        captured["git_deps"] = git_deps
        payload = {
            expect_instance(
                dep.git_dep, str
            ): "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
            for dep in git_deps
        }
        yield UpdateEvent.value("opencode-desktop", payload)

    monkeypatch.setattr(
        opencode_desktop_module,
        "compute_import_cargo_lock_output_hashes",
        _compute_hashes,
    )

    events = _run(_collect(updater.fetch_hashes(VersionInfo("main", {}), object())))
    value_events = [event for event in events if event.kind == UpdateEventKind.VALUE]
    payload = expect_instance(value_events[-1].payload, list)
    check(len(payload) == 3)

    deps = expect_instance(captured["git_deps"], list)
    check(all(dep.match_name == dep.git_dep for dep in deps))


def test_element_desktop_reads_pinned_version_from_sources(
    element_desktop_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load pinned version from per-package sources file."""
    updater = element_desktop_module.ElementDesktopUpdater()
    monkeypatch.setattr(
        element_desktop_module, "package_dir_for", lambda _n: Path("/tmp/x")
    )
    monkeypatch.setattr(
        element_desktop_module.update_sources,
        "load_source_entry",
        lambda _p: SourceEntry.model_validate({"version": "1.12.8", "hashes": []}),
    )
    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "1.12.8")

    is_latest = _run(updater._is_latest(None, latest))
    check(is_latest is False)


def test_superset_fetches_desktop_release_assets(
    superset_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve desktop version and release asset URL from GitHub releases."""
    updater = superset_module.SupersetUpdater()
    monkeypatch.setattr(
        superset_module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "desktop-v1.2.3",
                "assets": [
                    {
                        "name": "superset-1.2.3-x86_64.AppImage",
                        "browser_download_url": "https://example.test/superset-1.2.3-x86_64.AppImage",
                    }
                ],
            },
        ),
    )

    latest = _run(updater.fetch_latest(object()))
    check(latest.version == "1.2.3")
    check(
        updater.get_download_url("x86_64-linux", latest)
        == "https://example.test/superset-1.2.3-x86_64.AppImage"
    )


def test_superset_rejects_release_without_expected_asset(
    superset_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail when latest desktop release misses the expected Linux AppImage."""
    updater = superset_module.SupersetUpdater()
    monkeypatch.setattr(
        superset_module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "desktop-v1.2.3",
                "assets": [
                    {
                        "name": "superset-1.2.3-arm64.AppImage",
                        "browser_download_url": "https://example.test/other.AppImage",
                    }
                ],
            },
        ),
    )

    with pytest.raises(
        RuntimeError, match="Could not find Superset desktop release asset"
    ):
        _run(updater.fetch_latest(object()))


def test_superset_rejects_unexpected_release_tag(
    superset_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject release payloads that are not desktop-tagged."""
    updater = superset_module.SupersetUpdater()
    monkeypatch.setattr(
        superset_module,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(
            0,
            result={
                "tag_name": "v1.2.3",
                "assets": [],
            },
        ),
    )

    with pytest.raises(RuntimeError, match="Unexpected Superset release tag format"):
        _run(updater.fetch_latest(object()))


def test_superset_release_url_format(superset_module: ModuleType) -> None:
    """Generate fallback release URL and reject unsupported platforms."""
    updater = superset_module.SupersetUpdater()
    url = updater.get_download_url("x86_64-linux", VersionInfo("1.2.3", {}))
    check(
        url
        == "https://github.com/superset-sh/superset/releases/download/desktop-v1.2.3/superset-1.2.3-x86_64.AppImage"
    )

    with pytest.raises(RuntimeError, match="Unsupported platform"):
        updater.get_download_url("aarch64-linux", VersionInfo("1.2.3", {}))
