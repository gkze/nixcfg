"""Tests for HTTP/network helpers used by update flows."""

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import aiohttp
import pytest
from tenacity import AsyncRetrying, RetryCallState

from lib.update import net
from lib.update.config import resolve_config

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


_DEFAULT_RETRIES = 4
_DEFAULT_BACKOFF = 1.5
_MIN_POSITIONAL_ARGS = 2


def _run_with_session[T](
    run: Callable[[aiohttp.ClientSession], Awaitable[T]],
) -> T:
    async def _runner() -> T:
        async with aiohttp.ClientSession() as session:
            return await run(session)

    return asyncio.run(_runner())


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int,
        reason: str,
        payload: bytes,
        headers: dict[str, str] | None = None,
        url: str = "https://example.com",
    ) -> None:
        self.url = url
        self.status = status
        self.reason = reason
        self._payload = payload
        self.headers = headers or {}

    async def read(self) -> bytes:
        """Run this test case."""
        return object.__getattribute__(self, "_payload")


class _FakeResponseCM:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeResponse:
        return object.__getattribute__(self, "_response")

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
        _ = (exc_type, exc, tb)
        return False


def test_is_transient_discovery_failure_classifies_dns_timeouts_and_payloads() -> None:
    """Update 37546260513: wispr-flow RELEASES.json DNS must not fail prepare."""
    dns = RuntimeError(
        "Request to https://dl.wisprflow.com/wispr-flow/darwin/x64/RELEASES.json "
        "failed after 3 attempts: Cannot connect to host dl.wisprflow.com:443 "
        "ssl:default [nodename nor servname provided, or not known]"
    )
    assert net.is_transient_discovery_failure(dns)

    timeout = RuntimeError(
        "Request to https://example.com/RELEASES.json failed after 3 attempts"
    )
    timeout.__cause__ = TimeoutError("timed out")
    assert net.is_transient_discovery_failure(timeout)

    connector = RuntimeError("Request failed after 3 attempts")
    connector.__cause__ = aiohttp.ClientConnectorError(
        aiohttp.client_reqrep.ConnectionKey(
            host="dl.wisprflow.com",
            port=443,
            is_ssl=True,
            ssl=True,
            proxy=None,
            proxy_auth=None,
            proxy_headers_hash=None,
        ),
        OSError("nodename nor servname provided, or not known"),
    )
    assert net.is_transient_discovery_failure(connector)

    cycle = RuntimeError("nodename nor servname provided, or not known")
    wrapper = RuntimeError("wrapper")
    cycle.__cause__ = wrapper
    wrapper.__cause__ = cycle
    assert net.is_transient_discovery_failure(cycle)

    assert not net.is_transient_discovery_failure(
        RuntimeError(
            "Request to https://example.com/RELEASES.json failed after 3 attempts: HTTP 404 Not Found"
        )
    )
    assert not net.is_transient_discovery_failure(
        RuntimeError(
            "Invalid JSON response from https://example.com/RELEASES.json: Expecting value"
        )
    )
    assert not net.is_transient_discovery_failure(TypeError("Expected JSON object"))


def test_get_github_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    monkeypatch.delenv("GH_TOKEN", raising=False)
    assert object.__getattribute__(net, "_get_github_token")() == "env-token"


def test_get_github_token_from_netrc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run this test case."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".netrc").write_text(
        "machine github.com login u password p\n", encoding="utf-8"
    )

    class _NetrcObj:
        def authenticators(self, host: str) -> tuple[str, str, str] | None:
            """Run this test case."""
            if host == "github.com":
                return ("u", "a", "token-from-netrc")
            return None

    monkeypatch.setattr(net.http_utils.netrc, "netrc", lambda _path: _NetrcObj())
    assert object.__getattribute__(net, "_get_github_token")() == "token-from-netrc"


def test_get_github_token_netrc_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Run this test case."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".netrc").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        net.http_utils.netrc,
        "netrc",
        lambda _path: (_ for _ in ()).throw(OSError("boom")),
    )
    with caplog.at_level(logging.WARNING):
        assert object.__getattribute__(net, "_get_github_token")() is None
    assert "Failed to parse" in caplog.text


@pytest.mark.parametrize(
    ("headers", "url", "expected"),
    [
        ({}, "https://api.github.com/x", None),
        ({"X-RateLimit-Remaining": "bad"}, "https://api.github.com/x", None),
        ({"X-RateLimit-Remaining": "1"}, "https://api.github.com/x", None),
        ({"X-RateLimit-Remaining": "0"}, "https://example.com/x", None),
        (
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1700000000"},
            "https://api.github.com/x",
            "GitHub API rate limit exceeded for https://api.github.com/x. "
            "Resets at 2023-11-14T22:13:20+00:00.",
        ),
        (
            {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "n/a"},
            "https://api.github.com/x",
            "GitHub API rate limit exceeded for https://api.github.com/x. "
            "Resets at unknown.",
        ),
    ],
)
def test_github_rate_limit_detail(
    headers: dict[str, str], url: str, expected: str | None
) -> None:
    """Describe only an exhausted GitHub API quota."""
    assert net._github_rate_limit_detail(headers, url) == expected


def test_build_headers_and_http_error_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    monkeypatch.setattr(net, "_get_github_token", lambda: "tok")
    headers = object.__getattribute__(net, "_build_request_headers")(
        "https://api.github.com/repos/x/y", "ua"
    )
    assert headers["User-Agent"] == "ua"
    assert headers["Authorization"] == "Bearer tok"

    nongh = object.__getattribute__(net, "_build_request_headers")(
        "https://example.com", None
    )
    assert "Authorization" not in nongh


def test_request_success_and_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    requests: list[dict[str, object]] = []
    response_queue: list[_FakeResponse] = [
        _FakeResponse(
            status=200,
            reason="OK",
            payload=b"payload",
            headers={"X": "1"},
        )
    ]

    def _fake_request(
        self: aiohttp.ClientSession,
        method: str,
        url: str,
        **kwargs: object,
    ) -> _FakeResponseCM:
        _ = self
        requests.append({"method": method, "url": url, **kwargs})
        return _FakeResponseCM(response_queue.pop(0))

    monkeypatch.setattr(aiohttp.ClientSession, "request", _fake_request)
    monkeypatch.setattr(net, "_build_request_headers", lambda _url, _ua: {"H": "v"})

    cfg = resolve_config(
        http_timeout=7,
        retries=_DEFAULT_RETRIES,
        retry_backoff=_DEFAULT_BACKOFF,
    )
    response = _run_with_session(
        lambda session: net.request(session, "https://example.com", config=cfg)
    )
    assert response == net.HTTPResponse(
        status=200, url="https://example.com", headers={"X": "1"}, body=b"payload"
    )
    assert len(requests) == 1
    assert requests[0]["method"] == "GET"

    requests.clear()
    response_queue.extend([
        _FakeResponse(
            status=502,
            reason="Bad Gateway",
            payload=b"upstream",
        ),
        _FakeResponse(
            status=502,
            reason="Bad Gateway",
            payload=b"upstream",
        ),
    ])

    expected_attempts = 2
    with pytest.raises(
        RuntimeError,
        match=rf"failed after {expected_attempts} attempts",
    ):
        _run_with_session(
            lambda session: net.request(
                session,
                "https://example.com",
                retries=expected_attempts,
                backoff=0.0,
                config=cfg,
            )
        )

    assert len(requests) == expected_attempts


@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [
        ("0", 0.0),
        ("7", 7.0),
        ("999999", 300.0),
        ("Fri, 31 Dec 9999 23:59:59 GMT", 300.0),
        (None, 3.0),
        ("not-a-date", 3.0),
        ("31 Dec 9999 23:59:59", 3.0),
        ("Thu, 01 Jan 1970 00:00:00 GMT", 3.0),
    ],
)
def test_request_honors_bounded_retry_after_or_exponential_fallback(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str | None,
    expected_delay: float,
) -> None:
    """Use valid server cooldowns and fall back for absent or unusable values."""
    response_headers = {} if retry_after is None else {"Retry-After": retry_after}
    response_queue = [
        _FakeResponse(
            status=429,
            reason="Too Many Requests",
            payload=b"slow down",
            headers=response_headers,
        ),
        _FakeResponse(status=200, reason="OK", payload=b"payload"),
    ]
    requests = 0

    def _fake_request(
        self: aiohttp.ClientSession,
        method: str,
        url: str,
        **kwargs: object,
    ) -> _FakeResponseCM:
        _ = (self, method, url, kwargs)
        nonlocal requests
        requests += 1
        return _FakeResponseCM(response_queue.pop(0))

    observed_delays: list[float] = []
    real_retry_wait = net._retry_wait

    def _recording_retry_wait(
        backoff: float,
    ) -> Callable[[RetryCallState], float]:
        wait = real_retry_wait(backoff)

        def _wait(retry_state: RetryCallState) -> float:
            observed_delays.append(wait(retry_state))
            return 0.0

        return _wait

    monkeypatch.setattr(aiohttp.ClientSession, "request", _fake_request)
    monkeypatch.setattr(net, "_build_request_headers", lambda _url, _ua: {})
    monkeypatch.setattr(net, "_retry_wait", _recording_retry_wait)

    response = _run_with_session(
        lambda session: net.request(
            session,
            "https://example.com",
            retries=2,
            backoff=3.0,
        )
    )

    assert response.body == b"payload"
    assert requests == 2
    assert observed_delays == [expected_delay]


@pytest.mark.parametrize(
    "retry_after",
    ["7", "Fri, 31 Dec 9999 23:59:59 GMT"],
)
def test_request_retries_forbidden_only_with_valid_retry_after(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str,
) -> None:
    """A server-directed 403 cooldown is transient rather than an auth failure."""
    responses = [
        _FakeResponse(
            status=403,
            reason="Forbidden",
            payload=b"temporarily throttled",
            headers={"Retry-After": retry_after},
        ),
        _FakeResponse(status=200, reason="OK", payload=b"payload"),
    ]
    requests = 0

    def _fake_request(
        _self: aiohttp.ClientSession,
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> _FakeResponseCM:
        nonlocal requests
        requests += 1
        return _FakeResponseCM(responses.pop(0))

    monkeypatch.setattr(aiohttp.ClientSession, "request", _fake_request)
    monkeypatch.setattr(net, "_build_request_headers", lambda _url, _ua: {})
    monkeypatch.setattr(net, "_retry_wait", lambda _backoff: lambda _state: 0.0)

    response = _run_with_session(
        lambda session: net.request(
            session,
            "https://example.com/rate-limited",
            retries=2,
        )
    )

    assert response.body == b"payload"
    assert requests == 2


@pytest.mark.parametrize(
    "retry_after",
    [None, "not-a-date", "Thu, 01 Jan 1970 00:00:00 GMT"],
)
def test_request_keeps_ordinary_forbidden_responses_permanent(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str | None,
) -> None:
    """Missing or invalid cooldown metadata must not retry an authentication 403."""
    requests = 0
    headers = {} if retry_after is None else {"Retry-After": retry_after}

    def _fake_request(
        _self: aiohttp.ClientSession,
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> _FakeResponseCM:
        nonlocal requests
        requests += 1
        return _FakeResponseCM(
            _FakeResponse(
                status=403,
                reason="Forbidden",
                payload=b"bad credentials",
                headers=headers,
            )
        )

    monkeypatch.setattr(aiohttp.ClientSession, "request", _fake_request)
    monkeypatch.setattr(net, "_build_request_headers", lambda _url, _ua: {})

    with pytest.raises(RuntimeError, match="HTTP 403 Forbidden"):
        _run_with_session(
            lambda session: net.request(
                session,
                "https://example.com/auth",
                retries=3,
                backoff=0.0,
            )
        )

    assert requests == 1


def test_retry_wait_without_outcome_uses_exponential_fallback() -> None:
    """Preserve exponential behavior when Tenacity has no response outcome."""
    retry_state = RetryCallState(AsyncRetrying(), None, (), {})
    assert net._retry_wait(3.0)(retry_state) == 3.0


def test_request_rejects_non_https_targets() -> None:
    """Reject update fetch targets before opening a request."""
    with pytest.raises(ValueError, match="Only absolute HTTPS URLs are allowed"):
        _run_with_session(
            lambda session: net.request(
                session,
                "file:///tmp/source.json",
            )
        )


def test_request_does_not_close_callers_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Run this test case."""

    def _fake_request(
        self: aiohttp.ClientSession,
        method: str,
        url: str,
        **kwargs: object,
    ) -> _FakeResponseCM:
        _ = (self, method, url, kwargs)
        return _FakeResponseCM(
            _FakeResponse(
                status=200,
                reason="OK",
                payload=b"payload",
            )
        )

    monkeypatch.setattr(aiohttp.ClientSession, "request", _fake_request)
    monkeypatch.setattr(net, "_build_request_headers", lambda _url, _ua: {})

    async def _runner() -> bool:
        async with aiohttp.ClientSession() as session:
            await net.request(
                session,
                "https://example.com",
            )
            return session.closed

    assert asyncio.run(_runner()) is False


def test_request_retries_on_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    call_count = 0

    def _fake_request(
        self: aiohttp.ClientSession,
        method: str,
        url: str,
        **kwargs: object,
    ) -> _FakeResponseCM:
        _ = (self, method, url, kwargs)
        nonlocal call_count
        call_count += 1
        msg = "network down"
        raise aiohttp.ClientConnectionError(msg)

    monkeypatch.setattr(aiohttp.ClientSession, "request", _fake_request)
    monkeypatch.setattr(net, "_build_request_headers", lambda _url, _ua: {})

    expected_attempts = 3
    with pytest.raises(
        RuntimeError,
        match=rf"failed after {expected_attempts} attempts",
    ):
        _run_with_session(
            lambda session: net.request(
                session,
                "https://example.com",
                retries=expected_attempts,
                backoff=0.0,
            )
        )

    assert call_count == expected_attempts


def _response(
    status: int = 200,
    *,
    body: bytes = b"",
    headers: dict[str, str] | None = None,
) -> net.HTTPResponse:
    return net.HTTPResponse(
        status=status, url="https://example.com", headers=headers or {}, body=body
    )


def test_fetch_url_and_headers_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thin wrappers delegate to ``request`` with their method."""
    calls: list[dict[str, object]] = []

    async def _request(*args: object, **kwargs: object) -> net.HTTPResponse:
        assert len(args) >= _MIN_POSITIONAL_ARGS
        calls.append({"url": args[1], **kwargs})
        return _response(body=b"body", headers={"X": "y"})

    monkeypatch.setattr(net, "request", _request)
    body = _run_with_session(
        lambda session: net.fetch_url(
            session,
            "https://example.com",
            request_timeout=2.0,
        )
    )
    assert body == b"body"
    assert calls[-1]["request_timeout"] == 2.0

    headers = _run_with_session(
        lambda session: net.fetch_headers(session, "https://example.com")
    )
    assert headers == {"X": "y"}
    assert calls[-1]["method"] == "HEAD"


def test_fetch_json_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Decode JSON bodies and name the URL on invalid payloads."""
    monkeypatch.setattr(
        net, "fetch_url", lambda *_a, **_k: asyncio.sleep(0, result=b"[1,2]")
    )
    assert _run_with_session(
        lambda session: net.fetch_json(session, "https://example.com/data")
    ) == [1, 2]

    for payload in (b"not json", b"\xff"):
        monkeypatch.setattr(
            net,
            "fetch_url",
            lambda *_a, payload=payload, **_k: asyncio.sleep(0, result=payload),
        )
        with pytest.raises(
            RuntimeError, match="Invalid JSON response from https://example.com/bad"
        ):
            _run_with_session(
                lambda session: net.fetch_json(session, "https://example.com/bad")
            )


def _install_responses(
    monkeypatch: pytest.MonkeyPatch, responses: list[_FakeResponse]
) -> list[dict[str, object]]:
    requests: list[dict[str, object]] = []

    def _fake_request(
        _self: aiohttp.ClientSession, method: str, url: str, **kwargs: object
    ) -> _FakeResponseCM:
        requests.append({"method": method, "url": url, **kwargs})
        return _FakeResponseCM(responses.pop(0))

    monkeypatch.setattr(aiohttp.ClientSession, "request", _fake_request)
    monkeypatch.setattr(net, "_build_request_headers", lambda _url, _ua: {"A": "1"})
    monkeypatch.setattr(net, "_retry_wait", lambda _backoff: lambda _state: 0.0)
    return requests


def test_request_sends_headers_body_and_redirect_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Caller headers override defaults; JSON body and redirect mode pass through."""
    requests = _install_responses(
        monkeypatch,
        [
            _FakeResponse(
                status=302,
                reason="Found",
                payload=b"",
                headers={"Location": "https://cdn.example.com/a"},
            )
        ],
    )
    response = _run_with_session(
        lambda session: net.request(
            session,
            "https://example.com",
            method="POST",
            headers={"A": "2", "B": "3"},
            json_body={"k": "v"},
            allow_redirects=False,
        )
    )
    assert response.status == 302
    assert response.headers["Location"] == "https://cdn.example.com/a"
    assert requests[0]["method"] == "POST"
    assert requests[0]["headers"] == {"A": "2", "B": "3"}
    assert requests[0]["json"] == {"k": "v"}
    assert requests[0]["allow_redirects"] is False


@pytest.mark.parametrize(
    ("status", "allow_redirects"),
    [(302, False), (300, True), (304, True)],
)
def test_request_returns_unfollowed_response_without_validating_location(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    *,
    allow_redirects: bool,
) -> None:
    """Only a redirect hop that will be followed requires a validated target."""
    location = "http://example.com/insecure"
    requests = _install_responses(
        monkeypatch,
        [
            _FakeResponse(
                status=status,
                reason="Redirect",
                payload=b"body",
                headers={"Location": location},
            )
        ],
    )

    response = _run_with_session(
        lambda session: net.request(
            session,
            "https://example.com/start",
            allow_redirects=allow_redirects,
            retries=1,
        )
    )

    assert response.status == status
    assert response.body == b"body"
    assert response.headers["Location"] == location
    assert len(requests) == 1


def test_request_rejects_redirect_to_non_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A followed redirect must not downgrade to plain HTTP."""
    _install_responses(
        monkeypatch,
        [
            _FakeResponse(
                status=200,
                reason="OK",
                payload=b"x",
                url="http://example.com/plain",
            )
        ],
    )
    with pytest.raises(ValueError, match="Only absolute HTTPS URLs are allowed"):
        _run_with_session(lambda session: net.request(session, "https://example.com"))


@pytest.mark.parametrize(
    "targets",
    [
        ["http://example.com/plain"],
        ["https://example.com/allowed", "http://example.com/plain"],
    ],
)
def test_request_never_issues_insecure_redirect_requests(
    monkeypatch: pytest.MonkeyPatch,
    targets: list[str],
) -> None:
    """Aiohttp must validate a redirect target before issuing its next request."""
    start = "https://example.com/start"
    responses = [
        _FakeResponse(
            status=302,
            reason="Found",
            payload=b"",
            headers={"Location": targets[0]},
            url=start,
        )
    ]
    if len(targets) > 1:
        responses.append(
            _FakeResponse(
                status=302,
                reason="Found",
                payload=b"",
                headers={"Location": targets[1]},
                url=targets[0],
            )
        )
    requests = _install_responses(monkeypatch, responses)
    with pytest.raises(ValueError, match="Only absolute HTTPS URLs"):
        _run_with_session(lambda session: net.request(session, start, retries=1))
    assert [call["url"] for call in requests] == [start, *targets[:-1]]
    assert targets[-1] not in [call["url"] for call in requests]


def test_request_follows_valid_redirects_with_native_header_and_method_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Valid redirects remain functional while cross-origin credentials are removed."""
    start = "https://example.com/start"
    target = "https://cdn.example.com/final"
    requests = _install_responses(
        monkeypatch,
        [
            _FakeResponse(
                status=302,
                reason="Found",
                payload=b"",
                headers={"Location": target},
                url=start,
            ),
            _FakeResponse(status=200, reason="OK", payload=b"body", url=target),
        ],
    )
    response = _run_with_session(
        lambda session: net.request(
            session,
            start,
            method="POST",
            headers={
                "Authorization": "Bearer secret",
                "Cookie": "a=1",
                "X-Test": "yes",
            },
            retries=1,
        )
    )
    assert response.body == b"body"
    assert response.url == target
    assert len(requests) == 2
    assert all(call["allow_redirects"] is False for call in requests)
    assert requests[1]["method"] == "GET"
    assert requests[1]["headers"] == {"A": "1", "X-Test": "yes"}


def test_request_preserves_same_origin_redirect_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Relative same-origin redirects retain the caller's headers and method."""
    start = "https://example.com/start"
    requests = _install_responses(
        monkeypatch,
        [
            _FakeResponse(
                status=307,
                reason="Temporary Redirect",
                payload=b"",
                headers={"URI": "/final"},
                url=start,
            ),
            _FakeResponse(
                status=200,
                reason="OK",
                payload=b"body",
                url="https://example.com/final",
            ),
        ],
    )
    response = _run_with_session(
        lambda session: net.request(
            session,
            start,
            method="PUT",
            headers={"Authorization": "Bearer secret", "Cookie": "a=1"},
            retries=1,
        )
    )
    assert response.body == b"body"
    assert requests[1]["url"] == "https://example.com/final"
    assert requests[1]["method"] == "PUT"
    assert requests[1]["headers"]["Authorization"] == "Bearer secret"
    assert requests[1]["headers"]["Cookie"] == "a=1"


def test_request_bounds_manual_redirects(monkeypatch: pytest.MonkeyPatch) -> None:
    """The aiohttp-compatible ten-redirect ceiling remains in force."""
    responses = []
    for index in range(10):
        response = _FakeResponse(
            status=302,
            reason="Found",
            payload=b"",
            headers={"Location": f"/hop-{index + 1}"},
            url=f"https://example.com/hop-{index}",
        )
        response.request_info = SimpleNamespace(real_url=response.url)
        responses.append(response)
    requests = _install_responses(monkeypatch, responses)
    with pytest.raises(RuntimeError, match="failed after 1 attempts"):
        _run_with_session(
            lambda session: net.request(
                session, "https://example.com/hop-0", retries=1, backoff=0.0
            )
        )
    assert len(requests) == 10


def test_request_rejects_insecure_response_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final HTTPS response cannot conceal an insecure URL in its history."""
    response = _FakeResponse(status=200, reason="OK", payload=b"body")
    response.history = (SimpleNamespace(url="http://example.com/plain"),)
    _install_responses(monkeypatch, [response])
    with pytest.raises(ValueError, match="Only absolute HTTPS URLs"):
        _run_with_session(
            lambda session: net.request(session, "https://example.com", retries=1)
        )


def test_request_returns_valid_github_payload_with_zero_remaining_quota(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The last request of a GitHub quota window still yields its payload."""
    _install_responses(
        monkeypatch,
        [
            _FakeResponse(
                status=200,
                reason="OK",
                payload=b"[]",
                headers={"X-RateLimit-Remaining": "0"},
                url="https://api.github.com/x",
            )
        ],
    )
    assert (
        _run_with_session(
            lambda session: net.fetch_json(session, "https://api.github.com/x")
        )
        == []
    )


@pytest.mark.parametrize(("retry_after", "expected_requests"), [(None, 1), ("1", 2)])
def test_request_fails_fast_on_exhausted_github_quota(
    monkeypatch: pytest.MonkeyPatch,
    retry_after: str | None,
    expected_requests: int,
) -> None:
    """An exhausted primary quota fails fast unless the server names a cooldown."""
    headers = {"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "n/a"}
    if retry_after is not None:
        headers["Retry-After"] = retry_after
    limited = _FakeResponse(
        status=403,
        reason="Forbidden",
        payload=b"limited",
        headers=headers,
        url="https://api.github.com/x",
    )
    requests = _install_responses(monkeypatch, [limited, limited])
    with pytest.raises(RuntimeError, match="rate limit exceeded|HTTP 403"):
        _run_with_session(
            lambda session: net.request(session, "https://api.github.com/x", retries=2)
        )
    assert len(requests) == expected_requests


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (
            307,
            {"Location": "/desktop/stable/1/x.dmg"},
            "https://example.com/desktop/stable/1/x.dmg",
        ),
        (
            302,
            {"Location": "https://cdn.example.com/a.dmg"},
            "https://cdn.example.com/a.dmg",
        ),
        (200, {"Location": "https://cdn.example.com/a.dmg"}, "Expected a redirect"),
        (302, {}, "did not include Location"),
    ],
)
def test_fetch_redirect_location(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    headers: dict[str, str],
    expected: str,
) -> None:
    """Return the absolute Location of a 3xx and reject anything else."""
    calls: list[dict[str, object]] = []

    async def _request(*_args: object, **kwargs: object) -> net.HTTPResponse:
        calls.append(kwargs)
        return _response(status, headers=headers)

    monkeypatch.setattr(net, "request", _request)
    run = lambda session: net.fetch_redirect_location(  # noqa: E731
        session, "https://example.com/download", method="GET"
    )
    if expected.startswith("https://"):
        assert _run_with_session(run) == expected
        assert calls == [{"method": "GET", "allow_redirects": False, "config": None}]
    else:
        with pytest.raises(RuntimeError, match=expected):
            _run_with_session(run)


def test_fetch_redirect_location_rejects_http_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unfollowed redirect target must obey the same HTTPS-only policy."""

    async def _request(*_args: object, **_kwargs: object) -> net.HTTPResponse:
        return _response(302, headers={"Location": "http://example.com/plain"})

    monkeypatch.setattr(net, "request", _request)
    with pytest.raises(ValueError, match="Only absolute HTTPS URLs"):
        _run_with_session(
            lambda session: net.fetch_redirect_location(
                session, "https://example.com/download"
            )
        )


def test_github_url_helpers_and_api_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    assert (
        net.github_raw_url("a", "b", "c", "d/e")
        == "https://raw.githubusercontent.com/a/b/c/d/e"
    )
    assert net.github_api_url("repos/a/b") == "https://api.github.com/repos/a/b"

    calls: list[tuple[str, dict[str, object]]] = []

    async def _fetch_json(_session: object, url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return {"default_branch": "main"}

    monkeypatch.setattr(net, "fetch_json", _fetch_json)
    cfg = resolve_config(user_agent="ua", http_timeout=3)
    data = _run_with_session(
        lambda session: net.fetch_github_api(session, "repos/a/b", config=cfg, x="1")
    )
    assert data == {"default_branch": "main"}
    assert calls[0][0] == "https://api.github.com/repos/a/b?x=1"
    assert calls[0][1]["user_agent"] == "ua"

    async def _fetch_repo(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(default_branch="trunk")

    monkeypatch.setattr(net, "_fetch_github_repo", _fetch_repo)
    assert (
        _run_with_session(
            lambda session: net.fetch_github_default_branch(session, "a", "b")
        )
        == "trunk"
    )

    async def _fetch_commits(*_args: object, **_kwargs: object) -> tuple[object, ...]:
        return (SimpleNamespace(sha="deadbeef"),)

    monkeypatch.setattr(net, "_fetch_github_commits", _fetch_commits)
    sha = _run_with_session(
        lambda session: net.fetch_github_latest_commit(
            session,
            ("a", "b"),
            file_path="p",
            branch="main",
        )
    )
    assert sha == "deadbeef"

    async def _fetch_no_commits(
        *_args: object, **_kwargs: object
    ) -> tuple[object, ...]:
        return ()

    monkeypatch.setattr(net, "_fetch_github_commits", _fetch_no_commits)
    with pytest.raises(RuntimeError, match="No commits found"):
        _run_with_session(
            lambda session: net.fetch_github_latest_commit(
                session,
                ("a", "b"),
                file_path="p",
                branch="main",
            )
        )

    async def _fetch_bad_repo(*_args: object, **_kwargs: object) -> object:
        return SimpleNamespace(default_branch=1)

    monkeypatch.setattr(net, "_fetch_github_repo", _fetch_bad_repo)
    with pytest.raises(TypeError, match="Expected default_branch"):
        _run_with_session(
            lambda session: net.fetch_github_default_branch(session, "a", "b")
        )

    async def _fetch_bad_commits(
        *_args: object, **_kwargs: object
    ) -> tuple[object, ...]:
        return (SimpleNamespace(sha=1),)

    monkeypatch.setattr(net, "_fetch_github_commits", _fetch_bad_commits)
    with pytest.raises(TypeError, match="Expected commit sha"):
        _run_with_session(
            lambda session: net.fetch_github_latest_commit(
                session,
                ("a", "b"),
                file_path="p",
                branch="main",
            )
        )


def test_githubkit_fetch_helpers_wrap_client_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use GitHubKit through the narrow async wrappers and normalize failures."""
    created: list[tuple[str | None, str | None, float]] = []

    class _Repos:
        async def async_get(self, owner: str, repo: str) -> object:
            return SimpleNamespace(
                parsed_data=SimpleNamespace(
                    owner=owner, repo=repo, default_branch="main"
                )
            )

        async def async_list_commits(
            self,
            owner: str,
            repo: str,
            *,
            path: str,
            sha: str,
            per_page: int,
        ) -> object:
            return SimpleNamespace(
                parsed_data=(
                    SimpleNamespace(
                        owner=owner,
                        repo=repo,
                        path=path,
                        branch=sha,
                        per_page=per_page,
                        sha="deadbeef",
                    ),
                )
            )

    class _Client:
        def __init__(
            self,
            token: str | None,
            *,
            user_agent: str | None,
            timeout: float,
        ) -> None:
            created.append((token, user_agent, timeout))

        def rest(self) -> object:
            return SimpleNamespace(repos=_Repos())

    monkeypatch.setattr(net, "_get_github_token", lambda: "token")
    monkeypatch.setattr(net, "GitHub", _Client)
    cfg = resolve_config(user_agent="ua", http_timeout=3)

    repo = asyncio.run(net._fetch_github_repo("acme", "demo", config=cfg))
    commits = asyncio.run(
        net._fetch_github_commits(
            "acme",
            "demo",
            branch="main",
            config=cfg,
            file_path="flake.lock",
        )
    )

    assert repo.default_branch == "main"
    assert commits[0].sha == "deadbeef"
    assert created == [
        ("token", "ua", 3),
        ("token", "ua", 3),
    ]

    class _FailingRepos:
        async def async_get(self, *_args: object, **_kwargs: object) -> object:
            msg = "repo failed"
            raise RuntimeError(msg)

        async def async_list_commits(self, *_args: object, **_kwargs: object) -> object:
            msg = "commits failed"
            raise RuntimeError(msg)

    class _FailingClient:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def rest(self) -> object:
            return SimpleNamespace(repos=_FailingRepos())

    monkeypatch.setattr(net, "GitHubException", RuntimeError)
    monkeypatch.setattr(net, "GitHub", _FailingClient)

    with pytest.raises(RuntimeError, match="repo metadata request failed"):
        asyncio.run(net._fetch_github_repo("acme", "demo", config=cfg))
    with pytest.raises(RuntimeError, match="commits request failed"):
        asyncio.run(
            net._fetch_github_commits(
                "acme",
                "demo",
                branch="main",
                config=cfg,
                file_path="flake.lock",
            )
        )


def test_fetch_github_api_paginated_stops_after_short_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pagination should stop once a short page is returned."""
    page_calls: list[str] = []

    async def _fetch(
        _session: object,
        _api_path: str,
        *,
        config: object,
        **params: str,
    ) -> object:
        _ = config
        page = params["page"]
        page_calls.append(page)
        if page == "1":
            return [{"name": "v1"}, {"name": "v2"}]
        return [{"name": "v3"}]

    monkeypatch.setattr(net, "fetch_github_api", _fetch)

    items = _run_with_session(
        lambda session: net.fetch_github_api_paginated(
            session,
            "repos/a/b/tags",
            per_page=2,
            max_pages=5,
        )
    )

    assert items == [{"name": "v1"}, {"name": "v2"}, {"name": "v3"}]
    assert page_calls == ["1", "2"]


def test_fetch_github_api_paginated_respects_item_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pagination should stop early when item_limit is reached."""
    call_count = 0
    page_size = 2
    item_limit = 3
    expected_calls = 2

    async def _fetch(
        _session: object,
        _api_path: str,
        *,
        config: object,
        **params: str,
    ) -> object:
        _ = (config, params)
        nonlocal call_count
        call_count += 1
        return [1, 2]

    monkeypatch.setattr(net, "fetch_github_api", _fetch)

    items = _run_with_session(
        lambda session: net.fetch_github_api_paginated(
            session,
            "repos/a/b/tags",
            per_page=page_size,
            max_pages=5,
            item_limit=item_limit,
        )
    )

    assert items == [1, 2, 1]
    assert call_count == expected_calls


def test_fetch_github_api_paginated_requires_list_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pagination helper should fail for non-list API payloads."""

    async def _fetch(
        _session: object,
        _api_path: str,
        *,
        config: object,
        **params: str,
    ) -> object:
        _ = (config, params)
        return {"name": "v1"}

    monkeypatch.setattr(net, "fetch_github_api", _fetch)

    with pytest.raises(
        RuntimeError,
        match="Expected JSON array from GitHub API list repos/a/b/tags page 1",
    ):
        _run_with_session(
            lambda session: net.fetch_github_api_paginated(
                session,
                "repos/a/b/tags",
            )
        )


def test_json_list_type_errors() -> None:
    """Validate list coercion errors."""
    with pytest.raises(RuntimeError, match="Expected JSON array"):
        object.__getattribute__(net, "_expect_json_list")(
            [{"nested": {1, 2}}],
            context="ctx",
        )


def test_token_discovery_without_netrc_auth_and_rate_limit_unknown_reset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Handle missing netrc auths and unknown GitHub reset timestamps."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".netrc").write_text(
        "machine example login u password p\n", encoding="utf-8"
    )

    class _NetrcObj:
        def authenticators(self, host: str) -> None:
            _ = host

    monkeypatch.setattr(net.http_utils.netrc, "netrc", lambda _path: _NetrcObj())
    assert object.__getattribute__(net, "_get_github_token")() is None

    detail = net.http_utils.format_http_error(
        404,
        "Not Found",
        b"",
    )
    assert detail == "HTTP 404 Not Found"


def test_get_github_token_without_netrc(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Return ``None`` when netrc is absent."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    assert object.__getattribute__(net, "_get_github_token")() is None


def test_request_non_retryable_and_exhausted_retryer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail fast on 4xx and raise fallback when retryer yields no attempts."""

    def _request_404(
        _self: aiohttp.ClientSession,
        _method: str,
        _url: str,
        **_kwargs: object,
    ) -> _FakeResponseCM:
        return _FakeResponseCM(
            _FakeResponse(status=404, reason="Not Found", payload=b"missing")
        )

    monkeypatch.setattr(aiohttp.ClientSession, "request", _request_404)
    monkeypatch.setattr(net, "_build_request_headers", lambda _url, _ua: {})

    with pytest.raises(RuntimeError, match="failed after 1 attempts"):
        _run_with_session(
            lambda session: net.request(
                session,
                "https://example.com/404",
                retries=1,
                backoff=0.0,
            )
        )

    class _EmptyRetryer:
        def __aiter__(self) -> _EmptyRetryer:
            return self

        async def __anext__(self) -> object:
            raise StopAsyncIteration

    monkeypatch.setattr(net, "AsyncRetrying", lambda **_kwargs: _EmptyRetryer())
    with pytest.raises(RuntimeError, match="failed after 1 attempts$"):
        _run_with_session(
            lambda session: net.request(
                session,
                "https://example.com/empty",
                retries=1,
                backoff=0.0,
            )
        )


def test_fetch_github_api_no_params_and_paginated_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover no-query branch and pagination validation/final-page return."""
    calls: list[str] = []

    async def _fetch_json(_session: object, url: str, **_kwargs: object) -> object:
        calls.append(url)
        return {"ok": 1}

    monkeypatch.setattr(net, "fetch_json", _fetch_json)
    payload = _run_with_session(
        lambda session: net.fetch_github_api(session, "repos/a/b")
    )
    assert payload == {"ok": 1}
    assert calls == ["https://api.github.com/repos/a/b"]

    with pytest.raises(ValueError, match="per_page must be >= 1"):
        _run_with_session(
            lambda session: net.fetch_github_api_paginated(
                session, "repos/a/b", per_page=0
            )
        )
    with pytest.raises(ValueError, match="max_pages must be >= 1"):
        _run_with_session(
            lambda session: net.fetch_github_api_paginated(
                session, "repos/a/b", max_pages=0
            )
        )
    with pytest.raises(ValueError, match="item_limit must be >= 1"):
        _run_with_session(
            lambda session: net.fetch_github_api_paginated(
                session, "repos/a/b", item_limit=0
            )
        )

    async def _fetch_full_pages(
        _session: object,
        _api_path: str,
        *,
        config: object,
        **params: str,
    ) -> object:
        _ = (config, params)
        return [{"x": 1}]

    monkeypatch.setattr(net, "fetch_github_api", _fetch_full_pages)
    with pytest.warns(UserWarning, match=r"may be truncated after 2 page\(s\)"):
        full = _run_with_session(
            lambda session: net.fetch_github_api_paginated(
                session,
                "repos/a/b/tags",
                per_page=1,
                max_pages=2,
            )
        )
    assert full == [{"x": 1}, {"x": 1}]
