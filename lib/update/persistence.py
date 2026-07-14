"""Persistence helpers for update source and artifact results."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.nix.models.sources import SourcesFile
from lib.update import artifacts as update_artifacts
from lib.update import sources as update_sources

if TYPE_CHECKING:
    from lib.nix.models.sources import SourceEntry
    from lib.update.artifacts import GeneratedArtifact
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
    update_artifacts.save_generated_artifacts(
        flatten_artifact_updates(successful_updates)
    )


def persist_source_updates(
    *,
    do_sources: bool,
    source_names: list[str],
    dry_run: bool,
    native_only: bool,
    sources: SourcesFile,
    source_updates: dict[str, SourceEntry],
    details: dict[str, SummaryStatus],
) -> None:
    """Persist per-package sources.json updates from one update run."""
    if not (do_sources and source_names):
        return

    selected_names = set(source_names)
    successful_updates = {
        name: entry
        for name, entry in source_updates.items()
        if name in selected_names and details.get(name) == "updated"
    }
    if not successful_updates:
        return

    if native_only and not dry_run:
        merged_updates = update_sources.save_source_updates(
            successful_updates,
            merge_existing=True,
        )
    else:
        merged_updates = merge_source_updates(
            sources.entries,
            successful_updates,
            native_only=native_only,
        )

    if not dry_run and not native_only:
        update_sources.save_sources(SourcesFile(entries=merged_updates))
    sources.entries.update(merged_updates)


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
) -> None:
    """Persist generated artifacts first, then per-package sources."""
    persist_generated_artifacts(
        do_sources=do_sources,
        source_names=source_names,
        dry_run=dry_run,
        artifact_updates=artifact_updates,
        details=details,
    )
    persist_source_updates(
        do_sources=do_sources,
        source_names=source_names,
        dry_run=dry_run,
        native_only=native_only,
        sources=sources,
        source_updates=source_updates,
        details=details,
    )


__all__ = [
    "flatten_artifact_updates",
    "merge_source_updates",
    "persist_generated_artifacts",
    "persist_materialized_updates",
    "persist_source_updates",
]
