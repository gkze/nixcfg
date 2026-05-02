"""Tests for extracted update planning and persistence seams."""

from __future__ import annotations

from dataclasses import dataclass

from lib.nix.models.sources import SourceEntry, SourcesFile
from lib.update.artifacts import GeneratedArtifact
from lib.update.cli_options import UpdateOptions
from lib.update.persistence import (
    persist_generated_artifacts,
    persist_source_updates,
)
from lib.update.planner import resolve_update_targets
from lib.update.refs import FlakeInputRef

HASH_A = "sha256-1111111111111111111111111111111111111111111="
HASH_B = "sha256-2222222222222222222222222222222222222222222="


@dataclass(frozen=True)
class _ResolvedTargets:
    all_source_names: set[str]
    all_ref_inputs: list[FlakeInputRef]
    all_ref_names: set[str]
    all_known_names: set[str]
    do_refs: bool
    do_sources: bool
    do_input_refresh: bool
    dry_run: bool
    native_only: bool
    ref_inputs: list[FlakeInputRef]
    source_names: list[str]


class _RootUpdater:
    pass


class _ChildUpdater:
    companion_of = "root"


class _InputBackedUpdater:
    input_name = "shared-input"


def _source_backing_input_name(
    _name: str,
    updater_cls: type[object] | None,
    _entry: object | None,
) -> str | None:
    return getattr(updater_cls, "input_name", None)


def test_resolve_update_targets_expands_companions_and_backing_inputs() -> None:
    """Resolve target selections through the pure planner seam."""
    ref_input = FlakeInputRef(
        name="ref-only",
        owner="owner",
        repo="repo",
        ref="v1",
        input_type="github",
    )

    resolved = resolve_update_targets(
        UpdateOptions(targets=("root", "shared-input")),
        updaters={
            "root": _RootUpdater,
            "child": _ChildUpdater,
            "input-backed": _InputBackedUpdater,
        },
        ref_inputs=[ref_input],
        source_backing_input_name=_source_backing_input_name,
        result_type=_ResolvedTargets,
    )

    assert resolved.source_names == ["root", "input-backed", "child"]
    assert resolved.ref_inputs == []
    assert resolved.do_refs is False
    assert resolved.do_sources is True


def test_persist_source_updates_merges_native_hashes_and_saves() -> None:
    """Native-only source persistence should merge rather than replace hashes."""
    sources = SourcesFile(
        entries={
            "pkg": SourceEntry(hashes={"x86_64-linux": HASH_A}),
        }
    )
    saved: list[SourcesFile] = []

    persist_source_updates(
        do_sources=True,
        source_names=["pkg"],
        dry_run=False,
        native_only=True,
        sources=sources,
        source_updates={"pkg": SourceEntry(hashes={"aarch64-darwin": HASH_B})},
        details={"pkg": "updated"},
        save_source_file=saved.append,
    )

    assert saved == [sources]
    assert sources.entries["pkg"].hashes.mapping == {
        "aarch64-darwin": HASH_B,
        "x86_64-linux": HASH_A,
    }


def test_persist_generated_artifacts_only_writes_successful_updates() -> None:
    """Generated artifact persistence should ignore unchanged sources."""
    updated = GeneratedArtifact.text("packages/pkg/generated.txt", "new\n")
    unchanged = GeneratedArtifact.text("packages/other/generated.txt", "old\n")
    saved: list[GeneratedArtifact] = []

    persist_generated_artifacts(
        do_sources=True,
        source_names=["pkg", "other"],
        dry_run=False,
        artifact_updates={"pkg": (updated,), "other": (unchanged,)},
        details={"pkg": "updated", "other": "no_change"},
        save_artifacts=saved.extend,
    )

    assert saved == [updated]
