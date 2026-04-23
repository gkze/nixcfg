"""Tests for the OpenCode Desktop Electron updater module."""

from __future__ import annotations

import asyncio
import json
from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.nix.models.sources import SourceEntry
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo


def _load_updater_module() -> ModuleType:
    """Load the updater module under test."""
    return load_module_from_path(
        REPO_ROOT / "packages/opencode-desktop-electron/updater.py",
        "opencode_desktop_electron_updater_test",
    )


def _run(awaitable):
    """Run a small updater coroutine in tests."""
    return asyncio.run(awaitable)


def test_opencode_desktop_electron_updater_tracks_all_supported_platform_hashes() -> (
    None
):
    """The updater should preserve the full persisted platform hash matrix."""
    updater_cls = _load_updater_module().OpencodeDesktopElectronUpdater
    payload = json.loads(
        (REPO_ROOT / "packages/opencode-desktop-electron/sources.json").read_text(
            encoding="utf-8"
        )
    )

    assert updater_cls.input_name == "opencode"
    assert updater_cls.hash_type == "nodeModulesHash"
    assert updater_cls.platform_specific is True
    assert updater_cls.native_only is False
    hashes = payload.get("hashes")
    assert isinstance(hashes, list)
    assert len(hashes) == 4


def test_opencode_desktop_electron_platform_targets_dedupes_current_platform() -> None:
    """The current platform should not be duplicated when already supported."""
    updater = _load_updater_module().OpencodeDesktopElectronUpdater()

    assert updater._platform_targets("x86_64-linux") == (
        "x86_64-linux",
        "aarch64-darwin",
        "x86_64-darwin",
        "aarch64-linux",
    )


@pytest.mark.parametrize(
    ("base_latest", "current", "expected"),
    [
        pytest.param(False, None, False, id="superclass-false"),
        pytest.param(True, None, False, id="missing-entry"),
        pytest.param(
            True,
            SourceEntry.model_validate({
                "version": "1.2.3",
                "hashes": [
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                        "platform": "aarch64-darwin",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                        "platform": "x86_64-darwin",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                        "platform": "aarch64-linux",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
                        "platform": "x86_64-linux",
                    },
                    {
                        "hashType": "sha256",
                        "hash": "sha256-EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE=",
                        "platform": "x86_64-linux",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF=",
                    },
                ],
            }),
            True,
            id="entries-match-supported-platforms",
        ),
        pytest.param(
            True,
            SourceEntry.model_validate({
                "version": "1.2.3",
                "hashes": {
                    "aarch64-darwin": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                    "x86_64-darwin": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                    "aarch64-linux": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                    "x86_64-linux": "sha256-DDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDDD=",
                },
            }),
            True,
            id="mapping-match-supported-platforms",
        ),
        pytest.param(
            True,
            SourceEntry.model_validate({
                "version": "1.2.3",
                "hashes": [
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
                        "platform": "aarch64-darwin",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB=",
                        "platform": "x86_64-darwin",
                    },
                    {
                        "hashType": "nodeModulesHash",
                        "hash": "sha256-CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC=",
                        "platform": "aarch64-linux",
                    },
                ],
            }),
            False,
            id="entries-mismatch-missing-platform",
        ),
    ],
)
def test_opencode_desktop_electron_is_latest_validates_platform_hash_coverage(
    monkeypatch: pytest.MonkeyPatch,
    base_latest: bool,
    current: SourceEntry | None,
    expected: bool,
) -> None:
    """Latest checks require a base match and a complete supported-platform set."""
    module = _load_updater_module()
    updater = module.OpencodeDesktopElectronUpdater()

    async def _base_is_latest(self, context, info):
        _ = (self, context, info)
        return base_latest

    monkeypatch.setattr(module.FlakeInputHashUpdater, "_is_latest", _base_is_latest)

    assert _run(updater._is_latest(current, VersionInfo(version="1.2.3"))) is expected
