"""Tests for GitHub Actions live-tail HTML and polling helpers."""

from __future__ import annotations

import asyncio
import io
import json
from datetime import UTC, datetime
from typing import cast

import httpx
import pytest

from lib import http_utils
from lib.github_actions import client as gha_client
from lib.github_actions import tail as gha_tail


class _FakeApiClient:
    def __init__(
        self,
        *,
        jobs_by_call: list[tuple[gha_client.JobSummary, ...]],
        runs_by_call: list[gha_client.WorkflowRun],
    ) -> None:
        self._jobs_by_call = jobs_by_call
        self._runs_by_call = runs_by_call
        self._job_calls = 0
        self._run_calls = 0

    def list_run_jobs(self, _run_id: int) -> tuple[gha_client.JobSummary, ...]:
        index = min(self._job_calls, len(self._jobs_by_call) - 1)
        self._job_calls += 1
        return self._jobs_by_call[index]

    def get_workflow_run(self, _run_id: int) -> gha_client.WorkflowRun:
        index = min(self._run_calls, len(self._runs_by_call) - 1)
        self._run_calls += 1
        return self._runs_by_call[index]


class _FakeLiveClient:
    def __init__(
        self,
        *,
        steps_url: str | None,
        steps_by_call: list[tuple[gha_tail.LiveStepRecord, ...]],
        backscroll_by_step: dict[str, list[tuple[gha_tail.LiveLogLine, ...]]],
    ) -> None:
        self._steps_url = steps_url
        self._steps_by_call = steps_by_call
        self._backscroll_by_step = backscroll_by_step
        self._step_calls = 0
        self._backscroll_calls: dict[str, int] = {}

    async def discover_job_page(self, *, job_url: str) -> gha_tail.LiveJobPageInfo:
        assert job_url.startswith("https://github.com/")
        return gha_tail.LiveJobPageInfo(steps_url=self._steps_url)

    async def fetch_steps(
        self,
        *,
        steps_url: str,
        change_id: int,
        referer: str | None = None,
    ) -> tuple[gha_tail.LiveStepRecord, ...]:
        assert steps_url == self._steps_url
        assert change_id >= 0
        assert referer is None or referer.startswith("https://github.com/")
        index = min(self._step_calls, len(self._steps_by_call) - 1)
        self._step_calls += 1
        return self._steps_by_call[index]

    async def fetch_backscroll(
        self,
        *,
        steps_url: str | None = None,
        step_id: str | None = None,
        backscroll_url: str | None = None,
        referer: str | None = None,
    ) -> tuple[gha_tail.LiveLogLine, ...]:
        assert steps_url == self._steps_url
        assert step_id is not None
        assert backscroll_url is None or backscroll_url.startswith(
            "https://github.com/"
        )
        assert referer is None or referer.startswith("https://github.com/")
        index = self._backscroll_calls.get(step_id, 0)
        self._backscroll_calls[step_id] = index + 1
        entries = self._backscroll_by_step[step_id]
        return entries[min(index, len(entries) - 1)]

    async def aclose(self) -> None:
        return None


class _FakeCookieProvider:
    def __init__(
        self,
        *,
        cdp_cookies: httpx.Cookies | None = None,
        browser_cookies: httpx.Cookies | None = None,
    ) -> None:
        self._cdp_cookies = cdp_cookies
        self._browser_cookies = browser_cookies
        self.cdp_calls = 0
        self.browser_calls = 0

    async def get_cdp_cookies(self) -> httpx.Cookies | None:
        self.cdp_calls += 1
        return self._cdp_cookies

    async def get_cookies(self) -> httpx.Cookies | None:
        self.browser_calls += 1
        return self._browser_cookies


def _workflow() -> gha_client.Workflow:
    timestamp = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    return gha_client.Workflow.model_construct(
        id=1,
        node_id="WF_1",
        name="Periodic Flake Update",
        path=".github/workflows/update.yml",
        state="active",
        created_at=timestamp,
        updated_at=timestamp,
        url="https://api.github.com/repos/acme/demo/actions/workflows/1",
        html_url="https://github.com/acme/demo/actions/workflows/1",
        badge_url="https://github.com/acme/demo/actions/workflows/1/badge.svg",
        deleted_at=None,
    )


def _run(status: str, conclusion: str | None = None) -> gha_client.WorkflowRun:
    created_at = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    updated_at = datetime(2026, 4, 2, 16, 1, tzinfo=UTC)
    return gha_client.WorkflowRun.model_construct(
        id=9,
        name="update.yml",
        node_id="WR_9",
        check_suite_id=100,
        check_suite_node_id="CS_100",
        head_branch="main",
        head_sha="deadbeef",
        path=".github/workflows/update.yml@refs/heads/main",
        run_number=683,
        run_attempt=1,
        referenced_workflows=[],
        event="workflow_dispatch",
        status=status,
        conclusion=conclusion,
        workflow_id=1,
        url="https://api.github.com/repos/acme/demo/actions/runs/9",
        html_url="https://github.com/acme/demo/actions/runs/9",
        pull_requests=[],
        created_at=created_at,
        updated_at=updated_at,
        actor=None,
        triggering_actor=None,
        run_started_at=created_at,
        jobs_url="https://api.github.com/repos/acme/demo/actions/runs/9/jobs",
        logs_url="https://api.github.com/repos/acme/demo/actions/runs/9/logs",
        check_suite_url="https://api.github.com/check-suites/100",
        artifacts_url="https://api.github.com/repos/acme/demo/actions/runs/9/artifacts",
        cancel_url="https://api.github.com/repos/acme/demo/actions/runs/9/cancel",
        rerun_url="https://api.github.com/repos/acme/demo/actions/runs/9/rerun",
        previous_attempt_url=None,
        workflow_url="https://api.github.com/repos/acme/demo/actions/workflows/1",
        head_commit={},
        repository={},
        head_repository={},
        head_repository_id=1,
        display_title="Periodic Flake Update",
    )


def _job(
    job_id: int,
    name: str,
    status: str,
    conclusion: str | None = None,
) -> gha_client.JobSummary:
    started_at = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    completed_at = (
        None if status != "completed" else datetime(2026, 4, 2, 16, 5, tzinfo=UTC)
    )
    return gha_client.JobSummary(
        id=job_id,
        name=name,
        status=status,
        conclusion=conclusion,
        html_url=f"https://github.com/acme/demo/actions/runs/9/job/{job_id}",
        started_at=started_at,
        completed_at=completed_at,
    )


def _live_step(
    step_id: str,
    number: int,
    name: str,
    status: str,
) -> gha_tail.LiveStepRecord:
    return gha_tail.LiveStepRecord(
        id=step_id,
        name=name,
        status=status,
        conclusion=None if status != "completed" else "success",
        number=number,
        change_id=number,
        started_at="2026-04-02T16:00:00Z",
        completed_at=None if status != "completed" else "2026-04-02T16:00:05Z",
    )


def _cookie_jar() -> httpx.Cookies:
    jar = httpx.Cookies()
    jar.set("user_session", "abc", domain="github.com", path="/")
    return jar


def test_parse_steps_url_from_html_supports_attribute_and_json_key() -> None:
    """Extract hidden live transport URLs from both known HTML shapes."""
    attr_html = (
        '<check-steps job-steps-url="/acme/demo/actions/runs/9/jobs/55/steps" '
        'data-streaming-url="/acme/demo/commit/deadbeef/checks/42/live_logs">'
        '<check-step data-external-id="step-1" '
        'data-job-step-backscroll-url="/acme/demo/actions/runs/9/jobs/55/steps/step-1/backscroll">'
        "</check-step>"
        "</check-steps>"
    )
    info = gha_tail._parse_live_job_page_from_html(
        attr_html,
        job_url="https://github.com/acme/demo/actions/runs/9/job/42",
    )
    assert info.steps_url == "https://github.com/acme/demo/actions/runs/9/jobs/55/steps"
    assert (
        info.streaming_url
        == "https://github.com/acme/demo/commit/deadbeef/checks/42/live_logs"
    )
    assert (
        info.backscroll_url_for("step-1")
        == "https://github.com/acme/demo/actions/runs/9/jobs/55/steps/step-1/backscroll"
    )
    assert info.check_steps_found is True
    assert info.static_step_count == 1

    regex_html = '{"jobStepsUrl":"/acme/demo/actions/runs/9/jobs/66/steps?change_id=0"}'
    assert (
        gha_tail._parse_steps_url_from_html(
            regex_html,
            job_url="https://github.com/acme/demo/actions/runs/9/job/42",
        )
        == "https://github.com/acme/demo/actions/runs/9/jobs/66/steps"
    )

    empty_info = gha_tail._parse_live_job_page_from_html(
        "<html><body>no live steps here</body></html>",
        job_url="https://github.com/acme/demo/actions/runs/9/job/42",
    )
    assert empty_info.steps_url is None
    assert empty_info.streaming_url is None
    assert empty_info.backscroll_urls == {}
    assert empty_info.check_steps_found is False

    static_steps_info = gha_tail._parse_live_job_page_from_html(
        (
            '<check-steps data-job-status="in_progress">'
            '<check-step data-name="Set up job" data-number="1" '
            'data-external-id="step-1" data-log-url="/acme/demo/logs/1">'
            "</check-step>"
            "</check-steps>"
        ),
        job_url="https://github.com/acme/demo/actions/runs/9/job/42",
    )
    assert static_steps_info.steps_url is None
    assert static_steps_info.check_steps_found is True
    assert static_steps_info.static_step_count == 1
    assert static_steps_info.logged_in is False

    with pytest.raises(ValueError, match="steps URL path"):
        gha_tail._parse_steps_url_from_html(
            '{"jobStepsUrl":"/acme/demo/actions/runs/9/jobs/66/not-steps"}',
            job_url="https://github.com/acme/demo/actions/runs/9/job/42",
        )

    with pytest.raises(ValueError, match="steps URL origin"):
        gha_tail._parse_steps_url_from_html(
            '{"jobStepsUrl":"https://example.com/acme/demo/actions/runs/9/jobs/66/steps"}',
            job_url="https://github.com/acme/demo/actions/runs/9/job/42",
        )

    with pytest.raises(ValueError, match="step backscroll URL origin"):
        gha_tail._parse_live_job_page_from_html(
            '<check-steps><check-step data-external-id="step-1" '
            'data-job-step-backscroll-url="https://example.com/acme/demo/actions/runs/9/jobs/55/steps/step-1/backscroll">'
            "</check-step></check-steps>",
            job_url="https://github.com/acme/demo/actions/runs/9/job/42",
        )


def test_live_client_fetches_html_steps_and_backscroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Decode the hidden job page, steps poll, and backscroll payloads."""
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )
    seen_requests: list[tuple[str, dict[str, str] | None, httpx.Cookies | None]] = []

    async def _fetch_url_bytes_async(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: httpx.Cookies | None = None,
        **_kwargs: object,
    ) -> tuple[bytes, dict[str, str]]:
        seen_requests.append((url, headers, cookies))
        assert headers is not None
        assert "Authorization" not in headers
        assert cookies is None
        if url.endswith("/job/42"):
            html = (
                b'<check-steps job-steps-url="/acme/demo/actions/runs/9/jobs/55/steps">'
                b"</check-steps>"
            )
            return html, {}
        if url.endswith("/jobs/55/steps?change_id=0"):
            body = [
                {
                    "id": "step-1",
                    "name": "Pre-fetch flake inputs",
                    "status": "in_progress",
                    "conclusion": None,
                    "number": 7,
                    "change_id": 3,
                    "started_at": "2026-04-02T16:00:00Z",
                    "completed_at": None,
                }
            ]
            return json.dumps(body).encode("utf-8"), {}
        if url.endswith("/jobs/55/steps/step-1/backscroll"):
            body = {"lines": [{"id": "1", "line": "hello"}]}
            return json.dumps(body).encode("utf-8"), {}
        msg = f"unexpected url {url}"
        raise AssertionError(msg)

    monkeypatch.setattr(
        gha_tail.http_utils, "fetch_url_bytes_async", _fetch_url_bytes_async
    )

    async def _exercise() -> None:
        client = gha_tail.GitHubActionsLiveClient(
            token="test" + "-token",
            context=context,
        )
        try:
            steps_url = await client.discover_steps_url(
                job_url="https://github.com/acme/demo/actions/runs/9/job/42"
            )
            assert (
                steps_url == "https://github.com/acme/demo/actions/runs/9/jobs/55/steps"
            )

            referer = "https://github.com/acme/demo/actions/runs/9/job/42"
            steps = await client.fetch_steps(
                steps_url=steps_url,
                change_id=0,
                referer=referer,
            )
            assert steps[0].name == "Pre-fetch flake inputs"
            assert steps[0].change_id == 3

            backscroll = await client.fetch_backscroll(
                steps_url=steps_url,
                step_id="step-1",
                referer=referer,
            )
            assert backscroll == (gha_tail.LiveLogLine(id="1", line="hello"),)
        finally:
            await client.aclose()

    asyncio.run(_exercise())

    assert seen_requests[-1][0].endswith("/jobs/55/steps/step-1/backscroll")
    html_headers = seen_requests[0][1]
    steps_headers = seen_requests[1][1]
    backscroll_headers = seen_requests[2][1]
    assert html_headers is not None
    assert "X-Requested-With" not in html_headers
    assert steps_headers is not None
    assert steps_headers["X-Requested-With"] == "XMLHttpRequest"
    assert (
        steps_headers["Referer"] == "https://github.com/acme/demo/actions/runs/9/job/42"
    )
    assert backscroll_headers is not None
    assert (
        backscroll_headers["Referer"]
        == "https://github.com/acme/demo/actions/runs/9/job/42"
    )


def test_live_client_uses_explicit_backscroll_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Support the exact backscroll URL embedded in the browser DOM."""
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )
    seen_urls: list[str] = []

    async def _fetch_url_bytes_async(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> tuple[bytes, dict[str, str]]:
        assert headers is not None
        seen_urls.append(url)
        body = {"lines": [{"id": "1", "line": "hello"}]}
        return json.dumps(body).encode("utf-8"), {}

    monkeypatch.setattr(
        gha_tail.http_utils, "fetch_url_bytes_async", _fetch_url_bytes_async
    )

    async def _exercise() -> None:
        client = gha_tail.GitHubActionsLiveClient(
            token="test" + "-token",
            context=context,
        )
        try:
            lines = await client.fetch_backscroll(
                backscroll_url=(
                    "https://github.com/acme/demo/actions/runs/9/jobs/55/steps/step-1/backscroll"
                ),
                referer="https://github.com/acme/demo/actions/runs/9/job/42",
            )
            assert lines == (gha_tail.LiveLogLine(id="1", line="hello"),)
        finally:
            await client.aclose()

    asyncio.run(_exercise())

    assert seen_urls == [
        "https://github.com/acme/demo/actions/runs/9/jobs/55/steps/step-1/backscroll"
    ]


def test_live_client_falls_back_to_authenticated_web_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry GitHub web polling with bearer auth after public access is denied."""
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )
    seen_headers: list[dict[str, str]] = []

    async def _fetch_url_bytes_async(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> tuple[bytes, dict[str, str]]:
        assert headers is not None
        seen_headers.append(headers)
        if "Authorization" not in headers:
            raise http_utils.SyncRequestError(
                url=url,
                attempts=1,
                kind="status",
                detail="HTTP 404 Not Found",
                status=404,
            )
        body = [
            {
                "id": "step-1",
                "name": "Pre-fetch flake inputs",
                "status": "in_progress",
                "conclusion": None,
                "number": 7,
                "change_id": 3,
                "started_at": "2026-04-02T16:00:00Z",
                "completed_at": None,
            }
        ]
        return json.dumps(body).encode("utf-8"), {}

    monkeypatch.setattr(
        gha_tail.http_utils, "fetch_url_bytes_async", _fetch_url_bytes_async
    )

    async def _exercise() -> None:
        client = gha_tail.GitHubActionsLiveClient(
            token="test" + "-token",
            context=context,
        )
        try:
            steps = await client.fetch_steps(
                steps_url="https://github.com/acme/demo/actions/runs/9/jobs/55/steps",
                change_id=0,
            )
            assert steps[0].id == "step-1"
        finally:
            await client.aclose()

    asyncio.run(_exercise())

    assert len(seen_headers) == 2
    assert "Authorization" not in seen_headers[0]
    assert seen_headers[1]["Authorization"] == "Bearer test-token"


def test_live_client_retries_auth_when_json_endpoint_returns_html(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Treat HTML login pages as auth misses and continue to the next strategy."""
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )
    seen_headers: list[dict[str, str]] = []

    async def _fetch_url_bytes_async(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> tuple[bytes, dict[str, str]]:
        assert headers is not None
        seen_headers.append(headers)
        if "Authorization" not in headers:
            return b"<html><body>login</body></html>", {"content-type": "text/html"}
        body = [
            {
                "id": "step-1",
                "name": "Pre-fetch flake inputs",
                "status": "in_progress",
                "conclusion": None,
                "number": 7,
                "change_id": 3,
                "started_at": "2026-04-02T16:00:00Z",
                "completed_at": None,
            }
        ]
        return json.dumps(body).encode("utf-8"), {"content-type": "application/json"}

    monkeypatch.setattr(
        gha_tail.http_utils, "fetch_url_bytes_async", _fetch_url_bytes_async
    )

    async def _exercise() -> None:
        client = gha_tail.GitHubActionsLiveClient(
            token="test" + "-token",
            context=context,
        )
        try:
            steps = await client.fetch_steps(
                steps_url="https://github.com/acme/demo/actions/runs/9/jobs/55/steps",
                change_id=0,
            )
            assert steps[0].id == "step-1"
        finally:
            await client.aclose()

    asyncio.run(_exercise())

    assert len(seen_headers) == 2
    assert "Authorization" not in seen_headers[0]
    assert seen_headers[1]["Authorization"] == "Bearer test-token"


def test_live_client_uses_cookie_provider_for_job_page_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Retry stripped job pages with browser cookies before giving up."""
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )
    provider = _FakeCookieProvider(browser_cookies=_cookie_jar())
    seen_cookies: list[httpx.Cookies | None] = []

    async def _fetch_url_bytes_async(
        url: str,
        *,
        cookies: httpx.Cookies | None = None,
        headers: dict[str, str] | None = None,
        **_kwargs: object,
    ) -> tuple[bytes, dict[str, str]]:
        assert url.endswith("/job/42")
        seen_cookies.append(cookies)
        if cookies is None and headers is not None and "Authorization" in headers:
            return b"<html><body>still stripped</body></html>", {}
        if cookies is None:
            return b"<html><body>stripped</body></html>", {}
        html = (
            b'<check-steps data-job-steps-url="/acme/demo/actions/runs/9/jobs/55/steps">'
            b"</check-steps>"
        )
        return html, {}

    monkeypatch.setattr(
        gha_tail.http_utils, "fetch_url_bytes_async", _fetch_url_bytes_async
    )

    async def _exercise() -> None:
        client = gha_tail.GitHubActionsLiveClient(
            token="test" + "-token",
            context=context,
            cookie_provider=provider,
        )
        try:
            info = await client.discover_job_page(
                job_url="https://github.com/acme/demo/actions/runs/9/job/42"
            )
            assert info.steps_url == (
                "https://github.com/acme/demo/actions/runs/9/jobs/55/steps"
            )
        finally:
            await client.aclose()

    asyncio.run(_exercise())

    assert provider.cdp_calls == 1
    assert provider.browser_calls == 1
    assert seen_cookies[0] is None
    assert seen_cookies[1] is None
    assert isinstance(seen_cookies[2], httpx.Cookies)


def test_live_client_tolerates_stale_cdp_cookies_and_falls_back_to_bearer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failing cookie attempt should not block later auth strategies."""
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )
    provider = _FakeCookieProvider(cdp_cookies=_cookie_jar())
    auth_kinds: list[str] = []

    async def _fetch_url_bytes_async(
        url: str,
        *,
        headers: dict[str, str] | None = None,
        cookies: httpx.Cookies | None = None,
        **_kwargs: object,
    ) -> tuple[bytes, dict[str, str]]:
        assert headers is not None
        assert url.endswith("/job/42")
        if cookies is not None:
            auth_kinds.append("cookie")
            raise http_utils.SyncRequestError(
                url=url,
                attempts=1,
                kind="status",
                detail="HTTP 404 Not Found",
                status=404,
            )
        if "Authorization" in headers:
            auth_kinds.append("bearer")
            html = (
                b'<check-steps data-job-steps-url="/acme/demo/actions/runs/9/jobs/55/steps">'
                b"</check-steps>"
            )
            return html, {}
        auth_kinds.append("public")
        return b"<html><body>stripped</body></html>", {}

    monkeypatch.setattr(
        gha_tail.http_utils, "fetch_url_bytes_async", _fetch_url_bytes_async
    )

    async def _exercise() -> None:
        client = gha_tail.GitHubActionsLiveClient(
            token="test" + "-token",
            context=context,
            cookie_provider=provider,
        )
        try:
            info = await client.discover_job_page(
                job_url="https://github.com/acme/demo/actions/runs/9/job/42"
            )
            assert info.steps_url == (
                "https://github.com/acme/demo/actions/runs/9/jobs/55/steps"
            )
        finally:
            await client.aclose()

    asyncio.run(_exercise())

    assert auth_kinds == ["public", "cookie", "bearer"]


def test_tailer_requires_positive_poll_interval() -> None:
    """Reject non-positive polling intervals up front."""
    with pytest.raises(ValueError, match="poll_interval must be positive"):
        gha_tail.GitHubActionsTailer(
            api_client=object(),  # type: ignore[arg-type]
            live_client=object(),  # type: ignore[arg-type]
            poll_interval=0,
        )


def test_tailer_waits_for_queued_named_job_before_discovering_live_steps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Queued jobs should wait to start before probing the live-log transport."""
    jobs_by_call = [
        (_job(42, "darwin-lock-smoke", "queued"),),
        (_job(42, "darwin-lock-smoke", "queued"),),
        (_job(42, "darwin-lock-smoke", "in_progress"),),
        (_job(42, "darwin-lock-smoke", "completed", "success"),),
    ]
    api = _FakeApiClient(jobs_by_call=jobs_by_call, runs_by_call=[_run("in_progress")])
    output = io.StringIO()
    tailer = gha_tail.GitHubActionsTailer(
        api_client=cast("gha_client.GitHubActionsClient", api),
        live_client=cast("gha_tail.GitHubActionsLiveClient", object()),
        output=output,
        poll_interval=0.01,
    )
    sleep_calls = 0

    async def _no_sleep() -> None:
        nonlocal sleep_calls
        sleep_calls += 1

    class _QueuedLiveClient:
        def __init__(self) -> None:
            self._step_calls = 0

        async def discover_job_page(self, *, job_url: str) -> gha_tail.LiveJobPageInfo:
            assert sleep_calls > 0
            assert job_url.startswith("https://github.com/")
            return gha_tail.LiveJobPageInfo(
                steps_url="https://github.com/acme/demo/actions/runs/9/jobs/55/steps"
            )

        async def fetch_steps(
            self,
            *,
            steps_url: str,
            change_id: int,
            referer: str | None = None,
        ) -> tuple[gha_tail.LiveStepRecord, ...]:
            assert steps_url.endswith("/steps")
            assert change_id >= 0
            assert referer is None or referer.startswith("https://github.com/")
            self._step_calls += 1
            if self._step_calls == 1:
                return (
                    _live_step("step-1", 7, "Pre-fetch flake inputs", "in_progress"),
                )
            return (_live_step("step-1", 7, "Pre-fetch flake inputs", "completed"),)

        async def fetch_backscroll(
            self,
            *,
            steps_url: str | None = None,
            step_id: str | None = None,
            backscroll_url: str | None = None,
            referer: str | None = None,
        ) -> tuple[gha_tail.LiveLogLine, ...]:
            del steps_url, step_id, backscroll_url, referer
            return (gha_tail.LiveLogLine(id="1", line="queued line"),)

    tailer.live_client = cast("gha_tail.GitHubActionsLiveClient", _QueuedLiveClient())
    monkeypatch.setattr(tailer, "_sleep", _no_sleep)

    asyncio.run(
        tailer.tail_workflow(
            workflow=_workflow(),
            run=_run("in_progress"),
            requested_job_name="darwin-lock-smoke",
        )
    )

    rendered = output.getvalue()
    assert "== waiting for job to start [queued] ==" in rendered
    assert "queued line" in rendered
    assert "== job completed [success] ==" in rendered
    assert sleep_calls > 0


def test_tailer_tails_named_job_and_dedupes_lines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Follow one job until completion and avoid printing duplicate lines."""
    jobs_by_call = [
        (_job(42, "darwin-lock-smoke", "in_progress"),),
        (_job(42, "darwin-lock-smoke", "in_progress"),),
        (_job(42, "darwin-lock-smoke", "completed", "success"),),
    ]
    api = _FakeApiClient(jobs_by_call=jobs_by_call, runs_by_call=[_run("in_progress")])
    live = _FakeLiveClient(
        steps_url="https://github.com/acme/demo/actions/runs/9/jobs/55/steps",
        steps_by_call=[
            (_live_step("step-1", 7, "Pre-fetch flake inputs", "in_progress"),),
            (_live_step("step-1", 7, "Pre-fetch flake inputs", "completed"),),
        ],
        backscroll_by_step={
            "step-1": [
                (
                    gha_tail.LiveLogLine(id="1", line="first line"),
                    gha_tail.LiveLogLine(id="2", line="second line"),
                ),
                (
                    gha_tail.LiveLogLine(id="1", line="first line"),
                    gha_tail.LiveLogLine(id="2", line="second line"),
                ),
            ]
        },
    )
    output = io.StringIO()
    tailer = gha_tail.GitHubActionsTailer(
        api_client=cast("gha_client.GitHubActionsClient", api),
        live_client=cast("gha_tail.GitHubActionsLiveClient", live),
        output=output,
        poll_interval=0.01,
    )

    async def _no_sleep() -> None:
        return None

    monkeypatch.setattr(tailer, "_sleep", _no_sleep)

    asyncio.run(
        tailer.tail_workflow(
            workflow=_workflow(),
            run=_run("in_progress"),
            requested_job_name="darwin-lock-smoke",
        )
    )

    rendered = output.getvalue()
    assert "== workflow 'Periodic Flake Update' run #683 [in_progress] ==" in rendered
    assert "== job 'darwin-lock-smoke' [in_progress] ==" in rendered
    assert "-- step 7: Pre-fetch flake inputs --" in rendered
    assert rendered.count("first line") == 1
    assert rendered.count("second line") == 1
    assert "== job completed [success] ==" in rendered


def test_tailer_replays_fast_completed_steps_before_the_current_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Emit logs for steps first seen already completed on a later poll."""
    jobs_by_call = [
        (_job(42, "darwin-lock-smoke", "in_progress"),),
        (_job(42, "darwin-lock-smoke", "in_progress"),),
        (_job(42, "darwin-lock-smoke", "completed", "success"),),
    ]
    api = _FakeApiClient(jobs_by_call=jobs_by_call, runs_by_call=[_run("in_progress")])
    live = _FakeLiveClient(
        steps_url="https://github.com/acme/demo/actions/runs/9/jobs/55/steps",
        steps_by_call=[
            (
                _live_step("step-1", 1, "Set up job", "completed"),
                _live_step("step-2", 2, "Pre-fetch flake inputs", "in_progress"),
            )
        ],
        backscroll_by_step={
            "step-1": [(gha_tail.LiveLogLine(id="1", line="setup line"),)],
            "step-2": [(gha_tail.LiveLogLine(id="2", line="active line"),)],
        },
    )
    output = io.StringIO()
    tailer = gha_tail.GitHubActionsTailer(
        api_client=cast("gha_client.GitHubActionsClient", api),
        live_client=cast("gha_tail.GitHubActionsLiveClient", live),
        output=output,
        poll_interval=0.01,
    )

    async def _no_sleep() -> None:
        return None

    monkeypatch.setattr(tailer, "_sleep", _no_sleep)

    asyncio.run(
        tailer.tail_workflow(
            workflow=_workflow(),
            run=_run("in_progress"),
            requested_job_name="darwin-lock-smoke",
        )
    )

    rendered = output.getvalue()
    assert "-- step 1: Set up job --" in rendered
    assert "setup line" in rendered
    assert "-- step 2: Pre-fetch flake inputs --" in rendered
    assert "active line" in rendered
    assert rendered.index("setup line") < rendered.index("active line")
    assert "== job completed [success] ==" in rendered


def test_tailer_discovers_live_steps_after_job_completes_before_first_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep one discovery attempt after completion so fast jobs can still replay logs."""
    jobs_by_call = [
        (_job(42, "darwin-lock-smoke", "in_progress"),),
        (_job(42, "darwin-lock-smoke", "completed", "success"),),
    ]
    api = _FakeApiClient(jobs_by_call=jobs_by_call, runs_by_call=[_run("in_progress")])
    live = _FakeLiveClient(
        steps_url="https://github.com/acme/demo/actions/runs/9/jobs/55/steps",
        steps_by_call=[(_live_step("step-1", 1, "Set up job", "completed"),)],
        backscroll_by_step={
            "step-1": [(gha_tail.LiveLogLine(id="1", line="late line"),)],
        },
    )
    output = io.StringIO()
    tailer = gha_tail.GitHubActionsTailer(
        api_client=cast("gha_client.GitHubActionsClient", api),
        live_client=cast("gha_tail.GitHubActionsLiveClient", live),
        output=output,
        poll_interval=0.01,
    )

    async def _no_sleep() -> None:
        return None

    monkeypatch.setattr(tailer, "_sleep", _no_sleep)

    asyncio.run(
        tailer.tail_workflow(
            workflow=_workflow(),
            run=_run("in_progress"),
            requested_job_name="darwin-lock-smoke",
        )
    )

    rendered = output.getvalue()
    assert "-- step 1: Set up job --" in rendered
    assert "late line" in rendered
    assert "== job completed [success] ==" in rendered
    assert "job completed before live steps became available" not in rendered


def test_tailer_exits_when_job_completes_without_final_step_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exit cleanly even if the last live step never leaves in-progress."""
    jobs_by_call = [
        (_job(42, "darwin-lock-smoke", "in_progress"),),
        (_job(42, "darwin-lock-smoke", "in_progress"),),
        (_job(42, "darwin-lock-smoke", "completed", "success"),),
    ]
    api = _FakeApiClient(jobs_by_call=jobs_by_call, runs_by_call=[_run("in_progress")])
    live = _FakeLiveClient(
        steps_url="https://github.com/acme/demo/actions/runs/9/jobs/55/steps",
        steps_by_call=[
            (_live_step("step-1", 7, "Pre-fetch flake inputs", "in_progress"),)
        ],
        backscroll_by_step={
            "step-1": [
                (gha_tail.LiveLogLine(id="1", line="final line"),),
                (gha_tail.LiveLogLine(id="1", line="final line"),),
            ]
        },
    )
    output = io.StringIO()
    tailer = gha_tail.GitHubActionsTailer(
        api_client=cast("gha_client.GitHubActionsClient", api),
        live_client=cast("gha_tail.GitHubActionsLiveClient", live),
        output=output,
        poll_interval=0.01,
    )

    async def _no_sleep() -> None:
        return None

    monkeypatch.setattr(tailer, "_sleep", _no_sleep)

    asyncio.run(
        tailer.tail_workflow(
            workflow=_workflow(),
            run=_run("in_progress"),
            requested_job_name="darwin-lock-smoke",
        )
    )

    rendered = output.getvalue()
    assert rendered.count("final line") == 1
    assert "== job completed [success] ==" in rendered


def test_tailer_hops_jobs_and_skips_completed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In workflow mode, follow the next active job and report skipped ones."""
    jobs_by_call = [
        (
            _job(10, "resolve-versions", "in_progress"),
            _job(11, "update-lock", "completed", "success"),
        ),
        (
            _job(10, "resolve-versions", "completed", "success"),
            _job(11, "update-lock", "completed", "success"),
        ),
    ]
    api = _FakeApiClient(
        jobs_by_call=jobs_by_call,
        runs_by_call=[_run("in_progress"), _run("completed", "success")],
    )
    live = _FakeLiveClient(steps_url=None, steps_by_call=[], backscroll_by_step={})
    output = io.StringIO()
    tailer = gha_tail.GitHubActionsTailer(
        api_client=cast("gha_client.GitHubActionsClient", api),
        live_client=cast("gha_tail.GitHubActionsLiveClient", live),
        output=output,
        poll_interval=0.01,
    )
    tailed_jobs: list[int] = []

    async def _no_sleep() -> None:
        return None

    async def _tail_one_job(*, run_id: int, job: gha_client.JobSummary) -> None:
        assert run_id == 9
        tailed_jobs.append(job.id)

    monkeypatch.setattr(tailer, "_sleep", _no_sleep)
    monkeypatch.setattr(tailer, "_tail_one_job", _tail_one_job)

    asyncio.run(
        tailer.tail_workflow(
            workflow=_workflow(),
            run=_run("in_progress"),
            requested_job_name=None,
        )
    )

    assert tailed_jobs == [10]
    rendered = output.getvalue()
    assert "== skipped already-completed job 'update-lock' [success] ==" in rendered
    assert "== workflow run completed [success] ==" in rendered
