"""GitHub web-session cookie discovery for live Actions log tailing."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import platform
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Final, cast
from urllib.parse import urlsplit

import aiohttp
import httpx

from lib import http_utils

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from typing import TextIO

_CDP_VERSION_PATH: Final[str] = "/json/version"
_DEVTOOLS_ACTIVE_PORT_FILES: Final[tuple[Path, ...]] = (
    Path.home() / "Library/Application Support/Google/Chrome/DevToolsActivePort",
    Path.home() / "Library/Application Support/Chromium/DevToolsActivePort",
)
_GITHUB_AUTH_COOKIE_NAMES: Final[frozenset[str]] = frozenset({
    "user_session",
    "__Host-user_session_same_site",
    "logged_in",
    "dotcom_user",
})
_DEFAULT_PLAYWRIGHT_TIMEOUT: Final[float] = 300.0
_DEFAULT_PLAYWRIGHT_PROFILE_DIR: Final[Path] = (
    Path.home() / ".cache" / "nixcfg" / "playwright-github"
)
_LOCAL_CDP_BASE_URLS: Final[tuple[str, ...]] = (
    "http://localhost:9222",
    "http://127.0.0.1:9222",
    "http://[::1]:9222",
)
_LOCAL_CDP_SCHEMES: Final[frozenset[str]] = frozenset({"http", "https"})


@dataclass
class GitHubWebCookieProvider:
    """Resolve reusable GitHub web cookies from CDP or Playwright."""

    server_url: str
    output: TextIO = field(default_factory=lambda: sys.stderr)
    allow_cdp: bool = True
    allow_playwright: bool = False
    playwright_timeout: float = _DEFAULT_PLAYWRIGHT_TIMEOUT
    playwright_profile_dir: Path = _DEFAULT_PLAYWRIGHT_PROFILE_DIR
    chrome_debugging_url: str | None = None
    chrome_executable: str | None = None
    _cached_cdp_cookies: httpx.Cookies | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _cached_browser_cookies: httpx.Cookies | None = field(
        init=False,
        default=None,
        repr=False,
    )
    _resolved_cdp: bool = field(init=False, default=False, repr=False)
    _resolved_browser: bool = field(init=False, default=False, repr=False)

    async def get_cdp_cookies(self) -> httpx.Cookies | None:
        """Return cached GitHub cookies from an existing CDP browser session."""
        if self._resolved_cdp:
            return self._cached_cdp_cookies
        self._cached_cdp_cookies = await self._resolve_cdp_cookies()
        self._resolved_cdp = True
        return self._cached_cdp_cookies

    async def get_cookies(self) -> httpx.Cookies | None:
        """Return cached GitHub cookies, falling back to Playwright if allowed."""
        if self._resolved_browser:
            return self._cached_browser_cookies

        cdp_cookies = await self.get_cdp_cookies()
        if cdp_cookies is not None or not self.allow_playwright:
            self._cached_browser_cookies = cdp_cookies
            self._resolved_browser = True
            return self._cached_browser_cookies

        self._cached_browser_cookies = await self._resolve_playwright_cookies()
        self._resolved_browser = True
        return self._cached_browser_cookies

    async def _resolve_cdp_cookies(self) -> httpx.Cookies | None:
        host = _require_host(self.server_url)
        if not self.allow_cdp:
            return None
        for ws_url in await _discover_cdp_browser_ws_urls(
            chrome_debugging_url=self.chrome_debugging_url
        ):
            try:
                raw_cookies = await _fetch_cdp_cookies(ws_url)
            except aiohttp.ClientError, OSError, RuntimeError, TypeError, ValueError:
                continue
            resolved = build_github_cookies(raw_cookies, host=host)
            if resolved is not None:
                return resolved
        return None

    async def _resolve_playwright_cookies(self) -> httpx.Cookies | None:
        host = _require_host(self.server_url)
        return await _cookies_from_playwright(
            host,
            server_url=self.server_url,
            output=self.output,
            login_timeout=self.playwright_timeout,
            profile_dir=self.playwright_profile_dir,
            chrome_executable=self.chrome_executable,
        )


def _require_host(server_url: str) -> str:
    host = urlsplit(server_url).hostname
    if host is None:
        msg = f"Could not parse GitHub host from {server_url!r}"
        raise ValueError(msg)
    return host


async def _discover_cdp_browser_ws_urls(
    *,
    chrome_debugging_url: str | None,
) -> tuple[str, ...]:
    discovered: list[str] = []
    async with httpx.AsyncClient(follow_redirects=True, timeout=2.0) as client:
        for base_url in _candidate_cdp_base_urls(
            chrome_debugging_url=chrome_debugging_url
        ):
            if base_url.startswith(("ws://", "wss://")):
                discovered.append(base_url)
                continue
            try:
                payload, _headers = await http_utils.fetch_url_bytes_async(
                    f"{base_url}{_CDP_VERSION_PATH}",
                    allowed_schemes=_LOCAL_CDP_SCHEMES,
                    client=client,
                )
            except http_utils.RequestError:
                continue
            try:
                decoded = json.loads(payload)
            except ValueError:
                continue
            ws_url = decoded.get("webSocketDebuggerUrl")
            if isinstance(ws_url, str) and ws_url:
                discovered.append(ws_url)
    return tuple(dict.fromkeys(discovered))


async def _fetch_cdp_cookies(ws_url: str) -> tuple[Mapping[str, object], ...]:
    async with (
        aiohttp.ClientSession() as session,
        session.ws_connect(ws_url, heartbeat=30) as websocket,
    ):
        request_id = 1
        await websocket.send_json({"id": request_id, "method": "Storage.getCookies"})
        while True:
            message = await websocket.receive()
            if message.type is aiohttp.WSMsgType.TEXT:
                payload = json.loads(message.data)
                if payload.get("id") != request_id:
                    continue
                if "error" in payload:
                    msg = f"CDP Storage.getCookies failed: {payload['error']}"
                    raise RuntimeError(msg)
                cookies = payload.get("result", {}).get("cookies", [])
                if not isinstance(cookies, list):
                    msg = "CDP Storage.getCookies returned a non-list cookies payload"
                    raise TypeError(msg)
                return tuple(_require_cookie_mapping(cookie) for cookie in cookies)
            if message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            }:
                msg = "CDP websocket closed before cookies were returned"
                raise RuntimeError(msg)
            if message.type is aiohttp.WSMsgType.ERROR:
                msg = "CDP websocket errored while reading cookies"
                raise RuntimeError(msg)


def _require_cookie_mapping(cookie: object) -> Mapping[str, object]:
    if not isinstance(cookie, dict):
        msg = f"Expected cookie mapping from browser, got {type(cookie).__name__}"
        raise TypeError(msg)
    return cast("Mapping[str, object]", cookie)


def _candidate_cdp_base_urls(*, chrome_debugging_url: str | None) -> tuple[str, ...]:
    candidates: list[str] = []
    env_url = chrome_debugging_url or os.environ.get("CHROME_REMOTE_DEBUGGING_URL")
    if env_url:
        candidates.append(env_url.removesuffix(_CDP_VERSION_PATH).rstrip("/"))

    for path in _DEVTOOLS_ACTIVE_PORT_FILES:
        session = _read_devtools_active_session(path)
        if session is None:
            continue
        port, websocket_path = session
        if websocket_path is not None:
            candidates.extend((
                f"ws://127.0.0.1:{port}{websocket_path}",
                f"ws://localhost:{port}{websocket_path}",
            ))
        candidates.extend((
            f"http://localhost:{port}",
            f"http://127.0.0.1:{port}",
            f"http://[::1]:{port}",
        ))

    candidates.extend(_LOCAL_CDP_BASE_URLS)
    return tuple(dict.fromkeys(candidates))


def _read_devtools_active_session(path: Path) -> tuple[int, str | None] | None:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return None
    if not lines:
        return None
    first = lines[0].strip()
    if not first.isdigit():
        return None
    websocket_path = lines[1].strip() if len(lines) > 1 else None
    if websocket_path == "":
        websocket_path = None
    return int(first), websocket_path


async def _cookies_from_playwright(
    host: str,
    *,
    server_url: str,
    output: TextIO,
    login_timeout: float,
    profile_dir: Path,
    chrome_executable: str | None,
) -> httpx.Cookies | None:
    try:
        playwright_module = importlib.import_module("playwright.async_api")
    except ImportError as exc:
        msg = (
            "Playwright is not installed; install `playwright` to use "
            "--allow-playwright-login"
        )
        raise RuntimeError(msg) from exc

    async_playwright = playwright_module.async_playwright
    executable_path = chrome_executable or _default_chrome_executable()
    _ensure_directory(profile_dir)
    deadline = time.monotonic() + login_timeout

    output.write(
        "Opening a Playwright Chrome session for GitHub login; complete login in the "
        f"browser window within {int(login_timeout)}s if prompted.\n"
    )
    output.flush()

    async with async_playwright() as playwright:
        launch_kwargs: dict[str, object] = {
            "user_data_dir": str(profile_dir),
            "headless": False,
        }
        if executable_path is not None:
            launch_kwargs["executable_path"] = executable_path
        else:
            launch_kwargs["channel"] = "chrome"

        context = await playwright.chromium.launch_persistent_context(**launch_kwargs)
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(server_url, wait_until="domcontentloaded")
            while time.monotonic() < deadline:
                cookies = await context.cookies([server_url])
                resolved = build_github_cookies(cookies, host=host)
                if resolved is not None:
                    return resolved
                await asyncio.sleep(1.0)
        finally:
            await context.close()
    return None


def build_github_cookies(
    cookies: Iterable[Mapping[str, object]],
    *,
    host: str,
    now: float | None = None,
) -> httpx.Cookies | None:
    """Return a reusable cookie jar when the browser session is authenticated."""
    now_value = time.time() if now is None else now
    filtered: list[Mapping[str, object]] = []
    for cookie in cookies:
        domain = _cookie_str(cookie, "domain")
        if domain is None or not _cookie_matches_host(domain, host=host):
            continue
        if not _cookie_is_live(cookie, now=now_value):
            continue
        filtered.append(cookie)

    if not _has_authenticated_github_session(filtered):
        return None

    cookie_jar = httpx.Cookies()
    for cookie in filtered:
        name = _cookie_str(cookie, "name")
        value = _cookie_str(cookie, "value")
        domain = _cookie_str(cookie, "domain")
        if name is None or value is None or domain is None:
            continue
        cookie_jar.set(
            name,
            value,
            domain=domain.lstrip("."),
            path=_cookie_str(cookie, "path") or "/",
        )
    return cookie_jar or None


def _cookie_matches_host(domain: str, *, host: str) -> bool:
    normalized_domain = domain.lstrip(".")
    return host == normalized_domain or host.endswith(f".{normalized_domain}")


def _cookie_is_live(cookie: Mapping[str, object], *, now: float) -> bool:
    expires = cookie.get("expires")
    if not isinstance(expires, int | float) or expires <= 0:
        return True
    return float(expires) >= now


def _has_authenticated_github_session(cookies: Iterable[Mapping[str, object]]) -> bool:
    names = {
        name for cookie in cookies if (name := _cookie_str(cookie, "name")) is not None
    }
    if names.intersection({"user_session", "__Host-user_session_same_site"}):
        return True
    for cookie in cookies:
        name = _cookie_str(cookie, "name")
        value = _cookie_str(cookie, "value")
        if name in _GITHUB_AUTH_COOKIE_NAMES and value not in {None, "", "no"}:
            return True
    return False


def _cookie_str(cookie: Mapping[str, object], key: str) -> str | None:
    value = cookie.get(key)
    if isinstance(value, str):
        return value
    return None


def _ensure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _default_chrome_executable() -> str | None:
    system = platform.system()
    if system == "Darwin":
        candidate = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
        if candidate.exists():
            return str(candidate)
    elif system == "Windows":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data is not None:
            candidate = Path(local_app_data) / "Google/Chrome/Application/chrome.exe"
            if candidate.exists():
                return str(candidate)
    else:
        for executable in ("google-chrome", "chromium", "chromium-browser", "chrome"):
            resolved = shutil.which(executable)
            if resolved is not None:
                return resolved
    return None


__all__ = ["GitHubWebCookieProvider", "build_github_cookies"]
