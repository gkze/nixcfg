"""Tests for the GitHub Desktop beta overlay updater."""

from __future__ import annotations

from types import ModuleType
from typing import TYPE_CHECKING, cast

import pytest

from lib.nix.models.flake_lock import FlakeLockNode, LockedRef, OriginalRef
from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.updaters.base import UpdateContext, VersionInfo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import aiohttp

ROOT_HASH = "sha256-rFnOc1QtnRBeEfv/moud3FTirqiPWCu0NEXJ6PQ+c14="
APP_HASH = "sha256-Yhmo0Ptl4VYBkg/uSkPwYrzObndH04SjzVV4IZduzws="


def _load_updater_module() -> ModuleType:
    return load_repo_module(
        "overlays/github-desktop/updater.py",
        "github_desktop_updater_test",
    )


def _locked_node(ref: str = "refs/tags/release-3.5.9-beta2") -> FlakeLockNode:
    return FlakeLockNode(
        original=OriginalRef(
            type="git",
            url="https://github.com/desktop/desktop.git",
            ref=ref,
        ),
        locked=LockedRef(
            type="git",
            url="https://github.com/desktop/desktop.git",
            ref=ref,
            rev="a" * 40,
            narHash="sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        ),
    )


def test_github_desktop_fetch_latest_reads_locked_release_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve package version from the beta flake input ref."""
    module = _load_updater_module()
    monkeypatch.setattr(module, "get_flake_input_node", lambda _name: _locked_node())
    updater = module.GitHubDesktopUpdater()

    info = _run(
        updater.fetch_latest(cast("aiohttp.ClientSession", object())),
    )

    assert info.version == "3.5.9-beta2"
    assert info.commit == "a" * 40


def test_github_desktop_fetch_latest_requires_release_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject refs that cannot map to GitHub Desktop package versions."""
    module = _load_updater_module()
    monkeypatch.setattr(
        module, "get_flake_input_node", lambda _name: _locked_node("main")
    )
    updater = module.GitHubDesktopUpdater()

    with pytest.raises(RuntimeError, match="Expected GitHub Desktop release ref"):
        _run(updater.fetch_latest(cast("aiohttp.ClientSession", object())))


def test_github_desktop_fetch_latest_rejects_empty_release_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject release refs that omit the version suffix."""
    module = _load_updater_module()
    monkeypatch.setattr(
        module,
        "get_flake_input_node",
        lambda _name: _locked_node("refs/tags/release-"),
    )
    updater = module.GitHubDesktopUpdater()

    with pytest.raises(RuntimeError, match="Empty GitHub Desktop version"):
        _run(updater.fetch_latest(cast("aiohttp.ClientSession", object())))


def test_github_desktop_build_result_tracks_input_and_hashes() -> None:
    """Persist beta metadata with the backing flake input name."""
    module = _load_updater_module()
    updater = module.GitHubDesktopUpdater()

    result = updater.build_result(
        VersionInfo(version="3.5.9-beta2"),
        [
            HashEntry.create("yarnRootHash", ROOT_HASH),
            HashEntry.create("yarnAppHash", APP_HASH),
        ],
    )

    assert result.version == "3.5.9-beta2"
    assert result.input == "github-desktop"
    assert result.hashes.entries == [
        HashEntry.create("yarnRootHash", ROOT_HASH),
        HashEntry.create("yarnAppHash", APP_HASH),
    ]
    assert (
        module.GitHubDesktopUpdater._has_required_hashes(SourceEntry(hashes={}))
        is False
    )


def test_github_desktop_fetch_hashes_computes_both_yarn_caches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compute root and app Yarn cache hashes from the overlay attrs."""
    module = _load_updater_module()
    calls: list[dict[str, object]] = []

    async def _fixed_hash(
        name: str,
        expr: str,
        *,
        env: object = None,
        config: object = None,
    ) -> AsyncIterator[UpdateEvent]:
        calls.append({"name": name, "expr": expr, "env": env, "config": config})
        yield UpdateEvent.value(name, ROOT_HASH if len(calls) == 1 else APP_HASH)

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)
    updater = module.GitHubDesktopUpdater()

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                VersionInfo(version="3.5.9-beta2"),
                cast("aiohttp.ClientSession", object()),
            )
        )
    )

    assert [call["name"] for call in calls] == ["github-desktop", "github-desktop"]
    assert all(call["env"] == {"FAKE_HASHES": "1"} for call in calls)
    assert "cacheRoot" in cast("str", calls[0]["expr"])
    assert "cacheApp" in cast("str", calls[1]["expr"])
    assert events[-1].kind is UpdateEventKind.VALUE
    assert events[-1].payload == [
        HashEntry.create("yarnRootHash", ROOT_HASH),
        HashEntry.create("yarnAppHash", APP_HASH),
    ]


def test_github_desktop_fetch_hashes_requires_each_cache_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail clearly if a cache hash command produces no value event."""
    module = _load_updater_module()

    async def _fixed_hash(
        name: str,
        _expr: str,
        *,
        env: object = None,
        config: object = None,
    ) -> AsyncIterator[UpdateEvent]:
        _ = (env, config)
        yield UpdateEvent.status(name, "hashing")

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)
    updater = module.GitHubDesktopUpdater()

    with pytest.raises(RuntimeError, match="Missing yarnRootHash output"):
        _run(
            _collect_events(
                updater.fetch_hashes(
                    VersionInfo(version="3.5.9-beta2"),
                    cast("aiohttp.ClientSession", object()),
                )
            )
        )


def test_github_desktop_is_latest_requires_hashes_and_drv_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Latest checks should include the cache set and fake-hash drv fingerprint."""
    module = _load_updater_module()
    updater = module.GitHubDesktopUpdater()
    entry = SourceEntry.model_validate({
        "version": "3.5.9-beta2",
        "input": "github-desktop",
        "drvHash": "drv",
        "hashes": [
            {"hashType": "yarnRootHash", "hash": ROOT_HASH},
            {"hashType": "yarnAppHash", "hash": APP_HASH},
        ],
    })

    async def _fingerprint(_name: str, *, config: object = None) -> str:
        _ = config
        return "drv"

    monkeypatch.setattr(module, "compute_drv_fingerprint", _fingerprint)

    assert _run(updater._is_latest(entry, VersionInfo(version="3.5.9-beta2"))) is True
    assert _run(updater._is_latest(entry, VersionInfo(version="3.5.9-beta3"))) is False
    assert (
        _run(
            updater._is_latest(
                entry.model_copy(update={"drv_hash": "old"}),
                VersionInfo(version="3.5.9-beta2"),
            )
        )
        is False
    )

    async def _fingerprint_failure(_name: str, *, config: object = None) -> str:
        _ = config
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(module, "compute_drv_fingerprint", _fingerprint_failure)
    assert _run(updater._is_latest(entry, VersionInfo(version="3.5.9-beta2"))) is False


def test_github_desktop_finalize_result_uses_cached_context_fingerprint() -> None:
    """Attach the drv fingerprint gathered during freshness checks."""
    module = _load_updater_module()
    updater = module.GitHubDesktopUpdater()
    entry = SourceEntry.model_validate({
        "version": "3.5.9-beta2",
        "input": "github-desktop",
        "hashes": [
            {"hashType": "yarnRootHash", "hash": ROOT_HASH},
            {"hashType": "yarnAppHash", "hash": APP_HASH},
        ],
    })
    context = UpdateContext(current=None, drv_fingerprint="drv")

    events = _run(_collect_events(updater._finalize_result(entry, context=context)))

    assert events[0].kind is UpdateEventKind.STATUS
    assert events[-1].kind is UpdateEventKind.VALUE
    assert events[-1].payload.drv_hash == "drv"


def test_github_desktop_finalize_result_warns_when_fingerprint_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve the result when drv fingerprinting is unavailable."""
    module = _load_updater_module()
    updater = module.GitHubDesktopUpdater()
    entry = SourceEntry.model_validate({
        "version": "3.5.9-beta2",
        "input": "github-desktop",
        "hashes": [
            {"hashType": "yarnRootHash", "hash": ROOT_HASH},
            {"hashType": "yarnAppHash", "hash": APP_HASH},
        ],
    })

    async def _fingerprint_failure(_name: str, *, config: object = None) -> str:
        _ = config
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr(module, "compute_drv_fingerprint", _fingerprint_failure)

    events = _run(_collect_events(updater._finalize_result(entry)))

    assert events[1].kind is UpdateEventKind.STATUS
    assert "Warning: derivation fingerprint unavailable (boom)" in events[1].message
    assert events[-1].kind is UpdateEventKind.VALUE
    assert events[-1].payload.drv_hash is None
