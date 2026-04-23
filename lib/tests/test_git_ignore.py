"""Tests for the git-ignore helper script."""

from __future__ import annotations

import base64
import runpy
import sys

import pytest

from lib.import_utils import load_module_from_path
from lib.update.paths import REPO_ROOT


def _load_module(module_name: str = "git_ignore_test"):
    return load_module_from_path(REPO_ROOT / "home/george/bin/git-ignore", module_name)


def test_git_ignore_as_object_dict_validates_shape() -> None:
    """Payload normalization should accept string-keyed dicts only."""
    module = _load_module()

    assert module._as_object_dict({"path": "Python.gitignore"}, context="payload") == {
        "path": "Python.gitignore"
    }

    with pytest.raises(module.GitignoreRequestError, match="invalid payload payload"):
        module._as_object_dict([], context="payload")

    with pytest.raises(module.GitignoreRequestError, match="invalid payload payload"):
        module._as_object_dict({1: "bad"}, context="payload")


def test_git_ignore_load_json_handles_http_edges(monkeypatch) -> None:
    """Network loader should normalize invalid URLs, HTTP errors, and bad JSON."""
    module = _load_module("git_ignore_http_test")

    with pytest.raises(module.GitignoreRequestError, match="invalid GitHub API URL"):
        module._load_json("http://example.com")

    class _Connection:
        def __init__(self, host: str, *, timeout: float) -> None:
            self.host = host
            self.timeout = timeout
            self.closed = False
            self.request_args: tuple[str, str, dict[str, str]] | None = None

        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            self.request_args = (method, path, headers)

        def getresponse(self):
            return type(
                "_Response", (), {"status": 200, "read": lambda self: b'{"ok": 1}'}
            )()

        def close(self) -> None:
            self.closed = True

    seen: dict[str, object] = {}

    def _fake_https_connection(host: str, *, timeout: float):
        conn = _Connection(host, timeout=timeout)
        seen["conn"] = conn
        return conn

    monkeypatch.setattr(module.http.client, "HTTPSConnection", _fake_https_connection)
    assert module._load_json("https://api.github.com/path?q=1") == {"ok": 1}
    conn = seen["conn"]
    assert conn.host == "api.github.com"
    assert conn.timeout == module.REQUEST_TIMEOUT_SECONDS
    assert conn.request_args == (
        "GET",
        "/path?q=1",
        {"User-Agent": "nixcfg-git-ignore/1.0"},
    )
    assert conn.closed is True

    class _ErrorConnection(_Connection):
        def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
            del method, path, headers
            raise OSError("boom")

    monkeypatch.setattr(
        module.http.client,
        "HTTPSConnection",
        lambda host, *, timeout: _ErrorConnection(host, timeout=timeout),
    )
    with pytest.raises(module.GitignoreRequestError, match=r"failed to fetch .*boom"):
        module._load_json("https://api.github.com/path")

    def _response(status: int, body: bytes):
        return type("_Response", (), {"status": status, "read": lambda self: body})()

    class _StaticConnection(_Connection):
        def __init__(self, host: str, *, timeout: float, response: object) -> None:
            super().__init__(host, timeout=timeout)
            self._response = response

        def getresponse(self):
            return self._response

    monkeypatch.setattr(
        module.http.client,
        "HTTPSConnection",
        lambda host, *, timeout: _StaticConnection(
            host,
            timeout=timeout,
            response=_response(module.HTTP_NOT_FOUND, b"{}"),
        ),
    )
    with pytest.raises(module.GitignoreRequestError, match="template URL not found"):
        module._load_json("https://api.github.com/missing")

    monkeypatch.setattr(
        module.http.client,
        "HTTPSConnection",
        lambda host, *, timeout: _StaticConnection(
            host,
            timeout=timeout,
            response=_response(500, b"{}"),
        ),
    )
    with pytest.raises(module.GitignoreRequestError, match="HTTP 500"):
        module._load_json("https://api.github.com/fail")

    monkeypatch.setattr(
        module.http.client,
        "HTTPSConnection",
        lambda host, *, timeout: _StaticConnection(
            host,
            timeout=timeout,
            response=_response(200, b"not-json"),
        ),
    )
    with pytest.raises(module.GitignoreRequestError, match="invalid JSON"):
        module._load_json("https://api.github.com/bad-json")


def test_git_ignore_language_listing_and_template_lookup(monkeypatch) -> None:
    """Listing and template lookup should filter paths and decode content."""
    module = _load_module("git_ignore_lookup_test")
    encoded = base64.b64encode(b"node_modules/\n").decode()
    payloads = {
        module.GITIGNORE_API: [
            {"path": "Python.gitignore"},
            {"path": "Node.gitignore"},
            {"path": "README.md"},
        ],
        f"{module.GITIGNORE_API}/Node.gitignore": {"content": encoded},
    }
    monkeypatch.setattr(module, "_load_json", lambda url: payloads[url])

    assert module.list_gitignore_languages() == ["Python", "Node"]
    assert module.get_gitignore("node") == "node_modules/\n"

    monkeypatch.setattr(module, "_load_json", lambda _url: {"oops": True})
    with pytest.raises(
        module.GitignoreRequestError, match="invalid template index payload"
    ):
        module.list_gitignore_languages()

    monkeypatch.setattr(module, "list_gitignore_languages", lambda: ["Python"])
    with pytest.raises(module.GitignoreNotFoundError) as excinfo:
        module.get_gitignore("Rust")
    assert excinfo.value.language == "Rust"

    monkeypatch.setattr(module, "list_gitignore_languages", lambda: ["Node"])
    monkeypatch.setattr(module, "_load_json", lambda _url: {"content": 42})
    with pytest.raises(
        module.GitignoreRequestError, match="invalid template payload for Node"
    ):
        module.get_gitignore("node")


def test_git_ignore_main_covers_usage_listing_errors_and_template_output(
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI wrapper should handle usage errors, listing, and template rendering."""
    module = _load_module("git_ignore_main_test")

    assert module.main(["Python", "Rust"]) == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "usage: git-ignore [language]\n"

    monkeypatch.setattr(module, "list_gitignore_languages", lambda: ["Node", "Python"])
    assert module.main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == "Node\nPython\n"
    assert captured.err == ""

    monkeypatch.setattr(
        module,
        "list_gitignore_languages",
        lambda: (_ for _ in ()).throw(module.GitignoreRequestError("fetch failed")),
    )
    assert module.main([]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "fetch failed\n"

    monkeypatch.setattr(
        module,
        "get_gitignore",
        lambda _language: (_ for _ in ()).throw(module.GitignoreNotFoundError("Rust")),
    )
    assert module.main(["Rust"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "error: unknown template: Rust\n"

    monkeypatch.setattr(
        module,
        "get_gitignore",
        lambda _language: (_ for _ in ()).throw(
            module.GitignoreRequestError("request failed")
        ),
    )
    assert module.main(["Python"]) == 1
    captured = capsys.readouterr()
    assert captured.err == "request failed\n"

    monkeypatch.setattr(module, "get_gitignore", lambda _language: "dist/")
    assert module.main(["Python"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "dist/\n"
    assert captured.err == ""

    monkeypatch.setattr(module, "get_gitignore", lambda _language: "venv/\n")
    assert module.main(["Python"]) == 0
    captured = capsys.readouterr()
    assert captured.out == "venv/\n"
    assert captured.err == ""


def test_git_ignore_main_guard_exits_with_main_result(monkeypatch) -> None:
    """Executing the script as __main__ should exit with main(argv) result."""
    called: dict[str, object] = {}

    monkeypatch.setattr(sys, "argv", ["git-ignore", "Python", "Rust"])
    monkeypatch.setattr(sys, "exit", lambda code: called.setdefault("exit", code))

    runpy.run_path(
        str(REPO_ROOT / "home/george/bin/git-ignore"),
        run_name="__main__",
    )

    assert called == {"exit": 2}
