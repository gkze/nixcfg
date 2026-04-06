"""Additional tests for Deno lock dependency resolution."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from lib.update import deno_lock


class _Response:
    def __init__(self, *, json_payload: object = None, body: bytes = b"") -> None:
        self._json_payload = json_payload
        self._body = body

    async def __aenter__(self) -> _Response:
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False

    async def json(self) -> object:
        return self._json_payload

    async def read(self) -> bytes:
        return self._body

    def raise_for_status(self) -> None:
        return None


class _Client:
    def __init__(self, payloads: dict[str, _Response]) -> None:
        self._payloads = payloads

    def get(self, url: str) -> _Response:
        return self._payloads[url]


def test_json_adapter_helpers_and_required_string() -> None:
    """Validate strict object/list/string coercion helpers."""
    assert deno_lock._as_object_dict({"x": 1}, context="ctx") == {"x": 1}
    with pytest.raises(TypeError, match="Expected JSON object"):
        deno_lock._as_object_dict([], context="ctx")

    assert deno_lock._as_object_list([1, 2], context="ctx") == [1, 2]
    with pytest.raises(TypeError, match="Expected JSON array"):
        deno_lock._as_object_list({}, context="ctx")

    assert deno_lock._get_required_str({"x": "y"}, "x", context="ctx") == "y"
    with pytest.raises(TypeError, match="Expected string field 'x'"):
        deno_lock._get_required_str({}, "x", context="ctx")
    with pytest.raises(TypeError, match="Expected string field 'x'"):
        deno_lock._get_required_str({"x": 1}, "x", context="ctx")

    assert deno_lock._as_package_map({"a": {}}, context="ctx") == {"a": {}}
    with pytest.raises(TypeError, match="Expected package map"):
        deno_lock._as_package_map([], context="ctx")

    assert deno_lock._parse_jsr_checksum("sha256-deadbeef", context="ctx") == "deadbeef"
    with pytest.raises(ValueError, match="Expected sha256 checksum"):
        deno_lock._parse_jsr_checksum("sha512-deadbeef", context="ctx")
    with pytest.raises(ValueError, match="Expected hexadecimal sha256 checksum"):
        deno_lock._parse_jsr_checksum("sha256-not-hex", context="ctx")


def test_guess_media_type_and_npm_helpers() -> None:
    """Infer media types and npm tarball/cache fields."""
    assert deno_lock._guess_media_type("mod.ts") == "text/typescript"
    assert deno_lock._guess_media_type("mod.jsx") == "text/javascript"
    assert deno_lock._guess_media_type("data.unknown") == "text/plain"
    assert deno_lock._url_to_cache_path(
        "https://example.com/mod.ts?target=esnext"
    ) == deno_lock._url_to_cache_path("https://example.com/mod.ts?target=esnext")
    assert deno_lock._parse_npm_pkg_key("left-pad@1.0.0") == ("left-pad", "1.0.0")
    assert deno_lock._parse_npm_pkg_key("@scope/left-pad@1.0.0_peer@npm:1") == (
        "@scope/left-pad",
        "1.0.0",
    )
    assert (
        deno_lock._npm_tarball_url("@scope/left-pad", "1.0.0")
        == "https://registry.npmjs.org/@scope/left-pad/-/left-pad-1.0.0.tgz"
    )


def test_fetch_jsr_meta_and_resolve_jsr_package() -> None:
    """Resolve JSR package files plus required meta JSON artifacts."""
    pkg_key = "@scope/pkg@1.2.3"
    meta = {
        "manifest": {
            "/mod.ts": {"checksum": "sha256-deadbeef"},
            "/data.json": {"checksum": "sha256-cafebabe"},
        }
    }
    version_meta_body = json.dumps(meta).encode("utf-8")
    responses = {
        "https://jsr.io/@scope/pkg/meta.json": _Response(body=b"{}"),
        "https://jsr.io/@scope/pkg/1.2.3_meta.json": _Response(
            body=version_meta_body,
            json_payload=meta,
        ),
    }
    client = _Client(responses)

    fetched = asyncio.run(deno_lock._fetch_jsr_meta(client, "@scope", "pkg", "1.2.3"))
    assert fetched.document == meta
    assert fetched.payload == version_meta_body

    package = asyncio.run(
        deno_lock._resolve_jsr_package(
            client,
            pkg_key,
            {"integrity": "sha256-integrity"},
        )
    )
    assert package.name == "@scope/pkg"
    assert package.version == "1.2.3"
    assert len(package.files) >= 4
    assert any(file.media_type == "application/json" for file in package.files)


def test_resolve_all_jsr_and_npm_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve package collections and surface per-package failures."""

    class _Session:
        def __init__(self, *args: object, **kwargs: object) -> None:
            _ = (args, kwargs)

        async def __aenter__(self) -> object:
            return object()

        async def __aexit__(self, *_args: object) -> bool:
            return False

    monkeypatch.setattr("lib.update.deno_lock.aiohttp.ClientSession", _Session)

    async def _resolve_pkg(
        _client: object,
        pkg_key: str,
        pkg_info: dict[str, object],
    ) -> deno_lock.JsrPackage:
        _ = pkg_info
        if pkg_key == "bad":
            msg = "boom"
            raise RuntimeError(msg)
        return deno_lock.JsrPackage(
            name=pkg_key,
            version="1.0.0",
            integrity="sha256-x",
            files=[],
        )

    monkeypatch.setattr("lib.update.deno_lock._resolve_jsr_package", _resolve_pkg)
    jsr = asyncio.run(deno_lock._resolve_all_jsr({"b": {}, "a": {}}))
    assert [pkg.name for pkg in jsr] == ["a", "b"]

    with pytest.raises(RuntimeError, match="boom"):
        asyncio.run(deno_lock._resolve_all_jsr({"bad": {}}))

    npm = deno_lock._resolve_all_npm({
        "left-pad@1.0.0": {"integrity": "sha512-a"},
        "left-pad@1.0.0_peer@npm:1": {"integrity": "sha512-a"},
        "@scope/pkg@2.0.0": {"integrity": "sha512-b"},
    })
    assert len(npm) == 2
    assert npm[0].name == "@scope/pkg"


def test_resolve_deno_deps_and_sync_wrapper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Resolve lock payloads and support synchronous wrapper execution."""
    lock_file = tmp_path / "deno.lock"
    lock_file.write_text(
        json.dumps({"version": "6", "jsr": {"a": {}}, "npm": {"b": {}}}),
        encoding="utf-8",
    )

    async def _resolve_jsr(
        _lock_jsr: dict[str, dict[str, object]],
    ) -> list[deno_lock.JsrPackage]:
        return [deno_lock.JsrPackage(name="a", version="1", integrity="x", files=[])]

    monkeypatch.setattr("lib.update.deno_lock._resolve_all_jsr", _resolve_jsr)
    monkeypatch.setattr(
        "lib.update.deno_lock._resolve_all_npm",
        lambda _lock_npm: [
            deno_lock.NpmPackage(
                name="b",
                version="1",
                integrity="y",
                tarball_url="https://registry.npmjs.org/b/-/b-1.tgz",
                cache_path="npm/registry.npmjs.org/b/1",
            )
        ],
    )

    manifest = asyncio.run(deno_lock.resolve_deno_deps(lock_file))
    assert manifest.lock_version == "6"
    assert len(manifest.jsr_packages) == 1
    assert "Unexpected deno.lock version" in caplog.text

    monkeypatch.setattr(
        "lib.update.deno_lock.resolve_deno_deps",
        lambda _path: asyncio.sleep(0, result=manifest),
    )
    assert deno_lock.resolve_deno_deps_sync(lock_file) == manifest
