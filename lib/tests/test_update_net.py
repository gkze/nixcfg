"""Tests for HTTP/network helpers used by update flows."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import aiohttp
import pytest

from lib.tests._assertions import check
from lib.update import net
from lib.update.config import resolve_config

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


_RESOLVED_TIMEOUT = 9.0
_REQUEST_TIMEOUT = 3.0
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
    ) -> None:
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


def test_get_github_token_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    monkeypatch.setenv("GITHUB_TOKEN", "env-token")
    check(object.__getattribute__(net, "_get_github_token")() == "env-token")


def test_get_github_token_from_netrc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Run this test case."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
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

    monkeypatch.setattr(net.netrc, "netrc", lambda _path: _NetrcObj())
    check(object.__getattribute__(net, "_get_github_token")() == "token-from-netrc")


def test_get_github_token_netrc_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Run this test case."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".netrc").write_text("x", encoding="utf-8")
    monkeypatch.setattr(
        net.netrc, "netrc", lambda _path: (_ for _ in ()).throw(OSError("boom"))
    )
    with caplog.at_level(logging.WARNING):
        check(object.__getattribute__(net, "_get_github_token")() is None)
    check("Failed to parse" in caplog.text)


def test_check_github_rate_limit_paths() -> None:
    """Run this test case."""
    object.__getattribute__(net, "_check_github_rate_limit")(
        {}, "https://api.github.com/x"
    )
    object.__getattribute__(net, "_check_github_rate_limit")(
        {"X-RateLimit-Remaining": "bad"}, "https://api.github.com/x"
    )
    object.__getattribute__(net, "_check_github_rate_limit")(
        {"X-RateLimit-Remaining": "1"}, "https://api.github.com/x"
    )

    with pytest.raises(RuntimeError, match="rate limit exceeded"):
        object.__getattribute__(net, "_check_github_rate_limit")(
            {
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1700000000",
            },
            "https://api.github.com/x",
        )


def test_build_headers_and_http_error_format(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    monkeypatch.setattr(net, "_get_github_token", lambda: "tok")
    headers = object.__getattribute__(net, "_build_request_headers")(
        "https://api.github.com/repos/x/y", "ua"
    )
    check(headers["User-Agent"] == "ua")
    check(headers["Authorization"] == "Bearer tok")

    nongh = object.__getattribute__(net, "_build_request_headers")(
        "https://example.com", None
    )
    check("Authorization" not in nongh)


def test_resolve_timeout_alias_delegates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    called: dict[str, object] = {}

    def _resolver(**kwargs: object) -> float:
        called.update(kwargs)
        return _RESOLVED_TIMEOUT

    monkeypatch.setattr(net, "resolve_timeout_alias", _resolver)
    result = object.__getattribute__(net, "_resolve_timeout_alias")(
        request_timeout=_REQUEST_TIMEOUT, kwargs={"timeout": 5}
    )
    check(result == _RESOLVED_TIMEOUT)
    check(called["named_timeout"] == _REQUEST_TIMEOUT)
    check(called["named_timeout_label"] == "request_timeout")


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
    payload, headers = _run_with_session(
        lambda session: object.__getattribute__(net, "_request")(
            session, "https://example.com", config=cfg
        )
    )
    check(payload == b"payload")
    check(headers == {"X": "1"})
    check(len(requests) == 1)
    check(requests[0]["method"] == "GET")

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
            lambda session: object.__getattribute__(net, "_request")(
                session,
                "https://example.com",
                retries=expected_attempts,
                backoff=0.0,
                config=cfg,
            )
        )

    check(len(requests) == expected_attempts)


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
            await object.__getattribute__(net, "_request")(
                session,
                "https://example.com",
            )
            return session.closed

    check(asyncio.run(_runner()) is False)


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
            lambda session: object.__getattribute__(net, "_request")(
                session,
                "https://example.com",
                retries=expected_attempts,
                backoff=0.0,
            )
        )

    check(call_count == expected_attempts)


def test_fetch_url_and_headers_wrappers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    calls: list[dict[str, object]] = []

    async def _request(*args: object, **kwargs: object) -> tuple[bytes, dict[str, str]]:
        check(len(args) >= _MIN_POSITIONAL_ARGS)
        url = args[1]
        check(isinstance(url, str))
        calls.append({
            "url": url,
            "user_agent": kwargs.get("user_agent"),
            "request_timeout": kwargs.get("request_timeout"),
            "method": kwargs.get("method", "GET"),
            "retries": kwargs.get("retries"),
            "backoff": kwargs.get("backoff"),
            "config": kwargs.get("config"),
        })
        return b"body", {"X": "y"}

    monkeypatch.setattr(net, "_request", _request)
    body = _run_with_session(
        lambda session: net.fetch_url(
            session,
            "https://example.com",
            request_timeout=2.0,
        )
    )
    check(body == b"body")

    headers = _run_with_session(
        lambda session: net.fetch_headers(session, "https://example.com")
    )
    check(headers == {"X": "y"})
    check(calls[-1]["method"] == "HEAD")


def test_fetch_json_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""

    async def _request(*_a: object, **_k: object) -> tuple[bytes, dict[str, str]]:
        return b'{"ok": 1}', {"X-RateLimit-Remaining": "1"}

    checks: list[str] = []
    monkeypatch.setattr(net, "_request", _request)
    monkeypatch.setattr(
        net, "_check_github_rate_limit", lambda _h, url: checks.append(url)
    )

    gh = _run_with_session(
        lambda session: net.fetch_json(session, "https://api.github.com/repos/x/y")
    )
    check(gh == {"ok": 1})
    check(checks == ["https://api.github.com/repos/x/y"])

    monkeypatch.setattr(
        net, "fetch_url", lambda *_a, **_k: asyncio.sleep(0, result=b"[1,2]")
    )
    non_gh = _run_with_session(
        lambda session: net.fetch_json(session, "https://example.com/data")
    )
    check(non_gh == [1, 2])

    monkeypatch.setattr(
        net, "fetch_url", lambda *_a, **_k: asyncio.sleep(0, result=b"not json")
    )
    with pytest.raises(RuntimeError, match="Invalid JSON response"):
        _run_with_session(
            lambda session: net.fetch_json(session, "https://example.com/bad")
        )


def test_github_url_helpers_and_api_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run this test case."""
    check(
        net.github_raw_url("a", "b", "c", "d/e")
        == "https://raw.githubusercontent.com/a/b/c/d/e"
    )
    check(net.github_api_url("repos/a/b") == "https://api.github.com/repos/a/b")

    calls: list[tuple[str, dict[str, object]]] = []

    async def _fetch_json(_session: object, url: str, **kwargs: object) -> object:
        calls.append((url, kwargs))
        return {"default_branch": "main"}

    monkeypatch.setattr(net, "fetch_json", _fetch_json)
    cfg = resolve_config(user_agent="ua", http_timeout=3)
    data = _run_with_session(
        lambda session: net.fetch_github_api(session, "repos/a/b", config=cfg, x="1")
    )
    check(data == {"default_branch": "main"})
    check(calls[0][0] == "https://api.github.com/repos/a/b?x=1")
    check(calls[0][1]["user_agent"] == "ua")

    monkeypatch.setattr(
        net,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(0, result={"default_branch": "trunk"}),
    )
    check(
        _run_with_session(
            lambda session: net.fetch_github_default_branch(session, "a", "b")
        )
        == "trunk"
    )

    monkeypatch.setattr(
        net,
        "fetch_github_api",
        lambda *_a, **_k: asyncio.sleep(0, result=[{"sha": "deadbeef"}]),
    )
    sha = _run_with_session(
        lambda session: net.fetch_github_latest_commit(
            session,
            ("a", "b"),
            file_path="p",
            branch="main",
        )
    )
    check(sha == "deadbeef")

    monkeypatch.setattr(
        net, "fetch_github_api", lambda *_a, **_k: asyncio.sleep(0, result=[])
    )
    with pytest.raises(RuntimeError, match="No commits found"):
        _run_with_session(
            lambda session: net.fetch_github_latest_commit(
                session,
                ("a", "b"),
                file_path="p",
                branch="main",
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

    check(items == [{"name": "v1"}, {"name": "v2"}, {"name": "v3"}])
    check(page_calls == ["1", "2"])


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

    check(items == [1, 2, 1])
    check(call_count == expected_calls)


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


def test_json_expect_helpers_and_optional_type_errors() -> None:
    """Validate JSON coercion helpers and option parser type errors."""
    with pytest.raises(RuntimeError, match="Expected JSON object"):
        object.__getattribute__(net, "_expect_json_dict")([], context="ctx")

    with pytest.raises(RuntimeError, match="Expected string field 'x' in ctx"):
        object.__getattribute__(net, "_expect_json_string_field")(
            {},
            "x",
            context="ctx",
        )

    with pytest.raises(TypeError, match="user_agent must be a string"):
        object.__getattribute__(net, "_pop_optional_str")(
            {"user_agent": 1},
            "user_agent",
        )

    with pytest.raises(TypeError, match="request_timeout must be numeric"):
        object.__getattribute__(net, "_pop_optional_numeric")(
            {"request_timeout": "bad"},
            "request_timeout",
        )

    with pytest.raises(TypeError, match="retries must be an integer"):
        object.__getattribute__(net, "_pop_optional_int")(
            {"retries": "bad"},
            "retries",
        )

    with pytest.raises(TypeError, match="config must be an UpdateConfig"):
        object.__getattribute__(net, "_pop_optional_config")({"config": object()})

    with pytest.raises(TypeError, match="method must be a string"):
        object.__getattribute__(net, "_parse_request_options")({"method": 1})

    with pytest.raises(TypeError, match=r"Unexpected keyword argument\(s\): extra"):
        object.__getattribute__(net, "_parse_request_options")({"extra": True})


def test_token_discovery_without_netrc_auth_and_rate_limit_unknown_reset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Handle missing netrc auths and unknown GitHub reset timestamps."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    (tmp_path / ".netrc").write_text(
        "machine example login u password p\n", encoding="utf-8"
    )

    class _NetrcObj:
        def authenticators(self, host: str) -> None:
            _ = host

    monkeypatch.setattr(net.netrc, "netrc", lambda _path: _NetrcObj())
    check(object.__getattribute__(net, "_get_github_token")() is None)

    with pytest.raises(RuntimeError, match="Resets at unknown"):
        object.__getattribute__(net, "_check_github_rate_limit")(
            {
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "n/a",
            },
            "https://api.github.com/x",
        )

    detail = object.__getattribute__(net, "_format_http_error")(
        _FakeResponse(status=404, reason="Not Found", payload=b""),
        b"",
    )
    check(detail == "HTTP 404 Not Found")


def test_get_github_token_without_netrc_and_optional_str_success(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Return ``None`` when netrc is absent and parse optional string values."""
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    check(object.__getattribute__(net, "_get_github_token")() is None)

    payload = {"user_agent": "ua"}
    value = object.__getattribute__(net, "_pop_optional_str")(payload, "user_agent")
    check(value == "ua")


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
            lambda session: object.__getattribute__(net, "_request")(
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
            lambda session: object.__getattribute__(net, "_request")(
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
    check(payload == {"ok": 1})
    check(calls == ["https://api.github.com/repos/a/b"])

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
    check(full == [{"x": 1}, {"x": 1}])
