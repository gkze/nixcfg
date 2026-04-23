"""Tests for the gemini-cli updater overlay surface."""

from __future__ import annotations

import asyncio
import json

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


def test_gemini_cli_updater_fetch_latest_and_override_payload(monkeypatch) -> None:
    """Resolve the GitHub release version and build the override payload."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/gemini-cli/updater.py",
        "gemini_cli_updater_test",
    )
    updater = module.GeminiCliUpdater()

    captured: dict[str, object] = {}

    async def _fetch_github_api(session, path: str, *, config=None):
        captured.update({"session": session, "path": path, "config": config})
        return {"tag_name": "v1.2.3"}

    monkeypatch.setattr(
        "lib.update.updaters.github_release.fetch_github_api",
        _fetch_github_api,
    )

    session = object()
    latest = _run(updater.fetch_latest(session))

    assert latest.version == "1.2.3"
    assert latest.metadata.tag == "v1.2.3"
    assert captured == {
        "session": session,
        "path": "repos/google-gemini/gemini-cli/releases/latest",
        "config": updater.config,
    }
    assert 'tag = "v1.2.3"' in updater._src_expr(latest.version)

    env = updater._override_env(latest.version, "sha256-src", "sha256-fake")
    assert json.loads(env["UPDATE_SOURCE_OVERRIDES_JSON"]) == {
        "gemini-cli": {
            "version": "1.2.3",
            "hashes": [
                {"hashType": "srcHash", "hash": "sha256-src"},
                {"hashType": "npmDepsHash", "hash": "sha256-fake"},
            ],
        }
    }


def test_gemini_cli_updater_fetch_hashes(monkeypatch) -> None:
    """Compute source and npm dependency hashes through mocked build streams."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/gemini-cli/updater.py",
        "gemini_cli_updater_test_hashes",
    )
    updater = module.GeminiCliUpdater()
    info = VersionInfo(version="1.2.3")

    calls: list[dict[str, object]] = []

    async def _fixed_hash(name: str, expr: str, *, env=None, config=None):
        calls.append({"name": name, "expr": expr, "env": env, "config": config})
        if len(calls) == 1:
            yield module.UpdateEvent.status(name, "building src")
            yield module.UpdateEvent.value(name, "sha256-src")
            return
        yield module.UpdateEvent.status(name, "building npm")
        yield module.UpdateEvent.value(name, "sha256-npm")

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(_collect_events(updater.fetch_hashes(info, object())))

    assert [event.kind for event in events[:-1]] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
    ]
    assert [event.message for event in events[:-1]] == ["building src", "building npm"]
    assert calls == [
        {
            "name": "gemini-cli",
            "expr": updater._src_expr(info.version),
            "env": None,
            "config": updater.config,
        },
        {
            "name": "gemini-cli",
            "expr": module._build_overlay_expr("gemini-cli"),
            "env": updater._override_env(
                info.version,
                "sha256-src",
                updater.config.fake_hash,
            ),
            "config": updater.config,
        },
    ]
    assert events[-1].payload == [
        HashEntry.create("srcHash", "sha256-src"),
        HashEntry.create("npmDepsHash", "sha256-npm"),
    ]


def test_gemini_cli_updater_requires_src_hash(monkeypatch) -> None:
    """Raise when the source hash build emits no VALUE event."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/gemini-cli/updater.py",
        "gemini_cli_updater_test_missing_src",
    )
    updater = module.GeminiCliUpdater()

    async def _no_hash(_name: str, _expr: str, *, env=None, config=None):
        _ = (env, config)
        if False:
            yield module.UpdateEvent.status("gemini-cli", "never")

    monkeypatch.setattr(module, "compute_fixed_output_hash", _no_hash)

    with pytest.raises(RuntimeError, match="Missing srcHash output"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.2.3"), object())
            )
        )


def test_gemini_cli_updater_requires_npm_hash(monkeypatch) -> None:
    """Raise when the npm dependency hash build emits no VALUE event."""
    module = load_module_from_path(
        REPO_ROOT / "overlays/gemini-cli/updater.py",
        "gemini_cli_updater_test_missing_npm",
    )
    updater = module.GeminiCliUpdater()

    async def _fixed_hash(_name: str, _expr: str, *, env=None, config=None):
        _ = (env, config)
        if _expr == updater._src_expr("1.2.3"):
            yield module.UpdateEvent.value("gemini-cli", "sha256-src")
            return
        if False:
            yield module.UpdateEvent.status("gemini-cli", "never")

    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    with pytest.raises(RuntimeError, match="Missing npmDepsHash output"):
        _run(
            _collect_events(
                updater.fetch_hashes(VersionInfo(version="1.2.3"), object())
            )
        )
