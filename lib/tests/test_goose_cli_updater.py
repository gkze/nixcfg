"""Tests for the goose-cli updater."""

from __future__ import annotations

import pytest

from lib.nix.models.sources import HashEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.nix import _build_fetch_from_github_call
from lib.update.updaters.base import VersionInfo


def _load_module(module_name: str):
    return load_repo_module("overlays/goose-cli/updater.py", module_name)


def test_goose_cli_updater_builds_github_tagged_src_expr(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The source hash should come from the GitHub release tag for the chosen version."""
    module = _load_module("goose_cli_updater_hash_test")
    updater = module.GooseCliUpdater()
    calls: list[str] = []

    async def _artifacts():
        if False:
            yield None

    async def _fixed_hash(_name: str, expr: str, **_kwargs):
        calls.append(expr)
        yield module.UpdateEvent.value(
            "goose-cli",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)
    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(_collect(updater.fetch_hashes(VersionInfo("1.2.3", {}), object())))

    assert_nix_ast_equal(
        calls[0],
        _build_fetch_from_github_call("block", "goose", tag="v1.2.3"),
    )
    assert events[-1].payload == [
        HashEntry.create(
            "srcHash",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )
    ]


def test_goose_cli_updater_forwards_fixed_hash_progress_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-value events from the source hash stream should pass through."""
    module = _load_module("goose_cli_updater_progress_test")
    updater = module.GooseCliUpdater()

    async def _artifacts():
        if False:
            yield None

    async def _fixed_hash(_name: str, _expr: str, **_kwargs):
        yield module.UpdateEvent.status("goose-cli", "hashing source")
        yield module.UpdateEvent.value(
            "goose-cli",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)
    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(_collect(updater.fetch_hashes(VersionInfo("1.2.3", {}), object())))

    assert [event.kind.value for event in events] == ["status", "value"]
    assert events[0].message == "hashing source"


def test_goose_cli_updater_forwards_materialized_artifact_events(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Artifact events emitted before hashing should be preserved."""
    module = _load_module("goose_cli_updater_artifact_test")
    updater = module.GooseCliUpdater()

    async def _artifacts():
        yield module.UpdateEvent.status("goose-cli", "materialized cargo artifacts")

    async def _fixed_hash(_name: str, _expr: str, **_kwargs):
        yield module.UpdateEvent.value(
            "goose-cli",
            "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        )

    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)
    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(_collect(updater.fetch_hashes(VersionInfo("1.2.3", {}), object())))

    assert [event.kind.value for event in events] == ["status", "value"]
    assert events[0].message == "materialized cargo artifacts"


def test_goose_cli_updater_requires_src_hash_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty fixed-output stream should fail instead of silently succeeding."""
    module = _load_module("goose_cli_updater_missing_hash_test")
    updater = module.GooseCliUpdater()

    async def _artifacts():
        if False:
            yield None

    async def _missing_hash(_name: str, _expr: str, **_kwargs):
        if False:
            yield None

    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)
    monkeypatch.setattr(module, "compute_fixed_output_hash", _missing_hash)

    with pytest.raises(RuntimeError, match="Missing srcHash output"):
        _run(_collect(updater.fetch_hashes(VersionInfo("1.2.3", {}), object())))
