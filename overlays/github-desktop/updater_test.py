"""Tests for the GitHub Desktop beta overlay updater."""

from types import ModuleType
from typing import TYPE_CHECKING, cast

import pytest

from lib.nix.models.flake_lock import FlakeLockNode, LockedRef, OriginalRef
from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters import UpdateContext, VersionInfo

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import aiohttp

ROOT_HASH = "sha256-rFnOc1QtnRBeEfv/moud3FTirqiPWCu0NEXJ6PQ+c14="
APP_HASH = "sha256-Yhmo0Ptl4VYBkg/uSkPwYrzObndH04SjzVV4IZduzws="
ELECTRON_VERSION = "42.0.1"


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


def _version_info(version: str = "3.5.9-beta2") -> VersionInfo:
    return VersionInfo(
        version=version,
        metadata={"electronVersion": ELECTRON_VERSION},
    )


def test_github_desktop_fetch_latest_reads_locked_release_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve package version from the beta flake input ref."""
    module = _load_updater_module()
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node", lambda _name: _locked_node()
    )
    fetch_calls: list[tuple[object, str, object]] = []

    async def _fetch_json(session: object, url: str, *, config: object = None):
        fetch_calls.append((session, url, config))
        return {"devDependencies": {"electron": ELECTRON_VERSION}}

    monkeypatch.setattr(module, "fetch_json", _fetch_json)
    updater = module.GitHubDesktopUpdater()
    session = cast("aiohttp.ClientSession", object())

    info = _run(updater.fetch_latest(session))

    assert info.version == "3.5.9-beta2"
    assert info.commit == "a" * 40
    assert info.metadata["electronVersion"] == ELECTRON_VERSION
    assert fetch_calls == [
        (
            session,
            f"https://raw.githubusercontent.com/desktop/desktop/{'a' * 40}/package.json",
            updater.config,
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        None,
        {},
        {"devDependencies": {}},
        {"devDependencies": {"electron": "^42.0.1"}},
    ],
)
def test_github_desktop_fetch_latest_requires_exact_electron_version(
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
) -> None:
    """Fail closed when the locked release manifest lacks an exact runtime."""
    module = _load_updater_module()
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node", lambda _name: _locked_node()
    )

    async def _fetch_json(_session: object, _url: str, *, config: object = None):
        _ = config
        return payload

    monkeypatch.setattr(module, "fetch_json", _fetch_json)

    with pytest.raises(TypeError, match="exact Electron version"):
        _run(module.GitHubDesktopUpdater().fetch_latest(object()))


def test_github_desktop_fetch_latest_requires_release_ref(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject refs that cannot map to GitHub Desktop package versions."""
    module = _load_updater_module()
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node", lambda _name: _locked_node("main")
    )
    updater = module.GitHubDesktopUpdater()

    with pytest.raises(RuntimeError, match="Expected GitHub Desktop release ref"):
        _run(updater.fetch_latest(cast("aiohttp.ClientSession", object())))


def test_github_desktop_fetch_latest_requires_locked_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject mutable flake input state before fetching its release manifest."""
    module = _load_updater_module()
    unlocked_node = _locked_node().model_copy(update={"locked": None})
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda _name: unlocked_node,
    )

    with pytest.raises(RuntimeError, match="missing an immutable commit"):
        _run(module.GitHubDesktopUpdater().fetch_latest(object()))


def test_github_desktop_fetch_latest_rejects_empty_release_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject release refs that omit the version suffix."""
    module = _load_updater_module()
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
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
        _version_info(),
        [
            HashEntry.create("yarnRootHash", ROOT_HASH),
            HashEntry.create("yarnAppHash", APP_HASH),
        ],
    )

    assert result.version == "3.5.9-beta2"
    assert result.input == "github-desktop"
    assert result.electron_version == ELECTRON_VERSION
    assert result.hashes.entries == [
        HashEntry.create("yarnRootHash", ROOT_HASH),
        HashEntry.create("yarnAppHash", APP_HASH),
    ]
    assert (
        module.GitHubDesktopUpdater._has_required_hashes(SourceEntry(hashes={}))
        is False
    )


def test_github_desktop_checked_in_source_can_satisfy_freshness() -> None:
    """Persist every field required by the updater's current-state check."""
    source = SourceEntry.model_validate_json(
        (REPO_ROOT / "overlays/github-desktop/sources.json").read_text(encoding="utf-8")
    )

    assert source.drv_hash is not None
    assert source.electron_version == ELECTRON_VERSION
    assert source.input == "github-desktop"
    assert _load_updater_module().GitHubDesktopUpdater._has_required_hashes(source)


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

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)
    updater = module.GitHubDesktopUpdater()

    events = _run(
        _collect_events(
            updater.fetch_hashes(
                _version_info(),
                cast("aiohttp.ClientSession", object()),
            )
        )
    )

    assert [call["name"] for call in calls] == ["github-desktop", "github-desktop"]
    assert all(call["env"] is None for call in calls)
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

    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)
    updater = module.GitHubDesktopUpdater()

    with pytest.raises(RuntimeError, match="Missing yarnRootHash output"):
        _run(
            _collect_events(
                updater.fetch_hashes(
                    _version_info(),
                    cast("aiohttp.ClientSession", object()),
                )
            )
        )


def test_github_desktop_electron_change_uses_candidate_override_and_converges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One update must hash and fingerprint the newly resolved Electron lock."""
    module = _load_updater_module()
    updater = module.GitHubDesktopUpdater()
    overlay_calls: list[dict[str, object]] = []
    fingerprint_calls: list[dict[str, object]] = []
    fixed_hash_calls = 0

    async def _latest(
        _self: object,
        _session: aiohttp.ClientSession,
    ) -> VersionInfo:
        return _version_info()

    def _overlay_attr(
        source: str,
        attr_path: str,
        *,
        system: str | None = None,
        source_overrides: dict[str, SourceEntry] | None = None,
        fake_hashes: bool | None = None,
    ) -> str:
        overlay_calls.append({
            "attr_path": attr_path,
            "fake_hashes": fake_hashes,
            "source": source,
            "source_overrides": source_overrides,
            "system": system,
        })
        return f"candidate-overlay{attr_path}"

    async def _fixed_hash(
        name: str,
        expr: str,
        *,
        config: object = None,
    ) -> AsyncIterator[UpdateEvent]:
        nonlocal fixed_hash_calls
        fixed_hash_calls += 1
        assert name == "github-desktop"
        _ = (expr, config)
        value = ROOT_HASH if fixed_hash_calls == 1 else APP_HASH
        yield UpdateEvent.value(name, value)

    async def _fingerprint(
        name: str,
        *,
        system: str | None = None,
        config: object = None,
        repo_root: str | None = None,
        source_overrides: dict[str, SourceEntry] | None = None,
        fake_hashes: bool | None = None,
    ) -> str:
        assert name == "github-desktop"
        fingerprint_calls.append({
            "fake_hashes": fake_hashes,
            "repo_root": repo_root,
            "source_overrides": source_overrides,
            "system": system,
        })
        _ = config
        return "candidate-drv"

    current = SourceEntry.model_validate({
        "version": "3.5.9-beta2",
        "input": "github-desktop",
        "electronVersion": "41.7.0",
        "drvHash": "old-drv",
        "hashes": [
            {"hashType": "yarnRootHash", "hash": ROOT_HASH},
            {"hashType": "yarnAppHash", "hash": APP_HASH},
        ],
    })
    monkeypatch.setattr(module.GitHubDesktopUpdater, "fetch_latest", _latest)
    monkeypatch.setattr(module, "_build_overlay_attr_expr", _overlay_attr)
    monkeypatch.setattr("lib.update.nix.compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr("lib.update.nix.compute_drv_fingerprint", _fingerprint)

    first_events = _run(
        _collect_events(
            updater.update_stream(
                current,
                cast("aiohttp.ClientSession", object()),
            )
        )
    )
    first_result = first_events[-1].payload
    assert isinstance(first_result, SourceEntry)
    second_events = _run(
        _collect_events(
            updater.update_stream(
                first_result,
                cast("aiohttp.ClientSession", object()),
            )
        )
    )

    assert first_result.electron_version == ELECTRON_VERSION
    assert first_result.drv_hash == "candidate-drv"
    assert fixed_hash_calls == 2
    assert second_events[-1] == UpdateEvent.result("github-desktop")
    assert len(overlay_calls) == 2
    for call in overlay_calls:
        assert call["fake_hashes"] is True
        source_overrides = call["source_overrides"]
        assert isinstance(source_overrides, dict)
        assert source_overrides["github-desktop"].electron_version == ELECTRON_VERSION
    assert len(fingerprint_calls) == 2
    for call in fingerprint_calls:
        assert call["fake_hashes"] is True
        source_overrides = call["source_overrides"]
        assert isinstance(source_overrides, dict)
        assert source_overrides["github-desktop"].electron_version == ELECTRON_VERSION
    assert fingerprint_calls[0] == fingerprint_calls[1]


def test_github_desktop_is_latest_requires_hashes_and_drv_fingerprint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Latest checks should include the cache set and fake-hash drv fingerprint."""
    module = _load_updater_module()
    updater = module.GitHubDesktopUpdater()
    entry = SourceEntry.model_validate({
        "version": "3.5.9-beta2",
        "input": "github-desktop",
        "electronVersion": ELECTRON_VERSION,
        "drvHash": "drv",
        "hashes": [
            {"hashType": "yarnRootHash", "hash": ROOT_HASH},
            {"hashType": "yarnAppHash", "hash": APP_HASH},
        ],
    })

    async def _fingerprint(
        _name: str,
        *,
        config: object = None,
        **_kwargs: object,
    ) -> str:
        _ = config
        return "drv"

    monkeypatch.setattr("lib.update.nix.compute_drv_fingerprint", _fingerprint)

    assert _run(updater._is_latest(entry, _version_info())) is True
    assert _run(updater._is_latest(entry, _version_info("3.5.9-beta3"))) is False
    assert (
        _run(
            updater._is_latest(
                entry.model_copy(update={"electron_version": "41.7.0"}),
                _version_info(),
            )
        )
        is False
    )
    assert (
        _run(
            updater._is_latest(
                entry.model_copy(update={"drv_hash": "old"}),
                _version_info(),
            )
        )
        is False
    )

    async def _fingerprint_failure(
        _name: str,
        *,
        config: object = None,
        **_kwargs: object,
    ) -> str:
        _ = config
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr("lib.update.nix.compute_drv_fingerprint", _fingerprint_failure)
    assert _run(updater._is_latest(entry, _version_info())) is False


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

    async def _fingerprint_failure(
        _name: str,
        *,
        config: object = None,
        **_kwargs: object,
    ) -> str:
        _ = config
        msg = "boom"
        raise RuntimeError(msg)

    monkeypatch.setattr("lib.update.nix.compute_drv_fingerprint", _fingerprint_failure)

    events = _run(_collect_events(updater._finalize_result(entry)))

    assert events[1].kind is UpdateEventKind.STATUS
    assert "Warning: derivation fingerprint unavailable (boom)" in events[1].message
    assert events[-1].kind is UpdateEventKind.VALUE
    assert events[-1].payload.drv_hash is None
