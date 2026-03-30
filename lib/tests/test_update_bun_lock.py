"""Tests for Bun source-package lock validation."""

from __future__ import annotations

import dataclasses
import io
import json
import subprocess
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

    attempts = {"count": 0}

    def _urlopen(url: str, *, timeout: float) -> _Response:
        assert timeout == bun_lock._FETCH_TIMEOUT_SECONDS
        if url != "https://example.test/pkg.tgz":
            msg = f"unexpected url: {url}"
            raise AssertionError(msg)
        if attempts["count"] == 0:
            attempts["count"] += 1
            raise OSError("temporary failure")
        return _Response()

    monkeypatch.setattr(bun_lock.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bun_lock.urllib.request, "urlopen", _urlopen)
    assert bun_lock._fetch_url_bytes("https://example.test/pkg.tgz") == b"payload"
    assert attempts["count"] == 1
    assert bun_lock._parse_github_release_asset("https://example.test/pkg.tgz") is None
    assert (
        bun_lock._rewrite_github_release_asset_version(
            "https://example.test/pkg.tgz",
            current_version="1.0.0",
            required_version="1.0.1",
            release_tag="release-v1",
        )
        is None
    )


def test_load_bun_lock_reports_read_and_parse_failures(tmp_path: Path) -> None:
    """Surface lockfile read and textual JSON parse failures."""
    with pytest.raises(OSError, match="Failed to read bun lockfile"):
        bun_lock._load_bun_lock(tmp_path / "missing.lock")

    bad_lock = tmp_path / "bun.lock"
    bad_lock.write_text("{]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid textual bun.lock JSON"):
        bun_lock._load_bun_lock(bad_lock)

    bad_json = tmp_path / "package.json"
    bad_json.write_text("{]\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON object in package.json"):
        bun_lock._load_json_object(bad_json, context="package.json")

    with pytest.raises(OSError, match="Failed to read package.json"):
        bun_lock._load_json_object(
            tmp_path / "missing-package.json", context="package.json"
        )


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


def test_json_and_github_release_helpers_round_trip(tmp_path: Path) -> None:
    """Round-trip JSON helpers and GitHub release asset rewriting."""
    payload_path = tmp_path / "package.json"
    bun_lock._write_json_object(payload_path, {"resolutions": {"dep": "demo"}})
    assert bun_lock._load_json_object(payload_path, context="package.json") == {
        "resolutions": {"dep": "demo"}
    }

    parsed = bun_lock._parse_github_release_asset(
        "https://github.com/org/repo/releases/download/tag-1/demo-1.0.0.tgz"
    )
    assert parsed == (
        "https",
        "github.com",
        "org",
        "repo",
        "tag-1",
        "demo-1.0.0.tgz",
    )
    assert (
        bun_lock._parse_github_release_asset(
            "https://github.com/org/repo/releases/list/tag-1/demo-1.0.0.tgz"
        )
        is None
    )
    assert (
        bun_lock._rewrite_github_release_asset_version(
            "https://github.com/org/repo/releases/download/tag-1/demo-1.0.0.tgz",
            current_version="1.0.0",
            required_version="1.0.1",
            release_tag="tag-2",
        )
        == "https://github.com/org/repo/releases/download/tag-2/demo-1.0.1.tgz"
    )
    assert (
        bun_lock._rewrite_github_release_asset_version(
            "https://github.com/org/repo/releases/download/tag-1/demo.tgz",
            current_version="1.0.0",
            required_version="1.0.1",
            release_tag="tag-2",
        )
        is None
    )


def test_fetch_url_bytes_reports_final_failure_and_zero_retry_runtime_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface terminal fetch failures and zero-retry configuration errors."""
    monkeypatch.setattr(bun_lock.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(bun_lock, "_FETCH_RETRIES", 1)
    monkeypatch.setattr(
        bun_lock.urllib.request,
        "urlopen",
        lambda _url, *, timeout: (_ for _ in ()).throw(OSError(timeout)),
    )
    with pytest.raises(OSError, match=str(bun_lock._FETCH_TIMEOUT_SECONDS)):
        bun_lock._fetch_url_bytes("https://example.test/pkg.tgz")

    monkeypatch.setattr(bun_lock, "_FETCH_RETRIES", 0)
    with pytest.raises(RuntimeError, match="Failed to fetch source package URL"):
        bun_lock._fetch_url_bytes("https://example.test/pkg.tgz")


def test_prepare_source_package_lock_skips_relock_when_valid(tmp_path: Path) -> None:
    """Return ``False`` without relocking when the lock is already valid."""
    calls: list[str] = []

    def _validate(path: Path) -> None:
        calls.append(f"validate:{path.name}")

    def _relock(_workspace: Path, _bun: str) -> None:
        msg = "relock should not run"
        raise AssertionError(msg)

    assert (
        bun_lock.prepare_source_package_lock(
            tmp_path,
            tmp_path / "bun.lock",
            validate=_validate,
            relock=_relock,
        )
        is False
    )
    assert calls == ["validate:bun.lock"]


def test_prepare_source_package_lock_relocks_after_validation_error(
    tmp_path: Path,
) -> None:
    """Relock once when validation reports a source-package mismatch."""
    calls: list[str] = []
    attempts = {"count": 0}

    def _validate(path: Path) -> None:
        calls.append(f"validate:{attempts['count']}:{path.name}")
        if attempts["count"] == 0:
            attempts["count"] += 1
            msg = "mismatch"
            raise bun_lock.BunSourcePackageValidationError(msg)

    def _relock(workspace: Path, bun_executable: str) -> None:
        calls.append(f"relock:{workspace.name}:{bun_executable}")

    assert (
        bun_lock.prepare_source_package_lock(
            tmp_path,
            tmp_path / "bun.lock",
            bun_executable="/nix/store/demo/bin/bun",
            validate=_validate,
            relock=_relock,
        )
        is True
    )
    assert calls == [
        "validate:0:bun.lock",
        f"relock:{tmp_path.name}:/nix/store/demo/bin/bun",
        "validate:1:bun.lock",
    ]


def test_prepare_source_package_lock_surfaces_relock_failure(tmp_path: Path) -> None:
    """Propagate relock command failures after an initial validation mismatch."""

    def _validate(_path: Path) -> None:
        msg = "mismatch"
        raise bun_lock.BunSourcePackageValidationError(msg)

    def _relock(_workspace: Path, _bun_executable: str) -> None:
        msg = "bun install failed"
        raise RuntimeError(msg)

    with pytest.raises(RuntimeError, match="bun install failed"):
        bun_lock.prepare_source_package_lock(
            tmp_path,
            tmp_path / "bun.lock",
            validate=_validate,
            relock=_relock,
        )


def test_prepare_source_package_lock_reraises_when_healing_is_unavailable(
    tmp_path: Path,
) -> None:
    """Reraise the validation error when relock cannot be healed."""
    attempts = {"count": 0}

    def _validate(_path: Path) -> None:
        attempts["count"] += 1
        msg = "mismatch"
        raise bun_lock.BunSourcePackageValidationError(msg)

    def _relock(_workspace: Path, _bun_executable: str) -> None:
        return None

    with pytest.raises(bun_lock.BunSourcePackageValidationError, match="mismatch"):
        bun_lock.prepare_source_package_lock(
            tmp_path,
            tmp_path / "bun.lock",
            validate=_validate,
            relock=_relock,
        )
    assert attempts["count"] == 2


def test_heal_package_json_source_resolutions_rewrites_github_release_candidate(
    tmp_path: Path,
) -> None:
    """Heal a mismatched source resolution using a sibling GitHub release asset."""
    bad_core_url = (
        "https://github.com/superset-sh/mastra/releases/download/"
        "mastracode-v0.4.0-superset.15/mastra-core-1.8.0-superset.2.tgz"
    )
    good_core_url = (
        "https://github.com/superset-sh/mastra/releases/download/"
        "mastracode-v0.4.0-superset.12/mastra-core-1.8.0-superset.1.tgz"
    )
    mastracode_url = (
        "https://github.com/superset-sh/mastra/releases/download/"
        "mastracode-v0.4.0-superset.12/mastracode-0.4.0-superset.12.tgz"
    )
    package_json_path = tmp_path / "package.json"
    package_json_path.write_text(
        json.dumps(
            {
                "name": "demo",
                "resolutions": {
                    "mastracode": mastracode_url,
                    "@mastra/core": bad_core_url,
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    lock_file = tmp_path / "bun.lock"
    lock_file.write_text(
        json.dumps(
            {
                "overrides": {
                    "@mastra/core": bad_core_url,
                    "mastracode": mastracode_url,
                },
                "packages": {
                    "@mastra/core": [
                        f"@mastra/core@{bad_core_url}",
                        {},
                        "sha512-a",
                    ],
                    "mastracode": [
                        f"mastracode@{mastracode_url}",
                        {"dependencies": {"@mastra/core": "1.8.0-superset.1"}},
                        "sha512-b",
                    ],
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    archives = {
        bad_core_url: _tarball_bytes({
            "name": "@mastra/core",
            "version": "1.8.0-superset.2",
        }),
        mastracode_url: _tarball_bytes({
            "name": "mastracode",
            "version": "0.4.0-superset.12",
        }),
        good_core_url: _tarball_bytes({
            "name": "@mastra/core",
            "version": "1.8.0-superset.1",
        }),
    }

    assert (
        bun_lock._heal_package_json_source_resolutions(
            package_json_path,
            lock_file,
            fetch_bytes=lambda url: archives[url],
        )
        is True
    )
    healed = json.loads(package_json_path.read_text(encoding="utf-8"))
    assert healed["resolutions"]["@mastra/core"] == good_core_url


def test_heal_package_json_source_resolutions_false_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return ``False`` for unhealable or no-op source resolution states."""
    package_json_path = tmp_path / "package.json"
    package_json_path.write_text(
        json.dumps({"resolutions": {"dep": "https://example.test/dep-1.0.0.tgz"}})
        + "\n",
        encoding="utf-8",
    )
    lock_file = tmp_path / "bun.lock"
    lock_file.write_text("{}\n", encoding="utf-8")

    mismatch = bun_lock.SourcePackageExactVersionMismatch(
        package_name="pkg",
        dependency_name="dep",
        required_version="1.0.1",
        current_version="1.0.0",
        dependency_url="https://github.com/org/repo/releases/download/tag-1/dep-1.0.0.tgz",
        package_url=None,
    )

    monkeypatch.setattr(
        bun_lock,
        "_collect_source_package_mismatches",
        lambda *_args, **_kwargs: (["error"], []),
    )
    assert (
        bun_lock._heal_package_json_source_resolutions(package_json_path, lock_file)
        is False
    )

    monkeypatch.setattr(
        bun_lock,
        "_collect_source_package_mismatches",
        lambda *_args, **_kwargs: ([], [mismatch]),
    )
    assert (
        bun_lock._heal_package_json_source_resolutions(package_json_path, lock_file)
        is False
    )

    mismatch_with_bad_package = dataclasses.replace(
        mismatch,
        package_url="https://example.test/not-a-release.tgz",
    )
    monkeypatch.setattr(
        bun_lock,
        "_collect_source_package_mismatches",
        lambda *_args, **_kwargs: ([], [mismatch_with_bad_package]),
    )
    assert (
        bun_lock._heal_package_json_source_resolutions(package_json_path, lock_file)
        is False
    )

    mismatch_with_bad_dependency = dataclasses.replace(
        mismatch,
        package_url="https://github.com/org/repo/releases/download/tag-1/pkg-1.0.0.tgz",
        dependency_url="https://github.com/org/repo/releases/download/tag-1/dep.tgz",
    )
    monkeypatch.setattr(
        bun_lock,
        "_collect_source_package_mismatches",
        lambda *_args, **_kwargs: ([], [mismatch_with_bad_dependency]),
    )
    assert (
        bun_lock._heal_package_json_source_resolutions(package_json_path, lock_file)
        is False
    )

    monkeypatch.setattr(
        bun_lock,
        "_collect_source_package_mismatches",
        lambda *_args, **_kwargs: (
            [],
            [
                dataclasses.replace(
                    mismatch,
                    package_url="https://github.com/org/repo/releases/download/tag-1/pkg-1.0.0.tgz",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        bun_lock,
        "_read_source_package_manifest",
        lambda _url, **_kwargs: bun_lock.SourcePackageManifest(
            name="dep",
            version="9.9.9",
            url="https://example.test/wrong.tgz",
        ),
    )
    assert (
        bun_lock._heal_package_json_source_resolutions(package_json_path, lock_file)
        is False
    )

    mismatch_a = dataclasses.replace(
        mismatch,
        dependency_name="dep-a",
        package_url="https://github.com/org/repo/releases/download/tag-1/pkg-a-1.0.0.tgz",
        dependency_url="https://github.com/org/repo/releases/download/tag-1/dep-a-1.0.0.tgz",
    )
    mismatch_b = dataclasses.replace(
        mismatch,
        dependency_name="dep-a",
        package_url="https://github.com/org/repo/releases/download/tag-2/pkg-b-1.0.0.tgz",
        dependency_url="https://github.com/org/repo/releases/download/tag-1/dep-a-1.0.0.tgz",
    )
    monkeypatch.setattr(
        bun_lock,
        "_collect_source_package_mismatches",
        lambda *_args, **_kwargs: ([], [mismatch_a, mismatch_b]),
    )
    monkeypatch.setattr(
        bun_lock,
        "_read_source_package_manifest",
        lambda url, **_kwargs: bun_lock.SourcePackageManifest(
            name="dep-a",
            version="1.0.1",
            url=url,
        ),
    )
    assert (
        bun_lock._heal_package_json_source_resolutions(package_json_path, lock_file)
        is False
    )

    package_json_path.write_text(
        json.dumps({
            "resolutions": {
                "dep": "https://github.com/org/repo/releases/download/tag-1/dep-1.0.1.tgz"
            }
        })
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        bun_lock,
        "_collect_source_package_mismatches",
        lambda *_args, **_kwargs: (
            [],
            [
                dataclasses.replace(
                    mismatch,
                    package_url="https://github.com/org/repo/releases/download/tag-1/pkg-1.0.0.tgz",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        bun_lock,
        "_read_source_package_manifest",
        lambda url, **_kwargs: bun_lock.SourcePackageManifest(
            name="dep",
            version="1.0.1",
            url=url,
        ),
    )
    assert (
        bun_lock._heal_package_json_source_resolutions(package_json_path, lock_file)
        is False
    )


def test_run_bun_lockfile_refresh_success_and_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run bun relock subprocess with isolated env and surface failures."""
    calls: list[tuple[list[str], Path, dict[str, str]]] = []

    def _success(**kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append((kwargs["args"], kwargs["cwd"], kwargs["env"]))
        return subprocess.CompletedProcess(kwargs["args"], 0, "", "")

    monkeypatch.setattr(
        bun_lock.subprocess,
        "run",
        lambda *args, **kwargs: _success(args=args[0], **kwargs),
    )
    bun_lock._run_bun_lockfile_refresh(tmp_path, "bun")
    args, cwd, env = calls[0]
    assert args == ["bun", "install", "--lockfile-only", "--ignore-scripts"]
    assert cwd == tmp_path
    assert all(
        key in env
        for key in (
            "HOME",
            "XDG_CACHE_HOME",
            "XDG_CONFIG_HOME",
            "XDG_DATA_HOME",
            "XDG_STATE_HOME",
        )
    )

    monkeypatch.setattr(
        bun_lock.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, "", "boom"),
    )
    with pytest.raises(RuntimeError, match="Failed to regenerate bun.lock"):
        bun_lock._run_bun_lockfile_refresh(tmp_path, "bun")


def test_prepare_source_package_lock_heals_after_relock_still_mismatches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Heal source resolutions and relock when a plain relock is insufficient."""
    package_json_path = tmp_path / "package.json"
    package_json_path.write_text('{"name":"demo"}\n', encoding="utf-8")
    calls: list[str] = []
    attempts = {"count": 0}

    def _validate(path: Path) -> None:
        calls.append(f"validate:{attempts['count']}:{path.name}")
        if attempts["count"] < 2:
            attempts["count"] += 1
            msg = "mismatch"
            raise bun_lock.BunSourcePackageValidationError(msg)

    def _relock(workspace: Path, bun_executable: str) -> None:
        calls.append(f"relock:{workspace.name}:{bun_executable}")

    monkeypatch.setattr(
        bun_lock,
        "_heal_package_json_source_resolutions",
        lambda package_json, lock_file, fetch_bytes=bun_lock._fetch_url_bytes: (
            calls.append(f"heal:{package_json.name}:{lock_file.name}") or True
        ),
    )

    assert (
        bun_lock.prepare_source_package_lock(
            tmp_path,
            tmp_path / "bun.lock",
            validate=_validate,
            relock=_relock,
        )
        is True
    )
    assert calls == [
        "validate:0:bun.lock",
        f"relock:{tmp_path.name}:bun",
        "validate:1:bun.lock",
        "heal:package.json:bun.lock",
        f"relock:{tmp_path.name}:bun",
        "validate:2:bun.lock",
    ]
