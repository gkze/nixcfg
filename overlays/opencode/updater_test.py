"""Tests for the OpenCode CLI updater module."""

import json
from types import ModuleType

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import HashEntry, SourceEntry
from lib.system_policy import supported_systems
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.bun_toolchain import bun_release_urls
from lib.update.net import github_raw_url
from lib.update.paths import REPO_ROOT
from lib.update.updaters import UpdateContext, VersionInfo
from lib.update.updaters.exact_bun import (
    ExactBunMetadata,
    ExactBunNodeModulesHashUpdater,
)

_REF = "gkze/patches"
_COMMIT = "d" * 40
_BUN_VERSION = "1.4.4"
_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


@pytest.fixture(scope="module")
def updater_module() -> ModuleType:
    """Load the updater module under test."""
    return load_repo_module(
        "overlays/opencode/updater.py",
        "opencode_updater_test",
    )


def _flake_node() -> FlakeLockNode:
    return FlakeLockNode.model_validate({
        "locked": {
            "type": "github",
            "owner": "gkze",
            "repo": "opencode",
            "rev": _COMMIT,
            "narHash": _HASH,
        },
        "original": {
            "type": "github",
            "owner": "gkze",
            "repo": "opencode",
            "ref": _REF,
        },
    })


def _source_entry(*platforms: str) -> SourceEntry:
    """Build a source entry with one nodeModulesHash per platform."""
    return SourceEntry.model_validate({
        "version": _REF,
        "hashes": [
            {"hashType": "nodeModulesHash", "hash": _HASH, "platform": platform}
            for platform in platforms
        ],
    })


def test_opencode_sources_pin_the_exact_bun_for_every_supported_platform() -> None:
    """The persisted matrix covers every exported system and its Bun runtime."""
    payload = json.loads(
        (REPO_ROOT / "overlays/opencode/sources.json").read_text(encoding="utf-8")
    )
    expected = supported_systems()

    def platforms(hash_type: str) -> set[str]:
        return {
            entry["platform"]
            for entry in payload["hashes"]
            if entry["hashType"] == hash_type
        }

    assert platforms("nodeModulesHash") == set(expected)
    assert {
        (entry["platform"], entry["url"])
        for entry in payload["hashes"]
        if entry["hashType"] == "bunRuntimeHash"
    } == set(bun_release_urls(payload["pins"]["bunVersion"], expected).items())


def test_fetch_latest_pins_bun_from_the_locked_manifest(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refreshed input commit's packageManager selects the installer Bun."""
    node = _flake_node()
    fetched_urls: list[str] = []
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node",
        lambda input_name: node if input_name == "opencode" else None,
    )

    async def fetch_json(_session: object, url: str, **_kwargs: object) -> object:
        fetched_urls.append(url)
        return {"name": "opencode", "packageManager": f"bun@{_BUN_VERSION}"}

    monkeypatch.setattr(updater_module, "fetch_json", fetch_json)
    updater = updater_module.OpencodeUpdater()

    info = _run(updater.fetch_latest(object(), context=UpdateContext(current=None)))

    assert fetched_urls == [github_raw_url("gkze", "opencode", _COMMIT, "package.json")]
    assert info.version == _REF
    assert info.metadata == ExactBunMetadata(
        node=node, commit=_COMMIT, bun_version=_BUN_VERSION
    )
    assert updater.build_result(info, []).pins == {"bunVersion": _BUN_VERSION}


@pytest.mark.parametrize(
    ("package_manager", "message"),
    [
        pytest.param("bun@^1.4.4", "exact semantic version", id="range"),
        pytest.param("pnpm@10.0.0", "exact bun@<version>", id="other-manager"),
    ],
)
def test_fetch_latest_rejects_an_inexact_bun_pin(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    package_manager: str,
    message: str,
) -> None:
    """Only an exact Bun release can reproduce the source lockfile."""
    monkeypatch.setattr(
        "lib.update.flake.get_flake_input_node", lambda _input_name: _flake_node()
    )

    async def fetch_json(_session: object, _url: str, **_kwargs: object) -> object:
        return {"packageManager": package_manager}

    monkeypatch.setattr(updater_module, "fetch_json", fetch_json)

    with pytest.raises(RuntimeError, match=message):
        _run(
            updater_module.OpencodeUpdater().fetch_latest(
                object(), context=UpdateContext(current=None)
            )
        )


@pytest.mark.parametrize(
    ("base_latest", "current", "expected"),
    [
        pytest.param(False, _source_entry(*supported_systems()), False, id="base"),
        pytest.param(True, None, False, id="missing-entry"),
        pytest.param(
            True, _source_entry(*supported_systems()), True, id="complete-platforms"
        ),
        pytest.param(
            True,
            _source_entry("aarch64-darwin", "x86_64-linux"),
            False,
            id="missing-platform",
        ),
        pytest.param(
            True,
            _source_entry(*supported_systems(), "x86_64-darwin"),
            False,
            id="unexpected-platform",
        ),
    ],
)
def test_opencode_is_latest_requires_exact_bun_and_platform_coverage(
    updater_module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    base_latest: bool,
    current: SourceEntry | None,
    expected: bool,
) -> None:
    """Latest needs the exact-Bun fingerprint and the supported-platform set."""

    async def _base_is_latest(_self: object, _context: object, _info: object) -> bool:
        return base_latest

    monkeypatch.setattr(ExactBunNodeModulesHashUpdater, "_is_latest", _base_is_latest)
    info = VersionInfo(
        version=_REF,
        metadata=ExactBunMetadata(
            node=_flake_node(), commit=_COMMIT, bun_version=_BUN_VERSION
        ),
    )

    assert (
        _run(
            updater_module.OpencodeUpdater()._is_latest(
                UpdateContext(current=current), info
            )
        )
        is expected
    )


def test_is_latest_rejects_runtime_sources_for_another_bun(
    updater_module: ModuleType,
) -> None:
    """A packageManager bump invalidates the persisted Bun runtime sources."""
    stale_urls = bun_release_urls("1.4.2", supported_systems())
    current = SourceEntry(
        version=_REF,
        hashes=[
            HashEntry.create("bunRuntimeHash", _HASH, platform=system, url=url)
            for system, url in stale_urls.items()
        ],
        pins={"bunVersion": "1.4.2"},
        drv_hash="unchanged-fingerprint",
    )
    info = VersionInfo(
        version=_REF,
        metadata=ExactBunMetadata(
            node=_flake_node(), commit=_COMMIT, bun_version=_BUN_VERSION
        ),
    )

    assert (
        _run(
            updater_module.OpencodeUpdater()._is_latest(
                UpdateContext(current=current), info
            )
        )
        is False
    )
