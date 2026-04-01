"""Tests for shared synchronous HTTP and GitHub auth helpers."""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Self

import pytest

from lib import http_utils


class _FakeResponse:
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


class _FakeClient:
    def __init__(self, responses: list[object]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str, dict[str, str]]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        _ = (exc_type, exc, tb)
        return False

    def request(
        self, method: str, url: str, *, headers: dict[str, str]
    ) -> _FakeResponse:
        self.calls.append((method, url, headers))
        response = self._responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class _NetrcObj:
    def __init__(self, auth: tuple[str, str, str] | None) -> None:
        self._auth = auth

    def authenticators(self, host: str) -> tuple[str, str, str] | None:
        if host == "github.com":
            return self._auth
        return None


def test_unwrap_go_keyring_token_variants() -> None:
    """Decode plain and go-keyring-wrapped GitHub tokens."""
    encoded = base64.b64encode(b"ghp-token\n").decode()
    assert http_utils.unwrap_go_keyring_token(" plain ") == "plain"
    assert (
        http_utils.unwrap_go_keyring_token(f"go-keyring-base64:{encoded}")
        == "ghp-token"
    )


def test_resolve_github_token_prefers_env_keyring_and_netrc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Resolve GitHub tokens from env, then keyring, then netrc."""
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.setattr(http_utils.keyring, "get_password", lambda *_a, **_k: "ignored")
    assert (
        http_utils.resolve_github_token(allow_keyring=True, allow_netrc=True)
        == "env-token"
    )

    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        http_utils.keyring, "get_password", lambda *_a, **_k: " keyring "
    )
    assert http_utils.resolve_github_token(allow_keyring=True) == "keyring"

    monkeypatch.setattr(http_utils.keyring, "get_password", lambda *_a, **_k: None)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".netrc").write_text(
        "machine github.com login u password netrc-token\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        http_utils.netrc,
        "netrc",
        lambda _path: _NetrcObj(("u", "x", "netrc-token")),
    )
    assert http_utils.resolve_github_token(allow_netrc=True) == "netrc-token"


def test_resolve_github_token_logs_netrc_parse_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log malformed netrc parsing failures when requested."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".netrc").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        http_utils.netrc,
        "netrc",
        lambda _path: (_ for _ in ()).throw(OSError("boom")),
    )

    logger = logging.getLogger("http-utils-test")
    with caplog.at_level(logging.WARNING):
        assert http_utils.resolve_github_token(allow_netrc=True, logger=logger) is None
    assert "Failed to parse" in caplog.text


def test_build_github_headers_limits_auth_to_api() -> None:
    """Attach auth only to GitHub API requests."""
    github_token = "s" * 6
    headers = http_utils.build_github_headers(
        "https://api.github.com/repos/x/y",
        accept="application/json",
        token=github_token,
        user_agent="ua",
    )
    assert headers == {
        "Accept": "application/json",
        "Authorization": f"Bearer {github_token}",
        "User-Agent": "ua",
    }

    assert (
        http_utils.build_github_headers(
            "https://example.com/data",
            token=github_token,
        )
        == {}
    )


def test_fetch_url_bytes_validates_url_and_handles_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Validate URL parsing and successful response handling."""
    with pytest.raises(ValueError, match="Only absolute HTTPS URLs"):
        http_utils.fetch_url_bytes("http://example.com")

    with pytest.raises(ValueError, match="Could not parse host from URL"):
        http_utils.fetch_url_bytes("https://:443/path")

    client = _FakeClient([
        _FakeResponse(status_code=200, content=b"payload", headers={"X": "1"})
    ])
    captured_wait: dict[str, object] = {}
    real_wait_exponential = http_utils.wait_exponential

    def _wait_exponential(**kwargs: object) -> object:
        captured_wait.update(kwargs)
        return real_wait_exponential(**kwargs)

    monkeypatch.setattr(http_utils.httpx, "Client", lambda **_kwargs: client)
    monkeypatch.setattr(http_utils, "wait_exponential", _wait_exponential)

    payload, headers = http_utils.fetch_url_bytes(
        "https://example.com/data",
        headers={"X-Test": "1"},
        backoff=0.25,
        max_backoff=3.0,
        timeout=7.0,
    )
    assert payload == b"payload"
    assert headers == {"X": "1"}
    assert client.calls == [("GET", "https://example.com/data", {"X-Test": "1"})]
    assert captured_wait == {
        "exp_base": 2,
        "max": 3.0,
        "multiplier": 0.25,
    }


def test_fetch_url_bytes_retries_and_reports_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry retryable statuses and transport errors, then surface details."""
    retry_client = _FakeClient([
        _FakeResponse(status_code=503, reason_phrase="Busy", content=b"busy"),
        _FakeResponse(status_code=200, content=b"ok"),
    ])
    monkeypatch.setattr(http_utils.httpx, "Client", lambda **_kwargs: retry_client)
    payload, headers = http_utils.fetch_url_bytes(
        "https://example.com/retry",
        backoff=0.0,
    )
    assert payload == b"ok"
    assert headers == {}
    assert len(retry_client.calls) == 2

    status_client = _FakeClient([
        _FakeResponse(status_code=404, reason_phrase="Missing", content=b"nope")
    ])
    monkeypatch.setattr(http_utils.httpx, "Client", lambda **_kwargs: status_client)
    with pytest.raises(
        http_utils.SyncRequestError, match="HTTP 404 Missing"
    ) as exc_info:
        http_utils.fetch_url_bytes("https://example.com/missing", backoff=0.0)
    assert exc_info.value.kind == "status"
    assert exc_info.value.status == 404
    assert exc_info.value.attempts == 1

    timeout_client = _FakeClient([http_utils.httpx.ReadTimeout("slow")])
    monkeypatch.setattr(http_utils.httpx, "Client", lambda **_kwargs: timeout_client)
    with pytest.raises(http_utils.SyncRequestError, match="slow") as exc_info:
        http_utils.fetch_url_bytes(
            "https://example.com/slow",
            attempts=1,
            backoff=0.0,
        )
    assert exc_info.value.kind == "timeout"
    assert exc_info.value.attempts == 1

    network_client = _FakeClient([http_utils.httpx.ConnectError("down")])
    monkeypatch.setattr(http_utils.httpx, "Client", lambda **_kwargs: network_client)
    with pytest.raises(http_utils.SyncRequestError, match="down") as exc_info:
        http_utils.fetch_url_bytes(
            "https://example.com/down",
            attempts=1,
            backoff=0.0,
        )
    assert exc_info.value.kind == "network"
    assert exc_info.value.attempts == 1


def test_fetch_url_bytes_rejects_zero_attempts() -> None:
    """Require at least one request attempt."""
    with pytest.raises(RuntimeError, match="Expected at least one HTTP attempt"):
        http_utils.fetch_url_bytes("https://example.com/data", attempts=0)
