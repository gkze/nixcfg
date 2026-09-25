"""Portable update candidates shared by native builders and repair attempts.

Git owns file identity and patch application. A candidate carries selected release
metadata, never a Python execution checkpoint. Preparation cannot promote files.
"""

import subprocess
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter

from lib.update.updaters.metadata import MappingMetadata, VersionInfo

if TYPE_CHECKING:
    from pathlib import Path

    from lib.update.persistence import IsolatedUpdateWorkspace

type GitTree = Annotated[str, Field(pattern=r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")]


def git(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    """Run Git without a shell, preserving binary patches and failure evidence."""
    return subprocess.run(  # noqa: S603 -- argv is owned by this module; patch is stdin
        ["git", "-C", str(root), *args],  # noqa: S607 -- Git comes from the pinned runtime
        input=input_bytes,
        capture_output=True,
        check=True,
    ).stdout


def _metadata_types() -> dict[str, type[MappingMetadata]]:
    # Only classes already registered by trusted updater code can be hydrated.
    # Artifact contents can never request an import or execute a pickle.
    return {
        f"{cls.__module__}.{cls.__qualname__}": cls
        for cls in MappingMetadata.__subclasses__()
    }


class ResolvedVersion(BaseModel):
    """A JSON representation of an upstream resolution, including typed metadata."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    metadata_type: str | None = None
    metadata: JsonValue = None

    @classmethod
    def capture(cls, info: VersionInfo) -> ResolvedVersion:
        """Serialize supported metadata without erasing custom dataclass fields."""
        metadata_type = None
        metadata = info.metadata
        if isinstance(metadata, MappingMetadata):
            metadata_type = f"{type(metadata).__module__}.{type(metadata).__qualname__}"
            metadata = TypeAdapter(type(metadata)).dump_python(metadata, mode="json")
        return cls(
            version=info.version,
            metadata_type=metadata_type,
            metadata=TypeAdapter(JsonValue).validate_python(metadata),
        )

    def restore(self) -> VersionInfo:
        """Restore only the metadata types supplied by the loaded updater runtime."""
        metadata: object = self.metadata
        if self.metadata_type is not None:
            cls = _metadata_types().get(self.metadata_type)
            if cls is None:
                msg = f"Unknown updater metadata type: {self.metadata_type}"
                raise ValueError(msg)
            metadata = TypeAdapter(cls).validate_python(metadata)
        return VersionInfo(version=self.version, metadata=metadata)


class Candidate(BaseModel):
    """One exact proposed tree; success means prepared, never validated."""

    model_config = ConfigDict(
        extra="forbid", frozen=True, ser_json_bytes="base64", val_json_bytes="base64"
    )

    schema_version: Literal[1] = 1
    base_tree: GitTree
    tree: GitTree
    targets: tuple[str, ...]
    sources: tuple[str, ...]
    systems: tuple[str, ...]
    resolutions: dict[str, ResolvedVersion]
    prepared: bool
    patch: bytes

    def apply(self, root: Path) -> None:
        """Reconstruct this candidate in a clean, isolated baseline checkout."""
        if git(root, "write-tree").decode().strip() != self.base_tree:
            msg = "Candidate baseline does not match the checkout"
            raise ValueError(msg)
        if self.patch:
            git(root, "apply", "--index", "--binary", "-", input_bytes=self.patch)
        if git(root, "write-tree").decode().strip() != self.tree:
            msg = "Candidate patch does not produce its recorded tree"
            raise ValueError(msg)


@dataclass
class Preparation:
    """One native preparation lifetime, explicitly separate from live promotion."""

    system: str
    targets: tuple[str, ...]
    previous: Candidate | None = None
    resolutions: dict[str, ResolvedVersion] = field(default_factory=dict)
    dependent_sources: set[str] = field(default_factory=set)
    candidate: Candidate | None = None

    def __post_init__(self) -> None:
        """Require each native stage to extend the same successful request."""
        if self.previous is not None:
            if not self.previous.prepared:
                msg = "A failed preparation requires repair before another platform"
                raise ValueError(msg)
            if self.system in self.previous.systems:
                msg = f"Candidate already prepared on {self.system}"
                raise ValueError(msg)
            if self.targets != self.previous.targets:
                msg = "Candidate target selection cannot change between platforms"
                raise ValueError(msg)
            self.resolutions.update(self.previous.resolutions)

    def resolved(self, name: str) -> VersionInfo | None:
        """Dependent metadata is recomputed from pinned prerequisite outputs."""
        if name in self.dependent_sources:
            return None
        resolution = self.resolutions.get(name)
        return None if resolution is None else resolution.restore()

    def record(self, name: str, info: VersionInfo | None) -> None:
        """Keep discoveries even when subsequent materialization fails."""
        if info is not None and name not in self.dependent_sources:
            self.resolutions[name] = ResolvedVersion.capture(info)

    def capture(
        self,
        workspace: IsolatedUpdateWorkspace,
        *,
        sources: tuple[str, ...],
        allowed_paths: tuple[Path, ...],
        succeeded: bool,
    ) -> None:
        """Export both successful and failed candidates before scratch cleanup."""
        patch = workspace.patch(allowed_paths)
        self.candidate = Candidate(
            base_tree=git(workspace.root, "rev-parse", "HEAD^{tree}").decode().strip(),
            tree=git(workspace.root, "write-tree").decode().strip(),
            targets=self.targets,
            sources=sources,
            systems=(
                *(() if self.previous is None else self.previous.systems),
                *((self.system,) if succeeded else ()),
            ),
            resolutions=self.resolutions,
            prepared=succeeded,
            patch=patch,
        )
