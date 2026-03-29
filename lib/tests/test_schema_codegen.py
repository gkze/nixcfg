"""Tests for declarative JSON Schema code generation config and runner."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

from lib.schema_codegen.config import DirectorySource
from lib.schema_codegen.runner import (
    DEFAULT_CONFIG_PATH,
    _entrypoint_class_suffix,
    _resolve_body_class_conflicts,
    generate_schema_codegen_target,
    list_schema_codegen_targets,
    load_schema_codegen_config,
)


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


def test_generate_schema_codegen_target_from_multiple_entrypoints_yields_valid_python(
    tmp_path: Path,
) -> None:
    """Compose multi-entrypoint outputs into syntactically valid Python."""
    repo_root = DEFAULT_CONFIG_PATH.parent
    config_path = tmp_path / "schema_codegen.yaml"
    config_path.write_text(
        f"""
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
    path: {repo_root / "schemas/codegen"}
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
      - ./codegen.schema.json
      - ./codegen-lock.schema.json
    prepare:
      dereference: inline-refs
      merge_ref_siblings: false
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
    spec = importlib.util.spec_from_file_location("_generated_demo", output_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module.__name__] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(module.__name__, None)
    manifest_model = module.CodegenManifest
    lockfile_model = module.CodegenLockfile

    assert "class CodegenManifest" in rendered
    assert "class CodegenLockfile" in rendered
    assert "SourcesCodegenLockSchema" in rendered
    assert lockfile_model.model_validate({
        "version": 1,
        "sources": {
            "local": {
                "kind": "directory",
                "path": "schemas",
                "content_sha256": "a" * 64,
            }
        },
    })
    assert manifest_model.model_validate({
        "version": 1,
        "sources": {
            "local": {
                "kind": "directory",
                "path": "schemas",
                "format": "json",
            }
        },
        "inputs": {
            "primary": {
                "kind": "jsonschema",
                "sources": ["local"],
                "entrypoints": ["./codegen.schema.json"],
            }
        },
        "generators": {
            "python": {
                "language": "python",
                "tool": "datamodel-code-generator",
            }
        },
        "products": {
            "models": {
                "inputs": ["primary"],
                "generators": ["python"],
                "output_template": "generated.py",
            }
        },
    })


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
