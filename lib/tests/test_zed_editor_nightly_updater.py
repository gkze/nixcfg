"""Tests for the Zed nightly updater."""

from __future__ import annotations

import asyncio

import pytest

from lib.nix.models.sources import SourceEntry
from lib.tests._updater_helpers import collect_events as _collect_events
from lib.tests._updater_helpers import load_repo_module
from lib.tests._updater_helpers import run_async as _run
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import UpdateEvent, UpdateEventKind, expect_artifact_updates
from lib.update.updaters.base import VersionInfo
from lib.update.updaters.metadata import FlakeInputMetadata


def _load_module(module_name: str):
    return load_repo_module("packages/zed-editor-nightly/updater.py", module_name)


def test_zed_editor_nightly_updater_tracks_manifest_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve the app version from the locked upstream Cargo manifest."""
    module = _load_module("zed_editor_nightly_updater_test")
    updater = module.ZedEditorNightlyUpdater()

    node = type(
        "Node",
        (),
        {
            "locked": type(
                "Locked",
                (),
                {
                    "owner": "zed-industries",
                    "repo": "zed",
                    "rev": "a" * 40,
                },
            )(),
        },
    )()
    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    monkeypatch.setattr(
        module,
        "fetch_url",
        lambda *_args, **_kwargs: asyncio.sleep(
            0,
            result=b'[package]\nversion = "0.999.0"\n',
        ),
    )

    async def _empty_stream(_name: str):
        if False:
            yield None

    monkeypatch.setattr(
        module.ZedEditorNightlyUpdater,
        "stream_materialized_artifacts",
        lambda _self: _empty_stream("zed-editor-nightly"),
    )

    info = _run(updater.fetch_latest(object()))
    assert info.version == "0.999.0"
    assert info.commit == "a" * 40
    assert info.metadata == FlakeInputMetadata(node=node, commit="a" * 40)

    events = _run(_collect_events(updater.fetch_hashes(info, object())))
    assert len(events) == 1
    assert events[0].payload == []

    result = updater.build_result(info, [])
    assert result == SourceEntry(
        version="0.999.0",
        hashes=[],
        input="zed",
        commit="a" * 40,
    )


def test_zed_editor_nightly_updater_rejects_missing_manifest_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail cleanly when the upstream manifest shape changes."""
    module = _load_module("zed_editor_nightly_updater_missing_version_test")
    updater = module.ZedEditorNightlyUpdater()

    node = type(
        "Node",
        (),
        {
            "locked": type(
                "Locked",
                (),
                {
                    "owner": "zed-industries",
                    "repo": "zed",
                    "rev": "b" * 40,
                },
            )(),
        },
    )()
    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)
    monkeypatch.setattr(
        module,
        "fetch_url",
        lambda *_args, **_kwargs: asyncio.sleep(0, result=b'[package]\nname = "zed"\n'),
    )

    with pytest.raises(RuntimeError, match="package.version"):
        _run(updater.fetch_latest(object()))


def test_zed_editor_nightly_updater_rejects_missing_locked_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail before fetching when the flake lock lacks owner/repo/rev fields."""
    module = _load_module("zed_editor_nightly_updater_missing_locked_metadata_test")
    updater = module.ZedEditorNightlyUpdater()

    node = type(
        "Node",
        (),
        {
            "locked": type(
                "Locked",
                (),
                {
                    "owner": "zed-industries",
                    "repo": "zed",
                    "rev": "",
                },
            )(),
        },
    )()
    monkeypatch.setattr(updater, "_resolve_flake_node", lambda _info: node)

    with pytest.raises(RuntimeError, match="missing owner/repo/rev metadata"):
        _run(updater.fetch_latest(object()))


def test_zed_editor_nightly_updater_refreshes_crate2nix_artifacts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit checked-in crate2nix artifacts during the hash/materialization phase."""
    module = _load_module("zed_editor_nightly_updater_crate2nix_test")
    updater = module.ZedEditorNightlyUpdater()
    assert updater.materialize_when_current is True
    assert updater.shows_materialize_artifacts_phase is True

    async def _fake_stream(name: str):
        yield UpdateEvent.status(
            name,
            "Refreshing crate2nix artifacts...",
            operation="materialize_artifacts",
            status="computing_hash",
            detail="crate2nix artifacts",
        )
        yield UpdateEvent.artifact(
            name,
            GeneratedArtifact.text(
                "packages/zed-editor-nightly/Cargo.nix",
                "{ zed = true; }\n",
            ),
        )
        yield UpdateEvent.status(
            name,
            "Prepared crate2nix artifacts",
            operation="materialize_artifacts",
            status="updated",
            detail="crate2nix artifacts",
        )

    monkeypatch.setattr(
        module.ZedEditorNightlyUpdater,
        "stream_materialized_artifacts",
        lambda _self: _fake_stream("zed-editor-nightly"),
    )

    info = VersionInfo(
        version="0.999.0",
        metadata=FlakeInputMetadata(node=object(), commit="c" * 40),
    )
    events = _run(_collect_events(updater.fetch_hashes(info, object())))

    assert [event.kind for event in events] == [
        UpdateEventKind.STATUS,
        UpdateEventKind.ARTIFACT,
        UpdateEventKind.STATUS,
        UpdateEventKind.VALUE,
    ]
    artifact_paths = tuple(
        str(artifact.path) for artifact in expect_artifact_updates(events[1].payload)
    )
    assert artifact_paths == ("packages/zed-editor-nightly/Cargo.nix",)
    assert events[-1].payload == []
