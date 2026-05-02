"""Persistence helpers for update source and artifact results."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.update.artifacts import GeneratedArtifact, save_generated_artifacts
from lib.update.sources import save_sources

if TYPE_CHECKING:
    from collections.abc import Callable

    from lib.nix.models.sources import SourceEntry, SourcesFile
    from lib.update.ui_state import SummaryStatus


def merge_source_updates(
    existing_entries: dict[str, SourceEntry],
    source_updates: dict[str, SourceEntry],
    *,
    native_only: bool,
) -> dict[str, SourceEntry]:
    """Merge source updates into existing entries for native-only runs."""
    if not native_only:
        return source_updates
    return {
        name: existing_entries[name].merge(entry) if name in existing_entries else entry
        for name, entry in source_updates.items()
    }


def flatten_artifact_updates(
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]],
) -> list[GeneratedArtifact]:
    """Flatten per-source generated artifact updates into one list."""
    return [
        artifact
        for source in sorted(artifact_updates)
        for artifact in artifact_updates[source]
    ]


def persist_generated_artifacts(
    *,
    do_sources: bool,
    source_names: list[str],
    dry_run: bool,
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]],
    details: dict[str, SummaryStatus],
    save_artifacts: Callable[
        [list[GeneratedArtifact]], None
    ] = save_generated_artifacts,
) -> None:
    """Persist generated artifacts emitted by successful source updaters."""
    if not (do_sources and source_names):
        return
    if dry_run or not artifact_updates:
        return
    successful_updates = {
        source: artifacts
        for source, artifacts in artifact_updates.items()
        if details.get(source) == "updated"
    }
    if not successful_updates:
        return
    save_artifacts(flatten_artifact_updates(successful_updates))


def persist_source_updates(
    *,
    do_sources: bool,
    source_names: list[str],
    dry_run: bool,
    native_only: bool,
    sources: SourcesFile,
    source_updates: dict[str, SourceEntry],
    details: dict[str, SummaryStatus],
    save_source_file: Callable[[SourcesFile], None] = save_sources,
) -> None:
    """Persist per-package sources.json updates from one update run."""
    if not (do_sources and source_names):
        return

    if source_updates:
        merged_updates = merge_source_updates(
            sources.entries,
            source_updates,
            native_only=native_only,
        )
        sources.entries.update(merged_updates)

    if (
        not dry_run
        and source_updates
        and any(details.get(name) == "updated" for name in source_names)
    ):
        save_source_file(sources)


def persist_materialized_updates(
    *,
    do_sources: bool,
    source_names: list[str],
    dry_run: bool,
    native_only: bool,
    sources: SourcesFile,
    source_updates: dict[str, SourceEntry],
    artifact_updates: dict[str, tuple[GeneratedArtifact, ...]],
    details: dict[str, SummaryStatus],
    save_artifacts: Callable[
        [list[GeneratedArtifact]], None
    ] = save_generated_artifacts,
    save_source_file: Callable[[SourcesFile], None] = save_sources,
) -> None:
    """Persist generated artifacts first, then per-package sources."""
    persist_generated_artifacts(
        do_sources=do_sources,
        source_names=source_names,
        dry_run=dry_run,
        artifact_updates=artifact_updates,
        details=details,
        save_artifacts=save_artifacts,
    )
    persist_source_updates(
        do_sources=do_sources,
        source_names=source_names,
        dry_run=dry_run,
        native_only=native_only,
        sources=sources,
        source_updates=source_updates,
        details=details,
        save_source_file=save_source_file,
    )


__all__ = [
    "flatten_artifact_updates",
    "merge_source_updates",
    "persist_generated_artifacts",
    "persist_materialized_updates",
    "persist_source_updates",
]
