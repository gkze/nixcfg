"""Tests for Bun source-package lock validation."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from lib.update import bun_lock


def _tarball_bytes(
    package_json: dict[str, object], *, member: str = "package/package.json"
) -> bytes:
    buffer = io.BytesIO()
    payload = json.dumps(package_json).encode("utf-8")
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        info = tarfile.TarInfo(member)
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    return buffer.getvalue()


def test_load_bun_lock_normalizes_textual_json(tmp_path: Path) -> None:
    """Parse Bun's textual lockfile format with trailing commas."""
    lock_file = tmp_path / "bun.lock"
    lock_file.write_text(
        '{"overrides":{"dep":"https://example.test/dep.tgz",},"packages":{},}\n',
        encoding="utf-8",
    )

    loaded = bun_lock._load_bun_lock(lock_file)
    assert loaded["overrides"] == {"dep": "https://example.test/dep.tgz"}


def test_helper_error_paths_and_url_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cover helper predicates, adapter failures, and URL fetch guards."""
    assert bun_lock._normalize_textual_json('{"x": [1,],}\n') == '{"x": [1]}\n'
    assert bun_lock._is_source_url("https://example.test/pkg.tgz") is True
    assert bun_lock._is_source_url("github:owner/repo") is True
    assert bun_lock._is_source_url("workspace:*") is False
    assert bun_lock._is_exact_version_spec("") is False
    assert bun_lock._is_exact_version_spec("latest") is False
    assert bun_lock._is_exact_version_spec("^1.0.0") is False
    assert bun_lock._is_exact_version_spec("1.2.3-superset.1") is True
    assert bun_lock._package_json_member_name(["package/package.json"]) == (
        "package/package.json"
    )
    assert bun_lock._package_json_member_name(["package.json"]) == "package.json"
    assert bun_lock._package_json_member_name(["nested/pkg/package.json"]) == (
        "nested/pkg/package.json"
    )
    assert bun_lock._package_json_member_name(["README.md"]) is None

    with pytest.raises(TypeError, match="Expected JSON object"):
        bun_lock._as_object_dict([], context="ctx")
    with pytest.raises(TypeError, match="Expected JSON array"):
        bun_lock._as_object_list({}, context="ctx")
    with pytest.raises(TypeError, match="Expected string field 'name'"):
        bun_lock._get_required_str({}, "name", context="ctx")
    with pytest.raises(TypeError, match="Expected string field 'name'"):
        bun_lock._get_required_str({"name": 1}, "name", context="ctx")
    with pytest.raises(ValueError, match="Unsupported source package URL scheme"):
        bun_lock._fetch_url_bytes("file:///tmp/demo.tgz")

    class _Response:
        def __enter__(self) -> _Response:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def read(self) -> bytes:
            return b"payload"

    monkeypatch.setattr(
        bun_lock.urllib.request,
        "urlopen",
        lambda url: _Response() if url == "https://example.test/pkg.tgz" else None,
    )
    assert bun_lock._fetch_url_bytes("https://example.test/pkg.tgz") == b"payload"


def test_load_bun_lock_reports_read_and_parse_failures(tmp_path: Path) -> None:
    """Surface lockfile read and textual JSON parse failures."""
    with pytest.raises(OSError, match="Failed to read bun lockfile"):
        bun_lock._load_bun_lock(tmp_path / "missing.lock")

    bad_lock = tmp_path / "bun.lock"
    bad_lock.write_text("{]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid textual bun.lock JSON"):
        bun_lock._load_bun_lock(bad_lock)


def test_read_source_package_manifest_extracts_package_json() -> None:
    """Read name and version from a source tarball package."""
    archive = _tarball_bytes({"name": "dep", "version": "1.2.3"})
    manifest = bun_lock._read_source_package_manifest(
        "https://example.test/dep.tgz",
        fetch_bytes=lambda _url: archive,
    )
    assert manifest == bun_lock.SourcePackageManifest(
        name="dep",
        version="1.2.3",
        url="https://example.test/dep.tgz",
    )


def test_read_source_package_manifest_rejects_missing_package_json() -> None:
    """Reject tarballs that do not carry a package manifest."""
    archive = _tarball_bytes({"name": "dep", "version": "1.2.3"}, member="README.md")
    with pytest.raises(ValueError, match="does not contain package.json"):
        bun_lock._read_source_package_manifest(
            "https://example.test/dep.tgz",
            fetch_bytes=lambda _url: archive,
        )


def test_read_source_package_manifest_rejects_extract_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject archives whose package.json member cannot be extracted."""

    class _Archive:
        def __enter__(self) -> _Archive:
            return self

        def __exit__(self, *_args: object) -> bool:
            return False

        def getnames(self) -> list[str]:
            return ["package/package.json"]

        def extractfile(self, _name: str) -> None:
            return None

    monkeypatch.setattr(bun_lock.tarfile, "open", lambda **_kwargs: _Archive())
    with pytest.raises(ValueError, match="Failed to extract package.json"):
        bun_lock._read_source_package_manifest(
            "https://example.test/dep.tgz",
            fetch_bytes=lambda _url: b"irrelevant",
        )


def test_validate_source_package_exact_versions_accepts_matching_override(
    tmp_path: Path,
) -> None:
    """Allow exact source dependencies when the override matches their tarball."""
    lock_file = tmp_path / "bun.lock"
    lock_file.write_text(
        """
        {
          "overrides": {
            "dep": "https://example.test/dep.tgz",
          },
          "packages": {
            "dep": ["dep@https://example.test/dep.tgz", {}, "sha512-x"],
            "parent": [
              "parent@https://example.test/parent.tgz",
              {
                "dependencies": {
                  "dep": "1.2.3",
                  "ignored": "^4.0.0"
                }
              },
              "sha512-y"
            ]
          }
        }
        """,
        encoding="utf-8",
    )

    bun_lock.validate_source_package_exact_versions(
        lock_file,
        fetch_bytes=lambda _url: _tarball_bytes({"name": "dep", "version": "1.2.3"}),
    )


def test_validate_source_package_exact_versions_rejects_version_mismatch(
    tmp_path: Path,
) -> None:
    """Reject source overrides that contradict exact dependency versions."""
    lock_file = tmp_path / "bun.lock"
    lock_file.write_text(
        """
        {
          "overrides": {
            "@mastra/core": "https://example.test/mastra-core.tgz",
            "mastracode": "https://example.test/mastracode.tgz"
          },
          "packages": {
            "@mastra/core": ["@mastra/core@https://example.test/mastra-core.tgz", {}, "sha512-a"],
            "mastracode": [
              "mastracode@https://example.test/mastracode.tgz",
              {
                "dependencies": {
                  "@mastra/core": "1.8.0-superset.1"
                }
              },
              "sha512-b"
            ]
          }
        }
        """,
        encoding="utf-8",
    )

    archives = {
        "https://example.test/mastra-core.tgz": _tarball_bytes({
            "name": "@mastra/core",
            "version": "1.8.0-superset.2",
        }),
        "https://example.test/mastracode.tgz": _tarball_bytes({
            "name": "mastracode",
            "version": "0.4.0-superset.12",
        }),
    }

    with pytest.raises(RuntimeError, match="exact-version mismatch"):
        bun_lock.validate_source_package_exact_versions(
            lock_file,
            fetch_bytes=lambda url: archives[url],
        )


def test_validate_source_package_exact_versions_rejects_name_mismatch(
    tmp_path: Path,
) -> None:
    """Reject source overrides whose tarball manifest names do not match."""
    lock_file = tmp_path / "bun.lock"
    lock_file.write_text(
        """
        {
          "overrides": {
            "dep": "https://example.test/dep.tgz"
          },
          "packages": {
            "dep": ["dep@https://example.test/dep.tgz", {}, "sha512-a"]
          }
        }
        """,
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="package name mismatch"):
        bun_lock.validate_source_package_exact_versions(
            lock_file,
            fetch_bytes=lambda _url: _tarball_bytes({
                "name": "different",
                "version": "1.2.3",
            }),
        )


def test_validate_source_package_exact_versions_fetches_each_manifest_once(
    tmp_path: Path,
) -> None:
    """Cache source package manifests across override and dependency checks."""
    lock_file = tmp_path / "bun.lock"
    lock_file.write_text(
        """
        {
          "overrides": {
            "dep": "https://example.test/dep.tgz"
          },
          "packages": {
            "dep": ["dep@https://example.test/dep.tgz", {}, "sha512-a"],
            "parent-a": [
              "parent-a@https://example.test/parent-a.tgz",
              {
                "dependencies": {
                  "dep": "1.2.3"
                }
              },
              "sha512-b"
            ],
            "parent-b": [
              "parent-b@https://example.test/parent-b.tgz",
              {
                "optionalDependencies": {
                  "dep": "1.2.3"
                }
              },
              "sha512-c"
            ]
          }
        }
        """,
        encoding="utf-8",
    )

    calls: list[str] = []
    archive = _tarball_bytes({"name": "dep", "version": "1.2.3"})

    def _fetch(url: str) -> bytes:
        calls.append(url)
        return archive

    bun_lock.validate_source_package_exact_versions(lock_file, fetch_bytes=_fetch)
    assert calls == ["https://example.test/dep.tgz"]


def test_validate_source_package_exact_versions_skips_irrelevant_shapes(
    tmp_path: Path,
) -> None:
    """Ignore non-list package entries and non-actionable dependency specs."""
    lock_file = tmp_path / "bun.lock"
    lock_file.write_text(
        """
        {
          "overrides": {
            "dep": "https://example.test/dep.tgz"
          },
          "packages": {
            "ignored-map": {"not": "a package entry"},
            "missing-metadata": ["missing-metadata@https://example.test/missing.tgz"],
            "wrong-metadata": ["wrong@https://example.test/wrong.tgz", ["oops"], "sha512-a"],
            "dep": ["dep@https://example.test/dep.tgz", {}, "sha512-b"],
            "parent": [
              "parent@https://example.test/parent.tgz",
              {
                "dependencies": {
                  "dep": 1,
                  "other": "1.2.3",
                  "range": "^2.0.0"
                }
              },
              "sha512-c"
            ]
          }
        }
        """,
        encoding="utf-8",
    )

    bun_lock.validate_source_package_exact_versions(
        lock_file,
        fetch_bytes=lambda _url: _tarball_bytes({"name": "dep", "version": "1.2.3"}),
    )


def test_read_source_package_manifest_rejects_invalid_archive() -> None:
    """Normalize malformed tarball content into ``ValueError``."""
    with pytest.raises(ValueError, match="Invalid source package archive metadata"):
        bun_lock._read_source_package_manifest(
            "https://example.test/dep.tgz",
            fetch_bytes=lambda _url: b"not a gzip tarball",
        )
