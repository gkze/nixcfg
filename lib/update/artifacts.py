"""Generated artifact models and persistence helpers for updater workflows."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from filelock import FileLock

from lib.update import io as update_io
from lib.update.paths import REPO_ROOT


def _resolve_repo_path(path: Path, *, repo_root: Path = REPO_ROOT) -> Path:
    """Resolve *path* under ``repo_root`` and ensure it stays inside the repo."""
    resolved_root = repo_root.resolve()
    candidate = path if path.is_absolute() else resolved_root / path
    resolved_path = candidate.resolve()
    try:
        resolved_path.relative_to(resolved_root)
    except ValueError as exc:
        msg = f"Artifact path escapes repository root: {path}"
        raise RuntimeError(msg) from exc
    return resolved_path


@dataclass(frozen=True)
class GeneratedArtifact:
    """A generated text artifact produced by an updater."""

    path: Path
    content: str

    @classmethod
    def text(cls, path: str | Path, content: str) -> GeneratedArtifact:
        """Build a text artifact from raw string content."""
        return cls(path=Path(path), content=content)

    @classmethod
    def json(
        cls,
        path: str | Path,
        payload: object,
        *,
        indent: int = 2,
        sort_keys: bool = True,
    ) -> GeneratedArtifact:
        """Build a text artifact from JSON payload with stable formatting."""
        content = json.dumps(payload, indent=indent, sort_keys=sort_keys) + "\n"
        return cls(path=Path(path), content=content)

    def resolved_path(self, *, repo_root: Path = REPO_ROOT) -> Path:
        """Return this artifact's absolute repository path."""
        return _resolve_repo_path(self.path, repo_root=repo_root)

    def repo_relative_path(self, *, repo_root: Path = REPO_ROOT) -> Path:
        """Return this artifact path relative to the repository root."""
        resolved_root = repo_root.resolve()
        return self.resolved_path(repo_root=resolved_root).relative_to(resolved_root)

    def has_changed(self, *, repo_root: Path = REPO_ROOT) -> bool:
        """Return whether the artifact content differs from the current file."""
        path = self.resolved_path(repo_root=repo_root)
        if not path.exists():
            return True
        return path.read_text(encoding="utf-8") != self.content

    def write(self, *, repo_root: Path = REPO_ROOT) -> None:
        """Persist this artifact atomically under the repository root."""
        path = self.resolved_path(repo_root=repo_root)
        lock_path = path.with_name(f"{path.name}.lock")
        with FileLock(lock_path):
            update_io.atomic_write_text(path, self.content, mkdir=True)


def dedupe_generated_artifacts(
    artifacts: list[GeneratedArtifact],
    *,
    repo_root: Path = REPO_ROOT,
) -> list[GeneratedArtifact]:
    """Return artifacts deduplicated by repository path with conflict checks."""
    by_path: dict[Path, GeneratedArtifact] = {}
    for artifact in artifacts:
        resolved_path = artifact.resolved_path(repo_root=repo_root)
        existing = by_path.get(resolved_path)
        if existing is not None and existing.content != artifact.content:
            rel_path = artifact.repo_relative_path(repo_root=repo_root)
            msg = f"Conflicting generated artifact updates for {rel_path}"
            raise RuntimeError(msg)
        by_path[resolved_path] = artifact
    return list(by_path.values())


def save_generated_artifacts(
    artifacts: list[GeneratedArtifact],
    *,
    repo_root: Path = REPO_ROOT,
) -> None:
    """Persist generated artifacts atomically after deduplicating by path."""
    for artifact in dedupe_generated_artifacts(artifacts, repo_root=repo_root):
        artifact.write(repo_root=repo_root)


__all__ = [
    "GeneratedArtifact",
    "dedupe_generated_artifacts",
    "save_generated_artifacts",
]
