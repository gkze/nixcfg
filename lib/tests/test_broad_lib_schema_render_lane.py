"""Coverage-focused tests for the broad-lib schema/render lane."""

from __future__ import annotations

import json
import logging
import os
import runpy
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Self

import httpx
import pytest

from lib import http_utils, mac_apps_helper
from lib.schema_codegen import _render
from lib.schema_codegen import lockfile as codegen_lockfile


class _NetrcPrefersApiGithub:
    def authenticators(self, host: str) -> tuple[str, str, str] | None:
        if host == "api.github.com":
            return ("u", "x", "api-token")
        return None


class _NetrcNoGithub:
    def authenticators(self, host: str) -> tuple[str, str, str] | None:
        _ = host
        return None


class _AsyncResponse:
    def __init__(
        self,
        *,
        status_code: int = 200,
        reason_phrase: str = "OK",
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.reason_phrase = reason_phrase
        self.content = content
        self.headers = headers or {}


class _FakeAsyncClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, str], object, float]] = []

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        _ = (exc_type, exc, tb)
        return False

    async def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        cookies: object,
        timeout: float,
    ) -> _AsyncResponse:
        self.calls.append((method, url, headers, cookies, timeout))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _GenerateConfig:
    validated: object | None = None

    @classmethod
    def model_validate(cls, obj: object) -> object:
        cls.validated = obj
        return {"validated": obj}


class _GeneratorModule:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[Path, object]] = []
        self.contents: list[str] = []

    def generate(self, source: Path | str | object, *, config: object) -> object:
        assert isinstance(source, Path)
        self.calls.append((source, config))
        self.contents.append(source.read_text(encoding="utf-8"))
        return self.result


class _EmptyRetrying:
    def __iter__(self) -> Iterator[object]:
        return iter(())


class _EmptyAsyncRetrying:
    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> object:
        raise StopAsyncIteration


def test_http_utils_additional_branches(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cover remaining auth and helper error branches in http_utils."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setenv("GH_TOKEN", "gh-env-token")
    assert http_utils.resolve_github_token() == "gh-env-token"
    monkeypatch.delenv("GH_TOKEN", raising=False)

    monkeypatch.setattr(
        http_utils.keyring,
        "get_password",
        lambda *_a, **_k: "go-keyring-base64:" + "Z2gta2V5cmluZwo=",
    )
    assert http_utils.resolve_github_token(allow_keyring=True) == "gh-keyring"

    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".netrc").write_text("machine api.github.com login u password api\n")
    monkeypatch.setattr(
        http_utils.netrc, "netrc", lambda _path: _NetrcPrefersApiGithub()
    )
    assert http_utils.resolve_github_token(allow_netrc=True) == "api-token"

    monkeypatch.setattr(
        http_utils.netrc,
        "netrc",
        lambda _path: (_ for _ in ()).throw(
            http_utils.netrc.NetrcParseError("bad", str(tmp_path / ".netrc"), 1)
        ),
    )
    logger = logging.getLogger("http-utils-more")
    with caplog.at_level(logging.WARNING):
        assert http_utils.resolve_github_token(allow_netrc=True, logger=logger) is None
    assert "Failed to parse" in caplog.text

    response = type("Response", (), {"status_code": 500, "reason_phrase": "Boom"})()
    assert http_utils._format_http_error(response, b"") == "HTTP 500 Boom"

    with pytest.raises(http_utils._RetryableStatusError, match="retry"):
        http_utils._raise_status_error(503, "retry")
    with pytest.raises(http_utils._NonRetryableStatusError, match="stop"):
        http_utils._raise_status_error(418, "stop")

    with pytest.raises(TypeError, match="Unsupported request error type"):
        http_utils._as_request_error(
            url="https://example.com",
            attempts=1,
            exc=ValueError("unsupported"),
        )

    monkeypatch.setattr(
        http_utils.keyring,
        "get_password",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert http_utils.resolve_github_token(allow_keyring=True) is None

    monkeypatch.setattr(http_utils.keyring, "get_password", lambda *_a, **_k: "   ")
    assert http_utils.resolve_github_token(allow_keyring=True) is None

    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    monkeypatch.setattr(Path, "home", staticmethod(lambda: empty_home))
    assert http_utils.resolve_github_token(allow_netrc=True) is None

    (empty_home / ".netrc").write_text("machine example.com login u password p\n")
    monkeypatch.setattr(http_utils.netrc, "netrc", lambda _path: _NetrcNoGithub())
    assert http_utils.resolve_github_token(allow_netrc=True) is None

    monkeypatch.setattr(
        http_utils.netrc,
        "netrc",
        lambda _path: (_ for _ in ()).throw(OSError("boom")),
    )
    assert http_utils.resolve_github_token(allow_netrc=True) is None

    monkeypatch.setattr(http_utils, "Retrying", lambda **_kwargs: _EmptyRetrying())
    monkeypatch.setattr(
        http_utils.httpx,
        "Client",
        lambda **_kwargs: type(
            "Client",
            (),
            {
                "__enter__": lambda self: self,
                "__exit__": lambda self, exc_type, exc, tb: False,
            },
        )(),
    )
    with pytest.raises(RuntimeError, match="Failed fetching https://example.com/empty"):
        http_utils.fetch_url_bytes("https://example.com/empty")


@pytest.mark.anyio
async def test_fetch_url_bytes_async_covers_success_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise async request success, retries, status, timeout, and network errors."""
    cookies = httpx.Cookies({"session": "1"})
    external = _FakeAsyncClient([
        _AsyncResponse(status_code=503, reason_phrase="Busy", content=b"later"),
        _AsyncResponse(status_code=200, content=b"ok", headers={"X": "1"}),
    ])
    payload, headers = await http_utils.fetch_url_bytes_async(
        "https://example.com/data",
        client=external,
        cookies=cookies,
        headers={"A": "B"},
        backoff=0.0,
        request_timeout=4.0,
    )
    assert payload == b"ok"
    assert headers == {"X": "1"}
    assert len(external.calls) == 2

    created = _FakeAsyncClient([_AsyncResponse(status_code=200, content=b"created")])
    monkeypatch.setattr(http_utils.httpx, "AsyncClient", lambda **_kwargs: created)
    created_payload, created_headers = await http_utils.fetch_url_bytes_async(
        "https://example.com/created",
        attempts=1,
        backoff=0.0,
    )
    assert created_payload == b"created"
    assert created_headers == {}

    with pytest.raises(http_utils.RequestError, match="HTTP 404 Missing") as status_exc:
        await http_utils.fetch_url_bytes_async(
            "https://example.com/missing",
            client=_FakeAsyncClient([
                _AsyncResponse(
                    status_code=404, reason_phrase="Missing", content=b"gone"
                )
            ]),
            attempts=1,
            backoff=0.0,
        )
    assert status_exc.value.kind == "status"

    with pytest.raises(http_utils.RequestError, match="slow") as timeout_exc:
        await http_utils.fetch_url_bytes_async(
            "https://example.com/slow",
            client=_FakeAsyncClient([httpx.ReadTimeout("slow")]),
            attempts=1,
            backoff=0.0,
        )
    assert timeout_exc.value.kind == "timeout"

    with pytest.raises(http_utils.RequestError, match="down") as network_exc:
        await http_utils.fetch_url_bytes_async(
            "https://example.com/down",
            client=_FakeAsyncClient([httpx.ConnectError("down")]),
            attempts=1,
            backoff=0.0,
        )
    assert network_exc.value.kind == "network"

    monkeypatch.setattr(
        http_utils, "AsyncRetrying", lambda **_kwargs: _EmptyAsyncRetrying()
    )
    with pytest.raises(RuntimeError, match="Failed fetching https://example.com/empty"):
        await http_utils.fetch_url_bytes_async(
            "https://example.com/empty",
            client=_FakeAsyncClient([]),
            attempts=1,
            backoff=0.0,
        )


def test_render_helpers_cover_additional_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hit remaining render helper paths without altering generation behavior."""
    assert _render.normalize_entrypoint_name("/tmp/.yaml") == "schema"
    assert (
        _render.strip_generated_headers(
            "\n".join([
                "# generated by datamodel-codegen",
                "#   filename: model.py",
                "#   timestamp: now",
                "class Model: ...",
            ])
        )
        == "class Model: ..."
    )

    imports, body = _render.collect_imports(
        "from __future__ import annotations\n"
        "import os\n"
        "from x import y\n"
        "class Model: ...\n"
    )
    assert imports == {
        "from __future__ import annotations",
        "import os",
        "from x import y",
    }
    assert body == "class Model: ..."

    imports_block = _render.compose_imports_block({
        "from __future__ import annotations",
        "import zlib",
        "from pydantic import BaseModel",
        "from collections import abc as c_abc",
        "import os as operating_system",
    })
    assert imports_block == "\n".join([
        "import os as operating_system",
        "import zlib",
        "from collections import abc as c_abc",
        "",
        "from pydantic import BaseModel",
    ])
    assert _render.compose_imports_block({"x = 1"}) == ""

    seen_signatures = {"Thing": "previous"}
    used_names = {"ThingLock", "ThingLock2"}
    resolved = _render.resolve_body_class_conflicts(
        "class Thing:\n    value: int\n\nclass Uses:\n    item: Thing\n",
        entrypoint="lock.json",
        seen_signatures=seen_signatures,
        used_names=used_names,
    )
    assert "class ThingLock3:" in resolved
    assert "item: ThingLock3" in resolved

    assert (
        _render.dedupe_classes(
            "class A(Base):\n    x = 1\n\nclass A(Base):\n    y = 2\nvalue = 3\n"
        )
        == "class A(Base):\n    x = 1\n\nvalue = 3"
    )
    assert _render.collapse_excess_blank_lines("a\n\n\n\n\nb") == "a\n\n\nb"

    monkeypatch.setattr(
        _render, "_rewrite_constr_type_hints", lambda code: code + "|constr"
    )
    monkeypatch.setattr(
        _render, "_normalize_pydantic_imports", lambda code: code + "|imports"
    )
    target = type(
        "Target",
        (),
        {
            "prepare": type(
                "Prepare",
                (),
                {
                    "python_transforms": (
                        _render.PythonTransform.REWRITE_CONSTR_ANNOTATIONS,
                        _render.PythonTransform.NORMALIZE_PYDANTIC_IMPORTS,
                    )
                },
            )()
        },
    )()
    assert (
        _render.apply_python_transforms("code", target=target) == "code|constr|imports"
    )

    monkeypatch.setattr(
        _render,
        "_import_optional",
        lambda name, feature: (
            type("CfgMod", (), {"GenerateConfig": _GenerateConfig})()
            if name.endswith(".config")
            else _GeneratorModule("class Model: ...")
        ),
    )
    config = _render._map_generator_options({"output": "ignored", "a": 1})
    assert config == {"validated": {"a": 1}}
    assert _GenerateConfig.validated == {"a": 1}


def test_generate_models_handles_string_object_and_none_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Materialize temp schemas and handle each datamodel-code-generator return shape."""
    generator = _GeneratorModule("class Model: ...")
    monkeypatch.setattr(
        _render,
        "_import_optional",
        lambda name, feature: (
            type("CfgMod", (), {"GenerateConfig": _GenerateConfig})()
            if name.endswith(".config")
            else generator
        ),
    )
    generated = _render.generate_models(
        entrypoint="nested/root.yaml",
        generator_options={"output": "ignored"},
        schema={"type": "object"},
    )
    assert generated == "class Model: ..."
    assert generator.calls[0][0].name == "root.json"
    assert generator.contents[0].endswith("\n")

    object_generator = _GeneratorModule(123)
    monkeypatch.setattr(
        _render,
        "_import_optional",
        lambda name, feature: (
            type("CfgMod", (), {"GenerateConfig": _GenerateConfig})()
            if name.endswith(".config")
            else object_generator
        ),
    )
    assert (
        _render.generate_models(
            entrypoint="root.json",
            generator_options={},
            schema={"type": "object"},
        )
        == "123"
    )

    monkeypatch.setattr(
        _render,
        "_import_optional",
        lambda name, feature: (
            type("CfgMod", (), {"GenerateConfig": _GenerateConfig})()
            if name.endswith(".config")
            else _GeneratorModule(None)
        ),
    )
    with pytest.raises(RuntimeError, match="codegen returned None for none.yaml"):
        _render.generate_models(
            entrypoint="none.yaml",
            generator_options={},
            schema={"type": "object"},
        )


def test_lockfile_helper_error_and_metadata_branches(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Cover direct helper errors and optional metadata branches in lockfile code."""
    reported: list[str] = []
    codegen_lockfile._emit_progress(reported.append, "hello")
    codegen_lockfile._emit_progress(None, "ignored")
    assert reported == ["hello"]

    with pytest.raises(TypeError, match="Expected object for manifest"):
        codegen_lockfile._ensure_mapping([], context="manifest")
    with pytest.raises(TypeError, match="Expected string key in manifest"):
        codegen_lockfile._ensure_mapping({1: "x"}, context="manifest")
    with pytest.raises(TypeError, match="Expected string for value"):
        codegen_lockfile._ensure_string(1, context="value")

    assert codegen_lockfile._normalize_posix_string("/") == "//"
    assert codegen_lockfile._normalize_posix_string(".") == "."
    with pytest.raises(RuntimeError, match="must not be empty"):
        codegen_lockfile._normalize_posix_string("")

    original_pure_posix_path = codegen_lockfile.PurePosixPath
    monkeypatch.setattr(
        codegen_lockfile,
        "PurePosixPath",
        lambda _raw: type("PosixParts", (), {"parts": ()})(),
    )
    assert codegen_lockfile._normalize_posix_string("/") == "/"
    monkeypatch.setattr(codegen_lockfile, "PurePosixPath", original_pure_posix_path)

    regular = tmp_path / "regular.txt"
    regular.write_text("x", encoding="utf-8")
    symlink = tmp_path / "regular-link.txt"
    symlink.symlink_to(regular)
    assert codegen_lockfile._is_regular_file(regular) is True
    assert codegen_lockfile._is_regular_file(symlink) is False

    missing_dir = tmp_path / "missing"
    with pytest.raises(RuntimeError, match="does not exist"):
        codegen_lockfile._iter_materialized_directory_files(
            source_root=missing_dir,
            include_patterns=(),
        )

    not_dir = tmp_path / "file.txt"
    not_dir.write_text("x", encoding="utf-8")
    with pytest.raises(RuntimeError, match="is not a directory"):
        codegen_lockfile._iter_materialized_directory_files(
            source_root=not_dir,
            include_patterns=(),
        )

    symlink_dir = tmp_path / "symlinked"
    symlink_dir.mkdir()
    (symlink_dir / "linked.txt").symlink_to(regular)
    with pytest.raises(RuntimeError, match="unsupported symlink"):
        codegen_lockfile._iter_materialized_directory_files(
            source_root=symlink_dir,
            include_patterns=(),
        )

    fifo_dir = tmp_path / "fifo"
    fifo_dir.mkdir()
    fifo = fifo_dir / "pipe"
    os.mkfifo(fifo)
    with pytest.raises(RuntimeError, match="unsupported non-regular file"):
        codegen_lockfile._iter_materialized_directory_files(
            source_root=fifo_dir,
            include_patterns=(),
        )

    monkeypatch.setattr(
        codegen_lockfile.http_utils,
        "fetch_url_bytes",
        lambda *_a, **_k: (_ for _ in ()).throw(ValueError("bad")),
    )
    with pytest.raises(RuntimeError, match="Only absolute HTTPS URLs are supported"):
        codegen_lockfile._fetch_https_bytes("https://example.com")

    monkeypatch.setattr(
        codegen_lockfile.http_utils,
        "fetch_url_bytes",
        lambda *_a, **_k: (_ for _ in ()).throw(
            http_utils.RequestError(
                url="https://example.com",
                attempts=1,
                kind="status",
                detail="HTTP 404 Missing",
                status=404,
            )
        ),
    )
    with pytest.raises(
        RuntimeError, match="Failed to fetch https://example.com: HTTP 404 Missing"
    ):
        codegen_lockfile._fetch_https_bytes("https://example.com")

    monkeypatch.setattr(
        codegen_lockfile, "_fetch_https_bytes", lambda _url: (b"[]", {})
    )
    with pytest.raises(TypeError, match="Expected object for JSON response"):
        codegen_lockfile._fetch_https_json("https://example.com/data.json")
    monkeypatch.setattr(codegen_lockfile, "_fetch_https_bytes", lambda _url: (b"{", {}))
    with pytest.raises(RuntimeError, match="Invalid JSON response"):
        codegen_lockfile._fetch_https_json("https://example.com/data.json")

    sha = "a" * 40
    assert codegen_lockfile._resolve_github_commit("o", "r", sha) == sha
    monkeypatch.setattr(
        codegen_lockfile, "_fetch_https_json", lambda _url: {"sha": "short"}
    )
    with pytest.raises(RuntimeError, match="returned invalid SHA"):
        codegen_lockfile._resolve_github_commit("o", "r", "main")
    monkeypatch.setattr(
        codegen_lockfile, "_fetch_https_json", lambda _url: {"sha": sha}
    )
    assert codegen_lockfile._resolve_github_commit("o", "r", "main") == sha

    manifest_dir = tmp_path / "manifest"
    manifest_dir.mkdir()
    source_dir = manifest_dir / "schemas"
    source_dir.mkdir()
    (source_dir / "root.json").write_text("{}\n", encoding="utf-8")
    timestamp = codegen_lockfile._utcnow()
    monkeypatch.setattr(codegen_lockfile, "_utcnow", lambda: timestamp)
    directory_locked = codegen_lockfile._build_locked_directory_source(
        manifest_dir=manifest_dir,
        source_name="dir",
        source={"path": "schemas", "include": ["*.json"]},
        include_metadata=True,
    )
    assert directory_locked["generated_at"] == timestamp
    with pytest.raises(TypeError, match="Expected array for source dir.include"):
        codegen_lockfile._build_locked_directory_source(
            manifest_dir=manifest_dir,
            source_name="dir",
            source={"path": "schemas", "include": "*.json"},
            include_metadata=False,
        )
    absolute_locked = codegen_lockfile._build_locked_directory_source(
        manifest_dir=manifest_dir,
        source_name="dir",
        source={"path": str(source_dir)},
        include_metadata=False,
    )
    assert absolute_locked["path"] == "schemas"

    monkeypatch.setattr(
        codegen_lockfile,
        "_fetch_https_bytes",
        lambda _url: (b"payload", {"Last-Modified": "Tue, 01 Jan 2030 00:00:00 GMT"}),
    )
    url_locked = codegen_lockfile._build_locked_url_source(
        source_name="remote",
        source={"uri": "https://example.com/schema.json"},
        include_metadata=True,
    )
    assert url_locked["last_modified"] == "Tue, 01 Jan 2030 00:00:00 GMT"
    monkeypatch.setattr(
        codegen_lockfile, "_fetch_https_bytes", lambda _url: (b"payload", {})
    )
    url_locked_without_headers = codegen_lockfile._build_locked_url_source(
        source_name="remote",
        source={"uri": "https://example.com/schema.json"},
        include_metadata=True,
    )
    assert "etag" not in url_locked_without_headers
    assert "last_modified" not in url_locked_without_headers

    monkeypatch.setattr(codegen_lockfile, "_resolve_github_commit", lambda *_a: sha)
    monkeypatch.setattr(
        codegen_lockfile, "_fetch_https_bytes", lambda _url: (b"{}", {})
    )
    github_locked = codegen_lockfile._build_locked_github_raw_source(
        source_name="gh",
        source={
            "owner": "o",
            "repo": "r",
            "ref": "main",
            "path": "schemas/root.json",
        },
        include_metadata=True,
    )
    assert "fetched_at" in github_locked
    with pytest.raises(TypeError, match="Expected object for source gh.metadata"):
        codegen_lockfile._build_locked_github_raw_source(
            source_name="gh",
            source={
                "owner": "o",
                "repo": "r",
                "ref": "main",
                "path": "schemas/root.json",
                "metadata": "bad",
            },
            include_metadata=True,
        )
    github_locked_sparse = codegen_lockfile._build_locked_github_raw_source(
        source_name="gh",
        source={
            "owner": "o",
            "repo": "r",
            "ref": "main",
            "path": "schemas/root.json",
            "metadata": {"tag": None, "package": "pkg"},
        },
        include_metadata=True,
    )
    assert github_locked_sparse["package"] == "pkg"
    assert "tag" not in github_locked_sparse

    manifest_path = tmp_path / "codegen.yaml"
    manifest_path.write_text("version: 1\n", encoding="utf-8")
    monkeypatch.setattr(
        codegen_lockfile,
        "load_codegen_manifest",
        lambda *, manifest_path: type(
            "Manifest",
            (),
            {
                "model_dump": lambda self, mode: {
                    "sources": {"bad": {"kind": "mystery"}}
                }
            },
        )(),
    )
    with pytest.raises(RuntimeError, match="Unsupported source kind 'mystery'"):
        codegen_lockfile.build_codegen_lockfile(manifest_path=manifest_path)

    with pytest.raises(TypeError, match="Canonical JSON does not permit float values"):
        codegen_lockfile._ensure_canonical_json_value(1.5, context="value")
    assert codegen_lockfile._ensure_canonical_json_value(
        [1, {"ok": True}], context="value"
    ) == [1, {"ok": True}]


def test_mac_apps_helper_additional_paths(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Cover CLI parsing, fs cleanup, rsync, stale cleanup, and main dispatch."""
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
    assert mac_apps_helper._load_payload(["prog", "cmd", str(payload_path)]) == (
        "cmd",
        {"ok": True},
    )

    with pytest.raises(SystemExit) as usage_exc:
        mac_apps_helper._load_payload(["prog"])
    assert usage_exc.value.code == 2
    assert "usage: mac_apps_helper.py" in capsys.readouterr().err

    payload_path.write_text("[]", encoding="utf-8")
    with pytest.raises(SystemExit) as dict_exc:
        mac_apps_helper._load_payload(["prog", "cmd", str(payload_path)])
    assert dict_exc.value.code == 2
    assert "expected JSON object payload" in capsys.readouterr().err

    missing = tmp_path / "missing"
    mac_apps_helper._remove_path(missing)
    file_path = tmp_path / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    mac_apps_helper._remove_path(file_path)
    assert not file_path.exists()
    dir_path = tmp_path / "dir"
    dir_path.mkdir()
    mac_apps_helper._remove_path(dir_path)
    assert not dir_path.exists()
    fifo_path = tmp_path / "pipe"
    os.mkfifo(fifo_path)
    mac_apps_helper._remove_path(fifo_path)
    assert not fifo_path.exists()

    assert mac_apps_helper._read_manifest(tmp_path / "nope.txt") == []
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    state_file = state_dir / "current.txt"
    state_file.write_text("Example.app\n", encoding="utf-8")
    (state_dir / "other.txt").write_text("Other.app\n", encoding="utf-8")
    (state_dir / "other-dir.txt").mkdir()
    assert (
        mac_apps_helper._app_in_other_manifests("Example.app", state_dir, state_file)
        is False
    )
    assert (
        mac_apps_helper._app_in_other_manifests("Other.app", state_dir, state_file)
        is True
    )

    calls: list[list[str]] = []

    def _run_success(
        cmd: list[str], *, check: bool
    ) -> subprocess.CompletedProcess[str]:
        assert check is False
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(mac_apps_helper.subprocess, "run", _run_success)
    src = tmp_path / "src.app"
    dst = tmp_path / "dst.app"
    mac_apps_helper._rsync_copy(src, dst, rsync_path="/usr/bin/rsync", writable=True)
    assert calls == [
        [
            "/usr/bin/rsync",
            "--checksum",
            "--copy-unsafe-links",
            "--archive",
            "--delete",
            "--chmod=+w",
            "--no-group",
            "--no-owner",
            f"{src}/",
            str(dst),
        ]
    ]

    monkeypatch.setattr(
        mac_apps_helper.subprocess,
        "run",
        lambda cmd, *, check: subprocess.CompletedProcess(cmd, 7),
    )
    with pytest.raises(SystemExit) as rsync_exc:
        mac_apps_helper._rsync_copy(
            src, dst, rsync_path="/usr/bin/rsync", writable=False
        )
    assert rsync_exc.value.code == 7

    apps_dir = tmp_path / "Applications"
    apps_dir.mkdir()
    source_bundle = tmp_path / "Source.app"
    source_bundle.mkdir()
    existing = apps_dir / "Source.app"
    existing.write_text("old", encoding="utf-8")
    mac_apps_helper._install_managed_app(
        bundle_name="Source.app",
        mode="symlink",
        source_path=str(source_bundle),
        target_directory=apps_dir,
        rsync_path="/usr/bin/rsync",
        writable=False,
    )
    assert existing.is_symlink()
    assert existing.resolve() == source_bundle.resolve()

    with pytest.raises(SystemExit, match="1"):
        mac_apps_helper._install_managed_app(
            bundle_name="Missing.app",
            mode="copy",
            source_path=str(tmp_path / "Missing.app"),
            target_directory=apps_dir,
            rsync_path="/usr/bin/rsync",
            writable=False,
        )

    copied_bundle = tmp_path / "Copied.app"
    copied_bundle.mkdir()
    existing_dir = apps_dir / "Copied.app"
    existing_dir.mkdir()
    copied: list[Path] = []
    monkeypatch.setattr(
        mac_apps_helper,
        "_rsync_copy",
        lambda src, dst, *, rsync_path, writable: copied.extend([src, dst]),
    )
    mac_apps_helper._install_managed_app(
        bundle_name="Copied.app",
        mode="copy",
        source_path=str(copied_bundle),
        target_directory=apps_dir,
        rsync_path="/usr/bin/rsync",
        writable=False,
    )
    assert copied == [copied_bundle, existing_dir]

    package_path = tmp_path / "pkg"
    applications = package_path / "Applications"
    applications.mkdir(parents=True)
    (applications / "Cursor.app").write_text("not a dir", encoding="utf-8")
    mac_apps_helper._profile_bundle_leak_audit({
        "label": "home.packages",
        "managedBundleNames": ["Cursor.app"],
        "packagePaths": [str(package_path), str(tmp_path / "missing-package")],
    })

    stale_target = apps_dir / "Stale.app"
    stale_target.mkdir()
    managed_bundle = tmp_path / "Managed.app"
    managed_bundle.mkdir()
    state_dir2 = tmp_path / "state2"
    state_dir2.mkdir()
    (state_dir2 / "lane.txt").write_text("\nStale.app\nManaged.app\n", encoding="utf-8")
    mac_apps_helper._system_applications({
        "entries": [
            {
                "bundleName": "Managed.app",
                "mode": "symlink",
                "sourcePath": str(managed_bundle),
            }
        ],
        "rsyncPath": "/usr/bin/rsync",
        "stateDirectory": str(state_dir2),
        "stateName": "lane",
        "targetDirectory": str(apps_dir),
        "writable": False,
    })
    assert not stale_target.exists()
    assert (state_dir2 / "lane.txt").read_text(encoding="utf-8") == "Managed.app\n"

    system_payload = tmp_path / "system.json"
    system_payload.write_text(
        json.dumps({
            "entries": [],
            "rsyncPath": "/usr/bin/rsync",
            "stateDirectory": str(tmp_path / "state-main"),
            "stateName": "main",
            "targetDirectory": str(tmp_path / "apps-main"),
            "writable": False,
        }),
        encoding="utf-8",
    )
    assert (
        mac_apps_helper.main(["prog", "system-applications", str(system_payload)]) == 0
    )

    audit_payload = tmp_path / "audit.json"
    audit_payload.write_text(
        json.dumps({
            "label": "home.packages",
            "managedBundleNames": [],
            "packagePaths": [],
        }),
        encoding="utf-8",
    )
    assert (
        mac_apps_helper.main(["prog", "profile-bundle-leak-audit", str(audit_payload)])
        == 0
    )
    assert mac_apps_helper.main(["prog", "unknown", str(audit_payload)]) == 2
    assert "unknown command: unknown" in capsys.readouterr().err


def test_mac_apps_helper_module_main_guard_executes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Run the module as __main__ so the CLI guard stays covered."""
    payload = tmp_path / "payload.json"
    payload.write_text(
        json.dumps({
            "label": "home.packages",
            "managedBundleNames": [],
            "packagePaths": [],
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        sys, "argv", ["mac_apps_helper.py", "profile-bundle-leak-audit", str(payload)]
    )
    imported = sys.modules.pop("lib.mac_apps_helper", None)
    try:
        with pytest.raises(SystemExit) as exc:
            runpy.run_module("lib.mac_apps_helper", run_name="__main__")
        assert exc.value.code == 0
    finally:
        if imported is not None:
            sys.modules["lib.mac_apps_helper"] = imported
