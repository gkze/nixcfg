"""Tests for canonical codegen manifest lockfile materialization."""

import copy
import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lib.schema_codegen import lockfile as codegen_lockfile
from lib.schema_codegen.lockfile import (
    build_codegen_lockfile,
    load_codegen_manifest,
    render_codegen_lockfile,
    write_codegen_lockfile,
)
from lib.schema_codegen.models._generated import CodegenLockfile
from lib.update.paths import REPO_ROOT


def _write_manifest(path: Path, *, source_block: str, entrypoint: str) -> None:
    path.write_text(
        f"""
version: 1
sources:
{source_block}
inputs:
  primary:
    kind: jsonschema
    sources:
      - source
    entrypoints:
      - {entrypoint}
generators:
  python:
    language: python
    tool: datamodel-code-generator
products:
  models:
    inputs:
      - primary
    generators:
      - python
    output_template: generated.py
""".lstrip(),
        encoding="utf-8",
    )


def _directory_content_sha256(entries: dict[str, bytes]) -> str:
    records = b"".join(
        f"{path}\0{hashlib.sha256(entries[path]).hexdigest()}\n".encode()
        for path in sorted(entries)
    )
    return hashlib.sha256(records).hexdigest()


def _canonical_manifest_payload() -> dict[str, object]:
    return {
        "version": 1,
        "sources": {
            "source": {"kind": "directory", "path": "schemas", "format": "json"}
        },
        "registry_profiles": {
            "local": {
                "aliases": ["basename"],
                "resource": {"mode": "from-contents"},
            }
        },
        "inputs": {
            "primary": {
                "kind": "jsonschema",
                "sources": ["source"],
                "registry_profile": "local",
                "entrypoints": ["./root.json"],
                "prepare": {"schema_transforms": ["drop-description"]},
            }
        },
        "generators": {
            "python": {"language": "python", "tool": "datamodel-code-generator"}
        },
        "products": {
            "models": {
                "inputs": ["primary"],
                "generators": ["python"],
                "output_template": "generated.py",
            }
        },
    }


def test_load_codegen_manifest_accepts_canonical_unique_items(
    tmp_path: Path,
) -> None:
    """The canonical-schema preflight preserves valid manifest behavior."""
    manifest_path = tmp_path / "codegen.json"
    manifest_path.write_text(
        json.dumps(_canonical_manifest_payload()), encoding="utf-8"
    )

    assert load_codegen_manifest(manifest_path=manifest_path).version == 1


def test_manifest_validation_uses_optional_dependency_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Load jsonschema only when manifest validation actually runs."""
    manifest_path = tmp_path / "codegen.json"
    manifest_path.write_text(
        json.dumps(_canonical_manifest_payload()), encoding="utf-8"
    )
    import_optional = codegen_lockfile._import_optional
    calls: list[tuple[str, str]] = []

    def _record_import(module_name: str, *, feature: str) -> object:
        calls.append((module_name, feature))
        return import_optional(module_name, feature=feature)

    monkeypatch.setattr(codegen_lockfile, "_import_optional", _record_import)
    load_codegen_manifest(manifest_path=manifest_path)

    assert calls == [("jsonschema", "codegen manifest validation")]


def test_packaged_manifest_schema_is_the_canonical_repository_schema() -> None:
    """Package data exposes the canonical schema without a second authority."""
    packaged = codegen_lockfile._CODEGEN_MANIFEST_SCHEMA_PATH
    canonical = REPO_ROOT / "schemas/codegen/codegen.schema.json"

    assert packaged.is_file()
    assert packaged.samefile(canonical)
    assert packaged.read_bytes() == canonical.read_bytes()


@pytest.mark.parametrize(
    ("uri", "source_name"),
    [
        ("http://example.com/schema.json", "source"),
        ("https://example.com/schema.json", "1invalid"),
    ],
)
def test_load_codegen_manifest_enforces_canonical_patterns(
    tmp_path: Path,
    uri: str,
    source_name: str,
) -> None:
    """AnyUrl and dictionary projections cannot discard canonical regex constraints."""
    payload = copy.deepcopy(_canonical_manifest_payload())
    source = {
        "kind": "url",
        "uri": uri,
        "format": "json",
    }
    payload["sources"] = {source_name: source}
    manifest_path = tmp_path / "codegen.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="does not match"):
        load_codegen_manifest(manifest_path=manifest_path)


@pytest.mark.parametrize(
    ("path", "duplicates"),
    [
        (("registry_profiles", "local", "aliases"), ["basename", "basename"]),
        (("inputs", "primary", "sources"), ["source", "source"]),
        (
            ("inputs", "primary", "prepare", "schema_transforms"),
            ["drop-description", "drop-description"],
        ),
        (("products", "models", "inputs"), ["primary", "primary"]),
        (("products", "models", "generators"), ["python", "python"]),
    ],
)
def test_load_codegen_manifest_enforces_every_canonical_unique_items_constraint(
    tmp_path: Path,
    path: tuple[str, ...],
    duplicates: list[str],
) -> None:
    """Generated-model projections cannot admit duplicates forbidden by the schema."""
    payload = copy.deepcopy(_canonical_manifest_payload())
    current: object = payload
    for key in path[:-1]:
        assert isinstance(current, dict)
        current = current[key]
    assert isinstance(current, dict)
    current[path[-1]] = duplicates
    manifest_path = tmp_path / "codegen.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match="non-unique elements") as raised:
        load_codegen_manifest(manifest_path=manifest_path)

    assert f"$.{'.'.join(path)}" in str(raised.value)


@pytest.mark.parametrize(
    ("path", "value", "expected_path", "expected_message"),
    [
        (
            ("inputs", "primary", "sources"),
            ["source", 1],
            "$.inputs.primary.sources[1]",
            "is not of type 'string'",
        ),
        (("sources",), [], "$.sources", "is not of type 'object'"),
    ],
)
def test_load_codegen_manifest_validates_raw_shapes_before_models(
    tmp_path: Path,
    path: tuple[str, ...],
    value: object,
    expected_path: str,
    expected_message: str,
) -> None:
    """Canonical JSON Schema diagnostics precede generated-model construction."""
    payload = copy.deepcopy(_canonical_manifest_payload())
    current: object = payload
    for key in path[:-1]:
        assert isinstance(current, dict)
        current = current[key]
    assert isinstance(current, dict)
    current[path[-1]] = value
    manifest_path = tmp_path / "codegen.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=expected_message) as raised:
        load_codegen_manifest(manifest_path=manifest_path)

    assert expected_path in str(raised.value)
    assert "non-unique elements" not in str(raised.value)


@pytest.mark.parametrize("composition", ["anyOf", "oneOf"])
def test_manifest_validation_uses_standard_union_semantics(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    composition: str,
) -> None:
    """A nonmatching union branch cannot invalidate a matching alternative."""
    schema_path = tmp_path / "union.schema.json"
    schema_path.write_text(
        json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            composition: [
                {
                    "properties": {
                        "kind": {"const": "first"},
                        "value": {"pattern": "^first$"},
                    },
                    "required": ["kind", "value"],
                },
                {
                    "properties": {
                        "kind": {"const": "second"},
                        "value": {"pattern": "^second$"},
                    },
                    "required": ["kind", "value"],
                },
            ],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(codegen_lockfile, "_CODEGEN_MANIFEST_SCHEMA_PATH", schema_path)

    codegen_lockfile._validate_canonical_constraints({
        "kind": "second",
        "value": "second",
    })


def test_manifest_validation_enforces_exactly_one_oneof_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The maintained validator retains oneOf's exactly-one semantics."""
    schema_path = tmp_path / "oneof.schema.json"
    schema_path.write_text(
        json.dumps({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "oneOf": [{"type": "string"}, {"minLength": 1}],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(codegen_lockfile, "_CODEGEN_MANIFEST_SCHEMA_PATH", schema_path)

    with pytest.raises(ValueError, match="is valid under each of"):
        codegen_lockfile._validate_canonical_constraints("value")


@pytest.mark.parametrize(
    ("uri", "expected_message", "expected_path"),
    [
        ("https://", "Input should be a valid URL", None),
        ("https://exa mple.com/schema.json", "is not a 'uri'", "$.sources.source.uri"),
    ],
)
def test_load_codegen_manifest_asserts_uri_and_url_constraints(
    tmp_path: Path,
    uri: str,
    expected_message: str,
    expected_path: str | None,
) -> None:
    """Raw URI format checks and model URL checks preserve the HTTPS contract."""
    payload = copy.deepcopy(_canonical_manifest_payload())
    payload["sources"] = {"source": {"kind": "url", "uri": uri, "format": "json"}}
    manifest_path = tmp_path / "codegen.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValueError, match=expected_message) as raised:
        load_codegen_manifest(manifest_path=manifest_path)

    if expected_path is not None:
        assert expected_path in str(raised.value)


def test_load_codegen_manifest_rejects_raw_schema_errors_before_model_construction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raw canonical validation runs before the generated Pydantic model."""
    payload = copy.deepcopy(_canonical_manifest_payload())
    payload["unexpected"] = True
    manifest_path = tmp_path / "codegen.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    def _unexpected_model_construction(_payload: object) -> None:
        raise AssertionError("generated model must not construct an invalid manifest")

    monkeypatch.setattr(
        codegen_lockfile.CodegenManifest,
        "model_validate",
        _unexpected_model_construction,
    )
    with pytest.raises(ValueError, match="Additional properties are not allowed"):
        load_codegen_manifest(manifest_path=manifest_path)


def test_build_http_headers_uses_extended_github_token_resolution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve GitHub auth consistently for lockfile fetches."""
    captured: dict[str, object] = {}

    def _resolve_github_token(**kwargs: object) -> str:
        captured["resolve"] = kwargs
        return "gh-token"

    def _build_github_headers(url: str, **kwargs: object) -> dict[str, str]:
        captured["url"] = url
        captured["headers"] = kwargs
        return {"Authorization": "Bearer gh-token"}

    monkeypatch.setattr(
        codegen_lockfile.http_utils,
        "resolve_github_token",
        _resolve_github_token,
    )
    monkeypatch.setattr(
        codegen_lockfile.http_utils,
        "build_github_headers",
        _build_github_headers,
    )

    headers = codegen_lockfile._build_http_headers("https://api.github.com/repos/x/y")

    assert headers == {"Authorization": "Bearer gh-token"}
    assert captured["url"] == "https://api.github.com/repos/x/y"
    assert captured["resolve"] == {
        "allow_keyring": True,
        "allow_netrc": True,
    }
    assert captured["headers"] == {
        "token": "gh-token",
        "user_agent": "nixcfg-codegen-lockfile",
    }


def test_write_codegen_lockfile_hashes_directory_sources_deterministically(
    tmp_path: Path,
) -> None:
    """Hash matched directory contents and emit canonical JSON."""
    schemas_dir = tmp_path / "schemas"
    nested_dir = schemas_dir / "nested"
    nested_dir.mkdir(parents=True)
    root_bytes = b'{"title":"Root"}\n'
    child_bytes = b'{"title":"Child"}\n'
    ignored_bytes = b"ignore me\n"
    (schemas_dir / "root.json").write_bytes(root_bytes)
    (nested_dir / "child.json").write_bytes(child_bytes)
    (schemas_dir / "ignored.txt").write_bytes(ignored_bytes)

    manifest_path = tmp_path / "codegen.yaml"
    _write_manifest(
        manifest_path,
        source_block="""  source:
    kind: directory
    path: schemas
    include:
      - "**/*.json"
    format: json
""",
        entrypoint="./root.json",
    )

    output_path = write_codegen_lockfile(manifest_path=manifest_path)
    rendered = output_path.read_text(encoding="utf-8")
    expected_sha256 = _directory_content_sha256({
        "nested/child.json": child_bytes,
        "root.json": root_bytes,
    })

    assert output_path == tmp_path / "codegen.lock.json"
    assert rendered == (
        "{"
        '"manifest_path":"codegen.yaml",'
        '"sources":{"source":{'
        f'"content_sha256":"{expected_sha256}",'
        '"kind":"directory",'
        '"path":"schemas"'
        "}},"
        '"version":1'
        "}\n"
    )


def test_directory_sources_reject_root_and_nested_directory_symlinks(
    tmp_path: Path,
) -> None:
    """Resolve no directory-source identity until its symlink boundary is checked."""
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "root.json").write_text("{}\n", encoding="utf-8")
    linked_root = tmp_path / "linked-schemas"
    linked_root.symlink_to(schemas, target_is_directory=True)

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        codegen_lockfile._build_locked_directory_source(
            manifest_dir=tmp_path,
            source_name="local",
            source={"path": "linked-schemas"},
            include_metadata=False,
        )
    with pytest.raises(RuntimeError, match="must not be a symlink"):
        codegen_lockfile._iter_materialized_directory_files(
            source_root=linked_root,
            include_patterns=(),
        )

    outside = tmp_path / "outside"
    outside.mkdir()
    (schemas / "linked-directory").symlink_to(outside, target_is_directory=True)
    with pytest.raises(RuntimeError, match="unsupported symlink"):
        codegen_lockfile._iter_materialized_directory_files(
            source_root=schemas,
            include_patterns=(),
        )


def test_build_codegen_lockfile_omits_url_metadata_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Default reproducible mode omits volatile fetch metadata."""
    manifest_path = tmp_path / "codegen.yaml"
    _write_manifest(
        manifest_path,
        source_block="""  source:
    kind: url
    uri: https://example.com/schema.json
    format: json
""",
        entrypoint="https://example.com/schema.json",
    )

    monkeypatch.setattr(
        "lib.schema_codegen.lockfile._fetch_https_bytes",
        lambda url: (
            b'{"title":"Remote"}\n',
            {"etag": '"abc"', "last-modified": "Wed, 04 Mar 2026 00:25:01 GMT"},
        ),
    )

    lockfile = build_codegen_lockfile(manifest_path=manifest_path)
    rendered = render_codegen_lockfile(lockfile)
    payload = json.loads(rendered)
    source = payload["sources"]["source"]

    assert isinstance(lockfile, CodegenLockfile)
    assert "generated_at" not in payload
    assert "fetched_at" not in source
    assert "etag" not in source
    assert "last_modified" not in source
    assert source["kind"] == "url"
    assert source["uri"] == "https://example.com/schema.json"
    assert source["sha256"] == hashlib.sha256(b'{"title":"Remote"}\n').hexdigest()


def test_build_codegen_lockfile_can_include_optional_fetch_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Annotated mode includes informational timestamps and HTTP metadata."""
    manifest_path = tmp_path / "codegen.yaml"
    _write_manifest(
        manifest_path,
        source_block="""  source:
    kind: url
    uri: https://example.com/schema.json
    format: json
""",
        entrypoint="https://example.com/schema.json",
    )

    timestamp = datetime(2026, 3, 21, 12, 34, 56, tzinfo=UTC)
    monkeypatch.setattr("lib.schema_codegen.lockfile._utcnow", lambda: timestamp)
    monkeypatch.setattr(
        "lib.schema_codegen.lockfile._fetch_https_bytes",
        lambda url: (
            b'{"title":"Remote"}\n',
            {"etag": '"abc"', "last-modified": "Wed, 04 Mar 2026 00:25:01 GMT"},
        ),
    )

    payload = json.loads(
        render_codegen_lockfile(
            build_codegen_lockfile(
                manifest_path=manifest_path,
                include_metadata=True,
            )
        )
    )
    source = payload["sources"]["source"]

    assert payload["generated_at"] == "2026-03-21T12:34:56Z"
    assert source["fetched_at"] == "2026-03-21T12:34:56Z"
    assert source["etag"] == '"abc"'
    assert source["last_modified"] == "Wed, 04 Mar 2026 00:25:01 GMT"


def test_build_codegen_lockfile_resolves_github_raw_sources_without_provenance_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resolve GitHub refs to immutable SHAs and omit provenance in reproducible mode."""
    manifest_path = tmp_path / "codegen.yaml"
    _write_manifest(
        manifest_path,
        source_block="""  source:
    kind: github-raw
    owner: actions
    repo: languageservices
    ref: release-v0.3.49
    path: workflow-parser/src/workflow-v1.0.json
    format: json
    metadata:
      tag: release-v0.3.49
      package: "@actions/workflow-parser"
      package_version: 0.3.49
""",
        entrypoint="./workflow-v1.0.json",
    )

    resolved_sha = "83de320ba99ee2bdbb14a2869462a8033714cd96"
    expected_uri = (
        "https://raw.githubusercontent.com/actions/languageservices/"
        f"{resolved_sha}/workflow-parser/src/workflow-v1.0.json"
    )
    monkeypatch.setattr(
        "lib.schema_codegen.lockfile._resolve_github_commit",
        lambda owner, repo, ref: resolved_sha,
    )
    monkeypatch.setattr(
        "lib.schema_codegen.lockfile._fetch_https_bytes",
        lambda url: (
            (b"{}\n", {})
            if url == expected_uri
            else (_ for _ in ()).throw(AssertionError(f"unexpected fetch URL: {url}"))
        ),
    )

    rendered = render_codegen_lockfile(
        build_codegen_lockfile(manifest_path=manifest_path)
    )
    payload = json.loads(rendered)
    source = payload["sources"]["source"]

    assert source == {
        "kind": "github-raw",
        "owner": "actions",
        "path": "workflow-parser/src/workflow-v1.0.json",
        "ref": resolved_sha,
        "repo": "languageservices",
        "sha256": hashlib.sha256(b"{}\n").hexdigest(),
        "uri": expected_uri,
    }


def test_github_raw_source_normalizes_and_percent_encodes_repository_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep the logical repository path identical to the fetched raw URL path."""
    resolved_sha = "a" * 40
    monkeypatch.setattr(
        codegen_lockfile,
        "_resolve_github_commit",
        lambda *_args: resolved_sha,
    )
    fetched: list[str] = []
    monkeypatch.setattr(
        codegen_lockfile,
        "_fetch_https_bytes",
        lambda url: fetched.append(url) or (b"{}\n", {}),
    )

    locked = codegen_lockfile._build_locked_github_raw_source(
        source_name="source",
        source={
            "owner": "owner",
            "repo": "repo",
            "ref": "main",
            "path": "./schemas/nested/../workflow final?draft#v1.json",
        },
        include_metadata=False,
    )

    expected_uri = (
        f"https://raw.githubusercontent.com/owner/repo/{resolved_sha}/"
        "schemas/workflow%20final%3Fdraft%23v1.json"
    )
    assert locked["path"] == "schemas/workflow final?draft#v1.json"
    assert locked["uri"] == expected_uri
    assert fetched == [expected_uri]


@pytest.mark.parametrize(
    "path",
    [
        "../outside.json",
        "schemas/../../outside.json",
        "/absolute.json",
        ".",
        "schemas/..",
    ],
)
def test_github_raw_source_rejects_non_repository_file_paths(path: str) -> None:
    """Reject absolute, empty, and root-escaping repository paths before fetching."""
    with pytest.raises(RuntimeError, match="GitHub raw source path"):
        codegen_lockfile._build_locked_github_raw_source(
            source_name="source",
            source={
                "owner": "owner",
                "repo": "repo",
                "ref": "main",
                "path": path,
            },
            include_metadata=False,
        )


def test_build_codegen_lockfile_can_include_github_raw_provenance_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Annotated mode includes GitHub raw fetch timestamps and provenance fields."""
    manifest_path = tmp_path / "codegen.yaml"
    _write_manifest(
        manifest_path,
        source_block="""  source:
    kind: github-raw
    owner: actions
    repo: languageservices
    ref: release-v0.3.49
    path: workflow-parser/src/workflow-v1.0.json
    format: json
    metadata:
      tag: release-v0.3.49
      package: "@actions/workflow-parser"
      package_version: 0.3.49
""",
        entrypoint="./workflow-v1.0.json",
    )

    timestamp = datetime(2026, 3, 21, 12, 34, 56, tzinfo=UTC)
    resolved_sha = "83de320ba99ee2bdbb14a2869462a8033714cd96"
    expected_uri = (
        "https://raw.githubusercontent.com/actions/languageservices/"
        f"{resolved_sha}/workflow-parser/src/workflow-v1.0.json"
    )
    monkeypatch.setattr("lib.schema_codegen.lockfile._utcnow", lambda: timestamp)
    monkeypatch.setattr(
        "lib.schema_codegen.lockfile._resolve_github_commit",
        lambda owner, repo, ref: resolved_sha,
    )
    monkeypatch.setattr(
        "lib.schema_codegen.lockfile._fetch_https_bytes",
        lambda url: (
            (b"{}\n", {})
            if url == expected_uri
            else (_ for _ in ()).throw(AssertionError(f"unexpected fetch URL: {url}"))
        ),
    )

    rendered = render_codegen_lockfile(
        build_codegen_lockfile(manifest_path=manifest_path, include_metadata=True)
    )
    payload = json.loads(rendered)
    source = payload["sources"]["source"]

    assert source["fetched_at"] == "2026-03-21T12:34:56Z"
    assert source["tag"] == "release-v0.3.49"
    assert source["package"] == "@actions/workflow-parser"
    assert source["package_version"] == "0.3.49"


def test_write_codegen_lockfile_normalizes_manifest_path_against_custom_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Store manifest_path relative to the chosen lockfile location."""
    manifest_path = tmp_path / "manifests" / "codegen.yaml"
    manifest_path.parent.mkdir()
    _write_manifest(
        manifest_path,
        source_block="""  source:
    kind: url
    uri: https://example.com/schema.json
    format: json
""",
        entrypoint="https://example.com/schema.json",
    )
    monkeypatch.setattr(
        "lib.schema_codegen.lockfile._fetch_https_bytes",
        lambda url: (b"{}\n", {}),
    )

    output_path = tmp_path / "locks" / "codegen.lock.json"
    write_codegen_lockfile(manifest_path=manifest_path, lockfile_path=output_path)
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["manifest_path"] == "../manifests/codegen.yaml"


def test_write_codegen_lockfile_preserves_existing_file_on_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A failed atomic replacement cannot truncate the previous lockfile."""
    schemas = tmp_path / "schemas"
    schemas.mkdir()
    (schemas / "root.json").write_text("{}\n", encoding="utf-8")
    manifest_path = tmp_path / "codegen.yaml"
    _write_manifest(
        manifest_path,
        source_block="""  source:
    kind: directory
    path: schemas
    format: json
""",
        entrypoint="./root.json",
    )
    output_path = tmp_path / "codegen.lock.json"
    output_path.write_text("previous-lockfile\n", encoding="utf-8")
    replace = Path.replace

    def _fail_replace(path: Path, target: Path) -> Path:
        if path.parent == output_path.parent and path.name.startswith(
            ".codegen.lock.json."
        ):
            raise OSError("injected replace failure")
        return replace(path, target)

    monkeypatch.setattr(Path, "replace", _fail_replace)
    with pytest.raises(OSError, match="injected replace failure"):
        write_codegen_lockfile(manifest_path=manifest_path)

    assert output_path.read_text(encoding="utf-8") == "previous-lockfile\n"
    assert list(tmp_path.glob(".codegen.lock.json.*.tmp")) == []


def test_write_codegen_lockfile_matches_shared_golden_fixture(tmp_path: Path) -> None:
    """Keep Python lockfile bytes aligned with the shared cross-language fixture."""
    fixture_root = REPO_ROOT / "schemas/codegen/testdata/lockfile-golden"
    working_root = tmp_path / "fixture"
    shutil.copytree(fixture_root, working_root)

    output_path = write_codegen_lockfile(manifest_path=working_root / "codegen.yaml")
    rendered = output_path.read_text(encoding="utf-8")
    expected = (fixture_root / "expected.codegen.lock.json").read_text(encoding="utf-8")

    assert rendered == expected
