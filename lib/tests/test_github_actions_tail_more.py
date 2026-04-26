"""Additional branch coverage for GitHub Actions live tailing."""

from __future__ import annotations

import asyncio
import io
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest

from lib import http_utils
from lib.github_actions import client as gha_client
from lib.github_actions import tail as gha_tail


def _context() -> gha_client.RepositoryContext:
    return gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )


def _job(
    job_id: int, name: str, status: str, conclusion: str | None = None
) -> gha_client.JobSummary:
    return gha_client.JobSummary(
        id=job_id,
        name=name,
        status=status,
        conclusion=conclusion,
        html_url=f"https://github.com/acme/demo/actions/runs/9/job/{job_id}",
        started_at=datetime(2026, 4, 2, 16, 0, tzinfo=UTC),
        completed_at=None,
    )


def _run(status: str) -> gha_client.WorkflowRun:
    timestamp = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    return gha_client.WorkflowRun.model_construct(
        id=9,
        name="workflow.yml",
        node_id="WR_9",
        check_suite_id=100,
        check_suite_node_id="CS_100",
        head_branch="main",
        head_sha="deadbeef",
        path=".github/workflows/update.yml@refs/heads/main",
        run_number=1,
        run_attempt=1,
        referenced_workflows=[],
        event="workflow_dispatch",
        status=status,
        conclusion="success" if status == "completed" else None,
        workflow_id=1,
        url="https://api.github.com/repos/acme/demo/actions/runs/9",
        html_url="https://github.com/acme/demo/actions/runs/9",
        pull_requests=[],
        created_at=timestamp,
        updated_at=timestamp,
        actor=None,
        triggering_actor=None,
        run_started_at=timestamp,
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
        display_title="Workflow",
    )


def _step(
    step_id: str,
    number: int,
    status: str,
    started_at: str | None = "2026-04-02T16:00:00Z",
) -> gha_tail.LiveStepRecord:
    return gha_tail.LiveStepRecord(
        id=step_id,
        name=step_id,
        status=status,
        conclusion="success" if status == "completed" else None,
        number=number,
        change_id=number,
        started_at=started_at,
        completed_at=None if status != "completed" else "2026-04-02T16:00:05Z",
    )


def test_live_client_close_and_cookie_helpers() -> None:
    auth = "token"
    external = httpx.AsyncClient()
    client = gha_tail.GitHubActionsLiveClient(
        token=auth, context=_context(), http_client=external
    )
    asyncio.run(client.aclose())
    assert client._http_client is external
    assert asyncio.run(client._cdp_cookies()) is None
    assert asyncio.run(client._browser_cookies()) is None
    assert client._should_retry_web_fetch(
        http_utils.SyncRequestError(
            url="https://github.com",
            attempts=1,
            kind="status",
            detail="404",
            status=404,
        )
    )
    assert not client._should_retry_web_fetch(
        http_utils.SyncRequestError(
            url="https://github.com",
            attempts=1,
            kind="status",
            detail="500",
            status=500,
        )
    )
    asyncio.run(external.aclose())


def test_discover_job_page_last_error_and_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = "token"
    client = gha_tail.GitHubActionsLiveClient(token=auth, context=_context())

    async def _attempts() -> object:
        for _item in (({"Accept": "text/html"}, None),):
            yield _item

    async def _fetch_fail(
        *_args: object, **_kwargs: object
    ) -> tuple[bytes, dict[str, str]]:
        raise http_utils.SyncRequestError(
            url="https://github.com/acme/demo/actions/runs/9/job/1",
            attempts=1,
            kind="status",
            detail="404",
            status=404,
        )

    monkeypatch.setattr(client, "_job_page_attempts", _attempts)
    monkeypatch.setattr(client, "_fetch_web_bytes_once", _fetch_fail)

    with pytest.raises(http_utils.SyncRequestError):
        asyncio.run(
            client.discover_job_page(
                job_url="https://github.com/acme/demo/actions/runs/9/job/1"
            )
        )

    async def _fetch_non_retry(
        *_args: object, **_kwargs: object
    ) -> tuple[bytes, dict[str, str]]:
        raise http_utils.SyncRequestError(
            url="https://github.com/acme/demo/actions/runs/9/job/1",
            attempts=1,
            kind="status",
            detail="500",
            status=500,
        )

    monkeypatch.setattr(client, "_fetch_web_bytes_once", _fetch_non_retry)
    with pytest.raises(http_utils.SyncRequestError):
        asyncio.run(
            client.discover_job_page(
                job_url="https://github.com/acme/demo/actions/runs/9/job/1"
            )
        )

    async def _fetch_empty(
        *_args: object, **_kwargs: object
    ) -> tuple[bytes, dict[str, str]]:
        return b"<html></html>", {}

    monkeypatch.setattr(client, "_fetch_web_bytes_once", _fetch_empty)
    info = asyncio.run(
        client.discover_job_page(
            job_url="https://github.com/acme/demo/actions/runs/9/job/1"
        )
    )
    assert info.steps_url is None

    async def _no_attempts() -> object:
        if False:
            yield None

    monkeypatch.setattr(client, "_job_page_attempts", _no_attempts)
    with pytest.raises(RuntimeError, match="Failed fetching GitHub Actions job page"):
        asyncio.run(
            client.discover_job_page(
                job_url="https://github.com/acme/demo/actions/runs/9/job/1"
            )
        )


def test_fetch_backscroll_requires_inputs_and_fetch_web_bytes_fallbacks(  # noqa: PLR0915
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = "token"
    client = gha_tail.GitHubActionsLiveClient(token=auth, context=_context())

    with pytest.raises(ValueError, match="steps_url and step_id are required"):
        asyncio.run(client.fetch_backscroll())

    seen: list[tuple[dict[str, str], httpx.Cookies | None]] = []
    jar = httpx.Cookies()
    jar.set("user_session", "abc", domain="github.com", path="/")

    monkeypatch.setattr(
        client,
        "_web_headers_candidates",
        lambda **_kwargs: ({"Accept": "application/json"},),
    )
    monkeypatch.setattr(client, "_cdp_cookies", lambda: asyncio.sleep(0, result=jar))
    monkeypatch.setattr(
        client, "_browser_cookies", lambda: asyncio.sleep(0, result=jar)
    )

    async def _fetch_once(
        *_args: object,
        headers: dict[str, str],
        cookies: httpx.Cookies | None = None,
        **_kwargs: object,
    ) -> tuple[bytes, dict[str, str]]:
        seen.append((headers, cookies))
        if cookies is None:
            raise http_utils.SyncRequestError(
                url="https://github.com",
                attempts=1,
                kind="status",
                detail="404",
                status=404,
            )
        return b"[]", {"content-type": "application/json"}

    monkeypatch.setattr(client, "_fetch_web_bytes_once", _fetch_once)
    payload, headers = asyncio.run(
        client._fetch_web_bytes(
            "https://github.com/acme/demo", accept="application/json"
        )
    )
    assert payload == b"[]"
    assert headers == {"content-type": "application/json"}
    assert seen[-1][1] is jar

    other_client = gha_tail.GitHubActionsLiveClient(token="", context=_context())
    monkeypatch.setattr(other_client, "_web_headers_candidates", lambda **_kwargs: ())
    monkeypatch.setattr(
        other_client, "_cdp_cookies", lambda: asyncio.sleep(0, result=None)
    )
    monkeypatch.setattr(
        other_client, "_browser_cookies", lambda: asyncio.sleep(0, result=None)
    )
    with pytest.raises(RuntimeError, match="Failed fetching GitHub Actions web URL"):
        asyncio.run(
            other_client._fetch_web_bytes(
                "https://github.com/acme/demo", accept="application/json"
            )
        )

    retry_client = gha_tail.GitHubActionsLiveClient(token="", context=_context())
    monkeypatch.setattr(
        retry_client,
        "_web_headers_candidates",
        lambda **_kwargs: ({"Accept": "application/json"},),
    )
    monkeypatch.setattr(
        retry_client, "_cdp_cookies", lambda: asyncio.sleep(0, result=None)
    )
    monkeypatch.setattr(
        retry_client, "_browser_cookies", lambda: asyncio.sleep(0, result=None)
    )

    async def _always_404(
        *_args: object, **_kwargs: object
    ) -> tuple[bytes, dict[str, str]]:
        raise http_utils.SyncRequestError(
            url="https://github.com",
            attempts=1,
            kind="status",
            detail="404",
            status=404,
        )

    monkeypatch.setattr(retry_client, "_fetch_web_bytes_once", _always_404)
    with pytest.raises(http_utils.SyncRequestError):
        asyncio.run(
            retry_client._fetch_web_bytes(
                "https://github.com/acme/demo", accept="application/json"
            )
        )

    non_retry_client = gha_tail.GitHubActionsLiveClient(token="", context=_context())
    monkeypatch.setattr(
        non_retry_client,
        "_web_headers_candidates",
        lambda **_kwargs: ({"Accept": "application/json"},),
    )
    monkeypatch.setattr(
        non_retry_client, "_cdp_cookies", lambda: asyncio.sleep(0, result=None)
    )
    monkeypatch.setattr(
        non_retry_client, "_browser_cookies", lambda: asyncio.sleep(0, result=None)
    )

    async def _always_500(
        *_args: object, **_kwargs: object
    ) -> tuple[bytes, dict[str, str]]:
        raise http_utils.SyncRequestError(
            url="https://github.com",
            attempts=1,
            kind="status",
            detail="500",
            status=500,
        )

    monkeypatch.setattr(non_retry_client, "_fetch_web_bytes_once", _always_500)
    with pytest.raises(http_utils.SyncRequestError):
        asyncio.run(
            non_retry_client._fetch_web_bytes(
                "https://github.com/acme/demo", accept="application/json"
            )
        )

    browser_client = gha_tail.GitHubActionsLiveClient(token="", context=_context())
    browser_jar = httpx.Cookies()
    browser_jar.set("user_session", "abc", domain="github.com", path="/")
    monkeypatch.setattr(browser_client, "_web_headers_candidates", lambda **_kwargs: ())
    monkeypatch.setattr(
        browser_client, "_cdp_cookies", lambda: asyncio.sleep(0, result=None)
    )
    monkeypatch.setattr(
        browser_client, "_browser_cookies", lambda: asyncio.sleep(0, result=browser_jar)
    )

    async def _browser_success(
        *_args: object, cookies: httpx.Cookies | None = None, **_kwargs: object
    ) -> tuple[bytes, dict[str, str]]:
        assert cookies is browser_jar
        return b"[]", {"content-type": "application/json"}

    monkeypatch.setattr(browser_client, "_fetch_web_bytes_once", _browser_success)
    assert (
        asyncio.run(
            browser_client._fetch_web_bytes(
                "https://github.com/acme/demo", accept="application/json"
            )
        )[0]
        == b"[]"
    )


def test_job_page_attempts_and_header_helpers() -> None:
    auth = "token"
    provider_cookies = httpx.Cookies()
    provider_cookies.set("user_session", "abc", domain="github.com", path="/")

    class _Provider:
        async def get_cdp_cookies(self) -> httpx.Cookies:
            return provider_cookies

        async def get_cookies(self) -> httpx.Cookies:
            return httpx.Cookies()

    client = gha_tail.GitHubActionsLiveClient(
        token=auth, context=_context(), cookie_provider=_Provider()
    )
    attempts = list(asyncio.run(_collect_attempts(client)))
    assert len(attempts) == 4
    assert attempts[0][1] is None
    assert attempts[1][1] is provider_cookies
    assert attempts[2][0]["Authorization"] == f"Bearer {auth}"
    assert attempts[3][1] is not provider_cookies
    assert (
        client._build_web_headers(
            accept="application/json",
            referer="https://github.com/x",
            include_bearer_auth=True,
        )["X-Requested-With"]
        == "XMLHttpRequest"
    )
    assert (
        client._web_headers_candidates(accept="text/html", referer=None)[0]["Accept"]
        == "text/html"
    )
    assert gha_tail.GitHubActionsLiveClient(
        token="", context=_context()
    )._web_headers_candidates(accept="text/html", referer=None) == (
        {"Accept": "text/html", "User-Agent": "nixcfg-github-actions-tail/0.0.0"},
    )
    assert (
        gha_tail
        ._non_json_payload_error(
            url="https://github.com/x", headers={"content-type": "text/html"}
        )
        .args[0]
        .endswith("'text/html'")
    )

    attempts = list(
        asyncio.run(
            _collect_attempts(
                gha_tail.GitHubActionsLiveClient(token="", context=_context())
            )
        )
    )
    assert attempts == [
        (
            {"Accept": "text/html", "User-Agent": "nixcfg-github-actions-tail/0.0.0"},
            None,
        )
    ]


async def _collect_attempts(
    client: gha_tail.GitHubActionsLiveClient,
) -> list[tuple[dict[str, str], httpx.Cookies | None]]:
    return [item async for item in client._job_page_attempts()]


def test_tailer_error_paths_and_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    output = io.StringIO()
    tailer = gha_tail.GitHubActionsTailer(
        api_client=SimpleNamespace(),
        live_client=SimpleNamespace(),
        output=output,
        poll_interval=0.1,
    )

    async def _sleep() -> None:
        output.write("slept\n")

    monkeypatch.setattr(tailer, "_sleep", _sleep)
    assert asyncio.run(
        tailer._wait_for_job_start(
            current_job=_job(1, "build", "queued"),
            steps_url=None,
            announced_waiting_statuses=set(),
        )
    )
    assert "waiting for job to start [queued]" in output.getvalue()
    assert asyncio.run(
        tailer._wait_for_job_start(
            current_job=_job(1, "build", "queued"),
            steps_url=None,
            announced_waiting_statuses={"queued"},
        )
    )
    assert not asyncio.run(
        tailer._wait_for_job_start(
            current_job=_job(1, "build", "in_progress"),
            steps_url="https://github.com/steps",
            announced_waiting_statuses=set(),
        )
    )

    completed_output = io.StringIO()
    completed_tailer = gha_tail.GitHubActionsTailer(
        api_client=SimpleNamespace(),
        live_client=SimpleNamespace(),
        output=completed_output,
        poll_interval=0.1,
    )
    asyncio.run(
        completed_tailer._tail_one_job(
            run_id=9, job=_job(1, "build", "completed", "success")
        )
    )
    assert "== job completed [success] ==" in completed_output.getvalue()

    missing_tailer = gha_tail.GitHubActionsTailer(
        api_client=SimpleNamespace(list_run_jobs=lambda _run_id: ()),
        live_client=SimpleNamespace(),
        output=io.StringIO(),
        poll_interval=0.1,
    )
    with pytest.raises(RuntimeError, match="disappeared from run"):
        asyncio.run(
            missing_tailer._tail_one_job(run_id=9, job=_job(1, "build", "in_progress"))
        )


def test_tailer_named_job_and_data_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    run = _run("in_progress")

    ambiguous_tailer = gha_tail.GitHubActionsTailer(
        api_client=SimpleNamespace(
            list_run_jobs=lambda _run_id: (_job(1, "build", "queued"),),
            get_workflow_run=lambda _run_id: run,
        ),
        live_client=SimpleNamespace(),
        output=io.StringIO(),
        poll_interval=0.1,
    )
    monkeypatch.setattr(
        gha_tail,
        "select_named_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("Ambiguous job name; matches: build")
        ),
    )
    with pytest.raises(RuntimeError, match="Ambiguous job name"):
        asyncio.run(
            ambiguous_tailer._tail_named_job(run=run, requested_job_name="build")
        )

    missing_tailer = gha_tail.GitHubActionsTailer(
        api_client=SimpleNamespace(
            list_run_jobs=lambda _run_id: (_job(1, "build", "queued"),),
            get_workflow_run=lambda _run_id: _run("completed"),
        ),
        live_client=SimpleNamespace(),
        output=io.StringIO(),
        poll_interval=0.1,
    )
    monkeypatch.setattr(
        gha_tail,
        "select_named_job",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ValueError("Unknown job 'ship'")
        ),
    )
    with pytest.raises(RuntimeError, match="never appeared before the run completed"):
        asyncio.run(missing_tailer._tail_named_job(run=run, requested_job_name="ship"))

    pending_calls = iter([
        ValueError("Unknown job 'ship'"),
        _job(1, "ship", "in_progress"),
    ])

    def _select_named_job_once(
        *_args: object, **_kwargs: object
    ) -> gha_client.JobSummary:
        value = next(pending_calls)
        if isinstance(value, Exception):
            raise value
        return value

    waiting_tailer = gha_tail.GitHubActionsTailer(
        api_client=SimpleNamespace(
            list_run_jobs=lambda _run_id: (_job(1, "ship", "queued"),),
            get_workflow_run=lambda _run_id: _run("in_progress"),
        ),
        live_client=SimpleNamespace(),
        output=io.StringIO(),
        poll_interval=0.1,
    )
    monkeypatch.setattr(
        gha_tail,
        "select_named_job",
        _select_named_job_once,
    )
    monkeypatch.setattr(waiting_tailer, "_sleep", lambda: asyncio.sleep(0))
    monkeypatch.setattr(
        waiting_tailer, "_tail_one_job", lambda **_kwargs: asyncio.sleep(0)
    )
    asyncio.run(waiting_tailer._tail_named_job(run=run, requested_job_name="ship"))

    info = gha_tail.LiveJobPageInfo(streaming_url="https://github.com/a")
    richer = gha_tail.LiveJobPageInfo(
        steps_url="https://github.com/b", backscroll_urls={"s": "https://github.com/c"}
    )
    preferred = gha_tail._prefer_richer_job_page_info(info, richer)
    assert preferred.steps_url == "https://github.com/b"
    assert preferred.backscroll_url_for("s") == "https://github.com/c"
    incremental = gha_tail._prefer_richer_job_page_info(
        gha_tail.LiveJobPageInfo(steps_url="https://github.com/existing"),
        gha_tail.LiveJobPageInfo(
            streaming_url="https://github.com/stream",
            backscroll_urls={"x": "https://github.com/backscroll"},
        ),
    )
    assert incremental.streaming_url == "https://github.com/stream"
    assert incremental.backscroll_url_for("x") == "https://github.com/backscroll"
    step_upgrade = gha_tail._prefer_richer_job_page_info(
        gha_tail.LiveJobPageInfo(
            backscroll_urls={"existing": "https://github.com/existing"}
        ),
        gha_tail.LiveJobPageInfo(steps_url="https://github.com/steps"),
    )
    assert step_upgrade.steps_url == "https://github.com/steps"
    assert (
        gha_tail._payload_is_json(b"{bad", headers={"content-type": "application/json"})
        is False
    )
    assert (
        gha_tail._extract_json_string_field('{"jobStepsUrl": 1}', key="jobStepsUrl")
        is None
    )
    assert (
        gha_tail._extract_json_string_field('{"jobStepsUrl": "ok"}', key="jobStepsUrl")
        == "ok"
    )
    assert (
        gha_tail._extract_json_string_field(
            '{"jobStepsUrl": "unterminated', key="jobStepsUrl"
        )
        is None
    )
    with pytest.raises(TypeError, match="Malformed GitHub Actions live step payload"):
        gha_tail._parse_live_step({
            "id": "step",
            "name": "build",
            "status": "queued",
            "number": True,
            "change_id": 1,
        })
    with pytest.raises(TypeError, match="Malformed GitHub Actions live step payload"):
        gha_tail._parse_live_step({
            "id": "step",
            "name": "build",
            "status": "queued",
            "number": "1",
            "change_id": 1,
        })
    assert gha_tail._job_by_id((_job(1, "a", "queued"),), 2) is None
    assert [
        step.id
        for step in gha_tail._started_steps_in_order({
            "b": _step("b", 2, "completed"),
            "a": _step("a", 1, "in_progress"),
        })
    ] == ["a", "b"]
    assert gha_tail._active_step({"a": _step("a", 1, "completed")}) is None
    assert (
        gha_tail.GitHubActionsTailer(
            api_client=SimpleNamespace(),
            live_client=SimpleNamespace(),
            output=io.StringIO(),
            poll_interval=0.1,
        )._step_backscroll_url(job_page=None, step_id="x")
        is None
    )
    parser = gha_tail._CheckStepsHTMLParser()
    parser.handle_starttag("check-step", [("data-external-id", "only-id")])
    assert parser.backscroll_urls == {}

    with pytest.raises(ValueError, match="steps URL path"):
        gha_tail._parse_steps_url_candidate(
            job_url="https://github.com/acme/demo/actions/runs/9/job/1",
            candidate="/too-short/steps",
        )


def test_live_parser_and_payload_error_edges() -> None:
    """Cover live-log parser branches that do not need a full tailer instance."""
    assert gha_tail._parse_live_step({
        "id": "step",
        "name": "build",
        "status": "queued",
        "number": 7,
        "change_id": 3,
    }) == gha_tail.LiveStepRecord(
        id="step",
        name="build",
        status="queued",
        conclusion=None,
        number=7,
        change_id=3,
        started_at=None,
        completed_at=None,
    )
    with pytest.raises(TypeError, match="Malformed GitHub Actions live step payload"):
        gha_tail._parse_live_step({"id": 1})
    with pytest.raises(
        TypeError, match="Malformed GitHub Actions live log line payload"
    ):
        gha_tail._parse_live_line({"id": "line"})

    parser = gha_tail._CheckStepsHTMLParser()
    parser.feed(
        '<check-steps job-steps-url="/steps" data-streaming-url="/stream">'
        '<check-step data-external-id="step-1" '
        'data-job-step-backscroll-url="/backscroll"></check-step>'
        "</check-steps>"
    )
    assert parser.steps_url == "/steps"
    assert parser.streaming_url == "/stream"
    assert parser.backscroll_urls == {"step-1": "/backscroll"}
    parser.handle_starttag("check-steps", [("job-steps-url", "/other-steps")])
    assert parser.steps_url == "/other-steps"
    parser.handle_starttag("check-step", [("data-external-id", "only-id")])
    assert parser.backscroll_urls == {"step-1": "/backscroll"}
    parser.handle_starttag("check-steps", [])
    parser.handle_starttag("div", [])
    assert parser.steps_url == "/other-steps"
    partial_step_info = gha_tail._parse_live_job_page_from_html(
        '<check-steps><check-step data-external-id="step-1"></check-step></check-steps>',
        job_url="https://github.com/acme/demo/actions/runs/9/job/42",
    )
    assert partial_step_info.backscroll_urls == {}


def test_tailer_waits_for_steps_then_completes(monkeypatch: pytest.MonkeyPatch) -> None:
    jobs = iter([
        (_job(1, "build", "in_progress"),),
        (_job(1, "build", "completed", "success"),),
    ])
    output = io.StringIO()
    tailer = gha_tail.GitHubActionsTailer(
        api_client=SimpleNamespace(list_run_jobs=lambda _run_id: next(jobs)),
        live_client=SimpleNamespace(
            discover_job_page=lambda **_kwargs: asyncio.sleep(
                0, result=gha_tail.LiveJobPageInfo()
            )
        ),
        output=output,
        poll_interval=0.1,
    )
    monkeypatch.setattr(tailer, "_sleep", lambda: asyncio.sleep(0))
    asyncio.run(tailer._tail_one_job(run_id=9, job=_job(1, "build", "in_progress")))
    assert "job completed before live steps became available" in output.getvalue()


def test_tailer_sleep_delegates_to_asyncio(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[float] = []

    async def _fake_sleep(delay: float) -> None:
        seen.append(delay)

    monkeypatch.setattr(gha_tail.asyncio, "sleep", _fake_sleep)
    asyncio.run(
        gha_tail.GitHubActionsTailer(
            api_client=SimpleNamespace(),
            live_client=SimpleNamespace(),
            output=io.StringIO(),
            poll_interval=0.25,
        )._sleep()
    )
    assert seen == [0.25]
