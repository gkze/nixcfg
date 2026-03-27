"""Auto-generated Pydantic models from JSON schemas.

DO NOT EDIT MANUALLY. Regenerate with:
    nixcfg schema generate codegen-manifest-models
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    RootModel,
    constr,
)

# === codegen.schema ===


class Fetch(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    cache_dir: Annotated[str | None, Field(min_length=1, title="PathString")] = None
    """
    Filesystem path expressed with POSIX separators.
    """


class Defaults(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    fetch: Annotated[Fetch | None, Field(title="FetchDefaults")] = None
    generator: Annotated[dict[str, Any] | None, Field(title="GeneratorDefaults")] = None
    """
    Tool-specific default generator options merged into individual generator profiles.
    """


class IncludeItem(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title="NonEmptyString")]


class Format(StrEnum):
    JSON = "json"
    YAML = "yaml"


class Sources(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["directory"]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    Filesystem path expressed with POSIX separators.
    """
    include: Annotated[
        list[IncludeItem] | None, Field(min_length=1, title="IncludePatterns")
    ] = None
    format: Annotated[Format, Field(title="SchemaFormat")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Sources1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["url"]
    uri: Annotated[AnyUrl, Field(title="HttpsUrl")]
    """
    Absolute HTTPS URL.
    """
    format: Annotated[Format, Field(title="SchemaFormat")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Metadata(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    tag: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    package: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    package_version: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None


class Sources2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["github-raw"]
    owner: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """
    repo: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    ref: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    Filesystem path expressed with POSIX separators.
    """
    format: Annotated[Format, Field(title="SchemaFormat")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )
    metadata: Annotated[Metadata | None, Field(title="GitHubRawSourceMetadata")] = None


class Alias(StrEnum):
    BASENAME = "basename"
    INTERNAL_ID = "internal-id"
    RELATIVE_PATH = "relative-path"
    SOURCE_URI = "source-uri"


class Mode(StrEnum):
    FROM_CONTENTS = "from-contents"


class Resource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    mode: Mode
    default_specification: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None


class Kind(StrEnum):
    NONE = "none"


class Retrieve(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Kind


class Registry(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    crawl: bool | None = None
    retrieve: Annotated[Retrieve | None, Field(title="RetrieveConfig")] = None


class RegistryProfiles(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    aliases: Annotated[list[Alias] | None, Field(title="AliasStrategies")] = None
    resource: Annotated[Resource, Field(title="ResourceConfig")]
    registry: Annotated[Registry | None, Field(title="RegistryConfig")] = None


class Source(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """


class Entrypoint(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title="NonEmptyString")]


class Dereference(StrEnum):
    INLINE_REFS = "inline-refs"
    NONE = "none"


class SchemaTransform(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title="NonEmptyString")]


class Prepare(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    dereference: Dereference | None = None
    merge_ref_siblings: bool | None = None
    schema_transforms: Annotated[
        list[SchemaTransform] | None, Field(title="SchemaTransforms")
    ] = None


class Inputs(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["jsonschema"]
    sources: Annotated[list[Source], Field(min_length=1, title="SourceRefList")]
    registry_profile: Annotated[
        str | None, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")
    ] = None
    """
    Stable manifest identifier used as a map key.
    """
    entrypoints: Annotated[
        list[Entrypoint], Field(min_length=1, title="EntrypointList")
    ]
    prepare: Annotated[Prepare | None, Field(title="PrepareConfig")] = None
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Prepare1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    dereference: Dereference | None = None
    merge_ref_siblings: bool | None = None
    schema_transforms: Annotated[
        list[SchemaTransform] | None, Field(title="SchemaTransforms")
    ] = None


class Inputs1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["workflow-template-schema"]
    sources: Annotated[list[Source], Field(min_length=1, title="SourceRefList")]
    root: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    prepare: Annotated[Prepare1 | None, Field(title="PrepareConfig")] = None
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Generators(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    language: Annotated[str, Field(min_length=1, title="LanguageName")]
    """
    Target language identifier such as python or go.
    """
    tool: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    options: Annotated[dict[str, Any] | None, Field(title="GeneratorOptions")] = None
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Input(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """


class Generator(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """


class Products(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    inputs: Annotated[list[Input], Field(min_length=1, title="InputRefList")]
    generators: Annotated[
        list[Generator], Field(min_length=1, title="GeneratorRefList")
    ]
    output_template: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class CodegenManifest(BaseModel):
    """Language-agnostic manifest for pinned sources, prepared inputs, and generator products."""

    model_config = ConfigDict(
        extra="forbid",
    )
    version: Literal[1]
    """
    Manifest format version.
    """
    defaults: Annotated[Defaults | None, Field(title="Defaults")] = None
    sources: Annotated[
        dict[
            constr(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"), Sources | Sources1 | Sources2
        ],
        Field(title="SourceMap"),
    ]
    """
    Named source definitions materialized locally before generators run.
    """
    registry_profiles: Annotated[
        dict[constr(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"), RegistryProfiles] | None,
        Field(title="RegistryProfileMap"),
    ] = None
    """
    Reusable schema registry profiles for JSON Schema-oriented inputs.
    """
    inputs: Annotated[
        dict[constr(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"), Inputs | Inputs1],
        Field(title="InputMap"),
    ]
    """
    Prepared logical inputs derived from one or more sources.
    """
    generators: Annotated[
        dict[constr(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"), Generators],
        Field(title="GeneratorMap"),
    ]
    """
    Named generator profiles. Generators are tool-specific but source-agnostic.
    """
    products: Annotated[
        dict[constr(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"), Products],
        Field(title="ProductMap"),
    ]
    """
    Named products generated from configured inputs and generators.
    """


class Identifier(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """


class NonEmptyString(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title="NonEmptyString")]


class PathString(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title="PathString")]
    """
    Filesystem path expressed with POSIX separators.
    """


class HttpsUrl(RootModel[AnyUrl]):
    root: Annotated[AnyUrl, Field(title="HttpsUrl")]
    """
    Absolute HTTPS URL.
    """


class SchemaFormat(StrEnum):
    JSON = "json"
    YAML = "yaml"


class LanguageName(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title="LanguageName")]
    """
    Target language identifier such as python or go.
    """


class Defaults1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    fetch: Annotated[Fetch | None, Field(title="FetchDefaults")] = None
    generator: Annotated[dict[str, Any] | None, Field(title="GeneratorDefaults")] = None
    """
    Tool-specific default generator options merged into individual generator profiles.
    """


class FetchDefaults(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    cache_dir: Annotated[str | None, Field(min_length=1, title="PathString")] = None
    """
    Filesystem path expressed with POSIX separators.
    """


class GeneratorDefaults(BaseModel):
    """Tool-specific default generator options merged into individual generator profiles."""

    model_config = ConfigDict(
        extra="allow",
    )


class Source3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["directory"]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    Filesystem path expressed with POSIX separators.
    """
    include: Annotated[
        list[IncludeItem] | None, Field(min_length=1, title="IncludePatterns")
    ] = None
    format: Annotated[Format, Field(title="SchemaFormat")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Source4(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["url"]
    uri: Annotated[AnyUrl, Field(title="HttpsUrl")]
    """
    Absolute HTTPS URL.
    """
    format: Annotated[Format, Field(title="SchemaFormat")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Source5(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["github-raw"]
    owner: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """
    repo: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    ref: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    Filesystem path expressed with POSIX separators.
    """
    format: Annotated[Format, Field(title="SchemaFormat")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )
    metadata: Annotated[Metadata | None, Field(title="GitHubRawSourceMetadata")] = None


class Source2(RootModel[Source3 | Source4 | Source5]):
    root: Annotated[Source3 | Source4 | Source5, Field(title="Source")]


class DirectorySource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["directory"]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    Filesystem path expressed with POSIX separators.
    """
    include: Annotated[
        list[IncludeItem] | None, Field(min_length=1, title="IncludePatterns")
    ] = None
    format: Annotated[Format, Field(title="SchemaFormat")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class UrlSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["url"]
    uri: Annotated[AnyUrl, Field(title="HttpsUrl")]
    """
    Absolute HTTPS URL.
    """
    format: Annotated[Format, Field(title="SchemaFormat")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class GitHubRawSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["github-raw"]
    owner: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """
    repo: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    ref: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    Filesystem path expressed with POSIX separators.
    """
    format: Annotated[Format, Field(title="SchemaFormat")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )
    metadata: Annotated[Metadata | None, Field(title="GitHubRawSourceMetadata")] = None


class GitHubRawSourceMetadata(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    tag: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    package: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    package_version: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None


class Resource1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    mode: Mode
    default_specification: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None


class Retrieve1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Kind


class Registry1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    crawl: bool | None = None
    retrieve: Annotated[Retrieve1 | None, Field(title="RetrieveConfig")] = None


class RegistryProfile(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    aliases: Annotated[list[Alias] | None, Field(title="AliasStrategies")] = None
    resource: Annotated[Resource1, Field(title="ResourceConfig")]
    registry: Annotated[Registry1 | None, Field(title="RegistryConfig")] = None


class AliasStrategy(StrEnum):
    BASENAME = "basename"
    INTERNAL_ID = "internal-id"
    RELATIVE_PATH = "relative-path"
    SOURCE_URI = "source-uri"


class ResourceConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    mode: Mode
    default_specification: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None


class Retrieve2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Kind


class RegistryConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    crawl: bool | None = None
    retrieve: Annotated[Retrieve2 | None, Field(title="RetrieveConfig")] = None


class RetrieveConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Kind


class Source6(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """


class Prepare2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    dereference: Dereference | None = None
    merge_ref_siblings: bool | None = None
    schema_transforms: Annotated[
        list[SchemaTransform] | None, Field(title="SchemaTransforms")
    ] = None


class Input2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["jsonschema"]
    sources: Annotated[list[Source6], Field(min_length=1, title="SourceRefList")]
    registry_profile: Annotated[
        str | None, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")
    ] = None
    """
    Stable manifest identifier used as a map key.
    """
    entrypoints: Annotated[
        list[Entrypoint], Field(min_length=1, title="EntrypointList")
    ]
    prepare: Annotated[Prepare2 | None, Field(title="PrepareConfig")] = None
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Prepare3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    dereference: Dereference | None = None
    merge_ref_siblings: bool | None = None
    schema_transforms: Annotated[
        list[SchemaTransform] | None, Field(title="SchemaTransforms")
    ] = None


class Input3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["workflow-template-schema"]
    sources: Annotated[list[Source6], Field(min_length=1, title="SourceRefList")]
    root: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    prepare: Annotated[Prepare3 | None, Field(title="PrepareConfig")] = None
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Input1(RootModel[Input2 | Input3]):
    root: Annotated[Input2 | Input3, Field(title="Input")]


class Prepare4(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    dereference: Dereference | None = None
    merge_ref_siblings: bool | None = None
    schema_transforms: Annotated[
        list[SchemaTransform] | None, Field(title="SchemaTransforms")
    ] = None


class JsonSchemaInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["jsonschema"]
    sources: Annotated[list[Source6], Field(min_length=1, title="SourceRefList")]
    registry_profile: Annotated[
        str | None, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")
    ] = None
    """
    Stable manifest identifier used as a map key.
    """
    entrypoints: Annotated[
        list[Entrypoint], Field(min_length=1, title="EntrypointList")
    ]
    prepare: Annotated[Prepare4 | None, Field(title="PrepareConfig")] = None
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Prepare5(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    dereference: Dereference | None = None
    merge_ref_siblings: bool | None = None
    schema_transforms: Annotated[
        list[SchemaTransform] | None, Field(title="SchemaTransforms")
    ] = None


class WorkflowTemplateSchemaInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["workflow-template-schema"]
    sources: Annotated[list[Source6], Field(min_length=1, title="SourceRefList")]
    root: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    prepare: Annotated[Prepare5 | None, Field(title="PrepareConfig")] = None
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class SourceRefListItem(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """


class SourceRefList(RootModel[list[SourceRefListItem]]):
    root: Annotated[list[SourceRefListItem], Field(min_length=1, title="SourceRefList")]


class PrepareConfig(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    dereference: Dereference | None = None
    merge_ref_siblings: bool | None = None
    schema_transforms: Annotated[
        list[SchemaTransform] | None, Field(title="SchemaTransforms")
    ] = None


class Generator1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    language: Annotated[str, Field(min_length=1, title="LanguageName")]
    """
    Target language identifier such as python or go.
    """
    tool: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    options: Annotated[dict[str, Any] | None, Field(title="GeneratorOptions")] = None
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class Input4(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """


class Generator2(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """


class Product(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    inputs: Annotated[list[Input4], Field(min_length=1, title="InputRefList")]
    generators: Annotated[
        list[Generator2], Field(min_length=1, title="GeneratorRefList")
    ]
    output_template: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    description: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = (
        None
    )


class InputRefListItem(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """


class InputRefList(RootModel[list[InputRefListItem]]):
    root: Annotated[list[InputRefListItem], Field(min_length=1, title="InputRefList")]


class GeneratorRefListItem(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    """
    Stable manifest identifier used as a map key.
    """


class GeneratorRefList(RootModel[list[GeneratorRefListItem]]):
    root: Annotated[
        list[GeneratorRefListItem], Field(min_length=1, title="GeneratorRefList")
    ]


# === codegen-lock.schema ===


class SourcesCodegenLockSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["directory"]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    POSIX-style path string using '/' separators and no leading './'.
    """
    content_sha256: Annotated[
        str | None, Field(pattern="^[a-f0-9]{64}$", title="Sha256Hex")
    ] = None
    """
    Lowercase hexadecimal SHA-256 digest.
    """
    generated_at: AwareDatetime | None = None
    """
    Informational digest timestamp. Omit by default in reproducible output.
    """


class Sources1CodegenLockSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["url"]
    uri: Annotated[AnyUrl, Field(title="HttpsUrl")]
    sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$", title="Sha256Hex")]
    """
    Lowercase hexadecimal SHA-256 digest.
    """
    fetched_at: AwareDatetime | None = None
    """
    Informational fetch timestamp. Omit by default in reproducible output.
    """
    etag: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    last_modified: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None


class Sources2CodegenLockSchema(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["github-raw"]
    owner: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    repo: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    ref: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    POSIX-style path string using '/' separators and no leading './'.
    """
    uri: Annotated[AnyUrl, Field(title="HttpsUrl")]
    sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$", title="Sha256Hex")]
    """
    Lowercase hexadecimal SHA-256 digest.
    """
    fetched_at: AwareDatetime | None = None
    """
    Informational fetch timestamp. Omit by default in reproducible output.
    """
    tag: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    package: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    package_version: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None


class CodegenLockfile(BaseModel):
    """Resolved source pins and integrity metadata for a code generation manifest."""

    model_config = ConfigDict(
        extra="forbid",
    )
    version: Literal[1]
    """
    Lockfile format version.
    """
    generated_at: AwareDatetime | None = None
    """
    Informational lockfile timestamp. Omit by default in reproducible output.
    """
    manifest_path: Annotated[str | None, Field(min_length=1)] = None
    """
    Normalized relative POSIX path from the lockfile directory to the manifest file.
    """
    sources: Annotated[
        dict[
            constr(pattern=r"^[A-Za-z][A-Za-z0-9_-]*$"),
            SourcesCodegenLockSchema
            | Sources1CodegenLockSchema
            | Sources2CodegenLockSchema,
        ],
        Field(title="LockedSourceMap"),
    ]


class IdentifierCodegenLockSchema(RootModel[str]):
    root: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]


class PathStringCodegenLockSchema(RootModel[str]):
    root: Annotated[str, Field(min_length=1, title="PathString")]
    """
    POSIX-style path string using '/' separators and no leading './'.
    """


class HttpsUrlCodegenLockSchema(RootModel[AnyUrl]):
    root: Annotated[AnyUrl, Field(title="HttpsUrl")]


class Sha256Hex(RootModel[str]):
    root: Annotated[str, Field(pattern="^[a-f0-9]{64}$", title="Sha256Hex")]
    """
    Lowercase hexadecimal SHA-256 digest.
    """


class LockedSource1(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["directory"]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    POSIX-style path string using '/' separators and no leading './'.
    """
    content_sha256: Annotated[
        str | None, Field(pattern="^[a-f0-9]{64}$", title="Sha256Hex")
    ] = None
    """
    Lowercase hexadecimal SHA-256 digest.
    """
    generated_at: AwareDatetime | None = None
    """
    Informational digest timestamp. Omit by default in reproducible output.
    """


class LockedSource2(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["url"]
    uri: Annotated[AnyUrl, Field(title="HttpsUrl")]
    sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$", title="Sha256Hex")]
    """
    Lowercase hexadecimal SHA-256 digest.
    """
    fetched_at: AwareDatetime | None = None
    """
    Informational fetch timestamp. Omit by default in reproducible output.
    """
    etag: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    last_modified: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None


class LockedSource3(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["github-raw"]
    owner: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    repo: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    ref: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    POSIX-style path string using '/' separators and no leading './'.
    """
    uri: Annotated[AnyUrl, Field(title="HttpsUrl")]
    sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$", title="Sha256Hex")]
    """
    Lowercase hexadecimal SHA-256 digest.
    """
    fetched_at: AwareDatetime | None = None
    """
    Informational fetch timestamp. Omit by default in reproducible output.
    """
    tag: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    package: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    package_version: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None


class LockedSource(RootModel[LockedSource1 | LockedSource2 | LockedSource3]):
    root: Annotated[
        LockedSource1 | LockedSource2 | LockedSource3, Field(title="LockedSource")
    ]


class LockedDirectorySource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["directory"]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    POSIX-style path string using '/' separators and no leading './'.
    """
    content_sha256: Annotated[
        str | None, Field(pattern="^[a-f0-9]{64}$", title="Sha256Hex")
    ] = None
    """
    Lowercase hexadecimal SHA-256 digest.
    """
    generated_at: AwareDatetime | None = None
    """
    Informational digest timestamp. Omit by default in reproducible output.
    """


class LockedUrlSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["url"]
    uri: Annotated[AnyUrl, Field(title="HttpsUrl")]
    sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$", title="Sha256Hex")]
    """
    Lowercase hexadecimal SHA-256 digest.
    """
    fetched_at: AwareDatetime | None = None
    """
    Informational fetch timestamp. Omit by default in reproducible output.
    """
    etag: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    last_modified: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None


class LockedGitHubRawSource(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
    )
    kind: Literal["github-raw"]
    owner: Annotated[str, Field(pattern="^[A-Za-z][A-Za-z0-9_-]*$", title="Identifier")]
    repo: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    ref: Annotated[str, Field(min_length=1, title="NonEmptyString")]
    path: Annotated[str, Field(min_length=1, title="PathString")]
    """
    POSIX-style path string using '/' separators and no leading './'.
    """
    uri: Annotated[AnyUrl, Field(title="HttpsUrl")]
    sha256: Annotated[str, Field(pattern="^[a-f0-9]{64}$", title="Sha256Hex")]
    """
    Lowercase hexadecimal SHA-256 digest.
    """
    fetched_at: AwareDatetime | None = None
    """
    Informational fetch timestamp. Omit by default in reproducible output.
    """
    tag: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    package: Annotated[str | None, Field(min_length=1, title="NonEmptyString")] = None
    package_version: Annotated[
        str | None, Field(min_length=1, title="NonEmptyString")
    ] = None
