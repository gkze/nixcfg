"""Tests for in-page GitHub Actions live-log probing."""

import asyncio
import json

import aiohttp
import pytest

from lib.github_actions import page_session


def test_choose_existing_page_prefers_job_then_github_tabs() -> None:
    """Reuse the signed-in Actions tab and never invent a new page."""
    inspect = {
        "targetId": "inspect",
        "type": "page",
        "url": "chrome://inspect/#remote-debugging",
    }
    github = {
        "targetId": "github",
        "type": "page",
        "url": "https://github.com/gkze/nixcfg",
    }
    job = {
        "targetId": "job",
        "type": "page",
        "url": "https://github.com/gkze/nixcfg/actions/runs/1/job/2",
    }
    worker = {"targetId": "worker", "type": "worker", "url": "https://github.com/x"}
    prefer = "https://github.com/gkze/nixcfg/actions/runs/1/job/2"

    assert (
        page_session.choose_existing_page(
            (inspect, github, job),
            prefer_url=prefer,
        ).target_id
        == "job"
    )
    assert (
        page_session.choose_existing_page((inspect, github, worker)).target_id
        == "github"
    )
    assert page_session.choose_existing_page((inspect, job)).target_id == "job"

    with pytest.raises(page_session.PageSessionUnavailableError, match="no existing page"):
        page_session.choose_existing_page((worker,))
    with pytest.raises(page_session.PageSessionUnavailableError, match="no signed-in GitHub"):
        page_session.choose_existing_page((inspect,))
    assert page_session._parse_cdp_target({"targetId": 1, "type": "page", "url": "/"}) is None


def test_inpage_fetch_expression_embeds_request_payload() -> None:
    """The generated page script carries the exact request the UI would make."""
    expression = page_session.inpage_fetch_expression(
        "https://github.com/acme/demo/actions/runs/9/jobs/55/steps?change_id=0",
        {"Referer": "https://github.com/acme/demo/actions/runs/9/job/42"},
    )
    marker = "const req = "
    start = expression.index(marker) + len(marker)
    payload, _end = json.JSONDecoder().raw_decode(expression[start:])
    assert payload == {
        "url": "https://github.com/acme/demo/actions/runs/9/jobs/55/steps?change_id=0",
        "headers": {"Referer": "https://github.com/acme/demo/actions/runs/9/job/42"},
    }


def test_parse_inpage_fetch_result_validates_payload() -> None:
    """Accept only the status/body/headers shape Runtime.evaluate can return."""
    parsed = page_session.parse_inpage_fetch_result({
        "status": 200,
        "body": '[{"id":"step-1"}]',
        "headers": {"content-type": "application/json", "ignored": 1},
    })
    assert parsed.status == 200
    assert parsed.body == b'[{"id":"step-1"}]'
    assert parsed.headers == {"content-type": "application/json"}

    with pytest.raises(page_session.PageSessionError, match="fetch object"):
        page_session.parse_inpage_fetch_result([])
    with pytest.raises(page_session.PageSessionError, match="integer status"):
        page_session.parse_inpage_fetch_result({"status": "200", "body": "", "headers": {}})
    with pytest.raises(page_session.PageSessionError, match="string body"):
        page_session.parse_inpage_fetch_result({"status": 200, "body": None, "headers": {}})
    with pytest.raises(page_session.PageSessionError, match="headers object"):
        page_session.parse_inpage_fetch_result({"status": 200, "body": "", "headers": []})


class _FakeCdp:
    def __init__(self, *, responses: list[object], errors: list[Exception] | None = None) -> None:
        self.responses = list(responses)
        self.errors = list(errors or [])
        self.calls: list[tuple[str, object, str | None]] = []
        self.closed = False
        self.connected_url: str | None = None

    async def connect(self, ws_url: str) -> None:
        if self.errors:
            raise self.errors.pop(0)
        self.connected_url = ws_url

    async def close(self) -> None:
        self.closed = True

    async def call(
        self,
        method: str,
        params: object = None,
        *,
        session_id: str | None = None,
    ) -> object:
        self.calls.append((method, params, session_id))
        if self.errors:
            raise self.errors.pop(0)
        if not self.responses:
            msg = f"unexpected CDP call {method}"
            raise AssertionError(msg)
        return self.responses.pop(0)


def _connected_session(
    fake: _FakeCdp,
    monkeypatch: pytest.MonkeyPatch,
) -> page_session.GitHubPageSession:
    async def _discover(**_kwargs: object) -> tuple[str, ...]:
        return ("ws://127.0.0.1:9222/devtools/browser/demo",)

    monkeypatch.setattr(page_session, "_discover_cdp_browser_ws_urls", _discover)
    monkeypatch.setattr(page_session, "_CdpClient", lambda: fake)
    return page_session.GitHubPageSession(chrome_debugging_url="http://127.0.0.1:9222")


def test_page_session_fetches_and_reads_document_from_existing_tab(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Attach once, reuse the GitHub tab, and return in-page fetch JSON."""
    fake = _FakeCdp(
        responses=[
            {
                "targetInfos": [
                    {
                        "targetId": "job",
                        "type": "page",
                        "url": "https://github.com/acme/demo/actions/runs/9/job/42",
                    }
                ]
            },
            {"sessionId": "sess-1"},
            {},
            {
                "result": {
                    "value": "https://github.com/acme/demo/actions/runs/9/job/42"
                }
            },
            {
                "result": {
                    "value": {
                        "html": "<html><check-steps></check-steps></html>",
                        "url": "https://github.com/acme/demo/actions/runs/9/job/42",
                    }
                }
            },
            {
                "result": {
                    "value": {
                        "status": 200,
                        "body": "[]",
                        "headers": {"content-type": "application/json"},
                    }
                }
            },
        ]
    )
    session = _connected_session(fake, monkeypatch)

    async def _exercise() -> None:
        await session.ensure_job_page(
            "https://github.com/acme/demo/actions/runs/9/job/42"
        )
        html, url = await session.read_document_html()
        result = await session.fetch(
            "https://github.com/acme/demo/actions/runs/9/jobs/55/steps?change_id=0",
            headers={"Accept": "application/json"},
        )
        await session.aclose()
        assert html.startswith("<html>")
        assert url.endswith("/job/42")
        assert result.status == 200
        assert result.body == b"[]"

    asyncio.run(_exercise())
    assert fake.closed is True
    assert fake.connected_url == "ws://127.0.0.1:9222/devtools/browser/demo"
    assert [method for method, _params, _session in fake.calls] == [
        "Target.getTargets",
        "Target.attachToTarget",
        "Runtime.enable",
        "Runtime.evaluate",
        "Runtime.evaluate",
        "Runtime.evaluate",
    ]


def test_page_session_navigates_existing_github_tab_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Navigate the already-open GitHub tab; refuse chrome:// reuse."""
    fake = _FakeCdp(
        responses=[
            {
                "targetInfos": [
                    {
                        "targetId": "github",
                        "type": "page",
                        "url": "https://github.com/acme/demo",
                    }
                ]
            },
            {"sessionId": "sess-1"},
            {},
            {"result": {"value": "https://github.com/acme/demo"}},
            {},
            {},
            {"result": {"value": True}},
        ]
    )
    session = _connected_session(fake, monkeypatch)
    asyncio.run(
        session.ensure_job_page("https://github.com/acme/demo/actions/runs/9/job/42")
    )
    assert ("Page.navigate", {"url": "https://github.com/acme/demo/actions/runs/9/job/42"}, "sess-1") in [
        (method, params, session_id) for method, params, session_id in fake.calls
    ]

    inspect_fake = _FakeCdp(
        responses=[
            {
                "targetInfos": [
                    {
                        "targetId": "inspect",
                        "type": "page",
                        "url": "https://github.com/acme/demo",
                    }
                ]
            },
            {"sessionId": "sess-2"},
            {},
            {"result": {"value": "chrome://inspect/#remote-debugging"}},
        ]
    )
    inspect_session = _connected_session(inspect_fake, monkeypatch)
    with pytest.raises(page_session.PageSessionError, match="non-GitHub tab"):
        asyncio.run(
            inspect_session.ensure_job_page(
                "https://github.com/acme/demo/actions/runs/9/job/42"
            )
        )


def test_page_session_connect_and_evaluate_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Surface attach and evaluate failures without opening another browser."""

    async def _no_urls(**_kwargs: object) -> tuple[str, ...]:
        return ()

    monkeypatch.setattr(page_session, "_discover_cdp_browser_ws_urls", _no_urls)
    with pytest.raises(page_session.PageSessionUnavailableError, match="No Chrome DevTools"):
        asyncio.run(page_session.GitHubPageSession().ensure_job_page("https://github.com/x"))

    async def _urls(**_kwargs: object) -> tuple[str, ...]:
        return ("ws://127.0.0.1:9222/devtools/browser/demo",)

    monkeypatch.setattr(page_session, "_discover_cdp_browser_ws_urls", _urls)

    for response, match in [
        ("bad", "non-object payload"),
        ({}, "no targetInfos list"),
        ({"targetInfos": [{"targetId": "p", "type": "page", "url": "https://github.com/x"}]}, "non-object payload"),
        (
            {"targetInfos": [{"targetId": "p", "type": "page", "url": "https://github.com/x"}]},
            "sessionId",
        ),
    ]:
        responses: list[object]
        if match == "non-object payload" and response != "bad":
            responses = [response, "attached-bad"]
        elif match == "sessionId":
            responses = [response, {}]
        else:
            responses = [response]
        fake = _FakeCdp(responses=responses)
        monkeypatch.setattr(page_session, "_CdpClient", lambda fake=fake: fake)
        with pytest.raises(page_session.PageSessionError, match=match):
            asyncio.run(
                page_session.GitHubPageSession().ensure_job_page("https://github.com/x")
            )
        assert fake.closed is True

    connect_fail = _FakeCdp(
        responses=[],
        errors=[page_session.PageSessionUnavailableError("Failed connecting")],
    )
    monkeypatch.setattr(page_session, "_CdpClient", lambda: connect_fail)
    with pytest.raises(page_session.PageSessionUnavailableError, match="Failed connecting"):
        asyncio.run(page_session.GitHubPageSession().ensure_job_page("https://github.com/x"))
    assert connect_fail.closed is True


def test_page_session_evaluate_payload_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject malformed Runtime.evaluate payloads from the held tab."""
    session = page_session.GitHubPageSession()
    session._cdp = _FakeCdp(responses=["bad"])
    session._session_id = "sess"
    with pytest.raises(page_session.PageSessionError, match="not connected"):
        asyncio.run(page_session.GitHubPageSession()._evaluate_value("1"))
    with pytest.raises(page_session.PageSessionError, match="non-object payload"):
        asyncio.run(session._evaluate_value("1"))

    session._cdp = _FakeCdp(
        responses=[{"exceptionDetails": {"text": "boom"}}]
    )
    with pytest.raises(page_session.PageSessionError, match="boom"):
        asyncio.run(session._evaluate_value("1"))

    session._cdp = _FakeCdp(responses=[{"exceptionDetails": "x"}])
    with pytest.raises(page_session.PageSessionError, match="unknown exception"):
        asyncio.run(session._evaluate_value("1"))

    session._cdp = _FakeCdp(responses=[{}])
    with pytest.raises(page_session.PageSessionError, match="omitted a result"):
        asyncio.run(session._evaluate_value("1"))

    session._cdp = _FakeCdp(responses=[{"result": {"value": 1}}])
    with pytest.raises(page_session.PageSessionError, match="location.href"):
        asyncio.run(session.ensure_job_page("https://github.com/x"))

    session._cdp = _FakeCdp(responses=[{"result": {"value": "not-object"}}])
    with pytest.raises(page_session.PageSessionError, match="document HTML"):
        asyncio.run(session.read_document_html())

    session._cdp = _FakeCdp(responses=[{"result": {"value": {"html": 1, "url": "x"}}}])
    with pytest.raises(page_session.PageSessionError, match="malformed document"):
        asyncio.run(session.read_document_html())

    asyncio.run(session.aclose())
    assert session._cdp is None


def test_page_session_aclose_without_connection() -> None:
    """Closing an unused session is a no-op."""
    asyncio.run(page_session.GitHubPageSession().aclose())


def test_cdp_client_send_receive_and_transport_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cover the thin websocket adapter without talking to a live Chrome."""

    class _FakeMessage:
        def __init__(self, msg_type: aiohttp.WSMsgType, data: object = None) -> None:
            self.type = msg_type
            self.data = data

    class _FakeWebSocket:
        def __init__(self, messages: list[_FakeMessage]) -> None:
            self.messages = list(messages)
            self.sent: list[object] = []
            self.closed = False

        async def send_json(self, payload: object) -> None:
            self.sent.append(payload)

        async def receive(self) -> _FakeMessage:
            return self.messages.pop(0)

        async def close(self) -> None:
            self.closed = True

    class _FakeSession:
        def __init__(self, websocket: _FakeWebSocket | Exception) -> None:
            self.websocket = websocket
            self.closed = False

        async def ws_connect(self, _url: str, heartbeat: int) -> _FakeWebSocket:
            assert heartbeat == 30
            if isinstance(self.websocket, Exception):
                raise self.websocket
            return self.websocket

        async def close(self) -> None:
            self.closed = True

    ignore = _FakeMessage(
        aiohttp.WSMsgType.BINARY,
        b"noise",
    )
    stale = _FakeMessage(
        aiohttp.WSMsgType.TEXT,
        json.dumps({"id": 99, "result": {}}),
    )
    success = _FakeMessage(
        aiohttp.WSMsgType.TEXT,
        json.dumps({"id": 1, "result": {"ok": True}}),
    )
    websocket = _FakeWebSocket([ignore, stale, success])
    monkeypatch.setattr(
        page_session.aiohttp,
        "ClientSession",
        lambda: _FakeSession(websocket),
    )
    client = page_session._CdpClient()
    asyncio.run(client.connect("ws://127.0.0.1:9222/devtools/browser/demo"))
    result = asyncio.run(client.call("Runtime.enable", session_id="sess"))
    assert result == {"ok": True}
    assert websocket.sent == [
        {"id": 1, "method": "Runtime.enable", "sessionId": "sess"}
    ]
    asyncio.run(client.close())
    assert websocket.closed is True

    with pytest.raises(page_session.PageSessionError, match="not connected"):
        asyncio.run(page_session._CdpClient().call("Runtime.enable"))

    error_ws = _FakeWebSocket([
        _FakeMessage(
            aiohttp.WSMsgType.TEXT,
            json.dumps({"id": 1, "error": {"message": "denied"}}),
        )
    ])
    monkeypatch.setattr(
        page_session.aiohttp, "ClientSession", lambda: _FakeSession(error_ws)
    )
    error_client = page_session._CdpClient()
    asyncio.run(error_client.connect("ws://example"))
    with pytest.raises(page_session.PageSessionError, match="denied"):
        asyncio.run(error_client.call("Target.getTargets", {"x": 1}))

    string_error_ws = _FakeWebSocket([
        _FakeMessage(
            aiohttp.WSMsgType.TEXT,
            json.dumps({"id": 1, "error": "nope"}),
        )
    ])
    monkeypatch.setattr(
        page_session.aiohttp,
        "ClientSession",
        lambda: _FakeSession(string_error_ws),
    )
    string_client = page_session._CdpClient()
    asyncio.run(string_client.connect("ws://example"))
    with pytest.raises(page_session.PageSessionError, match="nope"):
        asyncio.run(string_client.call("Target.getTargets"))

    for msg_type, match in [
        (aiohttp.WSMsgType.CLOSE, "closed while waiting"),
        (aiohttp.WSMsgType.ERROR, "errored while waiting"),
    ]:
        fail_ws = _FakeWebSocket([_FakeMessage(msg_type)])
        monkeypatch.setattr(
            page_session.aiohttp, "ClientSession", lambda fail_ws=fail_ws: _FakeSession(fail_ws)
        )
        fail_client = page_session._CdpClient()
        asyncio.run(fail_client.connect("ws://example"))
        with pytest.raises(page_session.PageSessionError, match=match):
            asyncio.run(fail_client.call("Target.getTargets"))

    monkeypatch.setattr(
        page_session.aiohttp,
        "ClientSession",
        lambda: _FakeSession(aiohttp.ClientConnectionError("down")),
    )
    with pytest.raises(page_session.PageSessionUnavailableError, match="Failed connecting"):
        asyncio.run(page_session._CdpClient().connect("ws://example"))

    asyncio.run(page_session._CdpClient().close())
