"""Live GitHub Actions job tailing via the web UI's polling endpoints.

The official REST API is used for workflow/run/job discovery in
``lib.github_actions.client``. This module isolates the undocumented live-log
transport that powers the GitHub web UI today: a job page exposes an internal
``.../jobs/<id>/steps`` endpoint plus per-step ``backscroll`` endpoints.

The transport is intentionally kept separate so it can be swapped cleanly if
GitHub moves back to websocket-based live logs in the future.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import TYPE_CHECKING, TextIO
from urllib.parse import urljoin, urlsplit

import httpx

from lib import http_utils, json_utils
from lib.github_actions.client import (
    GitHubActionsClient,
    JobSummary,
    RepositoryContext,
    Workflow,
    WorkflowRun,
    choose_next_live_job,
    select_named_job,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from lib.github_actions.web_auth import GitHubWebCookieProvider

_JSON_ACCEPT = "application/json"
_HTML_ACCEPT = "text/html"
_USER_AGENT = "nixcfg-github-actions-tail/0.0.0"
_DEFAULT_POLL_INTERVAL = 1.0
_DEFAULT_TIMEOUT = 30.0
_JOB_STEPS_URL_ATTRIBUTES = ("job-steps-url", "data-job-steps-url")
_JOB_STEPS_URL_JSON_KEYS = ("jobStepsUrl",)
_STREAMING_URL_ATTRIBUTES = ("data-streaming-url",)
_STEP_BACKSCROLL_URL_ATTRIBUTE = "data-job-step-backscroll-url"
_STEP_ID_ATTRIBUTE = "data-external-id"
_MIN_STEPS_PATH_PARTS = 8
_WEB_FALLBACK_STATUSES = frozenset({401, 403, 404})
_PRELIVE_JOB_STATUSES = frozenset({"pending", "queued", "requested", "waiting"})


@dataclass
class LiveJobPageInfo:
    """Hidden live-log transport metadata embedded in one GitHub job page."""

    steps_url: str | None = None
    streaming_url: str | None = None
    backscroll_urls: dict[str, str] = field(default_factory=dict)

    def backscroll_url_for(self, step_id: str) -> str | None:
        """Return the exact per-step backscroll URL embedded in the job page."""
        return self.backscroll_urls.get(step_id)


@dataclass(frozen=True)
class LiveStepRecord:
    """One step returned by the job-step polling endpoint."""

    id: str
    name: str
    status: str
    conclusion: str | None
    number: int
    change_id: int
    started_at: str | None
    completed_at: str | None


@dataclass(frozen=True)
class LiveLogLine:
    """One log line from a step backscroll response."""

    id: str
    line: str


class GitHubActionsLiveClient:
    """HTTP client for the GitHub web UI's live-log polling endpoints."""

    def __init__(
        self,
        *,
        token: str,
        context: RepositoryContext,
        cookie_provider: GitHubWebCookieProvider | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Bind the live-log client to one repository context and token."""
        self._token = token
        self.context = context
        self._cookie_provider = cookie_provider
        self._http_client = http_client
        self._owns_http_client = http_client is None

    async def aclose(self) -> None:
        """Close the owned async HTTP client, if any."""
        if self._http_client is None or not self._owns_http_client:
            return
        await self._http_client.aclose()
        self._http_client = None

    async def discover_job_page(self, *, job_url: str) -> LiveJobPageInfo:
        """Return hidden live-log transport metadata exposed on one job page."""
        best_info = LiveJobPageInfo()
        saw_success = False
        last_error: http_utils.RequestError | None = None

        async for headers, cookies in self._job_page_attempts():
            try:
                payload, _headers = await self._fetch_web_bytes_once(
                    job_url,
                    headers=headers,
                    cookies=cookies,
                )
            except http_utils.RequestError as exc:
                if not self._should_retry_web_fetch(exc):
                    raise
                last_error = exc
                continue

            saw_success = True
            info = _parse_live_job_page_from_html(
                payload.decode("utf-8", errors="ignore"),
                job_url=job_url,
            )
            best_info = _prefer_richer_job_page_info(best_info, info)
            if _job_page_has_live_metadata(best_info):
                return best_info

        if saw_success:
            return best_info
        if last_error is not None:
            raise last_error
        msg = f"Failed fetching GitHub Actions job page {job_url}"
        raise RuntimeError(msg)

    async def discover_steps_url(self, *, job_url: str) -> str | None:
        """Return the internal ``steps`` endpoint for one job page, if exposed."""
        return (await self.discover_job_page(job_url=job_url)).steps_url

    async def fetch_steps(
        self,
        *,
        steps_url: str,
        change_id: int,
        referer: str | None = None,
    ) -> tuple[LiveStepRecord, ...]:
        """Return updated steps for one job from the live polling endpoint."""
        payload, _headers = await self._fetch_web_bytes(
            f"{steps_url}?change_id={change_id}",
            accept=_JSON_ACCEPT,
            referer=referer,
        )
        body = json.loads(payload)
        records = json_utils.as_object_list(body, context="live job steps")
        return tuple(_parse_live_step(item) for item in records)

    async def fetch_backscroll(
        self,
        *,
        steps_url: str | None = None,
        step_id: str | None = None,
        backscroll_url: str | None = None,
        referer: str | None = None,
    ) -> tuple[LiveLogLine, ...]:
        """Return currently available log lines for one live step."""
        if backscroll_url is None:
            if steps_url is None or step_id is None:
                msg = (
                    "steps_url and step_id are required when backscroll_url is omitted"
                )
                raise ValueError(msg)
            backscroll_url = f"{steps_url}/{step_id}/backscroll"
        payload, _headers = await self._fetch_web_bytes(
            backscroll_url,
            accept=_JSON_ACCEPT,
            referer=referer,
        )
        body = json.loads(payload)
        mapping = json_utils.as_object_dict(body, context="step backscroll")
        records = json_utils.as_object_list(
            mapping.get("lines", []),
            context="step backscroll lines",
        )
        return tuple(_parse_live_line(item) for item in records)

    async def _fetch_web_bytes(
        self,
        url: str,
        *,
        accept: str,
        referer: str | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        last_error: Exception | None = None

        async def _try_fetch(
            *, headers: dict[str, str], cookies: httpx.Cookies | None = None
        ) -> tuple[bytes, dict[str, str]] | None:
            nonlocal last_error
            try:
                payload, response_headers = await self._fetch_web_bytes_once(
                    url,
                    headers=headers,
                    cookies=cookies,
                )
            except http_utils.RequestError as exc:
                last_error = exc
                if not self._should_retry_web_fetch(exc):
                    raise
                return None
            if accept == _JSON_ACCEPT and not _payload_is_json(
                payload,
                headers=response_headers,
            ):
                last_error = _non_json_payload_error(url=url, headers=response_headers)
                return None
            return payload, response_headers

        for headers in self._web_headers_candidates(accept=accept, referer=referer):
            if (result := await _try_fetch(headers=headers)) is not None:
                return result

        cdp_cookies = await self._cdp_cookies()
        cdp_result = (
            None
            if cdp_cookies is None
            else await _try_fetch(
                headers=self._build_web_headers(accept=accept, referer=referer),
                cookies=cdp_cookies,
            )
        )
        if cdp_result is not None:
            return cdp_result

        browser_cookies = await self._browser_cookies()
        browser_result = (
            None
            if browser_cookies is None or browser_cookies is cdp_cookies
            else await _try_fetch(
                headers=self._build_web_headers(accept=accept, referer=referer),
                cookies=browser_cookies,
            )
        )
        if browser_result is not None:
            return browser_result

        if last_error is not None:
            raise last_error
        msg = f"Failed fetching GitHub Actions web URL {url}"
        raise RuntimeError(msg)

    async def _fetch_web_bytes_once(
        self,
        url: str,
        *,
        headers: dict[str, str],
        cookies: httpx.Cookies | None = None,
    ) -> tuple[bytes, dict[str, str]]:
        return await http_utils.fetch_url_bytes_async(
            url,
            headers=headers,
            cookies=cookies,
            client=await self._http_client_instance(),
        )

    async def _http_client_instance(self) -> httpx.AsyncClient:
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=_DEFAULT_TIMEOUT,
            )
        return self._http_client

    async def _job_page_attempts(
        self,
    ) -> AsyncIterator[tuple[dict[str, str], httpx.Cookies | None]]:
        yield self._build_web_headers(accept=_HTML_ACCEPT, referer=None), None
        cdp_cookies = await self._cdp_cookies()
        if cdp_cookies is not None:
            yield (
                self._build_web_headers(accept=_HTML_ACCEPT, referer=None),
                cdp_cookies,
            )
        if self._token:
            yield (
                self._build_web_headers(
                    accept=_HTML_ACCEPT,
                    referer=None,
                    include_bearer_auth=True,
                ),
                None,
            )
        browser_cookies = await self._browser_cookies()
        if browser_cookies is not None and browser_cookies is not cdp_cookies:
            yield (
                self._build_web_headers(accept=_HTML_ACCEPT, referer=None),
                browser_cookies,
            )

    async def _cdp_cookies(self) -> httpx.Cookies | None:
        if self._cookie_provider is None:
            return None
        return await self._cookie_provider.get_cdp_cookies()

    async def _browser_cookies(self) -> httpx.Cookies | None:
        if self._cookie_provider is None:
            return None
        return await self._cookie_provider.get_cookies()

    def _should_retry_web_fetch(self, exc: http_utils.RequestError) -> bool:
        return exc.kind == "status" and exc.status in _WEB_FALLBACK_STATUSES

    def _build_web_headers(
        self,
        *,
        accept: str,
        referer: str | None,
        include_bearer_auth: bool = False,
    ) -> dict[str, str]:
        headers = {
            "Accept": accept,
            "User-Agent": _USER_AGENT,
        }
        if referer is not None:
            headers["Referer"] = referer
        if accept == _JSON_ACCEPT:
            headers["X-Requested-With"] = "XMLHttpRequest"
        if include_bearer_auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        return headers

    def _web_headers_candidates(
        self,
        *,
        accept: str,
        referer: str | None,
    ) -> tuple[dict[str, str], ...]:
        unauthenticated = self._build_web_headers(accept=accept, referer=referer)
        if not self._token:
            return (unauthenticated,)
        authenticated = self._build_web_headers(
            accept=accept,
            referer=referer,
            include_bearer_auth=True,
        )
        return (unauthenticated, authenticated)


@dataclass
class GitHubActionsTailer:
    """High-level workflow/job tail orchestration."""

    api_client: GitHubActionsClient
    live_client: GitHubActionsLiveClient
    output: TextIO = field(default_factory=lambda: sys.stdout)
    poll_interval: float = _DEFAULT_POLL_INTERVAL

    def __post_init__(self) -> None:
        """Validate construction-time tailing options."""
        if self.poll_interval <= 0:
            msg = "poll_interval must be positive"
            raise ValueError(msg)

    async def tail_workflow(
        self,
        *,
        workflow: Workflow,
        run: WorkflowRun,
        requested_job_name: str | None = None,
    ) -> None:
        """Tail one workflow run, optionally narrowing to a single job."""
        self._write_line(
            f"== workflow {workflow.name!r} run #{run.run_number} [{run.status}] =="
        )
        self._write_line(run.html_url)

        if requested_job_name is not None:
            await self._tail_named_job(run=run, requested_job_name=requested_job_name)
            return

        await self._tail_run_jobs(run=run)

    async def _tail_named_job(
        self,
        *,
        run: WorkflowRun,
        requested_job_name: str,
    ) -> None:
        matched_job_id: int | None = None
        while True:
            jobs = self.api_client.list_run_jobs(run.id)
            if matched_job_id is None:
                try:
                    matched_job = select_named_job(jobs, requested_job_name)
                except ValueError as exc:
                    if str(exc).startswith("Ambiguous job name"):
                        raise RuntimeError(str(exc)) from exc
                    if self.api_client.get_workflow_run(run.id).status == "completed":
                        msg = f"Job {requested_job_name!r} never appeared before the run completed"
                        raise RuntimeError(msg) from None
                    await self._sleep()
                    continue
                matched_job_id = matched_job.id
                await self._tail_one_job(run_id=run.id, job=matched_job)
                return

            matched_job = _job_by_id(jobs, matched_job_id)
            if matched_job is None:
                msg = f"Job id {matched_job_id} disappeared from run {run.id}"
                raise RuntimeError(msg)
            await self._tail_one_job(run_id=run.id, job=matched_job)
            return

    async def _tail_run_jobs(self, *, run: WorkflowRun) -> None:
        seen_job_ids: set[int] = set()
        while True:
            jobs = self.api_client.list_run_jobs(run.id)
            active_candidate = choose_next_live_job(
                tuple(job for job in jobs if job.id not in seen_job_ids)
            )
            if active_candidate is not None:
                await self._tail_one_job(run_id=run.id, job=active_candidate)
                seen_job_ids.add(active_candidate.id)
                continue

            skipped = [
                job
                for job in jobs
                if job.id not in seen_job_ids and job.status == "completed"
            ]
            for job in skipped:
                self._write_line(
                    "== skipped already-completed job "
                    f"{job.name!r} [{job.conclusion or job.status}] =="
                )
                seen_job_ids.add(job.id)

            refreshed_run = self.api_client.get_workflow_run(run.id)
            if refreshed_run.status == "completed":
                self._write_line(
                    "== workflow run completed "
                    f"[{refreshed_run.conclusion or refreshed_run.status}] =="
                )
                return
            await self._sleep()

    async def _tail_one_job(self, *, run_id: int, job: JobSummary) -> None:
        self._write_line(f"== job {job.name!r} [{job.status}] ==")
        self._write_line(self._require_job_url(job))
        if job.status == "completed":
            self._write_job_completion(job)
            return

        steps_url: str | None = None
        job_page: LiveJobPageInfo | None = None
        current_step_id: str | None = None
        seen_line_ids: set[str] = set()
        announced_step_ids: set[str] = set()
        flushed_step_ids: set[str] = set()
        known_steps: dict[str, LiveStepRecord] = {}
        last_change_id = 0
        announced_waiting_statuses: set[str] = set()

        while True:
            current_job = self._refresh_job(run_id=run_id, job_id=job.id)
            if current_job is None:
                msg = f"Job id {job.id} disappeared from run {run_id}"
                raise RuntimeError(msg)
            current_job_url = self._require_job_url(current_job)

            if await self._wait_for_job_start(
                current_job=current_job,
                steps_url=steps_url,
                announced_waiting_statuses=announced_waiting_statuses,
            ):
                continue

            if steps_url is None:
                job_page = await self.live_client.discover_job_page(
                    job_url=current_job_url
                )
                steps_url = job_page.steps_url
                if steps_url is None:
                    if current_job.status == "completed":
                        self._write_line(
                            "== job completed before live steps became available "
                            f"[{current_job.conclusion or current_job.status}] =="
                        )
                        return
                    await self._sleep()
                    continue

            updates = await self.live_client.fetch_steps(
                steps_url=steps_url,
                change_id=last_change_id,
                referer=current_job_url,
            )
            for step in updates:
                known_steps[step.id] = step
                last_change_id = max(last_change_id, step.change_id)

            current_step_id = await self._flush_started_steps(
                steps_url=steps_url,
                steps=known_steps,
                include_in_progress=False,
                current_step_id=current_step_id,
                seen_line_ids=seen_line_ids,
                announced_step_ids=announced_step_ids,
                flushed_step_ids=flushed_step_ids,
                job_page=job_page,
                referer=current_job_url,
            )

            active_step = _active_step(known_steps)
            if (
                active_step is not None
                and active_step.id not in flushed_step_ids
                and active_step.id != current_step_id
            ):
                self._announce_step(active_step, announced_step_ids=announced_step_ids)
                current_step_id = active_step.id

            if current_step_id is not None and current_step_id not in flushed_step_ids:
                await self._emit_backscroll(
                    steps_url=steps_url,
                    step_id=current_step_id,
                    seen_line_ids=seen_line_ids,
                    backscroll_url=(
                        None
                        if job_page is None
                        else job_page.backscroll_url_for(current_step_id)
                    ),
                    referer=current_job_url,
                )

            if current_job.status == "completed":
                await self._flush_started_steps(
                    steps_url=steps_url,
                    steps=known_steps,
                    include_in_progress=True,
                    current_step_id=current_step_id,
                    seen_line_ids=seen_line_ids,
                    announced_step_ids=announced_step_ids,
                    flushed_step_ids=flushed_step_ids,
                    job_page=job_page,
                    referer=current_job_url,
                )
                self._write_job_completion(current_job)
                return

            await self._sleep()

    async def _wait_for_job_start(
        self,
        *,
        current_job: JobSummary,
        steps_url: str | None,
        announced_waiting_statuses: set[str],
    ) -> bool:
        if steps_url is not None or current_job.status not in _PRELIVE_JOB_STATUSES:
            return False
        if current_job.status not in announced_waiting_statuses:
            self._write_line(f"== waiting for job to start [{current_job.status}] ==")
            announced_waiting_statuses.add(current_job.status)
        await self._sleep()
        return True

    def _announce_step(
        self,
        step: LiveStepRecord,
        *,
        announced_step_ids: set[str],
    ) -> None:
        if step.id in announced_step_ids:
            return
        self._write_line(f"-- step {step.number}: {step.name} --")
        announced_step_ids.add(step.id)

    async def _flush_started_steps(
        self,
        *,
        steps_url: str,
        steps: dict[str, LiveStepRecord],
        include_in_progress: bool,
        current_step_id: str | None,
        seen_line_ids: set[str],
        announced_step_ids: set[str],
        flushed_step_ids: set[str],
        job_page: LiveJobPageInfo | None,
        referer: str | None,
    ) -> str | None:
        for step in _started_steps_in_order(steps):
            if step.id in flushed_step_ids:
                continue
            if not include_in_progress and step.status == "in_progress":
                continue
            await self._flush_step(
                steps_url=steps_url,
                step=step,
                seen_line_ids=seen_line_ids,
                announced_step_ids=announced_step_ids,
                flushed_step_ids=flushed_step_ids,
                job_page=job_page,
                referer=referer,
            )
            if current_step_id == step.id:
                current_step_id = None
        return current_step_id

    async def _flush_step(
        self,
        *,
        steps_url: str,
        step: LiveStepRecord,
        seen_line_ids: set[str],
        announced_step_ids: set[str],
        flushed_step_ids: set[str],
        job_page: LiveJobPageInfo | None,
        referer: str | None,
    ) -> None:
        self._announce_step(step, announced_step_ids=announced_step_ids)
        await self._emit_backscroll(
            steps_url=steps_url,
            step_id=step.id,
            seen_line_ids=seen_line_ids,
            backscroll_url=self._step_backscroll_url(
                job_page=job_page, step_id=step.id
            ),
            referer=referer,
        )
        flushed_step_ids.add(step.id)

    def _step_backscroll_url(
        self,
        *,
        job_page: LiveJobPageInfo | None,
        step_id: str,
    ) -> str | None:
        if job_page is None:
            return None
        return job_page.backscroll_url_for(step_id)

    async def _emit_backscroll(
        self,
        *,
        steps_url: str,
        step_id: str,
        seen_line_ids: set[str],
        backscroll_url: str | None,
        referer: str | None,
    ) -> None:
        for line in await self.live_client.fetch_backscroll(
            steps_url=steps_url,
            step_id=step_id,
            backscroll_url=backscroll_url,
            referer=referer,
        ):
            if line.id in seen_line_ids:
                continue
            seen_line_ids.add(line.id)
            self._write_line(line.line)

    def _refresh_job(self, *, run_id: int, job_id: int) -> JobSummary | None:
        return _job_by_id(self.api_client.list_run_jobs(run_id), job_id)

    async def _sleep(self) -> None:
        await asyncio.sleep(self.poll_interval)

    def _require_job_url(self, job: JobSummary) -> str:
        return job.html_url

    def _write_job_completion(self, job: JobSummary) -> None:
        self._write_line(f"== job completed [{job.conclusion or job.status}] ==")

    def _write_line(self, message: str) -> None:
        self.output.write(f"{message}\n")
        self.output.flush()


class _CheckStepsHTMLParser(HTMLParser):
    """Extract hidden live-log transport metadata from one job page."""

    def __init__(self) -> None:
        super().__init__()
        self.steps_url: str | None = None
        self.streaming_url: str | None = None
        self.backscroll_urls: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        mapping = {key: value for key, value in attrs if value is not None}
        if tag == "check-steps":
            for attribute in _JOB_STEPS_URL_ATTRIBUTES:
                if attribute in mapping:
                    self.steps_url = mapping[attribute]
                    break
            for attribute in _STREAMING_URL_ATTRIBUTES:
                if attribute in mapping:
                    self.streaming_url = mapping[attribute]
                    break
            return
        if tag != "check-step":
            return
        step_id = mapping.get(_STEP_ID_ATTRIBUTE)
        backscroll_url = mapping.get(_STEP_BACKSCROLL_URL_ATTRIBUTE)
        if step_id is None or backscroll_url is None:
            return
        self.backscroll_urls[step_id] = backscroll_url


def _parse_live_job_page_from_html(html: str, *, job_url: str) -> LiveJobPageInfo:
    parser = _CheckStepsHTMLParser()
    parser.feed(html)
    info = LiveJobPageInfo()
    if parser.steps_url is not None:
        info.steps_url = _parse_steps_url_candidate(
            job_url=job_url,
            candidate=parser.steps_url,
        )
    else:
        for key in _JOB_STEPS_URL_JSON_KEYS:
            candidate = _extract_json_string_field(html, key=key)
            if candidate is None:
                continue
            info.steps_url = _parse_steps_url_candidate(
                job_url=job_url,
                candidate=candidate,
            )
            break
    if parser.streaming_url is not None:
        info.streaming_url = _parse_same_origin_url_candidate(
            job_url=job_url,
            candidate=parser.streaming_url,
            context="streaming URL",
        )
    for step_id, candidate in parser.backscroll_urls.items():
        info.backscroll_urls[step_id] = _parse_same_origin_url_candidate(
            job_url=job_url,
            candidate=candidate,
            context="step backscroll URL",
        )
    return info


def _parse_steps_url_from_html(html: str, *, job_url: str) -> str | None:
    return _parse_live_job_page_from_html(html, job_url=job_url).steps_url


def _job_page_has_live_metadata(info: LiveJobPageInfo) -> bool:
    return info.steps_url is not None or bool(info.backscroll_urls)


def _prefer_richer_job_page_info(
    primary: LiveJobPageInfo,
    secondary: LiveJobPageInfo,
) -> LiveJobPageInfo:
    if _job_page_has_live_metadata(secondary) and not _job_page_has_live_metadata(
        primary
    ):
        return secondary
    if secondary.steps_url is not None and primary.steps_url is None:
        primary.steps_url = secondary.steps_url
    if secondary.streaming_url is not None and primary.streaming_url is None:
        primary.streaming_url = secondary.streaming_url
    if secondary.backscroll_urls and not primary.backscroll_urls:
        primary.backscroll_urls = dict(secondary.backscroll_urls)
    return primary


def _payload_is_json(payload: bytes, *, headers: dict[str, str]) -> bool:
    content_type = headers.get("content-type", "").casefold()
    stripped = payload.lstrip()
    if "json" not in content_type and not stripped.startswith((b"{", b"[")):
        return False
    try:
        json.loads(payload)
    except ValueError:
        return False
    return True


def _non_json_payload_error(*, url: str, headers: dict[str, str]) -> RuntimeError:
    content_type = headers.get("content-type", "<missing>")
    msg = f"Expected GitHub Actions JSON response from {url}, got content-type {content_type!r}"
    return RuntimeError(msg)


def _extract_json_string_field(document: str, *, key: str) -> str | None:
    marker = f'"{key}":'
    start = document.find(marker)
    if start < 0:
        return None
    decoder = json.JSONDecoder()
    raw_value = document[start + len(marker) :].lstrip()
    try:
        value, _end = decoder.raw_decode(raw_value)
    except json.JSONDecodeError:
        return None
    if isinstance(value, str):
        return value
    return None


def _parse_steps_url_candidate(*, job_url: str, candidate: str) -> str:
    resolved = _parse_same_origin_url_candidate(
        job_url=job_url,
        candidate=candidate,
        context="steps URL",
    ).removesuffix("/")
    parsed = urlsplit(resolved)
    path_parts = [part for part in parsed.path.split("/") if part]
    if len(path_parts) < _MIN_STEPS_PATH_PARTS:
        msg = f"Unexpected GitHub Actions steps URL path: {resolved!r}"
        raise ValueError(msg)
    actual_tail = path_parts[-6:]
    if (
        actual_tail[0] != "actions"
        or actual_tail[1] != "runs"
        or not actual_tail[2].isdigit()
        or actual_tail[3] != "jobs"
        or not actual_tail[4].isdigit()
        or actual_tail[5] != "steps"
    ):
        msg = f"Unexpected GitHub Actions steps URL path: {resolved!r}"
        raise ValueError(msg)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".removesuffix("/")


def _parse_same_origin_url_candidate(
    *, job_url: str, candidate: str, context: str
) -> str:
    resolved = urljoin(job_url, candidate)
    parsed = urlsplit(resolved)
    job_origin = urlsplit(job_url)
    if (parsed.scheme, parsed.netloc) != (job_origin.scheme, job_origin.netloc):
        msg = f"Unexpected GitHub Actions {context} origin: {resolved!r}"
        raise ValueError(msg)
    normalized = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
    if parsed.query:
        normalized = f"{normalized}?{parsed.query}"
    return normalized


def _parse_live_step(value: object) -> LiveStepRecord:
    payload = json_utils.as_object_dict(value, context="live step")
    return LiveStepRecord(
        id=json_utils.get_required_str(payload, "id", context="live step"),
        name=json_utils.get_required_str(payload, "name", context="live step"),
        status=json_utils.get_required_str(payload, "status", context="live step"),
        conclusion=_optional_str(payload, "conclusion"),
        number=_required_int(payload, "number", context="live step"),
        change_id=_required_int(payload, "change_id", context="live step"),
        started_at=_optional_str(payload, "started_at"),
        completed_at=_optional_str(payload, "completed_at"),
    )


def _parse_live_line(value: object) -> LiveLogLine:
    payload = json_utils.as_object_dict(value, context="live log line")
    return LiveLogLine(
        id=json_utils.get_required_str(payload, "id", context="live log line"),
        line=json_utils.get_required_str(payload, "line", context="live log line"),
    )


def _required_int(mapping: dict[str, object], key: str, *, context: str) -> int:
    value = mapping.get(key)
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    msg = f"Expected integer field {key!r} in {context}"
    raise TypeError(msg)


def _optional_str(mapping: dict[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        return value
    msg = f"Expected optional string field {key!r}"
    raise TypeError(msg)


def _job_by_id(jobs: tuple[JobSummary, ...], job_id: int) -> JobSummary | None:
    for job in jobs:
        if job.id == job_id:
            return job
    return None


def _started_steps_in_order(
    steps: dict[str, LiveStepRecord],
) -> list[LiveStepRecord]:
    started_steps = [step for step in steps.values() if step.started_at is not None]
    started_steps.sort(key=lambda step: step.number)
    return started_steps


def _active_step(steps: dict[str, LiveStepRecord]) -> LiveStepRecord | None:
    for step in _started_steps_in_order(steps):
        if step.status == "in_progress":
            return step
    return None


__all__ = [
    "GitHubActionsLiveClient",
    "GitHubActionsTailer",
    "LiveJobPageInfo",
    "LiveLogLine",
    "LiveStepRecord",
    "_parse_live_job_page_from_html",
    "_parse_steps_url_from_html",
]
