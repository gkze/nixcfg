"""Focused branch coverage for schema codegen preparation and runner helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from lib import http_utils
from lib.schema_codegen import _prepare
from lib.schema_codegen import runner as codegen_runner
from lib.schema_codegen.config import (
    AliasStrategy,
    CodegenTarget,
    DereferenceMode,
    DirectorySource,
    LoadedSchemaCodegenConfig,
    RegistryConfig,
    ResourceConfig,
    RetrieveConfig,
    SchemaCodegenConfig,
    SchemaFormat,
    SchemaTransform,
)


class _FakeResource:
    def __init__(
        self,
        contents: object,
        *,
        resource_id: str | None = None,
        resolver: _FakeResolver | None = None,
    ) -> None:
        self.contents = contents
        self._resource_id = resource_id
        self.resolver = resolver or _FakeResolver({})

    def id(self) -> str | None:
        return self._resource_id


class _FakeResolved:
    def __init__(self, contents: object, *, resolver: _FakeResolver) -> None:
        self.contents = contents
        self.resolver = resolver


class _FakeResolver:
    def __init__(self, mapping: dict[str, _FakeResolved]) -> None:
        self._mapping = mapping

    def lookup(self, ref: str) -> _FakeResolved:
        return self._mapping[ref]


class _FakeRegistry:
    def __init__(self, resources: dict[str, _FakeResource] | None = None) -> None:
        self.resources = resources or {}
        self.crawled = False

    def __getitem__(self, uri: str) -> _FakeResource:
        return self.resources[uri]

    def crawl(self) -> _FakeRegistry:
        self.crawled = True
        return self

    def resolver_with_root(self, resource: _FakeResource) -> _FakeResolver:
        return resource.resolver

    def with_resources(self, pairs: list[tuple[str, object]]) -> _FakeRegistry:
        return _FakeRegistry({
            **self.resources,
            **{uri: cast("_FakeResource", resource) for uri, resource in pairs},
        })


def _loaded_config_for_directory(
    tmp_path: Path,
    *,
    aliases: tuple[AliasStrategy, ...] = (AliasStrategy.BASENAME,),
) -> tuple[LoadedSchemaCodegenConfig, Path]:
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    config = SchemaCodegenConfig.model_validate({
        "defaults": {"generator": {}},
        "sources": {
            "local": {
                "kind": "directory",
                "path": str(schemas_dir),
                "include": ["**/*.json"],
                "format": "json",
            }
        },
        "registry_profiles": {
            "local": {
                "aliases": [alias.value for alias in aliases],
                "resource": {"mode": "from-contents"},
            }
        },
        "targets": {
            "demo": {
                "sources": ["local"],
                "registry_profile": "local",
                "entrypoints": ["./root.json"],
                "generator": {"output": str(tmp_path / "generated.py")},
            }
        },
    })
    return LoadedSchemaCodegenConfig(
        config=config, path=tmp_path / "schema_codegen.yaml"
    ), schemas_dir


def _install_fake_referencing(monkeypatch: pytest.MonkeyPatch) -> None:
    def _from_contents(
        contents: object,
        default_specification: object | None = None,
    ) -> _FakeResource:
        resource_id = None
        if isinstance(contents, dict):
            candidate = contents.get("$id")
            if isinstance(candidate, str):
                resource_id = candidate
        return _FakeResource(
            contents,
            resource_id=resource_id,
            resolver=_FakeResolver({}),
        )

    def registry_factory() -> _FakeRegistry:
        return _FakeRegistry()

    monkeypatch.setattr(
        _prepare,
        "_import_optional",
        lambda module_name, *, feature: SimpleNamespace(
            Resource=SimpleNamespace(from_contents=_from_contents),
            Registry=registry_factory,
        ),
    )


def test_load_source_documents_skips_non_files(tmp_path: Path) -> None:
    """Directory sources ignore globbed directories and only load files."""
    schemas_dir = tmp_path / "schemas"
    nested_dir = schemas_dir / "nested"
    nested_dir.mkdir(parents=True)
    (schemas_dir / "root.json").write_text('{"title": "Root"}\n', encoding="utf-8")

    documents = _prepare._load_source_documents(
        DirectorySource(
            path=schemas_dir,
            include=("*",),
            format=SchemaFormat.JSON,
        ),
        read_url_source=lambda source: (_ for _ in ()).throw(AssertionError(source)),
    )

    assert [document.basename for document in documents] == ["root.json"]
    assert documents[0].relative_path == "./root.json"


def test_make_resource_uses_default_specification_and_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default dialect lookup feeds the resource factory and alias URIs dedupe."""
    captured: dict[str, object] = {}

    def _from_contents(
        contents: object,
        default_specification: object | None = None,
    ) -> _FakeResource:
        captured["contents"] = contents
        captured["default_specification"] = default_specification
        return _FakeResource(contents, resource_id="urn:example:schema")

    def _import_optional(module_name: str, *, feature: str) -> object:
        assert feature == "schema code generation"
        if module_name == "referencing":
            return SimpleNamespace(
                Resource=SimpleNamespace(from_contents=_from_contents)
            )
        if module_name == "referencing.jsonschema":
            return SimpleNamespace(
                specification_with=lambda dialect_id: {"dialect": dialect_id}
            )
        raise AssertionError(module_name)

    monkeypatch.setattr(_prepare, "_import_optional", _import_optional)

    resource = _prepare._make_resource(
        {"$id": "urn:example:schema", "type": "object"},
        default_specification="https://json-schema.org/draft/2020-12/schema",
    )
    aliases = _prepare._alias_uris_for_document(
        aliases=(
            AliasStrategy.SOURCE_URI,
            AliasStrategy.RELATIVE_PATH,
            AliasStrategy.BASENAME,
            AliasStrategy.INTERNAL_ID,
            AliasStrategy.INTERNAL_ID,
        ),
        document=_prepare._LoadedSchemaDocument(
            basename="root.json",
            contents={},
            label="root.json",
            relative_path="./root.json",
            source_uri="https://example.com/root.json",
        ),
        resource=resource,
    )
    aliases_without_id = _prepare._alias_uris_for_document(
        aliases=(AliasStrategy.INTERNAL_ID,),
        document=_prepare._LoadedSchemaDocument(
            basename=None,
            contents={},
            label="missing-id",
            relative_path=None,
            source_uri=None,
        ),
        resource=_FakeResource({}, resource_id=None),
    )
    aliases_fall_back_to_basename = _prepare._alias_uris_for_document(
        aliases=(AliasStrategy.INTERNAL_ID, AliasStrategy.BASENAME),
        document=_prepare._LoadedSchemaDocument(
            basename="fallback.json",
            contents={},
            label="fallback",
            relative_path=None,
            source_uri=None,
        ),
        resource=_FakeResource({}, resource_id=None),
    )
    aliases_ignore_unknown_values = _prepare._alias_uris_for_document(
        aliases=(cast("AliasStrategy", "bogus"), AliasStrategy.BASENAME),
        document=_prepare._LoadedSchemaDocument(
            basename="ignored-unknown.json",
            contents={},
            label="unknown",
            relative_path=None,
            source_uri=None,
        ),
        resource=_FakeResource({}, resource_id=None),
    )

    assert captured["default_specification"] == {
        "dialect": "https://json-schema.org/draft/2020-12/schema"
    }
    assert aliases == (
        "https://example.com/root.json",
        "./root.json",
        "root.json",
        "urn:example:schema",
    )
    assert aliases_without_id == ()
    assert aliases_fall_back_to_basename == ("fallback.json",)
    assert aliases_ignore_unknown_values == ("ignored-unknown.json",)


def test_build_registry_rejects_unknown_profile(tmp_path: Path) -> None:
    """Unknown profile names fail before any registry construction."""
    loaded, _schemas_dir = _loaded_config_for_directory(tmp_path)
    target = loaded.config.targets["demo"].model_copy(
        update={"registry_profile": "missing"}
    )

    with pytest.raises(RuntimeError, match="Unknown registry profile"):
        _prepare.build_registry(
            loaded, target=target, read_url_source=lambda source: ""
        )


def test_build_registry_rejects_unsupported_resource_mode(tmp_path: Path) -> None:
    """Unsupported resource modes are rejected explicitly."""
    loaded, _schemas_dir = _loaded_config_for_directory(tmp_path)
    profile = loaded.config.registry_profiles["local"]
    profile.resource = ResourceConfig.model_construct(mode=cast("object", "bogus"))

    with pytest.raises(RuntimeError, match="Unsupported resource mode"):
        _prepare.build_registry(
            loaded,
            target=loaded.config.targets["demo"],
            read_url_source=lambda source: "",
        )


def test_build_registry_rejects_unsupported_retrieve_backend(tmp_path: Path) -> None:
    """Unsupported retrieve backends are rejected explicitly."""
    loaded, _schemas_dir = _loaded_config_for_directory(tmp_path)
    profile = loaded.config.registry_profiles["local"]
    profile.registry = RegistryConfig.model_construct(
        crawl=True,
        retrieve=RetrieveConfig.model_construct(kind=cast("object", "bogus")),
    )

    with pytest.raises(RuntimeError, match="Unsupported retrieve backend"):
        _prepare.build_registry(
            loaded,
            target=loaded.config.targets["demo"],
            read_url_source=lambda source: "",
        )


def test_build_registry_rejects_unknown_source_name(tmp_path: Path) -> None:
    """Targets cannot reference undefined source names."""
    loaded, _schemas_dir = _loaded_config_for_directory(tmp_path)
    target = loaded.config.targets["demo"].model_copy(update={"sources": ("missing",)})

    with pytest.raises(RuntimeError, match="Unknown schema source"):
        _prepare.build_registry(
            loaded, target=target, read_url_source=lambda source: ""
        )


def test_build_registry_requires_configured_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A source document must produce at least one registry URI alias."""
    loaded, schemas_dir = _loaded_config_for_directory(tmp_path, aliases=())
    (schemas_dir / "root.json").write_text('{"title": "Root"}\n', encoding="utf-8")
    _install_fake_referencing(monkeypatch)

    with pytest.raises(RuntimeError, match="has no configured registry aliases"):
        _prepare.build_registry(
            loaded,
            target=loaded.config.targets["demo"],
            read_url_source=lambda source: "",
        )


def test_build_registry_detects_alias_collisions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Colliding aliases from distinct source documents fail fast."""
    loaded, schemas_dir = _loaded_config_for_directory(tmp_path)
    (schemas_dir / "root.json").write_text('{"title": "Root"}\n', encoding="utf-8")
    nested = schemas_dir / "nested"
    nested.mkdir()
    (nested / "root.json").write_text('{"title": "NestedRoot"}\n', encoding="utf-8")
    _install_fake_referencing(monkeypatch)

    with pytest.raises(RuntimeError, match=r"Registry alias 'root.json' collides"):
        _prepare.build_registry(
            loaded,
            target=loaded.config.targets["demo"],
            read_url_source=lambda source: "",
        )


def test_merge_allof_helpers_normalize_existing_containers() -> None:
    """Property and required merges tolerate malformed existing containers."""
    result: dict[str, object] = {"properties": [], "required": ["keep", 1]}

    _prepare._merge_allof_properties(result, {})
    _prepare._merge_allof_properties(result, {"added": {"type": "string"}})
    _prepare._merge_allof_required(result, [])
    _prepare._merge_allof_required(result, ["keep", "next"])

    assert result["properties"] == {"added": {"type": "string"}}
    assert result["required"] == ["keep", "next"]


def test_merge_allof_branches_and_transforms_cover_merge_paths() -> None:
    """Mergeable allOf branches inline fields while preserving non-object branches."""
    schema = {
        "type": "object",
        "description": "drop me",
        "allOf": [
            7,
            {"type": "string"},
            {
                "properties": {"name": {"type": "string"}},
                "required": ["name", 1],
                "additionalProperties": False,
                "title": "ignored title",
            },
            {
                "properties": {"nickname": {"type": "string"}},
                "description": "also dropped",
            },
        ],
        "const": None,
    }

    transformed = _prepare._apply_schema_transforms(
        schema,
        transforms=(
            SchemaTransform.DROP_DESCRIPTION,
            SchemaTransform.INLINE_MERGEABLE_ALLOF,
            SchemaTransform.CONST_NULL_TO_TYPE_NULL,
        ),
    )
    fully_merged = _prepare._apply_schema_transforms(
        {
            "allOf": [
                {
                    "properties": {"id": {"type": "integer"}},
                    "required": ["id"],
                }
            ]
        },
        transforms=(SchemaTransform.INLINE_MERGEABLE_ALLOF,),
    )
    transformed_list = _prepare._apply_schema_transforms(
        [{"description": "gone"}, 3],
        transforms=(SchemaTransform.DROP_DESCRIPTION,),
    )

    assert transformed == {
        "type": "null",
        "allOf": [7, {"type": "string"}],
        "properties": {
            "name": {"type": "string"},
            "nickname": {"type": "string"},
        },
        "required": ["name"],
        "additionalProperties": False,
    }
    assert fully_merged == {
        "properties": {"id": {"type": "integer"}},
        "required": ["id"],
    }
    assert transformed_list == [{}, 3]


def test_inline_references_merges_ref_siblings() -> None:
    """Sibling keys can be merged into a resolved inline reference."""
    resolver = _FakeResolver({})
    resolver._mapping["#/defs/name"] = _FakeResolved(
        {"title": "Name", "type": "string"},
        resolver=resolver,
    )

    resolved = _prepare._inline_references(
        {"$ref": "#/defs/name", "description": "preferred display name"},
        merge_ref_siblings=True,
        resolver=resolver,
        stack=(),
    )

    assert resolved == {
        "title": "Name",
        "type": "string",
        "description": "preferred display name",
    }


def test_prepare_entrypoint_schema_wraps_lookup_errors_and_applies_transforms() -> None:
    """Entrypoint loading failures are wrapped, and transforms still apply."""
    target = CodegenTarget.model_validate({
        "sources": ["local"],
        "registry_profile": "local",
        "entrypoints": ["./root.json"],
        "generator": {"output": "generated.py"},
        "prepare": {
            "dereference": "none",
            "schema_transforms": ["drop-description", "const-null-to-type-null"],
        },
    })
    resource = _FakeResource({"description": "gone", "const": None})

    with pytest.raises(RuntimeError, match="Could not load schema entrypoint"):
        _prepare.prepare_entrypoint_schema(
            entrypoint="./missing.json",
            registry=_FakeRegistry(),
            target=target,
        )

    prepared = _prepare.prepare_entrypoint_schema(
        entrypoint="./root.json",
        registry=_FakeRegistry({"./root.json": resource}),
        target=target,
    )

    assert prepared == {"type": "null"}


def test_emit_progress_and_read_url_source_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Progress callbacks run, and HTTP validation errors become runtime errors."""
    messages: list[str] = []

    codegen_runner._emit_progress(messages.append, "hello")
    codegen_runner._emit_progress(None, "ignored")

    monkeypatch.setattr(
        codegen_runner.http_utils, "resolve_github_token", lambda **kwargs: None
    )
    monkeypatch.setattr(
        codegen_runner.http_utils,
        "build_github_headers",
        lambda url, **kwargs: {},
    )
    monkeypatch.setattr(
        codegen_runner.http_utils,
        "fetch_url_bytes",
        lambda url, **kwargs: (_ for _ in ()).throw(ValueError("bad url")),
    )

    with pytest.raises(
        RuntimeError, match="Only absolute HTTPS schema URLs are supported"
    ):
        codegen_runner._read_url_source(
            codegen_runner.URLSource(
                format=SchemaFormat.JSON,
                uri="http://example.com/schema.json",
            )
        )

    monkeypatch.setattr(
        codegen_runner.http_utils,
        "fetch_url_bytes",
        lambda url, **kwargs: (_ for _ in ()).throw(
            http_utils.RequestError(
                url=url,
                attempts=1,
                kind="network",
                detail="timeout",
            )
        ),
    )

    with pytest.raises(RuntimeError, match="Failed to fetch schema URL"):
        codegen_runner._read_url_source(
            codegen_runner.URLSource(
                format=SchemaFormat.JSON,
                uri="https://example.com/schema.json",
            )
        )

    assert messages == ["hello"]


def test_resolve_config_paths_preserves_absolute_and_missing_outputs(
    tmp_path: Path,
) -> None:
    """Only relative directory and output paths are rewritten in-place."""
    absolute_dir = tmp_path / "schemas"
    absolute_dir.mkdir()
    config = SchemaCodegenConfig.model_validate({
        "defaults": {"generator": {}},
        "sources": {
            "local": {
                "kind": "directory",
                "path": str(absolute_dir),
                "include": ["*.json"],
                "format": "json",
            }
        },
        "registry_profiles": {
            "local": {
                "aliases": ["relative-path"],
                "resource": {"mode": "from-contents"},
            }
        },
        "targets": {
            "demo": {
                "sources": ["local"],
                "registry_profile": "local",
                "entrypoints": ["./root.json"],
                "generator": {},
            }
        },
    })

    codegen_runner._resolve_config_paths(config, base_dir=tmp_path / "config")

    source = config.sources["local"]
    assert isinstance(source, DirectorySource)
    assert source.path == absolute_dir
    assert config.targets["demo"].generator.output is None


def test_list_targets_and_resolve_target_validate_missing_outputs(
    tmp_path: Path,
) -> None:
    """Runner helpers reject missing outputs and unknown target names."""
    config_path = tmp_path / "schema_codegen.yaml"
    config_path.write_text(
        """
defaults:
  generator: {}
sources:
  local:
    kind: directory
    path: schemas
    include:
      - "*.json"
    format: json
registry_profiles:
  local:
    aliases:
      - relative-path
    resource:
      mode: from-contents
targets:
  demo:
    sources:
      - local
    registry_profile: local
    entrypoints:
      - ./root.json
    generator: {}
""".lstrip(),
        encoding="utf-8",
    )
    (tmp_path / "schemas").mkdir()
    loaded = codegen_runner.load_schema_codegen_config(config_path=config_path)

    with pytest.raises(RuntimeError, match="missing a generator output path"):
        codegen_runner.list_schema_codegen_targets(config_path=config_path)

    with pytest.raises(RuntimeError, match="Unknown schema codegen target"):
        codegen_runner._resolve_target(loaded, target_name="missing")

    with pytest.raises(TypeError, match="missing a generator output path"):
        codegen_runner._resolve_target(loaded, target_name="demo")


def test_generate_schema_codegen_target_restores_trailing_newline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runner always writes a newline-terminated generated file."""
    output_path = tmp_path / "generated.py"
    loaded = LoadedSchemaCodegenConfig(
        config=SchemaCodegenConfig.model_validate({
            "defaults": {"generator": {}},
            "sources": {},
            "registry_profiles": {},
            "targets": {
                "demo": {
                    "sources": [],
                    "registry_profile": "local",
                    "entrypoints": [],
                    "generator": {"output": str(output_path)},
                    "prepare": {"dereference": DereferenceMode.NONE.value},
                }
            },
        }),
        path=tmp_path / "schema_codegen.yaml",
    )

    monkeypatch.setattr(
        codegen_runner, "load_schema_codegen_config", lambda config_path=None: loaded
    )
    monkeypatch.setattr(
        codegen_runner, "_build_registry", lambda loaded, target: _FakeRegistry()
    )
    monkeypatch.setattr(
        codegen_runner._render, "compose_imports_block", lambda imports: ""
    )
    monkeypatch.setattr(
        codegen_runner._render,
        "apply_python_transforms",
        lambda rendered, target: rendered.rstrip("\n"),
    )

    written_path = codegen_runner.generate_schema_codegen_target(target_name="demo")

    assert written_path == output_path
    assert output_path.read_text(encoding="utf-8").endswith("\n")


def test_generate_schema_codegen_target_preserves_existing_trailing_newline(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Runner leaves already-normalized trailing newlines alone."""
    output_path = tmp_path / "generated.py"
    loaded = LoadedSchemaCodegenConfig(
        config=SchemaCodegenConfig.model_validate({
            "defaults": {"generator": {}},
            "sources": {},
            "registry_profiles": {},
            "targets": {
                "demo": {
                    "sources": [],
                    "registry_profile": "local",
                    "entrypoints": [],
                    "generator": {"output": str(output_path)},
                }
            },
        }),
        path=tmp_path / "schema_codegen.yaml",
    )

    monkeypatch.setattr(
        codegen_runner, "load_schema_codegen_config", lambda config_path=None: loaded
    )
    monkeypatch.setattr(
        codegen_runner, "_build_registry", lambda loaded, target: _FakeRegistry()
    )
    monkeypatch.setattr(
        codegen_runner._render, "compose_imports_block", lambda imports: ""
    )
    monkeypatch.setattr(
        codegen_runner._render,
        "apply_python_transforms",
        lambda rendered, target: f"{rendered}\n",
    )

    codegen_runner.generate_schema_codegen_target(target_name="demo")

    assert output_path.read_text(encoding="utf-8").endswith("\n")
