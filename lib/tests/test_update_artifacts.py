"""Tests for generated artifact persistence helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.tests._assertions import check
from lib.update.artifacts import (
    GeneratedArtifact,
    dedupe_generated_artifacts,
    save_generated_artifacts,
)


def test_generated_artifact_json_save_and_change_detection(tmp_path: Path) -> None:
    """Persist JSON artifacts atomically and detect subsequent drift."""
    artifact = GeneratedArtifact.json(
        "nested/demo.json",
        {"b": 2, "a": 1},
    )

    check(artifact.has_changed(repo_root=tmp_path))
    save_generated_artifacts([artifact], repo_root=tmp_path)

    written = tmp_path / "nested" / "demo.json"
    check(written.read_text(encoding="utf-8") == '{\n  "a": 1,\n  "b": 2\n}\n')
    check(not artifact.has_changed(repo_root=tmp_path))


def test_dedupe_generated_artifacts_rejects_conflicts(tmp_path: Path) -> None:
    """Conflicting content for the same artifact path should raise."""
    original = GeneratedArtifact.text("nested/demo.nix", "one\n")
    duplicate = GeneratedArtifact.text("nested/demo.nix", "one\n")
    conflict = GeneratedArtifact.text("nested/demo.nix", "two\n")

    deduped = dedupe_generated_artifacts(
        [original, duplicate],
        repo_root=tmp_path,
    )
    check(deduped == [original])

    with pytest.raises(RuntimeError, match="Conflicting generated artifact updates"):
        dedupe_generated_artifacts([original, conflict], repo_root=tmp_path)


def test_generated_artifact_rejects_paths_outside_repo_root(tmp_path: Path) -> None:
    """Artifact paths must stay within the repository root."""
    artifact = GeneratedArtifact.text("../outside.txt", "bad\n")

    with pytest.raises(RuntimeError, match="Artifact path escapes repository root"):
        artifact.resolved_path(repo_root=tmp_path)
