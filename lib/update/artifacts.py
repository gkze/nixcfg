"""Generated artifact models and persistence helpers for updater workflows."""

import json
import tempfile
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path

from filelock import FileLock

from lib.update import io as update_io
from lib.update.paths import REPO_ROOT


def artifact_lock_path(path: Path) -> Path:
    """Return a stable cross-process lock path outside the repository tree."""
    lock_root = Path(tempfile.gettempdir()) / "nixcfg-generated-artifact-locks"
    lock_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    path_digest = sha256(str(path).encode()).hexdigest()
    return lock_root / f"{path_digest}.lock"


def resolve_repo_path(path: Path, *, repo_root: Path = REPO_ROOT) -> Path:
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
    """A generated text artifact and its optional producer-observed change state."""

    path: Path
    content: str
    changed_from_snapshot: bool | None = field(
        default=None,
        compare=False,
        repr=False,
    )

    @classmethod
    def text(
        cls,
        path: str | Path,
        content: str,
        *,
        changed_from_snapshot: bool | None = None,
    ) -> GeneratedArtifact:
        """Build a text artifact from raw string content."""
        return cls(
            path=Path(path),
            content=content,
            changed_from_snapshot=changed_from_snapshot,
        )

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
        return resolve_repo_path(self.path, repo_root=repo_root)

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
        lock_path = artifact_lock_path(path)
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
    "artifact_lock_path",
    "dedupe_generated_artifacts",
    "resolve_repo_path",
    "save_generated_artifacts",
]
