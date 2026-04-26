"""Additional branch coverage for GitHub web auth helpers."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import aiohttp
import httpx
import pytest

from lib import http_utils
from lib.github_actions import web_auth


def test_cookie_provider_cached_browser_and_host_helpers() -> None:
    provider = web_auth.GitHubWebCookieProvider(server_url="https://github.com")
    jar = httpx.Cookies()
    jar.set("user_session", "abc", domain="github.com", path="/")
    provider._resolved_browser = True
    provider._cached_browser_cookies = jar
    assert asyncio.run(provider.get_cookies()) is jar
    assert web_auth._require_host("https://github.com") == "github.com"

    with pytest.raises(ValueError, match="Could not parse GitHub host"):
        web_auth._require_host("not a url")


def test_resolve_cdp_cookies_skips_when_disabled_and_discovers_urls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = web_auth.GitHubWebCookieProvider(
        server_url="https://github.com", allow_cdp=False
    )
    assert asyncio.run(provider.get_cdp_cookies()) is None

    no_candidates_provider = web_auth.GitHubWebCookieProvider(
        server_url="https://github.com"
    )
    original_discover = web_auth._discover_cdp_browser_ws_urls
    monkeypatch.setattr(
        web_auth,
        "_discover_cdp_browser_ws_urls",
        lambda **_kwargs: asyncio.sleep(0, result=()),
    )
    assert asyncio.run(no_candidates_provider.get_cdp_cookies()) is None
    monkeypatch.setattr(web_auth, "_discover_cdp_browser_ws_urls", original_discover)

    fetches: list[str] = []

    async def _fetch(url: str, **_kwargs: object) -> tuple[bytes, dict[str, str]]:
        fetches.append(url)
        if url.endswith("9333/json/version"):
            return b"not-json", {}
        if url.endswith("9444/json/version"):
            raise http_utils.SyncRequestError(
                url=url, attempts=1, kind="status", detail="404", status=404
            )
        return json.dumps({
            "webSocketDebuggerUrl": "ws://127.0.0.1:9555/devtools/browser/demo"
        }).encode(), {}

    monkeypatch.setattr(
        web_auth,
        "_candidate_cdp_base_urls",
        lambda **_kwargs: (
            "ws://127.0.0.1:9222/devtools/browser/direct",
            "http://127.0.0.1:9333",
            "http://127.0.0.1:9444",
            "http://127.0.0.1:9555",
            "ws://127.0.0.1:9222/devtools/browser/direct",
        ),
    )
    monkeypatch.setattr(web_auth.http_utils, "fetch_url_bytes_async", _fetch)

    discovered = asyncio.run(
        web_auth._discover_cdp_browser_ws_urls(chrome_debugging_url=None)
    )
    assert discovered == (
        "ws://127.0.0.1:9222/devtools/browser/direct",
        "ws://127.0.0.1:9555/devtools/browser/demo",
    )
    assert fetches == [
        "http://127.0.0.1:9333/json/version",
        "http://127.0.0.1:9444/json/version",
        "http://127.0.0.1:9555/json/version",
    ]

    provider = web_auth.GitHubWebCookieProvider(server_url="https://github.com")
    monkeypatch.setattr(
        web_auth,
        "_discover_cdp_browser_ws_urls",
        lambda **_kwargs: asyncio.sleep(0, result=("ws://one", "ws://two")),
    )

    async def _fake_fetch_cookies(ws_url: str) -> tuple[dict[str, object], ...]:
        if ws_url == "ws://one":
            return (
                {
                    "name": "tz",
                    "value": "UTC",
                    "domain": ".github.com",
                    "path": "/",
                    "expires": 9999999999,
                },
            )
        return (
            {
                "name": "user_session",
                "value": "abc",
                "domain": ".github.com",
                "path": "/",
                "expires": 9999999999,
            },
        )

    monkeypatch.setattr(web_auth, "_fetch_cdp_cookies", _fake_fetch_cookies)
    resolved = asyncio.run(provider.get_cdp_cookies())
    assert isinstance(resolved, httpx.Cookies)


def test_discover_cdp_browser_urls_skips_empty_websocket_field(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        web_auth,
        "_candidate_cdp_base_urls",
        lambda **_kwargs: ("http://127.0.0.1:9222",),
    )

    async def _fetch(_url: str, **_kwargs: object) -> tuple[bytes, dict[str, str]]:
        return json.dumps({"webSocketDebuggerUrl": ""}).encode(), {}

    monkeypatch.setattr(web_auth.http_utils, "fetch_url_bytes_async", _fetch)

    assert (
        asyncio.run(web_auth._discover_cdp_browser_ws_urls(chrome_debugging_url=None))
        == ()
    )


class _FakeMessage:
    def __init__(self, message_type: aiohttp.WSMsgType, data: object = None) -> None:
        self.type = message_type
        self.data = data


class _FakeWebSocket:
    def __init__(self, messages: list[_FakeMessage]) -> None:
        self._messages = messages
        self.sent: list[dict[str, object]] = []

    async def __aenter__(self) -> _FakeWebSocket:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def send_json(self, payload: dict[str, object]) -> None:
        self.sent.append(payload)

    async def receive(self) -> _FakeMessage:
        return self._messages.pop(0)


class _FakeSession:
    def __init__(self, websocket: _FakeWebSocket) -> None:
        self._websocket = websocket

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    def ws_connect(self, _ws_url: str, heartbeat: int = 30) -> _FakeWebSocket:
        assert heartbeat == 30
        return self._websocket


def test_fetch_cdp_cookies_and_cookie_mapping_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    websocket = _FakeWebSocket([
        _FakeMessage(aiohttp.WSMsgType.BINARY, b"ignore"),
        _FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps({"id": 2})),
        _FakeMessage(
            aiohttp.WSMsgType.TEXT,
            json.dumps({"id": 1, "result": {"cookies": [{"name": "user_session"}]}}),
        ),
    ])
    monkeypatch.setattr(
        web_auth.aiohttp, "ClientSession", lambda: _FakeSession(websocket)
    )
    assert asyncio.run(web_auth._fetch_cdp_cookies("ws://example")) == (
        {"name": "user_session"},
    )
    assert websocket.sent == [{"id": 1, "method": "Storage.getCookies"}]

    with pytest.raises(TypeError, match="Expected cookie mapping"):
        web_auth._require_cookie_mapping("nope")

    for message, pattern in [
        (
            _FakeMessage(aiohttp.WSMsgType.TEXT, json.dumps({"id": 1, "error": "bad"})),
            "Storage.getCookies failed",
        ),
        (
            _FakeMessage(
                aiohttp.WSMsgType.TEXT,
                json.dumps({"id": 1, "result": {"cookies": "bad"}}),
            ),
            "non-list cookies payload",
        ),
        (_FakeMessage(aiohttp.WSMsgType.CLOSE), "closed before cookies were returned"),
        (_FakeMessage(aiohttp.WSMsgType.ERROR), "errored while reading cookies"),
    ]:
        monkeypatch.setattr(
            web_auth.aiohttp,
            "ClientSession",
            lambda message=message: _FakeSession(_FakeWebSocket([message])),
        )
        with pytest.raises((RuntimeError, TypeError), match=pattern):
            asyncio.run(web_auth._fetch_cdp_cookies("ws://example"))


def test_candidate_urls_port_file_playwright_and_cookie_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    port_file = tmp_path / "DevToolsActivePort"
    port_file.write_text("9222\n\n")
    monkeypatch.setattr(web_auth, "_DEVTOOLS_ACTIVE_PORT_FILES", (port_file,))
    monkeypatch.setenv(
        "CHROME_REMOTE_DEBUGGING_URL", "http://127.0.0.1:9999/json/version"
    )
    candidates = web_auth._candidate_cdp_base_urls(chrome_debugging_url=None)
    assert candidates[0] == "http://127.0.0.1:9999"
    assert "http://localhost:9222" in candidates
    assert web_auth._read_devtools_active_session(tmp_path / "missing") is None
    blank_file = tmp_path / "blank"
    blank_file.write_text("x\n")
    assert web_auth._read_devtools_active_session(blank_file) is None
    empty_file = tmp_path / "empty"
    empty_file.write_text("")
    assert web_auth._read_devtools_active_session(empty_file) is None
    assert web_auth._cookie_matches_host(".github.com", host="api.github.com")
    assert web_auth._cookie_is_live({"expires": 0}, now=10)
    assert not web_auth._cookie_is_live({"expires": 1}, now=10)
    assert web_auth._has_authenticated_github_session([
        {"name": "logged_in", "value": "yes"}
    ])
    assert not web_auth._has_authenticated_github_session([
        {"name": "logged_in", "value": "no"}
    ])
    assert web_auth._cookie_str({"name": "x"}, "name") == "x"
    assert web_auth._cookie_str({"name": 1}, "name") is None
    target = tmp_path / "nested" / "dir"
    web_auth._ensure_directory(target)
    assert target.is_dir()

    monkeypatch.setattr(web_auth, "_read_devtools_active_session", lambda _path: None)
    monkeypatch.setattr(web_auth, "_DEVTOOLS_ACTIVE_PORT_FILES", (Path("/missing"),))
    assert (
        web_auth._candidate_cdp_base_urls(chrome_debugging_url=None)[-1]
        == "http://[::1]:9222"
    )


def test_cookies_from_playwright_and_default_chrome_executable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = io.StringIO()

    class _FakePage:
        async def goto(self, _url: str, wait_until: str) -> None:
            assert wait_until == "domcontentloaded"

    class _FakeContext:
        def __init__(self) -> None:
            self.pages = []
            self.calls = 0

        async def new_page(self) -> _FakePage:
            return _FakePage()

        async def cookies(self, _urls: list[str]) -> list[dict[str, object]]:
            self.calls += 1
            if self.calls == 1:
                return [
                    {
                        "name": "user_session",
                        "value": "abc",
                        "domain": ".github.com",
                        "path": "/",
                        "expires": 9999999999,
                    }
                ]
            return []

        async def close(self) -> None:
            return None

    class _FakeChromium:
        async def launch_persistent_context(self, **kwargs: object) -> _FakeContext:
            assert kwargs["user_data_dir"] == str(tmp_path)
            return _FakeContext()

    class _FakePlaywright:
        chromium = _FakeChromium()

    class _AsyncPlaywright:
        async def __aenter__(self) -> _FakePlaywright:
            return _FakePlaywright()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(
        web_auth.importlib,
        "import_module",
        lambda _name: SimpleNamespace(async_playwright=lambda: _AsyncPlaywright()),
    )
    original_default = web_auth._default_chrome_executable
    monkeypatch.setattr(web_auth, "_default_chrome_executable", lambda: None)
    resolved = asyncio.run(
        web_auth._cookies_from_playwright(
            "github.com",
            server_url="https://github.com",
            output=output,
            login_timeout=1.0,
            profile_dir=tmp_path,
            chrome_executable=None,
        )
    )
    assert isinstance(resolved, httpx.Cookies)

    class _NoCookieContext(_FakeContext):
        async def cookies(self, _urls: list[str]) -> list[dict[str, object]]:
            return []

    class _NoCookieChromium:
        async def launch_persistent_context(self, **kwargs: object) -> _NoCookieContext:
            assert kwargs["channel"] == "chrome"
            return _NoCookieContext()

    class _NoCookiePlaywright:
        chromium = _NoCookieChromium()

    class _NoCookieAsyncPlaywright:
        async def __aenter__(self) -> _NoCookiePlaywright:
            return _NoCookiePlaywright()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    sleeps: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleeps.append(delay)
        raise RuntimeError("sleep-called")

    monkeypatch.setattr(
        web_auth.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            async_playwright=lambda: _NoCookieAsyncPlaywright()
        ),
    )
    monkeypatch.setattr(web_auth.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(web_auth, "_default_chrome_executable", lambda: None)

    with pytest.raises(RuntimeError, match="sleep-called"):
        asyncio.run(
            web_auth._cookies_from_playwright(
                "github.com",
                server_url="https://github.com",
                output=io.StringIO(),
                login_timeout=60.0,
                profile_dir=tmp_path,
                chrome_executable=None,
            )
        )
    assert sleeps == [1.0]
    assert resolved.get("user_session", domain="github.com", path="/") == "abc"
    assert "Opening a Playwright Chrome session" in output.getvalue()

    monkeypatch.setattr(web_auth, "_default_chrome_executable", original_default)


@pytest.mark.parametrize(
    ("system", "env_value", "path_exists", "which_value", "expected"),
    [
        ("Darwin", None, False, None, None),
        ("Windows", None, False, None, None),
        ("Windows", "/tmp/localappdata", False, None, None),
    ],
)
def test_default_chrome_executable_missing_platform_candidates(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    system: str,
    env_value: str | None,
    path_exists: bool,
    which_value: str | None,
    expected: str | None,
) -> None:
    monkeypatch.setattr(web_auth.platform, "system", lambda: system)
    if env_value is None:
        monkeypatch.delenv("LOCALAPPDATA", raising=False)
    else:
        monkeypatch.setenv("LOCALAPPDATA", env_value)
    monkeypatch.setattr(web_auth.Path, "exists", lambda self: path_exists)
    monkeypatch.setattr(web_auth.shutil, "which", lambda _name: which_value)

    assert web_auth._default_chrome_executable() is expected

    monkeypatch.setattr(web_auth.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(web_auth.Path, "exists", lambda self: True)
    assert (
        web_auth._default_chrome_executable()
        == "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )
    monkeypatch.setattr(web_auth.platform, "system", lambda: "Windows")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    assert web_auth._default_chrome_executable().endswith("chrome.exe")
    monkeypatch.setattr(web_auth.platform, "system", lambda: "Linux")
    monkeypatch.setattr(
        web_auth.shutil,
        "which",
        lambda name: "/usr/bin/chromium" if name == "chromium" else None,
    )
    assert web_auth._default_chrome_executable() == "/usr/bin/chromium"

    timeout_output = io.StringIO()

    class _NeverAuthPage:
        async def goto(self, _url: str, wait_until: str) -> None:
            assert wait_until == "domcontentloaded"

    class _NeverAuthContext:
        pages: ClassVar[list[object]] = []

        async def new_page(self) -> _NeverAuthPage:
            return _NeverAuthPage()

        async def cookies(self, _urls: list[str]) -> list[dict[str, object]]:
            return [
                {
                    "name": "tz",
                    "value": "UTC",
                    "domain": ".github.com",
                    "path": "/",
                    "expires": 9999999999,
                }
            ]

        async def close(self) -> None:
            return None

    class _NeverAuthChromium:
        async def launch_persistent_context(
            self, **kwargs: object
        ) -> _NeverAuthContext:
            assert kwargs["executable_path"] == "/custom/chrome"
            return _NeverAuthContext()

    class _NeverAuthPlaywright:
        chromium = _NeverAuthChromium()

    class _NeverAuthAsyncPlaywright:
        async def __aenter__(self) -> _NeverAuthPlaywright:
            return _NeverAuthPlaywright()

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setattr(
        web_auth.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            async_playwright=lambda: _NeverAuthAsyncPlaywright()
        ),
    )
    monkeypatch.setattr(web_auth.time, "monotonic", lambda: 10.0)
    assert (
        asyncio.run(
            web_auth._cookies_from_playwright(
                "github.com",
                server_url="https://github.com",
                output=timeout_output,
                login_timeout=0.0,
                profile_dir=tmp_path,
                chrome_executable="/custom/chrome",
            )
        )
        is None
    )

    jar = web_auth.build_github_cookies(
        [
            {
                "name": "user_session",
                "value": "abc",
                "domain": ".github.com",
                "path": "/",
                "expires": 9999999999,
            },
            {
                "name": None,
                "value": "skip",
                "domain": ".github.com",
                "path": "/",
                "expires": 9999999999,
            },
        ],
        host="github.com",
        now=10,
    )
    assert isinstance(jar, httpx.Cookies)
    monkeypatch.setattr(web_auth.platform, "system", lambda: "Linux")
    monkeypatch.setattr(web_auth.shutil, "which", lambda _name: None)
    assert web_auth._default_chrome_executable() is None

    sleep_calls: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monotonic_values = iter([0.0, 0.5, 2.0])

    def _monotonic() -> float:
        return next(monotonic_values, 2.0)

    monkeypatch.setattr(web_auth.asyncio, "sleep", _fake_sleep)
    monkeypatch.setattr(web_auth.time, "monotonic", _monotonic)
    monkeypatch.setattr(
        web_auth.importlib,
        "import_module",
        lambda _name: SimpleNamespace(
            async_playwright=lambda: _NeverAuthAsyncPlaywright()
        ),
    )
    assert (
        asyncio.run(
            web_auth._cookies_from_playwright(
                "github.com",
                server_url="https://github.com",
                output=io.StringIO(),
                login_timeout=1.0,
                profile_dir=tmp_path,
                chrome_executable="/custom/chrome",
            )
        )
        is None
    )
