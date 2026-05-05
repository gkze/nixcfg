"""Tests for the GitButler updater."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from lib.nix.models.flake_lock import FlakeLockNode
from lib.nix.models.sources import HashEntry, SourceEntry
from lib.tests._nix_ast import assert_nix_ast_equal
from lib.tests._updater_helpers import collect_events as _collect
from lib.tests._updater_helpers import empty_event_stream, load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.flake import flake_fetch_expression
from lib.update.nix import _build_fetch_pnpm_deps_expr
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.metadata import FlakeInputMetadata

HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="


def _load_module(module_name: str):
    return load_repo_module("packages/gitbutler/updater.py", module_name)


def _flake_node(
    *,
    ref: str | None = "release/0.19.9",
    rev: str | None = "a" * 40,
) -> FlakeLockNode:
    payload: dict[str, object] = {}
    if ref is not None:
        payload["original"] = {
            "type": "github",
            "owner": "gitbutlerapp",
            "repo": "gitbutler",
            "ref": ref,
        }
    if rev is not None:
        payload["locked"] = {
            "type": "github",
            "owner": "gitbutlerapp",
            "repo": "gitbutler",
            "rev": rev,
            "narHash": HASH,
        }
    return FlakeLockNode.model_validate(payload)


def test_gitbutler_updater_tracks_release_ref_and_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The package version should come from the locked release ref."""
    module = _load_module("gitbutler_updater_latest_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev="b" * 40)

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    info = _run(updater.fetch_latest(object()))

    assert module.GitButlerUpdater.hash_type == "npmDepsHash"
    assert module.GitButlerUpdater.input_name == "gitbutler"
    assert module.GitButlerUpdater.supported_platforms == (
        "aarch64-darwin",
        "x86_64-linux",
    )
    assert info.version == "0.19.9"
    assert info.commit == "b" * 40
    assert info.metadata == FlakeInputMetadata(node=node, commit="b" * 40)


def test_gitbutler_updater_allows_missing_locked_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Version parsing should not require locked metadata beyond the ref."""
    module = _load_module("gitbutler_updater_no_locked_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev=None)

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    info = _run(updater.fetch_latest(object()))

    assert info.version == "0.19.9"
    assert info.commit is None
    assert info.metadata == FlakeInputMetadata(node=node, commit=None)


@pytest.mark.parametrize(
    "node",
    [
        _flake_node(ref="main"),
        _flake_node(ref=None),
    ],
)
def test_gitbutler_updater_requires_release_ref(
    monkeypatch: pytest.MonkeyPatch,
    node: FlakeLockNode,
) -> None:
    """Unexpected flake input refs should fail before hashing."""
    module = _load_module("gitbutler_updater_bad_ref_test")
    updater = module.GitButlerUpdater()

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    with pytest.raises(RuntimeError, match="must be pinned to a release"):
        _run(updater.fetch_latest(object()))


def test_gitbutler_updater_builds_pnpm_hash_expression(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hash probes should target the locked GitButler pnpm dependency cache."""
    module = _load_module("gitbutler_updater_hash_expr_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev="c" * 40)
    captured: dict[str, object] = {}

    async def _fixed_hash(
        name: str,
        expr: str,
        *,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        captured.update({"name": name, "expr": expr, "config": config})
        yield UpdateEvent.status(name, "hashing pnpm deps")
        yield UpdateEvent.value(name, HASH)

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(
        _collect(
            updater._compute_hash_for_system(
                VersionInfo(version="0.19.9"),
                system="aarch64-darwin",
            )
        )
    )

    assert events == [
        UpdateEvent.status("gitbutler", "hashing pnpm deps"),
        UpdateEvent.value("gitbutler", HASH),
    ]
    assert captured["name"] == "gitbutler"
    assert captured["config"] is updater.config
    assert_nix_ast_equal(
        str(captured["expr"]),
        _build_fetch_pnpm_deps_expr(
            flake_fetch_expression(node),
            pname="gitbutler",
            version="0.19.9",
            fetcher_version=3,
        ),
    )


def test_gitbutler_updater_streams_artifacts_before_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Checked-in crate2nix artifacts should refresh before the pnpm hash."""
    module = _load_module("gitbutler_updater_artifact_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev="d" * 40)

    async def _artifacts() -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.status("gitbutler", "materialized cargo artifacts")

    async def _fixed_hash(
        name: str,
        _expr: str,
        *,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        _ = config
        yield UpdateEvent.status(name, "hashing pnpm deps")
        yield UpdateEvent.value(name, HASH)

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)
    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)

    events = _run(
        _collect(updater.fetch_hashes(VersionInfo(version="0.19.9"), object()))
    )

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    assert events[0].message == "materialized cargo artifacts"
    assert events[1].message == "hashing pnpm deps"
    assert events[2].payload == [HashEntry.create("npmDepsHash", HASH)]


def test_gitbutler_update_keeps_drv_hash_when_artifacts_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Generated artifact changes should not get a pre-persistence drvHash."""
    module = _load_module("gitbutler_updater_artifact_drv_hash_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev="f" * 40)
    old_hash = "sha256-BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB="
    current = SourceEntry.model_validate({
        "version": "0.19.9",
        "drvHash": "old-drv",
        "hashes": [
            {
                "hashType": "npmDepsHash",
                "hash": old_hash,
            }
        ],
        "input": "gitbutler",
    })

    async def _artifacts() -> AsyncIterator[UpdateEvent]:
        yield UpdateEvent.artifact(
            "gitbutler",
            GeneratedArtifact.text("packages/gitbutler/Cargo.nix", "updated"),
        )

    async def _fixed_hash(
        name: str,
        _expr: str,
        *,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        _ = config
        yield UpdateEvent.value(name, HASH)

    fingerprint_calls = 0

    async def _compute_drv_fingerprint(*_args: object, **_kwargs: object) -> str:
        nonlocal fingerprint_calls
        fingerprint_calls += 1
        return "pre-artifact-drv"

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    monkeypatch.setattr(updater, "stream_materialized_artifacts", _artifacts)
    monkeypatch.setattr(module, "compute_fixed_output_hash", _fixed_hash)
    monkeypatch.setattr(
        "lib.update.updaters.base.compute_drv_fingerprint",
        _compute_drv_fingerprint,
    )

    events = _run(_collect(updater.update_stream(current, object())))

    result_events = [event for event in events if event.kind == UpdateEventKind.RESULT]
    assert len(result_events) == 1
    result = result_events[0].payload
    assert isinstance(result, SourceEntry)
    assert result.drv_hash == "old-drv"
    assert result.hashes.entries == [HashEntry.create("npmDepsHash", HASH)]
    assert fingerprint_calls == 1


def test_gitbutler_updater_requires_npm_deps_hash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty fixed-output stream should fail instead of writing no hash."""
    module = _load_module("gitbutler_updater_missing_hash_test")
    updater = module.GitButlerUpdater()
    node = _flake_node(ref="release/0.19.9", rev="e" * 40)

    async def _missing_hash(
        _name: str,
        _expr: str,
        *,
        config: object | None = None,
    ) -> AsyncIterator[UpdateEvent]:
        _ = config
        async for event in empty_event_stream():
            yield event

    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    monkeypatch.setattr(updater, "stream_materialized_artifacts", empty_event_stream)
    monkeypatch.setattr(module, "compute_fixed_output_hash", _missing_hash)

    with pytest.raises(RuntimeError, match="Missing npmDepsHash output"):
        _run(_collect(updater.fetch_hashes(VersionInfo(version="0.19.9"), object())))
