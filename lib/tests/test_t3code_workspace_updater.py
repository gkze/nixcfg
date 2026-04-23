"""Tests for the T3 Code workspace updater."""

from __future__ import annotations

import asyncio
from types import ModuleType

import pytest

from lib.import_utils import load_module_from_path
from lib.nix.models.sources import SourceEntry
from lib.update.events import UpdateEventKind
from lib.update.paths import REPO_ROOT
from lib.update.updaters.base import VersionInfo

HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _load_module() -> ModuleType:
    return load_module_from_path(
        REPO_ROOT / "packages/t3code-workspace/updater.py",
        "t3code_workspace_updater_test",
    )


def _run(awaitable):
    return asyncio.run(awaitable)


async def _collect(stream):
    return [event async for event in stream]


def test_t3code_workspace_updater_tracks_only_aarch64_darwin() -> None:
    """The helper package should only run on its single supported platform."""
    updater_cls = _load_module().T3CodeWorkspaceUpdater

    assert updater_cls.input_name == "t3code"
    assert updater_cls.hash_type == "nodeModulesHash"
    assert updater_cls.platform_specific is True
    assert updater_cls.native_only is True
    assert updater_cls.supported_platforms == ("aarch64-darwin",)


def test_t3code_workspace_fetch_hashes_uses_direct_helper_expr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hashing should bypass the missing public package attr and build the helper directly."""
    module = _load_module()
    updater = module.T3CodeWorkspaceUpdater()
    captured: dict[str, object] = {}

    async def _fake_compute(expr: str):
        captured["expr"] = expr
        return HASH

    monkeypatch.setattr(module, "_compute_workspace_hash", _fake_compute)

    events = _run(_collect(updater.fetch_hashes(VersionInfo(version="main"), object())))

    assert "./packages/t3code-workspace/default.nix" in captured["expr"]
    assert "git+file://" in captured["expr"]
    assert events[-1].kind is UpdateEventKind.VALUE
    payload = events[-1].payload
    assert isinstance(payload, list)
    assert len(payload) == 1
    hash_entry = payload[0]
    assert hash_entry.hash == HASH
    assert hash_entry.platform == "aarch64-darwin"


def test_t3code_workspace_is_latest_uses_direct_fingerprint_expr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Latest checks should fingerprint the helper derivation directly."""
    module = _load_module()
    updater = module.T3CodeWorkspaceUpdater()
    current = SourceEntry.model_validate({
        "version": "main",
        "drvHash": "abc123",
        "hashes": [
            {
                "hashType": "nodeModulesHash",
                "hash": HASH,
                "platform": "aarch64-darwin",
            }
        ],
    })

    captured: dict[str, object] = {}

    async def _fake_fingerprint(source: str, expr: str, *, config: object):
        captured.update({"source": source, "expr": expr, "config": config})
        return "abc123"

    monkeypatch.setattr(module, "compute_expr_drv_fingerprint", _fake_fingerprint)

    assert _run(updater._is_latest(current, VersionInfo(version="main"))) is True
    assert captured["source"] == "t3code-workspace"
    assert "./packages/t3code-workspace/default.nix" in captured["expr"]
