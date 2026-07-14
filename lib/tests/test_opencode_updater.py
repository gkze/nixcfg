"""Tests for the OpenCode CLI updater module."""

from __future__ import annotations

import json
from types import ModuleType

import pytest

from lib.nix.models.sources import SourceEntry
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.paths import REPO_ROOT
from lib.update.updaters import UpdateContext, VersionInfo


def _load_updater_module() -> ModuleType:
    """Load the updater module under test."""
    return load_repo_module(
        "overlays/opencode/updater.py",
        "opencode_updater_test",
    )


def _source_entry(*platforms: str) -> SourceEntry:
    """Build a source entry with one nodeModulesHash per platform."""
    return SourceEntry.model_validate({
        "version": "1.2.3",
        "hashes": [
            {
                "hashType": "nodeModulesHash",
                "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                "platform": platform,
            }
            for platform in platforms
        ]
        + [
            {
                "hashType": "sha256",
                "hash": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                "platform": "x86_64-linux",
            },
            {
                "hashType": "nodeModulesHash",
                "hash": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
            },
        ],
    })


def _mapping_source_entry() -> SourceEntry:
    """Build a legacy platform-to-hash mapping source entry."""
    return SourceEntry.model_validate({
        "version": "1.2.3",
        "hashes": {
            "aarch64-darwin": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
            "aarch64-linux": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
            "x86_64-linux": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
        },
    })


def test_opencode_updater_tracks_all_supported_platform_hashes() -> None:
    """The updater and persisted matrix must cover every exported CLI system."""
    updater_cls = _load_updater_module().OpencodeUpdater
    payload = json.loads(
        (REPO_ROOT / "overlays/opencode/sources.json").read_text(encoding="utf-8")
    )

    expected = ("aarch64-darwin", "aarch64-linux", "x86_64-linux")
    assert expected == updater_cls.SUPPORTED_PLATFORMS
    assert expected == updater_cls.supported_platforms
    assert {
        entry["platform"]
        for entry in payload["hashes"]
        if entry["hashType"] == "nodeModulesHash"
    } == set(expected)


@pytest.mark.parametrize(
    ("base_latest", "current", "expected"),
    [
        pytest.param(False, None, False, id="superclass-false"),
        pytest.param(True, None, False, id="missing-entry"),
        pytest.param(
            True,
            _source_entry("aarch64-darwin", "aarch64-linux", "x86_64-linux"),
            True,
            id="complete-platform-set",
        ),
        pytest.param(
            True,
            _mapping_source_entry(),
            True,
            id="complete-platform-mapping",
        ),
        pytest.param(
            True,
            UpdateContext(
                current=_source_entry(
                    "aarch64-darwin",
                    "aarch64-linux",
                    "x86_64-linux",
                )
            ),
            True,
            id="update-context",
        ),
        pytest.param(
            True,
            _source_entry("aarch64-darwin", "x86_64-linux"),
            False,
            id="missing-platform",
        ),
        pytest.param(
            True,
            _source_entry(
                "aarch64-darwin",
                "x86_64-darwin",
                "aarch64-linux",
                "x86_64-linux",
            ),
            False,
            id="unexpected-platform",
        ),
    ],
)
def test_opencode_is_latest_validates_platform_hash_coverage(
    monkeypatch: pytest.MonkeyPatch,
    base_latest: bool,
    current: UpdateContext | SourceEntry | None,
    expected: bool,
) -> None:
    """Latest checks require a base match and the exact supported-platform set."""
    module = _load_updater_module()
    updater = module.OpencodeUpdater()

    async def _base_is_latest(self, context, info):
        _ = (self, context, info)
        return base_latest

    monkeypatch.setattr(
        module.BunNodeModulesHashUpdater,
        "_is_latest",
        _base_is_latest,
    )

    assert _run(updater._is_latest(current, VersionInfo(version="1.2.3"))) is expected
