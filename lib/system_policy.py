"""Shared target-system policy consumed by both Nix and Python."""

from functools import cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

_POLICY_PATH = Path(__file__).with_name("system-policy.json")

type SystemName = Annotated[
    str,
    Field(pattern=r"^(?:aarch64|x86_64)-(?:darwin|linux)$"),
]
type ElectronArtifact = Annotated[
    str,
    Field(pattern=r"^(?:darwin|linux)-(?:arm64|x64)$"),
]
type BunArtifact = Annotated[
    str,
    Field(pattern=r"^bun-(?:darwin|linux)-(?:aarch64|x64(?:-baseline)?)\.zip$"),
]
type RootClosureKind = Literal["darwin", "nixos", "home"]


class RootSystem(BaseModel):
    """Marker for one system whose configured roots are exported and validated."""

    model_config = ConfigDict(extra="forbid")


class SystemPolicy(BaseModel):
    """Versioned policy for configured roots and broader artifact support."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = Field(alias="schemaVersion")
    systems: dict[SystemName, RootSystem] = Field(min_length=1)
    required_root_kinds: tuple[RootClosureKind, ...] = Field(
        alias="requiredRootKinds",
        min_length=1,
    )
    electron_artifacts: dict[SystemName, ElectronArtifact] = Field(
        alias="electronArtifacts",
        min_length=1,
    )
    bun_artifacts: dict[SystemName, BunArtifact] = Field(
        alias="bunArtifacts",
        min_length=1,
    )


@cache
def system_policy() -> SystemPolicy:
    """Load and validate the checked-in target-system policy."""
    return SystemPolicy.model_validate_json(_POLICY_PATH.read_text(encoding="utf-8"))


def supported_systems() -> tuple[str, ...]:
    """Return the canonical exported systems in policy order."""
    return tuple(system_policy().systems)


def electron_artifact_tags() -> dict[str, str]:
    """Map every supported Electron system to its upstream artifact tag."""
    return dict(system_policy().electron_artifacts)


def bun_artifact_names() -> dict[str, str]:
    """Map supported Bun systems to their official release archive names."""
    return dict(system_policy().bun_artifacts)


def required_root_kinds() -> tuple[RootClosureKind, ...]:
    """Return root categories that this repository must always export."""
    return system_policy().required_root_kinds


__all__ = [
    "BunArtifact",
    "RootClosureKind",
    "RootSystem",
    "SystemPolicy",
    "bun_artifact_names",
    "electron_artifact_tags",
    "required_root_kinds",
    "supported_systems",
    "system_policy",
]
