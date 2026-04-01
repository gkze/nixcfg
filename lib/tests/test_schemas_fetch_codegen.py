"""Tests for schema fetch/codegen helper modules."""

from __future__ import annotations

import base64
import json
import types
from datetime import UTC
from typing import TYPE_CHECKING

import pytest

from lib.nix.schemas import _codegen as codegen
from lib.nix.schemas import _fetch as fetch

if TYPE_CHECKING:
    from pathlib import Path


def _as_object_dict(value: object, *, context: str) -> dict[str, object]:
    return object.__getattribute__(codegen, "_as_object_dict")(value, context=context)


class _FakeHTTPResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        reason: str = "OK",
        body: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.reason = reason
        self._body = body
        self.headers = headers or {}

    def read(self) -> bytes:
        """Run this test case."""
        return object.__getattribute__(self, "_body")


class _FakeHTTPSConnection:
    def __init__(self, response: _FakeHTTPResponse) -> None:
        self._response = response
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        self.closed = False

    def request(self, method: str, path: str, headers: dict[str, str]) -> None:
        """Run this test case."""
        self.requests.append((method, path, headers))

    def getresponse(self) -> _FakeHTTPResponse:
        """Run this test case."""
        return object.__getattribute__(self, "_response")

    def close(self) -> None:
        """Run this test case."""
        self.closed = True


class _FakeResolvedResource:
    def __init__(self, contents: object) -> None:
        self.contents = contents


class _FakeResolver:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping

    def lookup(self, uri: str) -> _FakeResolvedResource:
        """Run this test case."""
        return _FakeResolvedResource(object.__getattribute__(self, "_mapping")[uri])


class _FakeRegistry:
    def __init__(self, mapping: dict[str, object]) -> None:
        self._mapping = mapping

    def resolver(self) -> _FakeResolver:
        """Run this test case."""
        return _FakeResolver(object.__getattribute__(self, "_mapping"))


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
    object.__getattribute__(fetch, "_write_version")("deadbeef", "main", files)

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

    writes: list[tuple[str, str, list[dict[str, str]]]] = []

    def _write_version(
        commit: str, branch: str, version_files: list[dict[str, str]]
    ) -> None:
        writes.append((commit, branch, version_files))

    monkeypatch.setattr(fetch, "_write_version", _write_version)
    progress: list[str] = []
    fetch.fetch(progress=progress.append)

    assert (schemas_dir / "a.yaml").read_bytes() == b"https://example.com/a"
    assert (schemas_dir / "b.yaml").read_bytes() == b"https://example.com/b"
    assert writes == [("abc", "main", files)]
    assert progress == [
        f"Resolving default branch head for {fetch.REPO}.",
        "Listing schema files for main@abc.",
        "Fetching 2 schema file(s) from main@abc.",
        "Downloading 1/2: a.yaml",
        "Downloading 2/2: b.yaml",
        "Updating schema version manifest.",
        "Schema fetch complete.",
    ]


def test_fetch_check_happy_path_and_failure_modes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    monkeypatch.setattr(fetch, "SCHEMAS_DIR", schemas_dir)

    manifest = fetch.SchemaVersionManifest(
        commit="abc",
        branch="main",
        fetched=fetch.datetime.now(UTC),
        repo=fetch.REPO,
        path=fetch.SCHEMA_PATH,
        checksums={},
    )
    monkeypatch.setattr(fetch, "_parse_version", lambda: manifest)

    files = [
        {"name": "a.yaml", "download_url": "https://example.com/a"},
        {"name": "b.yaml", "download_url": "https://example.com/b"},
    ]
    monkeypatch.setattr(fetch, "_list_schema_files", lambda _sha: files)

    (schemas_dir / "a.yaml").write_bytes(b"a")
    (schemas_dir / "b.yaml").write_bytes(b"b")

    monkeypatch.setattr(
        fetch, "_download_schema", lambda url: b"a" if url.endswith("/a") else b"b"
    )
    assert fetch.check()

    # Missing local file.
    (schemas_dir / "b.yaml").unlink()
    assert not fetch.check()
    (schemas_dir / "b.yaml").write_bytes(b"b")

    # Mismatched contents.
    monkeypatch.setattr(fetch, "_download_schema", lambda _url: b"different")
    assert not fetch.check()

    # Stale local file.
    monkeypatch.setattr(
        fetch, "_download_schema", lambda url: b"a" if url.endswith("/a") else b"b"
    )
    (schemas_dir / "stale.yaml").write_bytes(b"x")
    assert not fetch.check()

    # No version manifest.
    monkeypatch.setattr(fetch, "_parse_version", lambda: None)
    assert not fetch.check()


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


def test_codegen_load_yaml_and_walk_pointer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run this test case."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    monkeypatch.setattr(codegen, "SCHEMAS_DIR", schemas_dir)
    (schemas_dir / "sample.yaml").write_text("a:\n  b: 1\n", encoding="utf-8")

    loaded = object.__getattribute__(codegen, "_load_yaml")("sample")
    assert loaded == {"a": {"b": 1}}
    assert (
        object.__getattribute__(codegen, "_walk_pointer")({"a": {"b": 1}}, "/a/b") == 1
    )

    with pytest.raises(TypeError, match="Cannot walk pointer"):
        object.__getattribute__(codegen, "_walk_pointer")({"a": [1]}, "/a/b")


def test_codegen_object_coercion_and_progress_helpers() -> None:
    """Run this test case."""
    with pytest.raises(TypeError, match="Expected JSON object for demo"):
        _as_object_dict([], context="demo")

    with pytest.raises(TypeError, match="Expected string key in demo"):
        _as_object_dict({1: "x"}, context="demo")

    object.__getattribute__(codegen, "_emit_progress")(None, "ignored")


def test_codegen_ref_resolver_additional_paths() -> None:
    """Run this test case."""
    resolver_cls = object.__getattribute__(codegen, "_SchemaRefResolver")
    resolver = resolver_cls(
        schema=object.__getattribute__(codegen, "_coerce_json_object")(
            {"type": "object"}, context="resolver root"
        ),
        registry=_FakeRegistry({}),
    )

    split_ref = object.__getattribute__(resolver_cls, "_split_ref")
    assert split_ref("remote.yaml") == ("remote.yaml", "")

    resolve_ref_target = object.__getattribute__(resolver, "_resolve_ref_target")
    target = resolve_ref_target("", root={"type": "object"})
    assert target is None

    obj_dict = {"$ref": "", "title": "demo"}
    resolve_ref = object.__getattribute__(resolver, "_resolve_ref")
    assert resolve_ref(ref="", obj_dict=obj_dict, seen=set(), root={}) == obj_dict

    resolved_list = resolver.resolve([{"x": 1}], seen=set(), root={"type": "object"})
    assert resolved_list == [{"x": 1}]


def test_codegen_allof_merge_helpers_and_import_normalization() -> None:
    """Run this test case."""
    result: dict[str, object] = {"type": "object"}
    object.__getattribute__(codegen, "_merge_allof_properties")(result, {})
    assert "properties" not in result

    rewritten = object.__getattribute__(codegen, "_rewrite_constr_type_hints")(
        "value: constr(pattern=r'^abc$')"
    )
    assert "Annotated[" in rewritten
    assert "StringConstraints(pattern=r'^abc$')" in rewritten

    normalized = object.__getattribute__(codegen, "_normalize_pydantic_imports")(
        "from pydantic import BaseModel, constr\n"
    )
    assert "StringConstraints" in normalized
    assert "constr" not in normalized

    normalized_existing = object.__getattribute__(
        codegen, "_normalize_pydantic_imports"
    )("from pydantic import BaseModel, StringConstraints, constr\n")
    assert normalized_existing.count("StringConstraints") == 1
    assert "constr" not in normalized_existing

    imports_block = object.__getattribute__(codegen, "_compose_imports_block")({
        "from pydantic import BaseModel",
        "from typing import Any",
        "import os",
    })
    assert "import os" in imports_block


def test_codegen_build_registry_registers_multiple_uris(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    schemas_dir = tmp_path / "schemas"
    schemas_dir.mkdir()
    monkeypatch.setattr(codegen, "SCHEMAS_DIR", schemas_dir)
    (schemas_dir / "one.yaml").write_text(
        "$id: https://schemas/one\n", encoding="utf-8"
    )
    (schemas_dir / "two.yaml").write_text("type: object\n", encoding="utf-8")

    class _ResourceFactory:
        @staticmethod
        def from_contents(
            contents: object, default_specification: object = None
        ) -> dict[str, object]:
            """Run this test case."""
            return {"contents": contents, "spec": default_specification}

    class _RegistryFactory:
        def __init__(self) -> None:
            self.resources: list[tuple[str, object]] = []

        def with_resources(
            self, resources: list[tuple[str, object]]
        ) -> _RegistryFactory:
            """Run this test case."""
            self.resources = list(resources)
            return self

    registry_obj = _RegistryFactory()
    fake_referencing = types.SimpleNamespace(
        Resource=_ResourceFactory,
        Registry=lambda: registry_obj,
    )
    fake_jsonschema = types.SimpleNamespace(DRAFT4=object())

    def _import(name: str) -> object:
        if name == "referencing":
            return fake_referencing
        if name == "referencing.jsonschema":
            return fake_jsonschema
        msg = f"unexpected import {name}"
        raise AssertionError(msg)

    monkeypatch.setattr(codegen.importlib, "import_module", _import)
    registry = object.__getattribute__(codegen, "_build_registry")()

    assert registry is registry_obj
    keys = [key for key, _resource in registry_obj.resources]
    assert "./one.yaml" in keys
    assert "one.yaml" in keys
    assert "https://schemas/one" in keys
    assert "./two.yaml" in keys


def test_codegen_resolve_refs_paths_and_errors() -> None:
    """Run this test case."""
    remote = {
        "defs": {
            "thing": {
                "type": "string",
            },
        },
    }
    schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "a": {"$ref": "remote.yaml#/defs/thing", "title": "A"},
            "b": {"$ref": "#/properties/a"},
        },
    }
    resolved = object.__getattribute__(codegen, "_resolve_refs")(
        object.__getattribute__(codegen, "_coerce_json_object")(
            schema, context="test remote schema"
        ),
        _FakeRegistry({"remote.yaml": remote}),
    )
    props_obj = _as_object_dict(resolved["properties"], context="resolved properties")
    prop_a = _as_object_dict(props_obj["a"], context="property a")
    prop_b = _as_object_dict(props_obj["b"], context="property b")
    assert prop_a["type"] == "string"
    assert prop_a["title"] == "A"
    assert prop_b["type"] == "string"

    circular_schema: dict[str, object] = {
        "loop": {"$ref": "#/loop"},
    }
    circular = object.__getattribute__(codegen, "_resolve_refs")(
        object.__getattribute__(codegen, "_coerce_json_object")(
            circular_schema, context="test circular schema"
        ),
        _FakeRegistry({}),
    )
    loop_val = _as_object_dict(circular["loop"], context="circular loop")
    assert loop_val["description"] == "Circular ref: #/loop"

    with pytest.raises(TypeError, match="Invalid schema registry instance"):
        object.__getattribute__(codegen, "_resolve_refs")(
            object.__getattribute__(codegen, "_coerce_json_object")(
                schema, context="test invalid registry"
            ),
            object(),
        )

    with pytest.raises(TypeError, match=r"\$ref value must be a string"):
        object.__getattribute__(codegen, "_resolve_refs")(
            object.__getattribute__(codegen, "_coerce_json_object")(
                {"bad": {"$ref": 1}}, context="bad ref type"
            ),
            _FakeRegistry({}),
        )

    with pytest.raises(
        TypeError, match="Expected JSON object for resolved schema root"
    ):
        object.__getattribute__(codegen, "_resolve_refs")(
            object.__getattribute__(codegen, "_coerce_json_object")(
                {"num": 1, "$ref": "#/num"},
                context="non-object root ref",
            ),
            _FakeRegistry({}),
        )


def test_codegen_merge_allof_and_fixup_schema() -> None:
    """Run this test case."""
    result: dict[str, object] = {
        "type": "object",
        "properties": {"existing": {"type": "integer"}},
        "required": ["existing"],
        "allOf": [
            {"properties": {"a": {"type": "string"}}, "required": ["a"], "x-extra": 1},
            {"properties": {"existing": {"type": "integer"}}, "required": ["existing"]},
            {"not": {"type": "null"}},
            1,
        ],
    }
    object.__getattribute__(codegen, "_merge_allof_branches")(result)

    props = _as_object_dict(result["properties"], context="merged properties")
    assert "a" in props
    assert result["required"] == ["existing", "a"]
    assert result["x-extra"] == 1
    assert result["allOf"] == [{"not": {"type": "null"}}, 1]

    fixed = object.__getattribute__(codegen, "_fixup_schema")({
        "description": "drop me",
        "const": None,
        "allOf": [{"properties": {"a": {"const": None}}}],
    })
    fixed_dict = _as_object_dict(fixed, context="fixed schema")
    assert "description" not in fixed_dict
    assert fixed_dict["type"] == "null"


def test_codegen_generate_models_and_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    class _InputFileType:
        JsonSchema = "json-schema"

    class _DataModelType:
        PydanticV2BaseModel = "pydantic-v2"

    class _PythonVersion:
        PY_314 = "3.14"

    class _GenerateConfig:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    class _Formatter:
        RUFF_FORMAT = "ruff-format"
        RUFF_CHECK = "ruff-check"

    recorded: dict[str, object] = {}

    def _generate(schema_json: str, *, config: object) -> str:
        recorded["schema_json"] = schema_json
        recorded["config"] = config
        return "from pydantic import BaseModel\nclass Item(BaseModel):\n    x: int\n"

    imports: dict[str, object] = {
        "datamodel_code_generator": types.SimpleNamespace(
            generate=_generate,
            InputFileType=_InputFileType,
            DataModelType=_DataModelType,
            PythonVersion=_PythonVersion,
        ),
        "datamodel_code_generator.config": types.SimpleNamespace(
            GenerateConfig=_GenerateConfig
        ),
        "datamodel_code_generator.format": types.SimpleNamespace(Formatter=_Formatter),
    }

    monkeypatch.setattr(codegen.importlib, "import_module", lambda name: imports[name])

    generated = object.__getattribute__(codegen, "_generate_models")(
        "demo", {"type": "object"}
    )
    assert "class Item" in generated
    assert "schema_json" in recorded

    monkeypatch.setattr(codegen, "_fixup_schema", lambda _obj: [])
    with pytest.raises(TypeError, match="Expected JSON object for fixed schema"):
        object.__getattribute__(codegen, "_generate_models")("bad", {"type": "object"})

    monkeypatch.setattr(codegen, "_fixup_schema", lambda obj: obj)
    imports["datamodel_code_generator"] = types.SimpleNamespace(
        generate=lambda *_a, **_k: None,
        InputFileType=_InputFileType,
        DataModelType=_DataModelType,
        PythonVersion=_PythonVersion,
    )
    with pytest.raises(RuntimeError, match="codegen returned None"):
        object.__getattribute__(codegen, "_generate_models")("none", {"type": "object"})

    stripped = object.__getattribute__(codegen, "_strip_generated_headers")(
        "# generated by datamodel-codegen\n"
        "#   filename: x\n"
        "#   timestamp: t\n"
        "from x import y\n"
    )
    assert stripped == "from x import y"

    imports_found, body = object.__getattribute__(codegen, "_collect_imports")(
        "from a import b\nimport c\nclass X:\n    pass\n"
    )
    assert imports_found == {"from a import b", "import c"}
    assert "class X" in body


def test_codegen_main_writes_deduped_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    output_file = tmp_path / "_generated.py"
    monkeypatch.setattr(codegen, "OUTPUT_FILE", output_file)
    monkeypatch.setattr(codegen, "TOP_LEVEL_SCHEMAS", ["one", "two"])

    def _build_registry() -> object:
        return object()

    monkeypatch.setattr(codegen, "_build_registry", _build_registry)
    monkeypatch.setattr(
        codegen,
        "_load_yaml",
        lambda _name: object.__getattribute__(codegen, "_coerce_json_object")(
            {"type": "object"},
            context="test top-level schema",
        ),
    )
    monkeypatch.setattr(codegen, "_resolve_refs", lambda schema, _registry: schema)

    def _generate(name: str, _resolved: codegen.JsonObject) -> str:
        if name == "one":
            return (
                "from pydantic import BaseModel\n"
                "from typing import Optional\n"
                "class Shared(BaseModel):\n"
                "    a: int\n"
                "\n"
                "class One(BaseModel):\n"
                "    b: Optional[int]\n"
            )
        return (
            "from pydantic import BaseModel\n"
            "from typing import Optional\n"
            "class Shared(BaseModel):\n"
            "    a: int\n"
            "\n"
            "class Two(BaseModel):\n"
            "    c: Optional[int]\n"
        )

    monkeypatch.setattr(codegen, "_generate_models", _generate)
    progress: list[str] = []
    codegen.main(progress=progress.append)

    content = output_file.read_text(encoding="utf-8")
    assert "class Shared(" in content
    assert content.count("class Shared(") == 1
    assert "class One(" in content
    assert "class Two(" in content
    assert "from pydantic import BaseModel" in content
    assert content.count("from typing import Optional") == 1
    assert progress == [
        "Building schema registry.",
        "Generating models for 2 top-level schema(s).",
        "Processing 1/2: one",
        "Processing 2/2: two",
        f"Writing generated models to {output_file}.",
        "Schema codegen complete.",
    ]


def test_codegen_main_readds_trailing_newline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    output_file = tmp_path / "_generated.py"
    monkeypatch.setattr(codegen, "OUTPUT_FILE", output_file)
    monkeypatch.setattr(codegen, "TOP_LEVEL_SCHEMAS", ["one"])

    def _build_registry() -> object:
        return object()

    monkeypatch.setattr(codegen, "_build_registry", _build_registry)
    monkeypatch.setattr(
        codegen,
        "_load_yaml",
        lambda _name: object.__getattribute__(codegen, "_coerce_json_object")(
            {"type": "object"},
            context="test top-level schema",
        ),
    )
    monkeypatch.setattr(codegen, "_resolve_refs", lambda schema, _registry: schema)
    monkeypatch.setattr(
        codegen,
        "_generate_models",
        lambda _name, _resolved: (
            "from pydantic import BaseModel\nclass One(BaseModel):\n    x: int"
        ),
    )
    monkeypatch.setattr(codegen, "_rewrite_constr_type_hints", lambda code: code)
    monkeypatch.setattr(
        codegen, "_normalize_pydantic_imports", lambda code: code.rstrip("\n")
    )

    codegen.main(progress=None)

    assert output_file.read_text(encoding="utf-8").endswith("\n")


def test_codegen_main_keeps_existing_trailing_newline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""
    output_file = tmp_path / "_generated.py"
    monkeypatch.setattr(codegen, "OUTPUT_FILE", output_file)
    monkeypatch.setattr(codegen, "TOP_LEVEL_SCHEMAS", ["one"])

    def _build_registry() -> object:
        return object()

    monkeypatch.setattr(codegen, "_build_registry", _build_registry)
    monkeypatch.setattr(
        codegen,
        "_load_yaml",
        lambda _name: object.__getattribute__(codegen, "_coerce_json_object")(
            {"type": "object"},
            context="test top-level schema",
        ),
    )
    monkeypatch.setattr(codegen, "_resolve_refs", lambda schema, _registry: schema)
    monkeypatch.setattr(
        codegen,
        "_generate_models",
        lambda _name, _resolved: (
            "from pydantic import BaseModel\nclass One(BaseModel):\n    x: int"
        ),
    )
    monkeypatch.setattr(codegen, "_rewrite_constr_type_hints", lambda code: code)
    monkeypatch.setattr(
        codegen, "_normalize_pydantic_imports", lambda code: f"{code}\n"
    )

    codegen.main(progress=None)

    assert output_file.read_text(encoding="utf-8").endswith("\n")
