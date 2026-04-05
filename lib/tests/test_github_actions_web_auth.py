"""Tests for browser-assisted GitHub web cookie discovery."""

from __future__ import annotations

import asyncio
import io

import httpx
import pytest

from lib.github_actions import web_auth


def test_build_github_cookies_returns_httpx_cookie_jar() -> None:
    """Keep only live, in-scope GitHub cookies and reuse httpx's jar type."""
    cookies = [
        {
            "name": "user_session",
            "value": "abc",
            "domain": ".github.com",
            "path": "/",
            "expires": 9_999_999_999,
        },
        {
            "name": "tz",
            "value": "UTC",
            "domain": "github.com",
            "path": "/",
            "expires": 9_999_999_999,
        },
        {
            "name": "other",
            "value": "skip",
            "domain": "example.com",
            "path": "/",
            "expires": 9_999_999_999,
        },
        {
            "name": "expired",
            "value": "skip",
            "domain": "github.com",
            "path": "/",
            "expires": 1,
        },
    ]

    jar = web_auth.build_github_cookies(cookies, host="github.com", now=10)

    assert isinstance(jar, httpx.Cookies)
    assert jar.get("user_session", domain="github.com", path="/") == "abc"
    assert jar.get("tz", domain="github.com", path="/") == "UTC"
    assert jar.get("expired", domain="github.com", path="/") is None


def test_build_github_cookies_requires_authenticated_session() -> None:
    """Ignore anonymous cookie jars that lack GitHub session cookies."""
    jar = web_auth.build_github_cookies(
        [
            {
                "name": "tz",
                "value": "UTC",
                "domain": "github.com",
                "path": "/",
                "expires": 9_999_999_999,
            }
        ],
        host="github.com",
    )

    assert jar is None


def test_candidate_cdp_urls_include_websocket_from_devtools_file(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use DevToolsActivePort websocket data even when /json/version is unavailable."""
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("9222\n/devtools/browser/demo-browser\n")
    monkeypatch.setattr(web_auth, "_DEVTOOLS_ACTIVE_PORT_FILES", (port_file,))

    candidates = web_auth._candidate_cdp_base_urls(chrome_debugging_url=None)

    assert "ws://127.0.0.1:9222/devtools/browser/demo-browser" in candidates
    assert "http://127.0.0.1:9222" in candidates


def test_cookie_provider_skips_stale_cdp_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stale DevTools websocket hints should not block later CDP candidates."""
    seen_fetches: list[str] = []

    async def _fake_fetch_url_bytes_async(
        url: str,
        **_kwargs: object,
    ) -> tuple[bytes, dict[str, str]]:
        seen_fetches.append(url)
        assert url == "http://127.0.0.1:9222/json/version"
        return (
            b'{"webSocketDebuggerUrl":"ws://127.0.0.1:9222/devtools/browser/live"}',
            {"content-type": "application/json"},
        )

    async def _fake_fetch_cdp_cookies(
        ws_url: str,
    ) -> tuple[dict[str, object], ...]:
        if ws_url.endswith("/stale"):
            raise RuntimeError("stale websocket")
        assert ws_url.endswith("/live")
        return (
            {
                "name": "user_session",
                "value": "abc",
                "domain": ".github.com",
                "path": "/",
                "expires": 9_999_999_999,
            },
        )

    monkeypatch.setattr(
        web_auth,
        "_candidate_cdp_base_urls",
        lambda *, chrome_debugging_url: (
            "ws://127.0.0.1:9222/devtools/browser/stale",
            "http://127.0.0.1:9222",
        ),
    )
    monkeypatch.setattr(
        web_auth.http_utils,
        "fetch_url_bytes_async",
        _fake_fetch_url_bytes_async,
    )
    monkeypatch.setattr(web_auth, "_fetch_cdp_cookies", _fake_fetch_cdp_cookies)

    provider = web_auth.GitHubWebCookieProvider(server_url="https://github.com")

    jar = asyncio.run(provider.get_cdp_cookies())

    assert isinstance(jar, httpx.Cookies)
    assert jar.get("user_session", domain="github.com", path="/") == "abc"
    assert seen_fetches == ["http://127.0.0.1:9222/json/version"]


@pytest.mark.parametrize(
    ("mode", "expected_calls"),
    [("cdp-only", []), ("with-playwright", ["playwright"])],
)
def test_cookie_provider_uses_cdp_then_optional_playwright(
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
    expected_calls: list[str],
) -> None:
    """CDP is preferred; Playwright is only used when explicitly enabled."""
    calls: list[str] = []

    async def _fake_cdp(*_args: object, **_kwargs: object) -> httpx.Cookies | None:
        calls.append("cdp")
        return None

    async def _fake_playwright(
        *_args: object, **_kwargs: object
    ) -> httpx.Cookies | None:
        calls.append("playwright")
        jar = httpx.Cookies()
        jar.set("user_session", "abc", domain="github.com", path="/")
        return jar

    monkeypatch.setattr(
        web_auth.GitHubWebCookieProvider, "_resolve_cdp_cookies", _fake_cdp
    )
    monkeypatch.setattr(
        web_auth.GitHubWebCookieProvider,
        "_resolve_playwright_cookies",
        _fake_playwright,
    )

    provider = web_auth.GitHubWebCookieProvider(
        server_url="https://github.com",
        output=io.StringIO(),
        allow_playwright=mode == "with-playwright",
    )

    jar = asyncio.run(provider.get_cookies())

    assert calls[0] == "cdp"
    assert calls[1:] == expected_calls
    if mode == "with-playwright":
        assert isinstance(jar, httpx.Cookies)
        assert jar.get("user_session", domain="github.com", path="/") == "abc"
    else:
        assert jar is None


def test_cookie_provider_caches_noninteractive_cdp_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated CDP lookups reuse the cached cookie jar."""
    calls = 0
    jar = httpx.Cookies()
    jar.set("user_session", "abc", domain="github.com", path="/")

    async def _fake_cdp(*_args: object, **_kwargs: object) -> httpx.Cookies | None:
        nonlocal calls
        calls += 1
        return jar

    monkeypatch.setattr(
        web_auth.GitHubWebCookieProvider, "_resolve_cdp_cookies", _fake_cdp
    )

    provider = web_auth.GitHubWebCookieProvider(server_url="https://github.com")

    first = asyncio.run(provider.get_cdp_cookies())
    second = asyncio.run(provider.get_cdp_cookies())
    full = asyncio.run(provider.get_cookies())

    assert calls == 1
    assert first is jar
    assert second is jar
    assert full is jar


def test_playwright_fallback_requires_installed_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Opt-in Playwright login should fail loudly when Playwright is missing."""

    async def _fake_cdp(*_args: object, **_kwargs: object) -> httpx.Cookies | None:
        return None

    def _missing_module(_name: str) -> object:
        raise ImportError("missing")

    monkeypatch.setattr(
        web_auth.GitHubWebCookieProvider, "_resolve_cdp_cookies", _fake_cdp
    )
    monkeypatch.setattr(web_auth.importlib, "import_module", _missing_module)

    provider = web_auth.GitHubWebCookieProvider(
        server_url="https://github.com",
        allow_playwright=True,
    )

    with pytest.raises(RuntimeError, match="Playwright is not installed"):
        asyncio.run(provider.get_cookies())
