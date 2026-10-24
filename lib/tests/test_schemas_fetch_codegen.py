"""Tests for schema fetch/codegen helper modules."""

import base64
import json
from datetime import UTC
from typing import TYPE_CHECKING

import pytest

from lib.nix.schemas import _fetch as fetch

if TYPE_CHECKING:
    from pathlib import Path


def _as_object_dict(value: object, *, context: str) -> dict[str, object]:
    if not isinstance(value, dict):
        msg = f"Expected JSON object for {context}"
        raise TypeError(msg)
    return value


def test_fetch_unwrap_gh_token_variants() -> None:
    """Run this test case."""
    plain = " ghp_plain \n"
    assert object.__getattribute__(fetch, "_unwrap_gh_token")(plain) == "ghp_plain"

    encoded = base64.b64encode(b"ghp_encoded\n").decode()
    wrapped = f"go-keyring-base64:{encoded}"
    assert object.__getattribute__(fetch, "_unwrap_gh_token")(wrapped) == "ghp_encoded"


def test_fetch_resolve_github_token_prefers_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setattr(
        fetch.http_utils.keyring,
        "get_password",
        lambda *_a, **_k: "ignored",
    )
    assert object.__getattribute__(fetch, "_resolve_github_token")() == "env-token"


def test_fetch_resolve_github_token_from_keyring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        fetch.http_utils.keyring,
        "get_password",
        lambda *_a, **_k: " keyring ",
    )
    assert object.__getattribute__(fetch, "_resolve_github_token")() == "keyring"


def test_fetch_resolve_github_token_keyring_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)

    def _boom(*_a: object, **_k: object) -> str:
        msg = "no keyring"
        raise RuntimeError(msg)

    monkeypatch.setattr(fetch.http_utils.keyring, "get_password", _boom)
    assert object.__getattribute__(fetch, "_resolve_github_token")() is None


def test_fetch_get_github_token_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    calls = 0

    def _resolve() -> str:
        nonlocal calls
        calls += 1
        return "cached"

    fetch.__dict__["_GITHUB_TOKEN"] = None
    monkeypatch.setattr(fetch, "_resolve_github_token", _resolve)

    assert object.__getattribute__(fetch, "_get_github_token")() == "cached"
    assert object.__getattribute__(fetch, "_get_github_token")() == "cached"
    assert calls == 1


def test_fetch_github_get_adds_auth_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    captured: dict[str, object] = {}

    def _https_get(url: str, *, headers: dict[str, str] | None = None) -> bytes:
        captured["url"] = url
        captured["headers"] = headers
        return b"{}"

    monkeypatch.setattr(fetch, "_get_github_token", lambda: "gh-token")
    monkeypatch.setattr(fetch, "_https_get", _https_get)
    payload = object.__getattribute__(fetch, "_github_get")(
        "https://api.github.com/repos/NixOS/nix"
    )

    assert payload == b"{}"
    headers_obj = _as_object_dict(captured.get("headers"), context="github headers")
    assert headers_obj["Authorization"] == "token gh-token"
    assert headers_obj["Accept"] == "application/vnd.github.v3+json"


def test_fetch_github_get_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    captured: dict[str, object] = {}

    def _https_get(url: str, *, headers: dict[str, str] | None = None) -> bytes:
        captured["url"] = url
        captured["headers"] = headers
        return b"{}"

    monkeypatch.setattr(fetch, "_get_github_token", lambda: None)
    monkeypatch.setattr(fetch, "_https_get", _https_get)
    payload = object.__getattribute__(fetch, "_github_get")(
        "https://api.github.com/repos/NixOS/nix"
    )

    assert payload == b"{}"
    headers_obj = _as_object_dict(captured.get("headers"), context="github headers")
    assert "Authorization" not in headers_obj
    assert headers_obj["Accept"] == "application/vnd.github.v3+json"


def test_fetch_download_schema_delegates_to_https_get(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    monkeypatch.setattr(fetch, "_https_get", lambda url: f"download:{url}".encode())
    payload = object.__getattribute__(fetch, "_download_schema")(
        "https://example.com/schema.yaml"
    )
    assert payload == b"download:https://example.com/schema.yaml"


def test_fetch_default_branch_and_schema_listing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    responses = {
        fetch.API_BASE: json.dumps({"default_branch": "main"}).encode(),
        f"{fetch.API_BASE}/branches/main": json.dumps({
            "commit": {"sha": "abc123"}
        }).encode(),
        f"{fetch.API_BASE}/contents/{fetch.SCHEMA_PATH}?ref=abc123": json.dumps([
            {"name": "a.yaml"},
            {"name": "notes.txt"},
            {"name": "b.yaml"},
        ]).encode(),
    }
    monkeypatch.setattr(fetch, "_github_get", lambda url: responses[url])

    sha, branch = object.__getattribute__(fetch, "_get_default_branch_head")()
    assert (sha, branch) == ("abc123", "main")

    files = object.__getattribute__(fetch, "_list_schema_files")("abc123")
    assert files == [
        {
            "name": "a.yaml",
            "download_url": f"{fetch.RAW_BASE}/abc123/{fetch.SCHEMA_PATH}/a.yaml",
        },
        {
            "name": "b.yaml",
            "download_url": f"{fetch.RAW_BASE}/abc123/{fetch.SCHEMA_PATH}/b.yaml",
        },
    ]


def test_fetch_https_get_validates_scheme() -> None:
    """Run this test case."""
    with pytest.raises(ValueError, match="Only absolute HTTPS URLs"):
        object.__getattribute__(fetch, "_https_get")("http://example.com")


def test_fetch_https_get_validates_hostname() -> None:
    """Run this test case."""
    with pytest.raises(ValueError, match="Could not parse host from URL"):
        object.__getattribute__(fetch, "_https_get")("https://:443/path")


def test_fetch_https_get_success_and_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    captured: dict[str, object] = {}

    def _fetch_url_bytes(url: str, **kwargs: object) -> tuple[bytes, dict[str, str]]:
        captured["url"] = url
        captured.update(kwargs)
        return b"payload", {"X": "1"}

    monkeypatch.setattr(fetch.http_utils, "fetch_url_bytes", _fetch_url_bytes)
    body = object.__getattribute__(fetch, "_https_get")(
        "https://example.com/path?q=1", headers={"X-Test": "1"}
    )
    assert body == b"payload"
    assert captured["url"] == "https://example.com/path?q=1"
    assert captured["attempts"] == object.__getattribute__(fetch, "_HTTP_MAX_ATTEMPTS")
    assert captured["max_backoff"] == object.__getattribute__(
        fetch, "_HTTP_BACKOFF_MAX_SECONDS"
    )
    assert captured["timeout"] == object.__getattribute__(
        fetch, "_HTTP_TIMEOUT_SECONDS"
    )
    assert captured["headers"] == {
        "User-Agent": "nixcfg-schema-fetch",
        "X-Test": "1",
    }

    def _status_error(_url: str, **_kwargs: object) -> tuple[bytes, dict[str, str]]:
        raise fetch.http_utils.SyncRequestError(
            url="https://example.com/missing",
            attempts=1,
            kind="status",
            detail="HTTP 404 Not Found",
            status=404,
        )

    monkeypatch.setattr(fetch.http_utils, "fetch_url_bytes", _status_error)
    with pytest.raises(RuntimeError, match="HTTP 404"):
        object.__getattribute__(fetch, "_https_get")("https://example.com/missing")


def test_fetch_https_get_wraps_timeout_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    def _timeout(_url: str, **_kwargs: object) -> tuple[bytes, dict[str, str]]:
        raise fetch.http_utils.SyncRequestError(
            url="https://example.com/slow",
            attempts=3,
            kind="timeout",
            detail="timed out",
        )

    monkeypatch.setattr(fetch.http_utils, "fetch_url_bytes", _timeout)
    with pytest.raises(
        RuntimeError,
        match=r"Timed out fetching https://example\.com/slow after 3 attempt\(s\)",
    ):
        object.__getattribute__(fetch, "_https_get")("https://example.com/slow")


def test_fetch_https_get_retries_retryable_http_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    calls = 0

    def _fetch_url_bytes(_url: str, **_kwargs: object) -> tuple[bytes, dict[str, str]]:
        nonlocal calls
        calls += 1
        return b"ok", {}

    monkeypatch.setattr(fetch.http_utils, "fetch_url_bytes", _fetch_url_bytes)
    body = object.__getattribute__(fetch, "_https_get")("https://example.com/retry")
    assert body == b"ok"
    assert calls == 1


def test_fetch_https_get_retries_oserror_and_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""

    def _network(_url: str, **_kwargs: object) -> tuple[bytes, dict[str, str]]:
        raise fetch.http_utils.SyncRequestError(
            url="https://example.com/os",
            attempts=3,
            kind="network",
            detail="network down",
        )

    monkeypatch.setattr(fetch.http_utils, "fetch_url_bytes", _network)
    with pytest.raises(
        RuntimeError,
        match=r"Network error fetching https://example\.com/os after 3 attempt\(s\): network down",
    ):
        object.__getattribute__(fetch, "_https_get")("https://example.com/os")


def test_fetch_https_get_retryable_status_exhausted_reports_attempts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""

    def _status(_url: str, **_kwargs: object) -> tuple[bytes, dict[str, str]]:
        raise fetch.http_utils.SyncRequestError(
            url="https://example.com/retry-fail",
            attempts=3,
            kind="status",
            detail="HTTP 503 Busy",
            status=503,
        )

    monkeypatch.setattr(fetch.http_utils, "fetch_url_bytes", _status)
    with pytest.raises(
        RuntimeError,
        match=r"HTTP 503 fetching https://example\.com/retry-fail after 3 attempt\(s\)",
    ):
        object.__getattribute__(fetch, "_https_get")("https://example.com/retry-fail")


def test_fetch_https_get_handles_zero_attempt_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    monkeypatch.setattr(fetch, "_HTTP_MAX_ATTEMPTS", 0)

    with pytest.raises(
        RuntimeError, match=r"Failed fetching https://example\.com/never"
    ):
        object.__getattribute__(fetch, "_https_get")("https://example.com/never")


def test_fetch_https_get_reraises_unexpected_value_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Preserve unrelated URL validation failures from the shared helper."""

    def _bad_value(_url: str, **_kwargs: object) -> tuple[bytes, dict[str, str]]:
        raise ValueError("unexpected validation failure")

    monkeypatch.setattr(fetch.http_utils, "fetch_url_bytes", _bad_value)
    with pytest.raises(ValueError, match="unexpected validation failure"):
        object.__getattribute__(fetch, "_https_get")("https://example.com/value")


def test_fetch_https_get_wraps_unknown_request_error_kinds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback to a generic fetch failure for unknown request error kinds."""

    def _unknown(_url: str, **_kwargs: object) -> tuple[bytes, dict[str, str]]:
        raise fetch.http_utils.SyncRequestError(
            url="https://example.com/unknown",
            attempts=2,
            kind="other",
            detail="mystery",
        )

    monkeypatch.setattr(fetch.http_utils, "fetch_url_bytes", _unknown)
    with pytest.raises(
        RuntimeError, match=r"Failed fetching https://example\.com/unknown"
    ):
        object.__getattribute__(fetch, "_https_get")("https://example.com/unknown")


def test_fetch_write_and_parse_version_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run this test case."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    monkeypatch.setattr(fetch, "SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(fetch, "VERSION_FILE", schemas_dir / "_version.json")

    (schemas_dir / "a.yaml").write_bytes(b"aaa")
    (schemas_dir / "b.yaml").write_bytes(b"bbb")

    files = [
        {"name": "a.yaml", "download_url": "https://example.com/a"},
        {"name": "b.yaml", "download_url": "https://example.com/b"},
        {"name": "missing.yaml", "download_url": "https://example.com/m"},
    ]
    object.__getattribute__(fetch, "_write_version")(
        "deadbeef",
        "main",
        files,
        schemas_dir=schemas_dir,
        version_file=schemas_dir / "_version.json",
    )

    manifest = object.__getattribute__(fetch, "_parse_version")()
    assert manifest is not None
    assert manifest.commit == "deadbeef"
    assert manifest.branch == "main"
    assert manifest.repo == fetch.REPO
    assert manifest.path == fetch.SCHEMA_PATH
    assert set(manifest.checksums) == {"a.yaml", "b.yaml"}


def test_fetch_parse_version_invalid_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run this test case."""
    version_file = tmp_path / "_version.json"
    monkeypatch.setattr(fetch, "VERSION_FILE", version_file)

    assert object.__getattribute__(fetch, "_parse_version")() is None

    version_file.write_text("{not json", encoding="utf-8")
    assert object.__getattribute__(fetch, "_parse_version")() is None

    version_file.write_text(json.dumps({"commit": "x"}), encoding="utf-8")
    assert object.__getattribute__(fetch, "_parse_version")() is None


def test_fetch_emit_progress_noop_when_unset() -> None:
    """Run this test case."""
    object.__getattribute__(fetch, "_emit_progress")(None, "ignored")


def test_fetch_downloads_files_and_writes_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    monkeypatch.setattr(fetch, "SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(fetch, "VERSION_FILE", schemas_dir / "_version.json")
    monkeypatch.setattr(fetch, "_get_default_branch_head", lambda: ("abc", "main"))

    files = [
        {"name": "a.yaml", "download_url": "https://example.com/a"},
        {"name": "b.yaml", "download_url": "https://example.com/b"},
    ]
    monkeypatch.setattr(fetch, "_list_schema_files", lambda _sha: files)
    monkeypatch.setattr(fetch, "_download_schema", lambda url: url.encode())
    (schemas_dir / "stale.yaml").write_bytes(b"deleted upstream")

    progress: list[str] = []
    fetch.fetch(progress=progress.append)

    assert (schemas_dir / "a.yaml").read_bytes() == b"https://example.com/a"
    assert (schemas_dir / "b.yaml").read_bytes() == b"https://example.com/b"
    assert not (schemas_dir / "stale.yaml").exists()
    manifest = fetch._parse_version()
    assert manifest is not None
    assert manifest.commit == "abc"
    assert set(manifest.checksums) == {"a.yaml", "b.yaml"}
    assert progress == [
        f"Resolving default branch head for {fetch.REPO}.",
        "Listing schema files for main@abc.",
        "Fetching 2 schema file(s) from main@abc.",
        "Downloading 1/2: a.yaml",
        "Downloading 2/2: b.yaml",
        "Updating schema version manifest.",
        "Schema fetch complete.",
    ]


def _schema_artifacts(schemas_dir: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes() for path in schemas_dir.iterdir() if path.is_file()
    }


def test_fetch_mid_download_failure_leaves_live_snapshot_untouched(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed download cannot partially replace the committed schema set."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "a.yaml").write_bytes(b"old-a")
    (schemas_dir / "stale.yaml").write_bytes(b"old-stale")
    (schemas_dir / "_version.json").write_bytes(b"old-manifest")
    before = _schema_artifacts(schemas_dir)
    monkeypatch.setattr(fetch, "SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(fetch, "VERSION_FILE", schemas_dir / "_version.json")
    monkeypatch.setattr(fetch, "_get_default_branch_head", lambda: ("abc", "main"))
    monkeypatch.setattr(
        fetch,
        "_list_schema_files",
        lambda _sha: [
            {"name": "a.yaml", "download_url": "https://example.com/a"},
            {"name": "b.yaml", "download_url": "https://example.com/b"},
        ],
    )

    def _download(url: str) -> bytes:
        if url.endswith("/b"):
            raise RuntimeError("mid-fetch failure")
        return b"new-a"

    monkeypatch.setattr(fetch, "_download_schema", _download)
    with pytest.raises(RuntimeError, match="mid-fetch failure"):
        fetch.fetch()

    assert _schema_artifacts(schemas_dir) == before
    assert list(schemas_dir.glob(".schema-fetch-*")) == []


def test_fetch_manifest_write_failure_restores_files_and_deletions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A manifest commit failure rolls back every previously applied file change."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "a.yaml").write_bytes(b"old-a")
    (schemas_dir / "stale.yaml").write_bytes(b"old-stale")
    (schemas_dir / "_version.json").write_bytes(b"old-manifest")
    before = _schema_artifacts(schemas_dir)
    monkeypatch.setattr(fetch, "SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(fetch, "VERSION_FILE", schemas_dir / "_version.json")
    monkeypatch.setattr(fetch, "_get_default_branch_head", lambda: ("abc", "main"))
    monkeypatch.setattr(
        fetch,
        "_list_schema_files",
        lambda _sha: [
            {"name": "a.yaml", "download_url": "https://example.com/a"},
            {"name": "b.yaml", "download_url": "https://example.com/b"},
        ],
    )
    monkeypatch.setattr(
        fetch,
        "_download_schema",
        lambda url: f"new-{url.rsplit('/', 1)[-1]}".encode(),
    )
    atomic_write_bytes = fetch.update_io.atomic_write_bytes
    failed = False

    def _fail_manifest_once(path: Path, content: bytes, **kwargs: object) -> None:
        nonlocal failed
        if path == fetch.VERSION_FILE and not failed:
            failed = True
            raise OSError("manifest write failed")
        atomic_write_bytes(path, content, **kwargs)

    monkeypatch.setattr(fetch.update_io, "atomic_write_bytes", _fail_manifest_once)
    with pytest.raises(OSError, match="manifest write failed"):
        fetch.fetch()

    assert failed
    assert _schema_artifacts(schemas_dir) == before
    assert list(schemas_dir.glob(".schema-fetch-*")) == []


def test_fetch_rollback_failure_retains_recovery_backup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unsuccessful rollback preserves the prior snapshot for manual recovery."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "a.yaml").write_bytes(b"old-a")
    (schemas_dir / "_version.json").write_bytes(b"old-manifest")
    before = _schema_artifacts(schemas_dir)
    monkeypatch.setattr(fetch, "SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(fetch, "VERSION_FILE", schemas_dir / "_version.json")
    monkeypatch.setattr(fetch, "_get_default_branch_head", lambda: ("abc", "main"))
    monkeypatch.setattr(
        fetch,
        "_list_schema_files",
        lambda _sha: [{"name": "a.yaml", "download_url": "https://example.com/a"}],
    )
    monkeypatch.setattr(fetch, "_download_schema", lambda _url: b"new-a")

    atomic_write_bytes = fetch.update_io.atomic_write_bytes

    def _fail_manifest(path: Path, content: bytes, **kwargs: object) -> None:
        if path == fetch.VERSION_FILE:
            raise OSError("persistent manifest failure")
        atomic_write_bytes(path, content, **kwargs)

    monkeypatch.setattr(fetch.update_io, "atomic_write_bytes", _fail_manifest)
    with pytest.raises(fetch._SnapshotRollbackError) as raised:
        fetch.fetch()

    message = str(raised.value)
    assert "Schema snapshot publication failed" in message
    assert "rollback also failed" in message
    assert "recovery backup retained" in message
    workspaces = list(schemas_dir.glob(".schema-fetch-*"))
    assert len(workspaces) == 1
    backup = workspaces[0] / "backup"
    assert (backup / "a.yaml").read_bytes() == before["a.yaml"]
    assert (backup / "_version.json").read_bytes() == before["_version.json"]


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_fetch_publication_interruption_restores_live_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    """Interrupts still run rollback, then propagate without deleting staging data early."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "a.yaml").write_bytes(b"old-a")
    (schemas_dir / "stale.yaml").write_bytes(b"old-stale")
    (schemas_dir / "_version.json").write_bytes(b"old-manifest")
    before = _schema_artifacts(schemas_dir)
    monkeypatch.setattr(fetch, "SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(fetch, "VERSION_FILE", schemas_dir / "_version.json")
    monkeypatch.setattr(fetch, "_get_default_branch_head", lambda: ("abc", "main"))
    monkeypatch.setattr(
        fetch,
        "_list_schema_files",
        lambda _sha: [
            {"name": "a.yaml", "download_url": "https://example.com/a"},
            {"name": "b.yaml", "download_url": "https://example.com/b"},
        ],
    )
    monkeypatch.setattr(
        fetch,
        "_download_schema",
        lambda url: f"new-{url.rsplit('/', 1)[-1]}".encode(),
    )
    interruption = interruption_type("publication interrupted")
    atomic_write_bytes = fetch.update_io.atomic_write_bytes
    interrupted = False

    def _interrupt_manifest_once(path: Path, content: bytes, **kwargs: object) -> None:
        nonlocal interrupted
        if path == fetch.VERSION_FILE and not interrupted:
            interrupted = True
            raise interruption
        atomic_write_bytes(path, content, **kwargs)

    monkeypatch.setattr(fetch.update_io, "atomic_write_bytes", _interrupt_manifest_once)
    with pytest.raises(interruption_type) as raised:
        fetch.fetch()

    assert raised.value is interruption
    assert interrupted
    assert _schema_artifacts(schemas_dir) == before
    assert list(schemas_dir.glob(".schema-fetch-*")) == []


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_fetch_publication_interruption_survives_rollback_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    """A rollback error cannot replace the original interrupt or delete its backup."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "a.yaml").write_bytes(b"old-a")
    (schemas_dir / "_version.json").write_bytes(b"old-manifest")
    before = _schema_artifacts(schemas_dir)
    monkeypatch.setattr(fetch, "SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(fetch, "VERSION_FILE", schemas_dir / "_version.json")
    monkeypatch.setattr(fetch, "_get_default_branch_head", lambda: ("abc", "main"))
    monkeypatch.setattr(
        fetch,
        "_list_schema_files",
        lambda _sha: [{"name": "a.yaml", "download_url": "https://example.com/a"}],
    )
    monkeypatch.setattr(fetch, "_download_schema", lambda _url: b"new-a")
    interruption = interruption_type("publication interrupted")
    atomic_write_bytes = fetch.update_io.atomic_write_bytes
    manifest_writes = 0

    def _interrupt_then_fail_rollback(
        path: Path, content: bytes, **kwargs: object
    ) -> None:
        nonlocal manifest_writes
        if path == fetch.VERSION_FILE:
            manifest_writes += 1
            if manifest_writes == 1:
                raise interruption
            raise OSError("rollback interrupted by secondary failure")
        atomic_write_bytes(path, content, **kwargs)

    monkeypatch.setattr(
        fetch.update_io, "atomic_write_bytes", _interrupt_then_fail_rollback
    )
    with pytest.raises(interruption_type) as raised:
        fetch.fetch()

    assert raised.value is interruption
    assert manifest_writes == 2
    assert _schema_artifacts(schemas_dir) == before
    workspaces = list(schemas_dir.glob(".schema-fetch-*"))
    assert len(workspaces) == 1
    assert _schema_artifacts(workspaces[0] / "backup") == before


@pytest.mark.parametrize("interruption_type", [KeyboardInterrupt, SystemExit])
def test_fetch_rollback_interruption_retains_backup_and_original_interrupt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    interruption_type: type[BaseException],
) -> None:
    """An interrupted rollback keeps both its partial live state and recovery evidence."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    (schemas_dir / "a.yaml").write_bytes(b"old-a")
    (schemas_dir / "stale.yaml").write_bytes(b"old-stale")
    (schemas_dir / "_version.json").write_bytes(b"old-manifest")
    before = _schema_artifacts(schemas_dir)
    monkeypatch.setattr(fetch, "SCHEMAS_DIR", schemas_dir)
    monkeypatch.setattr(fetch, "VERSION_FILE", schemas_dir / "_version.json")
    monkeypatch.setattr(fetch, "_get_default_branch_head", lambda: ("abc", "main"))
    monkeypatch.setattr(
        fetch,
        "_list_schema_files",
        lambda _sha: [
            {"name": "a.yaml", "download_url": "https://example.com/a"},
            {"name": "b.yaml", "download_url": "https://example.com/b"},
        ],
    )
    monkeypatch.setattr(
        fetch,
        "_download_schema",
        lambda url: f"new-{url.rsplit('/', 1)[-1]}".encode(),
    )
    interruption = interruption_type("rollback interrupted")
    atomic_write_bytes = fetch.update_io.atomic_write_bytes
    manifest_writes = 0
    a_writes = 0

    def _interrupt_rollback(path: Path, content: bytes, **kwargs: object) -> None:
        nonlocal a_writes, manifest_writes
        if path == fetch.VERSION_FILE:
            manifest_writes += 1
            if manifest_writes == 1:
                raise OSError("publication failed")
        if path == schemas_dir / "a.yaml":
            a_writes += 1
            if a_writes == 2:
                raise interruption
        atomic_write_bytes(path, content, **kwargs)

    monkeypatch.setattr(fetch.update_io, "atomic_write_bytes", _interrupt_rollback)
    with pytest.raises(interruption_type) as raised:
        fetch.fetch()

    assert raised.value is interruption
    assert manifest_writes == 1
    assert (schemas_dir / "a.yaml").read_bytes() == b"new-a"
    assert not (schemas_dir / "b.yaml").exists()
    assert not (schemas_dir / "stale.yaml").exists()
    assert (schemas_dir / "_version.json").read_bytes() == b"old-manifest"
    workspaces = list(schemas_dir.glob(".schema-fetch-*"))
    assert len(workspaces) == 1
    assert _schema_artifacts(workspaces[0] / "backup") == before


def test_fetch_check_verifies_recorded_checksums_offline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Check compares local bytes to manifest checksums and names each drift."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    monkeypatch.setattr(fetch, "SCHEMAS_DIR", schemas_dir)
    (schemas_dir / "a.yaml").write_bytes(b"a")
    (schemas_dir / "b.yaml").write_bytes(b"b")
    manifest = fetch.SchemaVersionManifest(
        commit="abc",
        branch="main",
        fetched=fetch.datetime.now(UTC),
        repo=fetch.REPO,
        path=fetch.SCHEMA_PATH,
        checksums={
            "a.yaml": fetch.hashlib.sha256(b"a").hexdigest(),
            "b.yaml": fetch.hashlib.sha256(b"b").hexdigest(),
        },
    )
    monkeypatch.setattr(fetch, "_parse_version", lambda: manifest)

    def _no_network(*_args: object, **_kwargs: object) -> bytes:
        raise AssertionError

    monkeypatch.setattr(fetch, "_https_get", _no_network)
    assert fetch.check()
    assert capsys.readouterr().err == ""

    (schemas_dir / "a.yaml").write_bytes(b"reformatted")
    (schemas_dir / "b.yaml").unlink()
    (schemas_dir / "stale.yaml").write_bytes(b"x")
    assert not fetch.check()
    assert capsys.readouterr().err.splitlines() == [
        "Vendored schema drift: a.yaml: checksum differs from _version.json",
        "Vendored schema drift: b.yaml: missing",
        "Vendored schema drift: stale.yaml: not recorded in _version.json",
    ]

    for broken in (None, manifest.model_copy(update={"checksums": {}})):
        monkeypatch.setattr(fetch, "_parse_version", lambda broken=broken: broken)
        assert not fetch.check()
        assert "missing, invalid, or empty" in capsys.readouterr().err


def test_vendored_schemas_match_recorded_checksums() -> None:
    """The checked-in schemas are the exact upstream bytes the manifest records."""
    assert fetch.check()


def test_fetch_main_check_and_fetch_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    exits: list[int] = []

    def _exit(code: int) -> None:
        exits.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(fetch.sys, "exit", _exit)

    monkeypatch.setattr(fetch, "check", lambda: True)
    monkeypatch.setattr(fetch.sys, "argv", ["fetch", "--check"])
    with pytest.raises(SystemExit):
        fetch.main()
    assert exits[-1] == 0

    called = {"fetch": 0}
    monkeypatch.setattr(
        fetch, "fetch", lambda: called.__setitem__("fetch", called["fetch"] + 1)
    )
    monkeypatch.setattr(fetch.sys, "argv", ["fetch"])
    fetch.main()
    assert called["fetch"] == 1
