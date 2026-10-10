"""In-page GitHub Actions live-log probe via one held CDP session.

The Actions job UI does not stream logs over a websocket. Network captures of
the signed-in job page show the same undocumented polling the web client uses:

- ``GET .../actions/runs/<run>/jobs/<checkId>/steps?change_id=N``
- ``GET .../actions/runs/<run>/jobs/<checkId>/steps/<step>/backscroll``

Those endpoints are cookie-authenticated and reject requests that omit the UI's
XHR headers (``X-Requested-With: XMLHttpRequest``, ``Accept: application/json``,
and ``X-Fetch-Nonce`` from ``<meta name="fetch-nonce">``). Evaluating
``fetch()`` inside an already-open GitHub tab keeps the session in the renderer
instead of exporting cookies.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

import aiohttp

from lib.github_actions.web_auth import _discover_cdp_browser_ws_urls

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


class CdpClientProtocol(Protocol):
    """Minimal CDP transport used by the held GitHub page session."""

    async def connect(self, ws_url: str) -> None:
        """Open one browser-level CDP websocket."""

    async def close(self) -> None:
        """Close the websocket if it was opened."""

    async def call(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> object:
        """Send one CDP command and return its result payload."""

_GITHUB_NETLOC = "github.com"
_ACTIONS_PATH_MARKER = "/actions/"
_FETCH_NONCE_META = "fetch-nonce"


class PageSessionUnavailableError(RuntimeError):
    """No existing Chrome debugging session or GitHub tab is available."""


class PageSessionError(RuntimeError):
    """The held Chrome tab could not run the in-page live-log probe."""


@dataclass(frozen=True)
class InPageFetchResult:
    """One ``fetch()`` result captured from the signed-in GitHub tab."""

    status: int
    body: bytes
    headers: dict[str, str]


@dataclass(frozen=True)
class _CdpTarget:
    """One Chrome target exposed by ``Target.getTargets``."""

    target_id: str
    type: str
    url: str


def choose_existing_page(
    targets: Sequence[Mapping[str, object]],
    *,
    prefer_url: str | None = None,
) -> _CdpTarget:
    """Pick an already-open page. Never create a tab or browser."""
    pages = tuple(
        target
        for item in targets
        if (target := _parse_cdp_target(item)) is not None and target.type == "page"
    )
    if not pages:
        msg = "Chrome has no existing page to attach to"
        raise PageSessionUnavailableError(msg)

    if prefer_url is not None:
        for page in pages:
            if page.url == prefer_url or page.url.startswith(prefer_url):
                return page

    for page in pages:
        if _is_actions_job_page(page.url):
            return page
    for page in pages:
        if _is_github_page(page.url):
            return page

    msg = (
        "Chrome has no signed-in GitHub tab to reuse; open the Actions job page "
        "in the existing window instead of launching another browser"
    )
    raise PageSessionUnavailableError(msg)


def inpage_fetch_expression(url: str, headers: Mapping[str, str]) -> str:
    """Return JS that performs one same-origin live-log fetch in the page."""
    payload = json.dumps({"url": url, "headers": dict(headers)}, separators=(",", ":"))
    return (
        "(async () => {"
        f"const req = {payload};"
        "const nonce = document.querySelector("
        f"'meta[name=\"{_FETCH_NONCE_META}\"]'"
        ")?.content;"
        "const headers = {"
        'Accept:"application/json",'
        '"X-Requested-With":"XMLHttpRequest",'
        "...req.headers"
        "};"
        'if (nonce && !headers["X-Fetch-Nonce"]) headers["X-Fetch-Nonce"] = nonce;'
        'const response = await fetch(req.url, {credentials:"same-origin", headers});'
        "const responseHeaders = {};"
        "response.headers.forEach((value, key) => { responseHeaders[key] = value; });"
        "return {status: response.status, headers: responseHeaders, body: await response.text()};"
        "})()"
    )


def parse_inpage_fetch_result(value: object) -> InPageFetchResult:
    """Validate one in-page ``fetch()`` result from ``Runtime.evaluate``."""
    if not isinstance(value, dict):
        msg = f"Expected in-page fetch object, got {type(value).__name__}"
        raise PageSessionError(msg)
    status = value.get("status")
    body = value.get("body")
    headers = value.get("headers")
    if not isinstance(status, int):
        msg = "In-page fetch result is missing an integer status"
        raise PageSessionError(msg)
    if not isinstance(body, str):
        msg = "In-page fetch result is missing a string body"
        raise PageSessionError(msg)
    if not isinstance(headers, dict):
        msg = "In-page fetch result is missing a headers object"
        raise PageSessionError(msg)
    header_map = {
        str(key): header
        for key, header in headers.items()
        if isinstance(header, str)
    }
    return InPageFetchResult(
        status=status,
        body=body.encode("utf-8"),
        headers=header_map,
    )


class GitHubPageSession:
    """Hold one CDP attach to an existing GitHub tab and fetch from inside it."""

    def __init__(self, *, chrome_debugging_url: str | None = None) -> None:
        """Bind optional Chrome debugging URL discovery without connecting yet."""
        self._chrome_debugging_url = chrome_debugging_url
        self._cdp: CdpClientProtocol | None = None
        self._session_id: str | None = None

    async def aclose(self) -> None:
        """Close the held CDP websocket, if any."""
        if self._cdp is None:
            return
        await self._cdp.close()
        self._cdp = None
        self._session_id = None

    async def ensure_job_page(self, job_url: str) -> None:
        """Attach once and reuse the existing GitHub tab for ``job_url``."""
        await self._ensure_connected(prefer_url=job_url)
        current_url = await self._evaluate_value("location.href")
        if not isinstance(current_url, str):
            msg = "Chrome tab did not report location.href"
            raise PageSessionError(msg)
        if current_url == job_url:
            return
        if not _is_github_page(current_url):
            msg = (
                "Refusing to navigate a non-GitHub tab; reuse the signed-in "
                "Actions job page instead of opening another browser"
            )
            raise PageSessionError(msg)
        await self._call("Page.enable")
        await self._call("Page.navigate", {"url": job_url})
        await self._evaluate_value(
            "new Promise((resolve) => {"
            "if (document.readyState === 'complete') { resolve(true); return; }"
            "window.addEventListener('load', () => resolve(true), {once: true});"
            "})"
        )

    async def read_document_html(self) -> tuple[str, str]:
        """Return the current tab HTML and URL without leaving the renderer."""
        await self._ensure_connected()
        value = await self._evaluate_value(
            "({html: document.documentElement.outerHTML, url: location.href})"
        )
        if not isinstance(value, dict):
            msg = "Chrome tab did not return document HTML"
            raise PageSessionError(msg)
        html = value.get("html")
        url = value.get("url")
        if not isinstance(html, str) or not isinstance(url, str):
            msg = "Chrome tab returned a malformed document snapshot"
            raise PageSessionError(msg)
        return html, url

    async def fetch(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
    ) -> InPageFetchResult:
        """Run the UI's live-log ``fetch()`` inside the signed-in tab."""
        await self._ensure_connected(prefer_url=url)
        value = await self._evaluate_value(inpage_fetch_expression(url, headers))
        return parse_inpage_fetch_result(value)

    async def _ensure_connected(self, *, prefer_url: str | None = None) -> None:
        if self._cdp is not None and self._session_id is not None:
            return
        ws_urls = await _discover_cdp_browser_ws_urls(
            chrome_debugging_url=self._chrome_debugging_url
        )
        if not ws_urls:
            msg = (
                "No Chrome DevTools websocket is available; enable remote "
                "debugging on the already-running signed-in Chrome"
            )
            raise PageSessionUnavailableError(msg)
        client = _CdpClient()
        try:
            session_id = await _attach_existing_page(
                client,
                ws_url=ws_urls[0],
                prefer_url=prefer_url,
            )
        except BaseException:
            await client.close()
            raise
        self._cdp = client
        self._session_id = session_id

    async def _call(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
    ) -> object:
        if self._cdp is None or self._session_id is None:
            msg = "GitHub page session is not connected"
            raise PageSessionError(msg)
        return await self._cdp.call(method, params, session_id=self._session_id)

    async def _evaluate_value(self, expression: str) -> object:
        result = await self._call(
            "Runtime.evaluate",
            {
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
        )
        if not isinstance(result, dict):
            msg = "Chrome Runtime.evaluate returned a non-object payload"
            raise PageSessionError(msg)
        if "exceptionDetails" in result:
            details = result["exceptionDetails"]
            text = (
                details.get("text")
                if isinstance(details, dict)
                else None
            )
            msg = f"In-page evaluate failed: {text or 'unknown exception'}"
            raise PageSessionError(msg)
        remote = result.get("result")
        if not isinstance(remote, dict):
            msg = "Chrome Runtime.evaluate omitted a result object"
            raise PageSessionError(msg)
        return remote.get("value")


class _CdpClient:
    """Minimal CDP websocket client for one held browser connection."""

    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None
        self._websocket: aiohttp.ClientWebSocketResponse | None = None
        self._next_id = 0

    async def connect(self, ws_url: str) -> None:
        """Open one browser-level CDP websocket."""
        self._session = aiohttp.ClientSession()
        try:
            self._websocket = await self._session.ws_connect(ws_url, heartbeat=30)
        except (
            aiohttp.ClientError,
            OSError,
            TimeoutError,
        ) as exc:
            await self.close()
            msg = f"Failed connecting to Chrome DevTools at {ws_url}"
            raise PageSessionUnavailableError(msg) from exc

    async def close(self) -> None:
        """Close the websocket and HTTP session if they were opened."""
        websocket = self._websocket
        session = self._session
        self._websocket = None
        self._session = None
        if websocket is not None:
            await websocket.close()
        if session is not None:
            await session.close()

    async def call(
        self,
        method: str,
        params: Mapping[str, object] | None = None,
        *,
        session_id: str | None = None,
    ) -> object:
        """Send one CDP command and return its ``result`` payload."""
        if self._websocket is None:
            msg = "CDP websocket is not connected"
            raise PageSessionError(msg)
        self._next_id += 1
        request_id = self._next_id
        payload: dict[str, object] = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        if session_id is not None:
            payload["sessionId"] = session_id
        await self._websocket.send_json(payload)
        while True:
            message = await self._websocket.receive()
            if message.type is aiohttp.WSMsgType.TEXT:
                decoded = json.loads(message.data)
                if decoded.get("id") != request_id:
                    continue
                if "error" in decoded:
                    error = decoded["error"]
                    detail = (
                        error.get("message")
                        if isinstance(error, dict)
                        else error
                    )
                    msg = f"CDP {method} failed: {detail}"
                    raise PageSessionError(msg)
                return decoded.get("result")
            if message.type in {
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSING,
            }:
                msg = f"CDP websocket closed while waiting for {method}"
                raise PageSessionError(msg)
            if message.type is aiohttp.WSMsgType.ERROR:
                msg = f"CDP websocket errored while waiting for {method}"
                raise PageSessionError(msg)


async def _attach_existing_page(
    client: CdpClientProtocol,
    *,
    ws_url: str,
    prefer_url: str | None,
) -> str:
    """Attach to one existing page and enable Runtime on that session."""
    await client.connect(ws_url)
    raw_targets = await client.call("Target.getTargets")
    if not isinstance(raw_targets, dict):
        msg = "Chrome Target.getTargets returned a non-object payload"
        raise PageSessionError(msg)
    target_infos = raw_targets.get("targetInfos")
    if not isinstance(target_infos, list):
        msg = "Chrome Target.getTargets returned no targetInfos list"
        raise PageSessionError(msg)
    page = choose_existing_page(
        tuple(item for item in target_infos if isinstance(item, dict)),
        prefer_url=prefer_url,
    )
    attached = await client.call(
        "Target.attachToTarget",
        {"targetId": page.target_id, "flatten": True},
    )
    if not isinstance(attached, dict):
        msg = "Chrome Target.attachToTarget returned a non-object payload"
        raise PageSessionError(msg)
    session_id = attached.get("sessionId")
    if not isinstance(session_id, str) or not session_id:
        msg = "Chrome Target.attachToTarget did not return a sessionId"
        raise PageSessionError(msg)
    await client.call("Runtime.enable", session_id=session_id)
    return session_id


def _parse_cdp_target(value: Mapping[str, object]) -> _CdpTarget | None:
    target_id = value.get("targetId")
    target_type = value.get("type")
    url = value.get("url")
    if (
        not isinstance(target_id, str)
        or not isinstance(target_type, str)
        or not isinstance(url, str)
    ):
        return None
    return _CdpTarget(target_id=target_id, type=target_type, url=url)


def _is_github_page(url: str) -> bool:
    host = urlsplit(url).hostname
    return host == _GITHUB_NETLOC or (
        host is not None and host.endswith(f".{_GITHUB_NETLOC}")
    )


def _is_actions_job_page(url: str) -> bool:
    parsed = urlsplit(url)
    return _is_github_page(url) and _ACTIONS_PATH_MARKER in parsed.path


__all__ = [
    "GitHubPageSession",
    "InPageFetchResult",
    "PageSessionError",
    "PageSessionUnavailableError",
    "choose_existing_page",
    "inpage_fetch_expression",
    "parse_inpage_fetch_result",
]
