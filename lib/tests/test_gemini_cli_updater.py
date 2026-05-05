"""Tests for the gemini-cli updater overlay surface."""

from __future__ import annotations

import json

from lib.nix.models.sources import HashEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import install_fixed_hash_stream, load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.events import UpdateEventKind
from lib.update.nix import _build_fetch_from_github_call
from lib.update.updaters.base import VersionInfo, source_override_env


def test_gemini_cli_updater_fetch_latest_and_override_payload(monkeypatch) -> None:
    """Resolve the GitHub release version and build the override payload."""
    module = load_repo_module(
        "overlays/gemini-cli/updater.py", "gemini_cli_updater_test"
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
    assert_nix_ast_equal(
        updater._src_expr(latest.version),
        _build_fetch_from_github_call("google-gemini", "gemini-cli", tag="v1.2.3"),
    )

    env = source_override_env(
        "gemini-cli",
        version=latest.version,
        src_hash="sha256-src",
        dependency_hash_type="npmDepsHash",
        dependency_hash="sha256-fake",
    )
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
    module = load_repo_module(
        "overlays/gemini-cli/updater.py", "gemini_cli_updater_test_hashes"
    )
    updater = module.GeminiCliUpdater()
    info = VersionInfo(version="1.2.3")

    calls = install_fixed_hash_stream(
        monkeypatch,
        (("building src", "sha256-src"), ("building npm", "sha256-npm")),
    )

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
            "env": source_override_env(
                "gemini-cli",
                version=info.version,
                src_hash="sha256-src",
                dependency_hash_type="npmDepsHash",
                dependency_hash=updater.config.fake_hash,
            ),
            "config": updater.config,
        },
    ]
    assert events[-1].payload == [
        HashEntry.create("srcHash", "sha256-src"),
        HashEntry.create("npmDepsHash", "sha256-npm"),
    ]
