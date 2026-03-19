"""Tests for declarative JSON Schema code generation config and runner."""

from __future__ import annotations

from pathlib import Path

import pytest

from lib.schema_codegen.config import DirectorySource
from lib.schema_codegen.runner import (
    DEFAULT_CONFIG_PATH,
    generate_schema_codegen_target,
    list_schema_codegen_targets,
    load_schema_codegen_config,
)
from lib.tests._assertions import check


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
    check(isinstance(source_path, Path))
    check(source_path == (tmp_path / "schemas").resolve())
    check(target.generator.output == (tmp_path / "generated/models.py").resolve())


def test_list_schema_codegen_targets_reads_repo_config() -> None:
    """Load the checked-in config and expose the initial targets."""
    summaries = list_schema_codegen_targets(config_path=DEFAULT_CONFIG_PATH)

    check([summary.name for summary in summaries] == ["github-actions", "nix-models"])
    check(str(summaries[0].output).endswith("lib/github_actions/models/_generated.py"))
    check(str(summaries[1].output).endswith("lib/nix/models/_generated.py"))


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

    check(loaded.path == config_path.resolve())
    check(loaded.config.targets["demo"].generator.output == (tmp_path / "generated.py"))


def test_generate_schema_codegen_target_from_directory_source(tmp_path: Path) -> None:
    """Generate models from a local directory source with inlined refs."""
    pytest.importorskip("datamodel_code_generator")
    pytest.importorskip("referencing")

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

    check(output_path == (tmp_path / "generated.py").resolve())
    check("class Person" in rendered)
    check("age:" in rendered)


def test_generate_schema_codegen_target_from_url_source(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Generate models from a URL-backed source using the source URI alias."""
    pytest.importorskip("datamodel_code_generator")
    pytest.importorskip("referencing")

    config_path = tmp_path / "schema_codegen.yaml"
    config_path.write_text(
        """
defaults:
  generator:
    input_file_type: jsonschema
    output_model_type: pydantic_v2.BaseModel
    target_python_version: "3.14"
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

    check(output_path == (tmp_path / "workflow_models.py").resolve())
    check("class Workflow" in rendered)
    check("name:" in rendered)


def test_generate_schema_codegen_target_rejects_recursive_refs(
    tmp_path: Path,
) -> None:
    """Fail fast when inline dereference encounters a recursive schema."""
    pytest.importorskip("datamodel_code_generator")
    pytest.importorskip("referencing")

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
