"""Tests for shared synchronous HTTP and GitHub auth helpers."""

import base64
import logging
from pathlib import Path
from typing import Self

import httpx
import pytest

from lib import http_utils


class _FakeResponse:
    url = "https://example.com/"
    history: tuple[object, ...] = ()

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
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        follow_redirects: bool = False,
    ) -> _FakeResponse:
        _ = follow_redirects
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


def test_resolve_github_token_logs_keyring_and_netrc_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Log malformed keyring/netrc lookups when a logger is provided."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(
        http_utils.keyring,
        "get_password",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("keyring-boom")),
    )
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".netrc").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        http_utils.netrc,
        "netrc",
        lambda _path: (_ for _ in ()).throw(OSError("boom")),
    )

    logger = logging.getLogger("http-utils-test")
    with caplog.at_level(logging.WARNING):
        assert (
            http_utils.resolve_github_token(
                allow_keyring=True,
                allow_netrc=True,
                logger=logger,
            )
            is None
        )
    assert "Failed to read GitHub token from keyring" in caplog.text
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
    with pytest.raises(http_utils.RequestError, match="HTTP 404 Missing") as exc_info:
        http_utils.fetch_url_bytes("https://example.com/missing", backoff=0.0)
    assert exc_info.value.kind == "status"
    assert exc_info.value.status == 404
    assert exc_info.value.attempts == 1

    timeout_client = _FakeClient([http_utils.httpx.ReadTimeout("slow")])
    monkeypatch.setattr(http_utils.httpx, "Client", lambda **_kwargs: timeout_client)
    with pytest.raises(http_utils.RequestError, match="slow") as exc_info:
        http_utils.fetch_url_bytes(
            "https://example.com/slow",
            attempts=1,
            backoff=0.0,
        )
    assert exc_info.value.kind == "timeout"
    assert exc_info.value.attempts == 1

    network_client = _FakeClient([http_utils.httpx.ConnectError("down")])
    monkeypatch.setattr(http_utils.httpx, "Client", lambda **_kwargs: network_client)
    with pytest.raises(http_utils.RequestError, match="down") as exc_info:
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


def _redirect_to(target: str) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": target})
        return httpx.Response(200, content=b"body")

    return httpx.MockTransport(_handler)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("target", "allowed"),
    [
        ("https://example.com/final", True),
        ("http://example.com/final", False),
    ],
)
async def test_fetch_url_bytes_revalidates_redirect_targets(
    monkeypatch: pytest.MonkeyPatch,
    target: str,
    *,
    allowed: bool,
) -> None:
    """A redirect must not downgrade an HTTPS-only fetch to another scheme."""
    real_client = httpx.Client
    transport = _redirect_to(target)
    monkeypatch.setattr(
        http_utils.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    async_client = httpx.AsyncClient(transport=transport, follow_redirects=True)
    start = "https://example.com/start"
    if allowed:
        assert http_utils.fetch_url_bytes(start)[0] == b"body"
        payload, _ = await http_utils.fetch_url_bytes_async(start, client=async_client)
        assert payload == b"body"
        return
    with pytest.raises(ValueError, match=f"Only absolute HTTPS URLs.*{target}"):
        http_utils.fetch_url_bytes(start)
    with pytest.raises(ValueError, match=f"Only absolute HTTPS URLs.*{target}"):
        await http_utils.fetch_url_bytes_async(start, client=async_client)


def test_next_redirect_request_rejects_missing_native_request() -> None:
    """A redirect without httpx's native next request fails before dispatch."""
    response = httpx.Response(
        302,
        headers={"Location": "/next"},
        request=httpx.Request("GET", "https://example.com/start"),
    )
    with pytest.raises(RuntimeError, match="did not include a next request"):
        http_utils._next_redirect_request(
            response, allowed_schemes=frozenset({"https"})
        )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "targets",
    [
        ["http://example.com/plain"],
        ["https://example.com/allowed", "http://example.com/plain"],
    ],
)
async def test_fetch_url_bytes_never_issues_insecure_redirect_requests(
    monkeypatch: pytest.MonkeyPatch,
    targets: list[str],
) -> None:
    """Validation must happen before every redirect hop, including chained hops."""
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/start":
            return httpx.Response(302, headers={"Location": targets[0]})
        if request.url.path == "/allowed" and len(targets) > 1:
            return httpx.Response(302, headers={"Location": targets[1]})
        return httpx.Response(200, content=b"unexpected")

    transport = httpx.MockTransport(_handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        http_utils.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    start = "https://example.com/start"
    with pytest.raises(ValueError, match="Only absolute HTTPS URLs"):
        http_utils.fetch_url_bytes(start)
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        with pytest.raises(ValueError, match="Only absolute HTTPS URLs"):
            await http_utils.fetch_url_bytes_async(start, client=client)
    assert [str(request.url) for request in requests] == [
        start,
        *targets[:-1],
        start,
        *targets[:-1],
    ]
    assert targets[-1] not in [str(request.url) for request in requests]
    assert all(request.url.scheme == "https" for request in requests)


@pytest.mark.anyio
async def test_fetch_url_bytes_preserves_native_redirect_request_semantics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legitimate redirects retain headers while cross-origin credentials are stripped."""
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/start":
            return httpx.Response(
                302,
                headers={"Location": "https://cdn.example.com/final"},
            )
        return httpx.Response(200, content=b"body")

    transport = httpx.MockTransport(_handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        http_utils.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, **kwargs),
    )
    start = "https://example.com/start"
    headers = {"Authorization": "Bearer secret", "Cookie": "manual=1", "X-Test": "yes"}
    assert (
        http_utils.fetch_url_bytes(start, method="POST", headers=headers)[0] == b"body"
    )
    async with httpx.AsyncClient(transport=transport, follow_redirects=True) as client:
        payload, _ = await http_utils.fetch_url_bytes_async(
            start,
            method="POST",
            headers=headers,
            client=client,
        )
    assert payload == b"body"
    assert [request.method for request in requests] == ["POST", "GET", "POST", "GET"]
    for request in (requests[1], requests[3]):
        assert request.headers.get("Authorization") is None
        assert request.headers.get("Cookie") is None
        assert request.headers["X-Test"] == "yes"


@pytest.mark.anyio
async def test_fetch_url_bytes_bounds_manual_redirects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Manual handling retains the client's redirect limit on sync and async paths."""
    requests: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        next_path = "/one" if request.url.path == "/start" else "/two"
        return httpx.Response(302, headers={"Location": next_path})

    transport = httpx.MockTransport(_handler)
    real_client = httpx.Client
    monkeypatch.setattr(
        http_utils.httpx,
        "Client",
        lambda **kwargs: real_client(transport=transport, max_redirects=1, **kwargs),
    )
    with pytest.raises(httpx.TooManyRedirects):
        http_utils.fetch_url_bytes("https://example.com/start")
    async with httpx.AsyncClient(
        transport=transport,
        follow_redirects=True,
        max_redirects=1,
    ) as client:
        with pytest.raises(httpx.TooManyRedirects):
            await http_utils.fetch_url_bytes_async(
                "https://example.com/start", client=client
            )
    assert [request.url.path for request in requests] == [
        "/start",
        "/one",
        "/start",
        "/one",
    ]
