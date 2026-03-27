"""Tests for canonical codegen manifest lockfile materialization."""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from lib.schema_codegen.lockfile import (
    build_codegen_lockfile,
    render_codegen_lockfile,
    write_codegen_lockfile,
)
from lib.schema_codegen.models._generated import CodegenLockfile
from lib.tests._assertions import check


class _MonkeyPatchLike:
    def setattr(self, target: str, value: object) -> None: ...


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

    check(output_path == tmp_path / "codegen.lock.json")
    check(
        rendered
        == (
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

    check(isinstance(lockfile, CodegenLockfile))
    check("generated_at" not in payload)
    check("fetched_at" not in source)
    check("etag" not in source)
    check("last_modified" not in source)
    check(source["kind"] == "url")
    check(source["uri"] == "https://example.com/schema.json")
    check(source["sha256"] == hashlib.sha256(b'{"title":"Remote"}\n').hexdigest())


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

    check(payload["generated_at"] == "2026-03-21T12:34:56Z")
    check(source["fetched_at"] == "2026-03-21T12:34:56Z")
    check(source["etag"] == '"abc"')
    check(source["last_modified"] == "Wed, 04 Mar 2026 00:25:01 GMT")


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

    check(
        source
        == {
            "kind": "github-raw",
            "owner": "actions",
            "path": "workflow-parser/src/workflow-v1.0.json",
            "ref": resolved_sha,
            "repo": "languageservices",
            "sha256": hashlib.sha256(b"{}\n").hexdigest(),
            "uri": expected_uri,
        }
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

    check(source["fetched_at"] == "2026-03-21T12:34:56Z")
    check(source["tag"] == "release-v0.3.49")
    check(source["package"] == "@actions/workflow-parser")
    check(source["package_version"] == "0.3.49")


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

    check(payload["manifest_path"] == "../manifests/codegen.yaml")


def test_write_codegen_lockfile_matches_shared_golden_fixture(tmp_path: Path) -> None:
    """Keep Python lockfile bytes aligned with the shared cross-language fixture."""
    repo_root = Path(__file__).resolve().parents[2]
    fixture_root = repo_root / "schemas/codegen/testdata/lockfile-golden"
    working_root = tmp_path / "fixture"
    shutil.copytree(fixture_root, working_root)

    output_path = write_codegen_lockfile(manifest_path=working_root / "codegen.yaml")
    rendered = output_path.read_text(encoding="utf-8")
    expected = (fixture_root / "expected.codegen.lock.json").read_text(encoding="utf-8")

    check(rendered == expected)
