"""Declarative config models for JSON Schema code generation."""

from __future__ import annotations

import pathlib  # noqa: TC003
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SchemaFormat(StrEnum):
    """Supported on-disk or remote schema serialization formats."""

    JSON = "json"
    YAML = "yaml"


class SourceKind(StrEnum):
    """Supported schema source kinds."""

    DIRECTORY = "directory"
    URL = "url"


class ResourceMode(StrEnum):
    """How source documents become referencing resources."""

    FROM_CONTENTS = "from-contents"


class AliasStrategy(StrEnum):
    """Supported URI aliases for loaded schema resources."""

    BASENAME = "basename"
    INTERNAL_ID = "internal-id"
    RELATIVE_PATH = "relative-path"
    SOURCE_URI = "source-uri"


class RetrieveKind(StrEnum):
    """Supported registry retrieval backends."""

    NONE = "none"


class DereferenceMode(StrEnum):
    """Supported schema preparation ref-resolution modes."""

    INLINE_REFS = "inline-refs"
    NONE = "none"


class SchemaTransform(StrEnum):
    """Schema-shape transforms applied before code generation."""

    CONST_NULL_TO_TYPE_NULL = "const-null-to-type-null"
    DROP_DESCRIPTION = "drop-description"
    INLINE_MERGEABLE_ALLOF = "inline-mergeable-allof"


class PythonTransform(StrEnum):
    """Post-generation Python transforms applied to rendered models."""

    NORMALIZE_PYDANTIC_IMPORTS = "normalize-pydantic-imports"
    REWRITE_CONSTR_ANNOTATIONS = "rewrite-constr-annotations"


class GeneratorOptions(BaseModel):
    """Declarative datamodel-code-generator options.

    We intentionally only type ``output`` here and allow arbitrary additional
    fields so the checked-in config can track upstream ``GenerateConfig``
    without us manually mirroring every option in this repo.
    """

    model_config = ConfigDict(extra="allow")

    output: pathlib.Path | None = None

    def merged_with(self, override: GeneratorOptions) -> GeneratorOptions:
        """Return ``self`` with any non-null values from ``override`` applied."""
        return type(self).model_validate({
            **self.model_dump(exclude_none=True),
            **override.model_dump(exclude_none=True),
        })


class CodegenDefaults(BaseModel):
    """Default generator options inherited by individual targets."""

    model_config = ConfigDict(extra="forbid")

    generator: GeneratorOptions = Field(default_factory=GeneratorOptions)


class DirectorySource(BaseModel):
    """A checked-in directory containing JSON or YAML schemas."""

    model_config = ConfigDict(extra="forbid")

    kind: SourceKind = SourceKind.DIRECTORY
    format: SchemaFormat
    include: tuple[str, ...] = ("*.json",)
    path: pathlib.Path


class URLSource(BaseModel):
    """A single schema fetched from a remote URL."""

    model_config = ConfigDict(extra="forbid")

    format: SchemaFormat
    kind: SourceKind = SourceKind.URL
    uri: str


SchemaSource = DirectorySource | URLSource


class ResourceConfig(BaseModel):
    """How to create referencing resources from source documents."""

    model_config = ConfigDict(extra="forbid")

    default_specification: str | None = None
    mode: ResourceMode = ResourceMode.FROM_CONTENTS


class RetrieveConfig(BaseModel):
    """Registry retrieval policy for unresolved remote references."""

    model_config = ConfigDict(extra="forbid")

    kind: RetrieveKind = RetrieveKind.NONE


class RegistryConfig(BaseModel):
    """How loaded resources are assembled into one referencing registry."""

    model_config = ConfigDict(extra="forbid")

    crawl: bool = True
    retrieve: RetrieveConfig = Field(default_factory=RetrieveConfig)


class RegistryProfile(BaseModel):
    """Reusable registry-construction policy shared by targets."""

    model_config = ConfigDict(extra="forbid")

    aliases: tuple[AliasStrategy, ...] = ()
    registry: RegistryConfig = Field(default_factory=RegistryConfig)
    resource: ResourceConfig = Field(default_factory=ResourceConfig)


class PrepareConfig(BaseModel):
    """Schema preparation steps that run before model generation."""

    model_config = ConfigDict(extra="forbid")

    dereference: DereferenceMode = DereferenceMode.NONE
    merge_ref_siblings: bool = False
    python_transforms: tuple[PythonTransform, ...] = ()
    schema_transforms: tuple[SchemaTransform, ...] = ()


class CodegenTarget(BaseModel):
    """One named schema-to-model generation target."""

    model_config = ConfigDict(extra="forbid")

    entrypoints: tuple[str, ...]
    generator: GeneratorOptions
    prepare: PrepareConfig = Field(default_factory=PrepareConfig)
    registry_profile: str
    sources: tuple[str, ...]


class SchemaCodegenConfig(BaseModel):
    """Top-level config document for schema code generation targets."""

    model_config = ConfigDict(extra="forbid")

    defaults: CodegenDefaults = Field(default_factory=CodegenDefaults)
    registry_profiles: dict[str, RegistryProfile]
    sources: dict[str, SchemaSource]
    targets: dict[str, CodegenTarget]


@dataclass(frozen=True)
class LoadedSchemaCodegenConfig:
    """Config plus its source path after path resolution."""

    config: SchemaCodegenConfig
    path: pathlib.Path
