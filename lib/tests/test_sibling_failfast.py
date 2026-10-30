"""Sibling-stop policy, the Actions job poll, and the hosted-job hook."""

import contextlib
import json
import os
import signal
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
import yaml

from lib.update import sibling_failfast
from lib.update.ci import jobs
from lib.update.sibling_failfast import (
    SiblingPollError,
    SiblingStop,
    WorkflowJob,
    failfast_group,
    fetch_workflow_jobs,
    parse_workflow_jobs,
    sibling_stop,
    stop_message,
)

ROOT = Path(__file__).resolve().parents[2]
_JOBS_API_VALUE = "actions-read-token"


def _job(
    name: str,
    *,
    status: str = "completed",
    conclusion: str | None = "failure",
    completed_at: str = "2026-10-30T00:00:00Z",
    failed_step: str = "",
) -> WorkflowJob:
    return WorkflowJob(
        name=name,
        status=status,
        conclusion=conclusion,
        completed_at=completed_at,
        failed_step=failed_step,
    )


@pytest.mark.parametrize(
    ("stage", "system", "scope", "canary", "expected"),
    [
        ("prepare", "aarch64-darwin", "all", False, "prepare"),
        ("prepare", "aarch64-linux", "all", False, "prepare"),
        ("prepare", "x86_64-linux", "all", False, "prepare"),
        ("prepare", "x86_64-darwin", "all", False, ""),
        ("prepare-pins", "x86_64-linux", "all", False, ""),
        ("merge-candidates", "x86_64-linux", "all", False, ""),
        ("plan-shards", "x86_64-linux", "all", False, ""),
        ("cache-root-deps", "aarch64-linux", "all", False, "linux-deps"),
        ("cache-root-deps", "x86_64-linux", "all", False, "linux-deps"),
        ("cache-root-deps", "aarch64-darwin", "all", False, ""),
        ("validate", "aarch64-linux", "all", False, "linux-validate"),
        ("validate", "x86_64-linux", "", False, "linux-validate"),
        ("validate", "aarch64-darwin", "all", False, ""),
        ("validate", "aarch64-darwin", "rust-warmup", False, "rust"),
        ("validate", "aarch64-darwin", "rust-warmup", True, "canary-rust"),
        ("validate", "aarch64-darwin", "cascade-warmup", False, "cascade"),
        ("validate", "aarch64-darwin", "closure-shard", False, "roots"),
        ("validate", "aarch64-darwin", "packages", False, ""),
        ("validate", "aarch64-darwin", "closures", False, ""),
        ("validate", "aarch64-darwin", "fod-canary", False, ""),
        ("validate", "aarch64-darwin", "leaf-prebuild", False, ""),
        ("validate", "aarch64-darwin", "cascade-canary", False, ""),
        ("validate", "aarch64-darwin", "zed-warmup", True, ""),
    ],
)
def test_failfast_group_follows_the_hosted_stage(
    stage: str, system: str, scope: str, canary: bool, expected: str
) -> None:
    assert (
        failfast_group(stage=stage, system=system, scope=scope, canary=canary)
        == expected
    )


def test_sibling_stop_ignores_an_empty_group_and_rejects_an_unknown_one() -> None:
    failure = _job("prepare-arm / native")
    assert sibling_stop("", [failure]) is None
    with pytest.raises(ValueError, match="unknown sibling-stop group"):
        sibling_stop("packages", [failure])


@pytest.mark.parametrize(
    ("status", "conclusion"),
    [
        ("in_progress", None),
        ("in_progress", "failure"),
        ("completed", "success"),
        ("completed", "cancelled"),
        ("completed", "skipped"),
        ("completed", None),
    ],
)
def test_sibling_stop_ignores_incomplete_or_non_failure_conclusions(
    status: str, conclusion: str | None
) -> None:
    assert (
        sibling_stop(
            "prepare",
            [_job("prepare-arm / native", status=status, conclusion=conclusion)],
        )
        is None
    )


@pytest.mark.parametrize("conclusion", ["failure", "timed_out", "startup_failure"])
def test_sibling_stop_accepts_completed_real_failures(conclusion: str) -> None:
    stop = sibling_stop(
        "prepare",
        [
            _job(
                "prepare-x86 / native",
                conclusion=conclusion,
                failed_step="Prepare or validate candidate",
            )
        ],
    )
    assert stop == SiblingStop(
        group="prepare",
        job="prepare-x86 / native",
        conclusion=conclusion,
        failed_step="Prepare or validate candidate",
    )


def test_sibling_stop_does_not_treat_a_longer_job_id_as_the_same_job() -> None:
    """prepare-darwin-retry must not match the prepare-darwin watch name."""
    assert sibling_stop("prepare", [_job("prepare-darwin-retry / native")]) is not None
    assert (
        sibling_stop(
            "prepare",
            [_job("prepare-darwin-retry / native", conclusion="success")],
        )
        is None
    )
    only_retry_success = [
        _job("prepare-darwin-retry / native", conclusion="success"),
        _job("prepare-darwin / native"),
    ]
    assert sibling_stop("prepare", only_retry_success) == SiblingStop(
        group="prepare",
        job="prepare-darwin / native",
        conclusion="failure",
        failed_step="",
    )


def test_matrix_cell_failure_stops_that_matrix_only() -> None:
    rust = _job(
        "validate-darwin-warm-rust (0) / native",
        completed_at="2026-10-23T12:00:00Z",
        failed_step="Prepare or validate candidate",
    )
    later = _job(
        "validate-darwin-warm-rust (2) / native",
        completed_at="2026-10-23T18:00:00Z",
    )
    assert sibling_stop("rust", [later, rust]) == SiblingStop(
        group="rust",
        job=rust.name,
        conclusion="failure",
        failed_step="Prepare or validate candidate",
    )
    assert sibling_stop("cascade", [rust]) is None
    assert sibling_stop("canary-rust", [rust]) is None
    assert sibling_stop("rust", [_job("canary-warm-rust (0) / native")]) is None
    assert sibling_stop("cascade", [_job("canary-warm-cascade / native")]) is None
    assert sibling_stop("rust", [_job("prebuild-darwin-leaves / native")]) is None


def test_plan_failure_stops_linux_validate_and_validate_does_not_stop_plan() -> None:
    plan = _job(
        "plan-darwin-closures / native", failed_step="Prepare or validate candidate"
    )
    arm = _job("validate-arm / native")
    assert sibling_stop("linux-validate", [arm]) is None
    assert sibling_stop("linux-validate", [plan, arm]) == SiblingStop(
        group="linux-validate",
        job=plan.name,
        conclusion="failure",
        failed_step="Prepare or validate candidate",
    )


def test_tied_completion_time_picks_the_name_order() -> None:
    later_name = _job("prepare-x86 / native", completed_at="2026-10-30T01:00:00Z")
    earlier_name = _job("prepare-arm / native", completed_at="2026-10-30T01:00:00Z")
    assert sibling_stop("prepare", [later_name, earlier_name]) == SiblingStop(
        group="prepare",
        job="prepare-arm / native",
        conclusion="failure",
        failed_step="",
    )


def test_stop_message_names_the_sibling_and_its_failed_step() -> None:
    detailed = SiblingStop(
        group="prepare",
        job="prepare-x86 / native",
        conclusion="failure",
        failed_step="Prepare or validate candidate",
    )
    text = stop_message(detailed)
    assert text.startswith("NIXCFG_SIBLING_STOP ")
    assert "sibling=prepare-x86 / native" in text
    assert "step=Prepare or validate candidate" in text
    assert "A green run still builds every closure." in text
    plain = stop_message(
        SiblingStop(
            group="rust",
            job="validate-darwin-warm-rust (0) / native",
            conclusion="timed_out",
            failed_step="",
        )
    )
    assert " step=" not in plain
    assert "conclusion=timed_out" in plain


def test_parse_workflow_jobs_skips_malformed_rows_and_reads_the_failed_step() -> None:
    payload = {
        "jobs": [
            "not-a-job",
            {"status": "completed"},
            {"name": "skipped-status", "status": 1},
            {"name": "prepare-arm / native"},
            {
                "name": "prepare-x86 / native",
                "status": "completed",
                "conclusion": None,
                "completed_at": None,
                "steps": "not-steps",
            },
            {
                "name": "validate-arm / native",
                "status": "completed",
                "conclusion": "failure",
                "completed_at": "2026-10-27T12:00:00Z",
                "steps": [
                    "skip",
                    {"conclusion": "success", "name": "Run runtime"},
                    {"conclusion": "failure"},
                    {"conclusion": "failure", "name": "Prepare or validate candidate"},
                ],
            },
            {
                "name": "validate-x86 / native",
                "status": "completed",
                "conclusion": "failure",
                "steps": [
                    {"conclusion": "failure", "name": ""},
                    {"conclusion": "cancelled", "name": "Flush Cachix"},
                ],
            },
        ]
    }
    parsed = parse_workflow_jobs(payload)
    assert [job.name for job in parsed] == [
        "prepare-x86 / native",
        "validate-arm / native",
        "validate-x86 / native",
    ]
    assert parsed[0].conclusion is None
    assert parsed[0].completed_at == ""
    assert parsed[0].failed_step == ""
    assert parsed[1].failed_step == "Prepare or validate candidate"
    assert parsed[2].failed_step == ""


@pytest.mark.parametrize(
    "payload",
    [None, [], {"runs": []}, {"jobs": {}}],
)
def test_parse_workflow_jobs_rejects_a_payload_without_a_job_list(
    payload: object,
) -> None:
    with pytest.raises(SiblingPollError):
        parse_workflow_jobs(payload)
    assert sibling_failfast._raw_job_count(payload) == 0


class _Response:
    def __init__(self, payload: object, *, broken: bool = False) -> None:
        self.payload = payload
        self.broken = broken

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self, length: int = -1) -> bytes:
        del length
        if self.broken:
            return b"not-json"
        return json.dumps(self.payload).encode()


def test_fetch_workflow_jobs_pages_the_latest_attempt_without_leaking_the_token() -> (
    None
):
    seen: list[tuple[str, dict[str, str]]] = []

    def opener(request: object, timeout: float) -> _Response:
        assert timeout == sibling_failfast._HTTP_TIMEOUT_SECONDS
        url = request.full_url  # type: ignore[attr-defined]
        headers = {key.lower(): value for key, value in request.header_items()}  # type: ignore[attr-defined]
        seen.append((url, headers))
        page = int(parse_qs(urlsplit(url).query)["page"][0])
        if page == 1:
            jobs: list[object] = [
                {"name": f"job-{index}", "status": "completed", "conclusion": "success"}
                for index in range(100)
            ]
            jobs[0] = "malformed"
            return _Response({"jobs": jobs})
        return _Response({
            "jobs": [
                {
                    "name": "prepare-arm / native",
                    "status": "completed",
                    "conclusion": "failure",
                }
            ]
        })

    found = fetch_workflow_jobs(
        repository="gkze/nixcfg",
        run_id="40577174548",
        token=_JOBS_API_VALUE,
        opener=opener,
    )
    assert [job.name for job in found if job.conclusion == "failure"] == [
        "prepare-arm / native"
    ]
    assert len(seen) == 2
    for url, headers in seen:
        assert _JOBS_API_VALUE not in url
        assert headers["authorization"] == f"Bearer {_JOBS_API_VALUE}"
        query = parse_qs(urlsplit(url).query)
        assert query["filter"] == ["latest"]
        assert query["per_page"] == ["100"]


def test_fetch_stops_after_the_page_cap() -> None:
    calls = 0

    def opener(request: object, timeout: float) -> _Response:
        nonlocal calls
        calls += 1
        del request, timeout
        jobs = [
            {"name": f"job-{calls}-{index}", "status": "in_progress"}
            for index in range(100)
        ]
        return _Response({"jobs": jobs})

    found = fetch_workflow_jobs(
        repository="gkze/nixcfg",
        run_id="1",
        token=_JOBS_API_VALUE,
        opener=opener,
    )
    assert calls == sibling_failfast._MAX_PAGES
    assert len(found) == sibling_failfast._MAX_PAGES * 100


def test_fetch_uses_urlopen_when_no_opener_is_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def urlopen(request: object, timeout: float) -> _Response:
        del request, timeout
        return _Response({"jobs": []})

    monkeypatch.setattr(sibling_failfast.urllib.request, "urlopen", urlopen)
    assert (
        fetch_workflow_jobs(repository="gkze/nixcfg", run_id="9", token=_JOBS_API_VALUE)
        == ()
    )


@pytest.mark.parametrize(
    ("repository", "run_id", "token", "message"),
    [
        ("not a repo", "1", _JOBS_API_VALUE, "repository"),
        ("gkze/nixcfg", "run", _JOBS_API_VALUE, "run_id"),
        ("gkze/nixcfg", "1", "", "token"),
        ("gkze/nixcfg", "1", "has space", "token"),
    ],
)
def test_fetch_rejects_coordinates_before_opening_a_url(
    repository: str, run_id: str, token: str, message: str
) -> None:
    def opener(request: object, timeout: float) -> _Response:
        del request, timeout
        raise AssertionError("coordinates must be checked before the request")

    with pytest.raises(SiblingPollError, match=message):
        fetch_workflow_jobs(
            repository=repository, run_id=run_id, token=token, opener=opener
        )


def test_fetch_reports_http_status_without_the_body() -> None:
    def opener(request: object, timeout: float) -> _Response:
        del request, timeout
        raise sibling_failfast.urllib.error.HTTPError(
            "https://api.github.com/jobs",
            403,
            "Forbidden",
            hdrs=None,  # type: ignore[arg-type]
            fp=None,
        )

    with pytest.raises(SiblingPollError, match="^HTTPError:403$") as caught:
        fetch_workflow_jobs(
            repository="gkze/nixcfg", run_id="1", token=_JOBS_API_VALUE, opener=opener
        )
    assert _JOBS_API_VALUE not in str(caught.value)


def test_fetch_reports_a_transport_or_json_error_by_type() -> None:
    def broken(request: object, timeout: float) -> _Response:
        del request, timeout
        return _Response({}, broken=True)

    with pytest.raises(SiblingPollError, match="^JSONDecodeError$"):
        fetch_workflow_jobs(
            repository="gkze/nixcfg", run_id="1", token=_JOBS_API_VALUE, opener=broken
        )

    def offline(request: object, timeout: float) -> _Response:
        del request, timeout
        raise sibling_failfast.urllib.error.URLError("dns")

    with pytest.raises(SiblingPollError, match="^URLError$") as caught:
        fetch_workflow_jobs(
            repository="gkze/nixcfg", run_id="1", token=_JOBS_API_VALUE, opener=offline
        )
    assert "dns" not in str(caught.value)


def _watch_env(monkeypatch: pytest.MonkeyPatch, **extra: str) -> None:
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("NIXCFG_ACTIONS_TOKEN", _JOBS_API_VALUE)
    monkeypatch.setenv("GITHUB_REPOSITORY", "gkze/nixcfg")
    monkeypatch.setenv("GITHUB_RUN_ID", "40577174548")
    monkeypatch.setenv("NIXCFG_CI_STAGE", "prepare")
    monkeypatch.setenv("NIXCFG_SYSTEM", "aarch64-darwin")
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "all")
    monkeypatch.delenv("NIXCFG_CANARY", raising=False)
    for key, value in extra.items():
        monkeypatch.setenv(key, value)


@pytest.mark.parametrize(
    "missing",
    ["GITHUB_ACTIONS", "NIXCFG_ACTIONS_TOKEN", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"],
)
def test_sibling_watch_stays_off_without_actions_coordinates(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    _watch_env(monkeypatch)
    if missing == "GITHUB_ACTIONS":
        monkeypatch.setenv(missing, "false")
    else:
        monkeypatch.setenv(missing, "")
    assert jobs._sibling_watch_configured("prepare") is False


def test_sibling_watch_follows_the_stage_group(monkeypatch: pytest.MonkeyPatch) -> None:
    _watch_env(monkeypatch)
    assert jobs._sibling_watch_configured("prepare") is True
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "zed-warmup")
    assert jobs._sibling_watch_configured("validate") is False
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "rust-warmup")
    monkeypatch.setenv("NIXCFG_CANARY", "true")
    assert jobs._sibling_watch_configured("validate") is True


def test_poll_returns_the_sibling_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    _watch_env(monkeypatch)

    def fetch(**kwargs: object) -> tuple[WorkflowJob, ...]:
        assert kwargs["token"] == _JOBS_API_VALUE
        assert kwargs["repository"] == "gkze/nixcfg"
        return (
            _job(
                "prepare-x86 / native",
                failed_step="Prepare or validate candidate",
            ),
        )

    monkeypatch.setattr(jobs.sibling_failfast, "fetch_workflow_jobs", fetch)
    stop = jobs._poll_sibling_stop("prepare", StringIO())
    assert stop is not None
    assert stop.job == "prepare-x86 / native"


def test_poll_failure_is_logged_without_the_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _watch_env(monkeypatch)

    def fetch(**kwargs: object) -> tuple[WorkflowJob, ...]:
        del kwargs
        raise SiblingPollError("HTTPError:403")

    monkeypatch.setattr(jobs.sibling_failfast, "fetch_workflow_jobs", fetch)
    log = StringIO()
    assert jobs._poll_sibling_stop("prepare", log) is None
    assert "Sibling failure poll skipped: HTTPError:403" in log.getvalue()
    assert _JOBS_API_VALUE not in log.getvalue()


def test_abort_waits_for_the_poll_interval(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(jobs, "_SIBLING_POLL_SECONDS", 60)

    def fetch(**kwargs: object) -> tuple[WorkflowJob, ...]:
        del kwargs
        raise AssertionError("poll is not due")

    monkeypatch.setattr(jobs.sibling_failfast, "fetch_workflow_jobs", fetch)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stderr=subprocess.DEVNULL,
    )
    try:
        assert (
            jobs._abort_for_sibling_failure(
                "prepare",
                process,
                StringIO(),
                tmp_path,
                [time.monotonic()],
                watch=True,
            )
            is False
        )
        assert process.poll() is None
    finally:
        process.kill()
        process.wait(timeout=5)


def test_wait_stops_a_running_updater_when_a_sibling_has_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch_env(monkeypatch)
    monkeypatch.setattr(jobs, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(jobs, "_SIBLING_POLL_SECONDS", 0)

    def fetch(**kwargs: object) -> tuple[WorkflowJob, ...]:
        del kwargs
        return (
            _job(
                "prepare-x86 / native",
                failed_step="Prepare or validate candidate",
            ),
        )

    monkeypatch.setattr(jobs.sibling_failfast, "fetch_workflow_jobs", fetch)
    command = [sys.executable, "-c", "import time; time.sleep(30)"]
    with subprocess.Popen(  # noqa: S603 -- argv is the test interpreter, not external input
        command, stderr=subprocess.PIPE, text=True, start_new_session=True
    ) as process:
        log = StringIO()
        assert (
            jobs._wait_for_diagnostics(process, log, "prepare", tmp_path, command) == 1
        )
        assert process.poll() is not None
    text = log.getvalue()
    marker = json.loads((tmp_path / "sibling-stop.json").read_text())
    assert marker["sibling"] == "prepare-x86 / native"
    assert marker["failedStep"] == "Prepare or validate candidate"
    assert "::error title=Sibling real failure::" in text
    assert "NIXCFG_SIBLING_STOP " in text
    assert _JOBS_API_VALUE not in text


def test_wait_stops_when_the_sibling_failure_arrives_during_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A chatty build still polls; it does not wait for a quiet heartbeat."""
    _watch_env(monkeypatch)
    monkeypatch.setattr(jobs, "_HEARTBEAT_INTERVAL_SECONDS", 5)
    monkeypatch.setattr(jobs, "_SIBLING_POLL_SECONDS", 0)

    def fetch(**kwargs: object) -> tuple[WorkflowJob, ...]:
        del kwargs
        return (_job("validate-darwin-warm-rust (0) / native"),)

    monkeypatch.setattr(jobs.sibling_failfast, "fetch_workflow_jobs", fetch)
    monkeypatch.setenv("NIXCFG_VALIDATE_SCOPE", "rust-warmup")
    command = [
        sys.executable,
        "-c",
        "import sys, time; print('building', file=sys.stderr, flush=True); time.sleep(30)",
    ]
    with subprocess.Popen(  # noqa: S603 -- argv is the test interpreter, not external input
        command, stderr=subprocess.PIPE, text=True, start_new_session=True
    ) as process:
        log = StringIO()
        assert (
            jobs._wait_for_diagnostics(process, log, "validate", tmp_path, command) == 1
        )
    assert "building" in log.getvalue()
    assert json.loads((tmp_path / "sibling-stop.json").read_text())["group"] == "rust"


def test_abort_continues_when_the_poll_finds_no_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch_env(monkeypatch)
    monkeypatch.setattr(jobs, "_SIBLING_POLL_SECONDS", 0)

    def fetch(**kwargs: object) -> tuple[WorkflowJob, ...]:
        del kwargs
        return (_job("prepare-arm / native", conclusion="success"),)

    monkeypatch.setattr(jobs.sibling_failfast, "fetch_workflow_jobs", fetch)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stderr=subprocess.DEVNULL,
    )
    try:
        assert (
            jobs._abort_for_sibling_failure(
                "prepare",
                process,
                StringIO(),
                tmp_path,
                [0.0],
                watch=True,
            )
            is False
        )
        assert process.poll() is None
        assert not (tmp_path / "sibling-stop.json").exists()
    finally:
        process.kill()
        process.wait(timeout=5)


def test_abort_kills_the_updater_when_the_stop_record_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A marker write failure must not leave the updater running."""
    _watch_env(monkeypatch)
    monkeypatch.setattr(jobs, "_SIBLING_POLL_SECONDS", 0)

    def fetch(**kwargs: object) -> tuple[WorkflowJob, ...]:
        del kwargs
        return (_job("prepare-x86 / native"),)

    def fail_record(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("disk")

    monkeypatch.setattr(jobs.sibling_failfast, "fetch_workflow_jobs", fetch)
    monkeypatch.setattr(jobs, "_record_sibling_stop", fail_record)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stderr=subprocess.DEVNULL,
    )
    try:
        with pytest.raises(OSError, match="disk"):
            jobs._abort_for_sibling_failure(
                "prepare",
                process,
                StringIO(),
                tmp_path,
                [0.0],
                watch=True,
            )
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_abort_ignores_a_stage_that_is_not_watching(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fetch(**kwargs: object) -> tuple[WorkflowJob, ...]:
        del kwargs
        raise AssertionError("an unwatched stage must not list jobs")

    monkeypatch.setattr(jobs.sibling_failfast, "fetch_workflow_jobs", fetch)
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stderr=subprocess.DEVNULL,
    )
    try:
        assert (
            jobs._abort_for_sibling_failure(
                "plan-shards",
                process,
                StringIO(),
                tmp_path,
                [0.0],
                watch=False,
            )
            is False
        )
    finally:
        process.kill()
        process.wait(timeout=5)


def test_wait_keeps_a_finished_updater_when_a_sibling_failed_too_late(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _watch_env(monkeypatch)
    monkeypatch.setattr(jobs, "_HEARTBEAT_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(jobs, "_SIBLING_POLL_SECONDS", 0)

    def fetch(**kwargs: object) -> tuple[WorkflowJob, ...]:
        del kwargs
        raise AssertionError("a finished updater is not a sibling-stop target")

    monkeypatch.setattr(jobs.sibling_failfast, "fetch_workflow_jobs", fetch)
    command = [sys.executable, "-c", "import sys; sys.exit(0)"]
    with subprocess.Popen(  # noqa: S603 -- argv is the test interpreter, not external input
        command, stderr=subprocess.PIPE, text=True
    ) as process:
        assert process.wait(timeout=5) == 0
        log = StringIO()
        assert (
            jobs._wait_for_diagnostics(process, log, "prepare", tmp_path, command) == 0
        )
    assert not (tmp_path / "sibling-stop.json").exists()


def test_unconfigured_wait_does_not_poll(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("NIXCFG_ACTIONS_TOKEN", raising=False)
    monkeypatch.setattr(jobs, "_HEARTBEAT_INTERVAL_SECONDS", 0.01)

    def fetch(**kwargs: object) -> tuple[WorkflowJob, ...]:
        del kwargs
        raise AssertionError("unconfigured stages must not list jobs")

    monkeypatch.setattr(jobs.sibling_failfast, "fetch_workflow_jobs", fetch)
    command = [sys.executable, "-c", "import sys; sys.exit(0)"]
    with subprocess.Popen(  # noqa: S603 -- argv is the test interpreter, not external input
        command, stderr=subprocess.PIPE, text=True
    ) as process:
        assert (
            jobs._wait_for_diagnostics(
                process, StringIO(), "prepare", tmp_path, command
            )
            == 0
        )


def test_terminate_updater_kills_session_grandchildren(tmp_path: Path) -> None:
    pidfile = tmp_path / "grandchild"
    script = (
        "import pathlib, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n"
        "time.sleep(120)\n"
    )
    process = subprocess.Popen(  # noqa: S603 -- argv is the test interpreter, not external input
        [sys.executable, "-c", script],
        start_new_session=True,
        stderr=subprocess.DEVNULL,
    )
    assert jobs._process_is_group_leader(process.pid) is True
    deadline = time.monotonic() + 5
    while not pidfile.exists():
        if process.poll() is not None or time.monotonic() > deadline:
            process.kill()
            raise AssertionError("grandchild pid was not written")
        time.sleep(0.01)
    grandchild = int(pidfile.read_text())
    try:
        jobs._terminate_updater(process)
        with pytest.raises(ProcessLookupError):
            os.kill(grandchild, 0)
    finally:
        for pid in (grandchild, process.pid):
            with contextlib.suppress(ProcessLookupError):
                os.kill(pid, signal.SIGKILL)
        process.wait(timeout=5)


def test_terminate_updater_signals_a_non_leader_without_killing_the_caller() -> None:
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stderr=subprocess.DEVNULL,
    )
    caller = os.getpid()
    try:
        assert jobs._process_is_group_leader(process.pid) is False
        jobs._terminate_updater(process)
        assert process.wait(timeout=5) is not None
        assert os.kill(caller, 0) is None
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)


def test_signal_updater_falls_back_when_killpg_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Process:
        pid = 4321
        returncode: int | None = None
        signals: list[int]
        raise_on_signal = False

        def __init__(self) -> None:
            self.signals = []

        def poll(self) -> int | None:
            return self.returncode

        def send_signal(self, sig: int) -> None:
            if self.raise_on_signal:
                raise OSError(sig)
            self.signals.append(sig)

    process = _Process()

    def getpgid(pid: int) -> int:
        return pid

    def killpg(pid: int, sig: int) -> None:
        del pid, sig
        raise OSError("busy")

    monkeypatch.setattr(jobs.os, "getpgid", getpgid)
    monkeypatch.setattr(jobs.os, "killpg", killpg)

    jobs._signal_updater(process, signal.SIGTERM)  # type: ignore[arg-type]
    assert process.signals == [signal.SIGTERM]

    process.signals.clear()

    def missing_group(pid: int) -> int:
        del pid
        raise OSError("gone")

    monkeypatch.setattr(jobs.os, "getpgid", missing_group)

    process.raise_on_signal = True
    jobs._signal_updater(process, signal.SIGKILL)  # type: ignore[arg-type]

    process.returncode = 0
    jobs._signal_updater(process, signal.SIGTERM)  # type: ignore[arg-type]


def test_terminate_updater_escalates_to_kill_and_then_gives_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Process:
        pid = 99
        returncode: int | None = None
        signals: list[int]
        waits = 0
        timeouts_before_exit = 1

        def __init__(self) -> None:
            self.signals = []

        def poll(self) -> int | None:
            return self.returncode

        def send_signal(self, sig: int) -> None:
            self.signals.append(sig)

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.waits += 1
            if self.waits <= self.timeouts_before_exit:
                raise subprocess.TimeoutExpired(cmd="updater", timeout=1)
            self.returncode = -signal.SIGKILL
            return self.returncode

    process = _Process()
    monkeypatch.setattr(jobs, "_process_is_group_leader", lambda pid: False)
    jobs._terminate_updater(process)  # type: ignore[arg-type]
    assert process.signals == [signal.SIGTERM, signal.SIGKILL]
    assert process.returncode == -signal.SIGKILL

    stuck = _Process()
    stuck.timeouts_before_exit = 3
    jobs._terminate_updater(stuck)  # type: ignore[arg-type]
    assert stuck.signals == [signal.SIGTERM, signal.SIGKILL]


def test_finish_native_stage_records_a_sibling_stop_instead_of_a_device_fault_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sibling-stop.json").write_text("{}\n")

    def unexpected() -> list[str]:
        raise AssertionError("a sibling stop must not retry on device fault")

    monkeypatch.setattr(jobs, "hosted_device_faults", unexpected)
    monkeypatch.setattr(jobs, "is_hosted_darwin_runner", lambda: True)
    log = StringIO()
    assert (
        jobs._finish_native_stage("prepare", tmp_path, tmp_path / "missing", log, 0)
        == 1
    )
    assert "not a device-fault retry" in log.getvalue()
    assert "Updater failed" not in log.getvalue()


def test_finish_native_stage_annotates_the_updater_failure(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text(
        json.dumps({"success": False, "errors": ["gitbutler"]})
    )
    log = StringIO()
    assert (
        jobs._finish_native_stage("validate", tmp_path, tmp_path / "missing", log, 2)
        == 2
    )
    text = log.getvalue()
    assert "Updater failed for: gitbutler." in text
    assert "::error title=Updater failed::" in text


def test_workflows_grant_actions_read_and_pass_the_poll_token() -> None:
    """The poll lists this run's jobs. It does not use the update PAT."""
    update = yaml.load(
        (ROOT / ".github/workflows/update.yml").read_text(), Loader=yaml.BaseLoader
    )
    native = yaml.load(
        (ROOT / ".github/workflows/update-native.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    prepare = yaml.load(
        (ROOT / ".github/workflows/pr-update-prepare.yml").read_text(),
        Loader=yaml.BaseLoader,
    )
    for workflow in (update, native, prepare):
        assert workflow["permissions"]["contents"] == "read"
        assert workflow["permissions"]["actions"] == "read"
    step = next(
        item
        for item in native["jobs"]["native"]["steps"]
        if item.get("name") == "Prepare or validate candidate"
    )
    assert step["env"]["NIXCFG_SYSTEM"] == "${{ inputs.system }}"
    assert step["env"]["NIXCFG_ACTIONS_TOKEN"] == "${{ github.token }}"  # noqa: S105 -- Actions expression, not a credential.
    assert step["env"]["GITHUB_TOKEN"] == "${{ secrets.UPDATE_SELF_HEAL_GITHUB_TOKEN }}"  # noqa: S105 -- Actions expression, not a credential.
    for name in (
        "canary-warm-rust",
        "validate-darwin-warm-rust",
        "validate-darwin-cascade-warmup",
        "validate-darwin-roots",
    ):
        assert update["jobs"][name]["strategy"]["fail-fast"] == "false"
