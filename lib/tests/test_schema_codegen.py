"""Tests for declarative JSON Schema code generation config and runner."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from lib.import_utils import load_module_from_path
from lib.schema_codegen import runner as codegen_runner
from lib.schema_codegen.config import (
    DereferenceMode,
    DirectorySource,
    SchemaFormat,
    URLSource,
)
from lib.schema_codegen.runner import (
    DEFAULT_CONFIG_PATH,
    _build_registry,
    _entrypoint_class_suffix,
    _prepare_entrypoint_schema,
    _resolve_body_class_conflicts,
    _resolve_target,
    generate_schema_codegen_target,
    list_schema_codegen_targets,
    load_schema_codegen_config,
)


def _load_generated_module(output_path: Path, *, module_name: str) -> object:
    """Import one generated Python module from *output_path*."""
    module = load_module_from_path(output_path, module_name)
    try:
        return module
    finally:
        sys.modules.pop(module.__name__, None)


def test_load_schema_codegen_config_resolves_relative_paths(tmp_path: Path) -> None:
    """Resolve source and output paths relative to the config file."""
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
    generator:
      output: generated/models.py
""".lstrip(),
        encoding="utf-8",
    )

    loaded = load_schema_codegen_config(config_path=config_path)

    source = loaded.config.sources["local"]
    target = loaded.config.targets["demo"]
    source_path = source.path if isinstance(source, DirectorySource) else None
    assert isinstance(source_path, Path)
    assert source_path == (tmp_path / "schemas").resolve()
    assert target.generator.output == (tmp_path / "generated/models.py").resolve()


def test_list_schema_codegen_targets_reads_repo_config() -> None:
    """Load the checked-in config and expose the initial targets."""
    summaries = list_schema_codegen_targets(config_path=DEFAULT_CONFIG_PATH)

    assert [summary.name for summary in summaries] == [
        "codegen-manifest-models",
        "github-actions",
        "nix-models",
    ]
    assert str(summaries[0].output).endswith("lib/schema_codegen/models/_generated.py")
    assert str(summaries[1].output).endswith("lib/github_actions/models/_generated.py")
    assert str(summaries[2].output).endswith("lib/nix/models/_generated.py")


def test_load_schema_codegen_config_resolves_explicit_paths_from_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resolve explicit relative config paths from the caller's working directory."""
    config_path = tmp_path / "custom.yaml"
    config_path.write_text(
        """
defaults:
  generator: {}
sources:
  remote:
    kind: url
    uri: https://example.com/schema.json
    format: json
registry_profiles:
  remote:
    aliases:
      - source-uri
    resource:
      mode: from-contents
targets:
  demo:
    sources:
      - remote
    registry_profile: remote
    entrypoints:
      - https://example.com/schema.json
    generator:
      output: generated.py
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.chdir(tmp_path)
    loaded = load_schema_codegen_config(config_path=Path("custom.yaml"))

    assert loaded.path == config_path.resolve()
    assert loaded.config.targets["demo"].generator.output == (tmp_path / "generated.py")


def test_generate_schema_codegen_target_from_directory_source(tmp_path: Path) -> None:
    """Generate models from a local directory source with inlined refs."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "defs.json").write_text(
        """
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "definitions": {
    "age": {
      "title": "Age",
      "type": "integer",
      "minimum": 0
    }
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (schemas_dir / "root.json").write_text(
        """
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Person",
  "type": "object",
  "properties": {
    "age": {
      "$ref": "./defs.json#/definitions/age"
    }
  },
  "required": ["age"]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config_path = tmp_path / "schema_codegen.yaml"
    config_path.write_text(
        """
defaults:
  generator:
    input_file_type: jsonschema
    output_model_type: pydantic_v2.BaseModel
    target_python_version: "3.14"
    use_annotated: true
    field_constraints: true
    formatters:
      - ruff-format
      - ruff-check
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
    prepare:
      dereference: inline-refs
    generator:
      output: generated.py
""".lstrip(),
        encoding="utf-8",
    )

    output_path = generate_schema_codegen_target(
        config_path=config_path,
        target_name="demo",
    )
    rendered = output_path.read_text(encoding="utf-8")

    assert output_path == (tmp_path / "generated.py").resolve()
    assert "class Person" in rendered
    assert "age:" in rendered


def test_read_url_source_uses_extended_github_token_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve GitHub auth consistently for remote schema fetches."""
    captured: dict[str, object] = {}

    def _resolve_github_token(**kwargs: object) -> str:
        captured["resolve"] = kwargs
        return "gh-token"

    def _build_github_headers(url: str, **kwargs: object) -> dict[str, str]:
        captured["headers"] = (url, kwargs)
        return {"Authorization": "Bearer gh-token"}

    def _fetch_url_bytes(url: str, **kwargs: object) -> tuple[bytes, dict[str, str]]:
        captured["fetch"] = (url, kwargs)
        return b"{}", {}

    monkeypatch.setattr(
        codegen_runner.http_utils,
        "resolve_github_token",
        _resolve_github_token,
    )
    monkeypatch.setattr(
        codegen_runner.http_utils,
        "build_github_headers",
        _build_github_headers,
    )
    monkeypatch.setattr(
        codegen_runner.http_utils,
        "fetch_url_bytes",
        _fetch_url_bytes,
    )

    payload = codegen_runner._read_url_source(
        URLSource(
            format=SchemaFormat.JSON,
            uri="https://api.github.com/repos/x/y/contents/schema.json",
        )
    )

    assert payload == "{}"
    assert captured["resolve"] == {
        "allow_keyring": True,
        "allow_netrc": True,
    }
    assert captured["headers"] == (
        "https://api.github.com/repos/x/y/contents/schema.json",
        {
            "token": "gh-token",
            "user_agent": "nixcfg-schema-codegen",
        },
    )
    assert captured["fetch"] == (
        "https://api.github.com/repos/x/y/contents/schema.json",
        {
            "headers": {"Authorization": "Bearer gh-token"},
            "timeout": 30.0,
        },
    )


def test_generate_schema_codegen_target_from_url_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generate models from a URL-backed source using the source URI alias."""
    config_path = tmp_path / "schema_codegen.yaml"
    config_path.write_text(
        """
defaults:
  generator:
    input_file_type: jsonschema
    output_model_type: pydantic_v2.BaseModel
    target_python_version: "3.14"
    formatters:
      - ruff-format
      - ruff-check
sources:
  remote:
    kind: url
    uri: https://example.com/workflow.json
    format: json
registry_profiles:
  remote:
    aliases:
      - source-uri
    resource:
      mode: from-contents
targets:
  workflow:
    sources:
      - remote
    registry_profile: remote
    entrypoints:
      - https://example.com/workflow.json
    generator:
      output: workflow_models.py
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "lib.schema_codegen.runner._read_url_source",
        lambda _source: (
            '{"$schema":"https://json-schema.org/draft/2020-12/schema",'
            '"title":"Workflow","type":"object","properties":{"name":{"type":"string"}}}'
        ),
    )

    output_path = generate_schema_codegen_target(
        config_path=config_path,
        target_name="workflow",
    )
    rendered = output_path.read_text(encoding="utf-8")

    assert output_path == (tmp_path / "workflow_models.py").resolve()
    assert "class Workflow" in rendered
    assert "name:" in rendered


def test_generate_schema_codegen_target_from_url_source_with_recursive_refs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generate models from a recursive URL-backed schema without inline refs."""
    config_path = tmp_path / "schema_codegen.yaml"
    config_path.write_text(
        """
defaults:
  generator:
    input_file_type: jsonschema
    output_model_type: pydantic_v2.BaseModel
    target_python_version: "3.14"
    use_annotated: true
    field_constraints: true
    formatters:
      - ruff-format
      - ruff-check
sources:
  remote:
    kind: url
    uri: https://json.schemastore.org/github-workflow.json
    format: json
registry_profiles:
  remote:
    aliases:
      - source-uri
    resource:
      mode: from-contents
targets:
  github-actions:
    sources:
      - remote
    registry_profile: remote
    entrypoints:
      - https://json.schemastore.org/github-workflow.json
    prepare:
      dereference: none
    generator:
      output: workflow_models.py
      class_name: GitHubWorkflow
      reuse_model: true
      collapse_reuse_models: true
      use_type_alias: true
""".lstrip(),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        "lib.schema_codegen.runner._read_url_source",
        lambda _source: json.dumps({
            "$schema": "http://json-schema.org/draft-07/schema#",
            "$id": "https://json.schemastore.org/github-workflow.json",
            "type": "object",
            "properties": {
                "jobs": {
                    "type": "object",
                    "additionalProperties": {"$ref": "#/definitions/configuration"},
                }
            },
            "definitions": {
                "configuration": {
                    "oneOf": [
                        {"type": "string"},
                        {
                            "type": "object",
                            "additionalProperties": {
                                "$ref": "#/definitions/configuration"
                            },
                        },
                        {
                            "type": "array",
                            "items": {"$ref": "#/definitions/configuration"},
                        },
                    ]
                }
            },
        }),
    )

    output_path = generate_schema_codegen_target(
        config_path=config_path,
        target_name="github-actions",
    )
    rendered = output_path.read_text(encoding="utf-8")
    module = _load_generated_module(output_path, module_name="_generated_workflow")

    assert output_path == (tmp_path / "workflow_models.py").resolve()
    assert "type Configuration =" in rendered
    assert "class GitHubWorkflow" in rendered

    workflow = module.GitHubWorkflow.model_validate({"jobs": {"build": "ready"}})
    assert workflow.jobs == {"build": "ready"}

    nested_workflow = module.GitHubWorkflow.model_validate({
        "jobs": {"build": {"sub": ["ready"]}}
    })
    assert nested_workflow.jobs == {"build": {"sub": ["ready"]}}


def test_generate_schema_codegen_target_from_multiple_entrypoints_yields_valid_python(
    tmp_path: Path,
) -> None:
    """Compose multi-entrypoint outputs into syntactically valid Python."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "alpha.json").write_text(
        """
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Alpha",
  "type": "object",
  "properties": {
    "shared": {
      "title": "Shared",
      "type": "object",
      "properties": {
        "value": {"type": "integer"}
      },
      "required": ["value"]
    }
  },
  "required": ["shared"]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )
    (schemas_dir / "beta.json").write_text(
        """
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Beta",
  "type": "object",
  "properties": {
    "shared": {
      "title": "Shared",
      "type": "object",
      "properties": {
        "value": {"type": "string"}
      },
      "required": ["value"]
    }
  },
  "required": ["shared"]
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config_path = tmp_path / "schema_codegen.yaml"
    config_path.write_text(
        """
defaults:
  generator:
    input_file_type: jsonschema
    output_model_type: pydantic_v2.BaseModel
    target_python_version: "3.14"
    use_annotated: true
    field_constraints: true
    formatters:
      - ruff-format
      - ruff-check
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
      - basename
    resource:
      mode: from-contents
targets:
  demo:
    sources:
      - local
    registry_profile: local
    entrypoints:
      - ./alpha.json
      - ./beta.json
    generator:
      output: generated.py
""".lstrip(),
        encoding="utf-8",
    )

    output_path = generate_schema_codegen_target(
        config_path=config_path,
        target_name="demo",
    )
    module = _load_generated_module(output_path, module_name="_generated_demo")

    assert output_path == (tmp_path / "generated.py").resolve()
    assert hasattr(module, "Alpha")
    assert hasattr(module, "Beta")
    assert hasattr(module, "Shared")

    alpha = module.Alpha.model_validate({"shared": {"value": 1}})
    beta = module.Beta.model_validate({"shared": {"value": "ready"}})
    beta_shared_name = f"Shared{_entrypoint_class_suffix('./beta.json')}"

    assert alpha.shared.__class__ is module.Shared
    assert alpha.shared.value == 1
    assert hasattr(module, beta_shared_name)
    assert beta.shared.__class__ is getattr(module, beta_shared_name)
    assert beta.shared.value == "ready"


def test_repo_github_actions_target_uses_safe_recursive_codegen_settings() -> None:
    """Keep the checked-in GitHub Actions target on recursive-safe settings."""
    loaded = load_schema_codegen_config(config_path=DEFAULT_CONFIG_PATH)
    target = loaded.config.targets["github-actions"]
    generator = target.generator.model_dump()

    assert target.prepare.dereference is DereferenceMode.NONE
    assert target.prepare.python_transforms == ()
    assert generator.get("class_name") == "GitHubWorkflow"
    assert generator.get("reuse_model") is True
    assert generator.get("collapse_reuse_models") is True
    assert generator.get("use_type_alias") is True


def test_prepare_repo_codegen_manifest_entrypoints_from_default_config() -> None:
    """Resolve and inline the checked-in codegen manifest schemas."""
    loaded = load_schema_codegen_config(config_path=DEFAULT_CONFIG_PATH)
    resolved = _resolve_target(loaded, target_name="codegen-manifest-models")
    registry = _build_registry(loaded, target=resolved.target)

    manifest_schema = _prepare_entrypoint_schema(
        entrypoint="./codegen.schema.json",
        registry=registry,
        target=resolved.target,
    )
    lockfile_schema = _prepare_entrypoint_schema(
        entrypoint="./codegen-lock.schema.json",
        registry=registry,
        target=resolved.target,
    )

    assert manifest_schema["title"] == "CodegenManifest"
    assert manifest_schema["type"] == "object"
    assert lockfile_schema["title"] == "CodegenLockfile"
    assert lockfile_schema["type"] == "object"


def test_resolve_body_class_conflicts_renames_mismatched_duplicates() -> None:
    """Rename conflicting helper classes while dropping byte-identical duplicates."""
    manifest_body = """
class Shared(BaseModel):
    value: int

class Different(BaseModel):
    value: int
"""
    lock_body = """
class Shared(BaseModel):
    value: int

class Different(BaseModel):
    value: str

class UsesDifferent(BaseModel):
    item: Different
"""
    seen_signatures: dict[str, str] = {}
    used_names: set[str] = set()

    first = _resolve_body_class_conflicts(
        manifest_body,
        entrypoint="./codegen.schema.json",
        seen_signatures=seen_signatures,
        used_names=used_names,
    )
    second = _resolve_body_class_conflicts(
        lock_body,
        entrypoint="./codegen-lock.schema.json",
        seen_signatures=seen_signatures,
        used_names=used_names,
    )

    assert _entrypoint_class_suffix("./codegen-lock.schema.json") == "CodegenLockSchema"
    assert "class Shared(BaseModel):" in first
    assert "class Shared(BaseModel):" not in second
    assert "class DifferentCodegenLockSchema(BaseModel):" in second
    assert "item: DifferentCodegenLockSchema" in second


def test_generate_schema_codegen_target_rejects_recursive_refs(
    tmp_path: Path,
) -> None:
    """Fail fast when inline dereference encounters a recursive schema."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "root.json").write_text(
        """
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Node",
  "type": "object",
  "properties": {
    "next": {
      "$ref": "#"
    }
  }
}
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config_path = tmp_path / "schema_codegen.yaml"
    config_path.write_text(
        """
defaults:
  generator:
    input_file_type: jsonschema
    output_model_type: pydantic_v2.BaseModel
    target_python_version: "3.14"
    field_constraints: true
    formatters:
      - ruff-format
      - ruff-check
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
    prepare:
      dereference: inline-refs
    generator:
      output: generated.py
""".lstrip(),
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match=r"Recursive \$ref not supported"):
        generate_schema_codegen_target(
            config_path=config_path,
            target_name="demo",
        )


def test_codegen_manifest_schemas_use_stable_urn_ids_and_optional_metadata() -> None:
    """Keep unpublished schema IDs non-hosted and metadata fields optional."""
    repo_root = DEFAULT_CONFIG_PATH.parent
    manifest_schema = json.loads(
        (repo_root / "schemas/codegen/codegen.schema.json").read_text(encoding="utf-8")
    )
    lock_schema = json.loads(
        (repo_root / "schemas/codegen/codegen-lock.schema.json").read_text(
            encoding="utf-8"
        )
    )

    assert str(manifest_schema["$id"]).startswith("urn:uuid:")
    assert str(lock_schema["$id"]).startswith("urn:uuid:")
    assert "generated_at" not in lock_schema["required"]
    assert "fetched_at" not in lock_schema["$defs"]["LockedUrlSource"].get(
        "required", []
    )
    assert "fetched_at" not in lock_schema["$defs"]["LockedGitHubRawSource"].get(
        "required", []
    )
