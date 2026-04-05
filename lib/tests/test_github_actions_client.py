"""Tests for GitHub Actions repository discovery and GitHubKit wrappers."""

from __future__ import annotations

from datetime import UTC, datetime
from subprocess import CompletedProcess
from types import SimpleNamespace

import pytest

from lib.github_actions import client as gha_client


class _FakeResponse[T]:
    def __init__(self, parsed_data: T, *, json_value: object | None = None) -> None:
        self.parsed_data = parsed_data
        self._json_value = json_value

    def json(self) -> object:
        return self._json_value if self._json_value is not None else self.parsed_data


class _FakeActions:
    def __init__(
        self,
        *,
        workflows: gha_client.ReposOwnerRepoActionsWorkflowsGetResponse200,
        runs: gha_client.ReposOwnerRepoActionsWorkflowsWorkflowIdRunsGetResponse200,
        run: gha_client.WorkflowRun,
        jobs_payload: dict[str, object],
        workflow_pages: list[gha_client.ReposOwnerRepoActionsWorkflowsGetResponse200]
        | None = None,
        run_pages: list[
            gha_client.ReposOwnerRepoActionsWorkflowsWorkflowIdRunsGetResponse200
        ]
        | None = None,
        jobs_payload_pages: list[dict[str, object]] | None = None,
    ) -> None:
        self._workflow_pages = workflow_pages or [workflows]
        self._run_pages = run_pages or [runs]
        self._run = run
        self._jobs_payload_pages = jobs_payload_pages or [jobs_payload]
        self.workflow_page_calls: list[tuple[int, int]] = []
        self.run_page_calls: list[tuple[int, int]] = []
        self.job_page_calls: list[tuple[int, int]] = []

    def list_repo_workflows(
        self,
        owner: str,
        repo: str,
        *,
        per_page: int,
        page: int | None = None,
    ) -> _FakeResponse[gha_client.ReposOwnerRepoActionsWorkflowsGetResponse200]:
        assert owner == "acme"
        assert repo == "demo"
        resolved_page = page or 1
        self.workflow_page_calls.append((resolved_page, per_page))
        index = min(resolved_page - 1, len(self._workflow_pages) - 1)
        return _FakeResponse(self._workflow_pages[index])

    def list_workflow_runs(
        self,
        owner: str,
        repo: str,
        workflow_id: int,
        *,
        per_page: int,
        page: int | None = None,
    ) -> _FakeResponse[
        gha_client.ReposOwnerRepoActionsWorkflowsWorkflowIdRunsGetResponse200
    ]:
        assert owner == "acme"
        assert repo == "demo"
        assert workflow_id == 1
        resolved_page = page or 1
        self.run_page_calls.append((resolved_page, per_page))
        index = min(resolved_page - 1, len(self._run_pages) - 1)
        return _FakeResponse(self._run_pages[index])

    def get_workflow_run(
        self,
        owner: str,
        repo: str,
        run_id: int,
    ) -> _FakeResponse[gha_client.WorkflowRun]:
        assert owner == "acme"
        assert repo == "demo"
        assert run_id == 9
        return _FakeResponse(self._run)

    def list_jobs_for_workflow_run(
        self,
        owner: str,
        repo: str,
        run_id: int,
        *,
        per_page: int,
        page: int | None = None,
    ) -> _FakeResponse[gha_client.ReposOwnerRepoActionsRunsRunIdJobsGetResponse200]:
        assert owner == "acme"
        assert repo == "demo"
        assert run_id == 9
        resolved_page = page or 1
        self.job_page_calls.append((resolved_page, per_page))
        parsed = (
            gha_client.ReposOwnerRepoActionsRunsRunIdJobsGetResponse200.model_construct(
                total_count=1,
                jobs=[],
            )
        )
        index = min(resolved_page - 1, len(self._jobs_payload_pages) - 1)
        return _FakeResponse(parsed, json_value=self._jobs_payload_pages[index])


class _FakeGitHub:
    def __init__(self, actions: _FakeActions) -> None:
        self._actions = actions
        self.requested_versions: list[str] = []

    def rest(self, version: str):
        self.requested_versions.append(version)
        return SimpleNamespace(actions=self._actions)


def _workflow(
    workflow_id: int,
    name: str,
    path: str,
    state: str,
) -> gha_client.Workflow:
    timestamp = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    return gha_client.Workflow.model_construct(
        id=workflow_id,
        node_id=f"WF_{workflow_id}",
        name=name,
        path=path,
        state=state,
        created_at=timestamp,
        updated_at=timestamp,
        url=f"https://api.github.com/repos/acme/demo/actions/workflows/{workflow_id}",
        html_url=f"https://github.com/acme/demo/actions/workflows/{workflow_id}",
        badge_url=(
            f"https://github.com/acme/demo/actions/workflows/{workflow_id}/badge.svg"
        ),
        deleted_at=None,
    )


def _run(
    run_id: int,
    *,
    run_number: int,
    status: str,
    conclusion: str | None = None,
) -> gha_client.WorkflowRun:
    created_at = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    updated_at = datetime(2026, 4, 2, 16, 1, tzinfo=UTC)
    return gha_client.WorkflowRun.model_construct(
        id=run_id,
        name="update.yml",
        node_id=f"WR_{run_id}",
        check_suite_id=100,
        check_suite_node_id="CS_100",
        head_branch="main",
        head_sha="deadbeef",
        path=".github/workflows/update.yml@refs/heads/main",
        run_number=run_number,
        run_attempt=1,
        referenced_workflows=[],
        event="workflow_dispatch",
        status=status,
        conclusion=conclusion,
        workflow_id=1,
        url=f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}",
        html_url=f"https://github.com/acme/demo/actions/runs/{run_id}",
        pull_requests=[],
        created_at=created_at,
        updated_at=updated_at,
        actor=None,
        triggering_actor=None,
        run_started_at=created_at,
        jobs_url=f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}/jobs",
        logs_url=f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}/logs",
        check_suite_url="https://api.github.com/check-suites/100",
        artifacts_url=(
            f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}/artifacts"
        ),
        cancel_url=(
            f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}/cancel"
        ),
        rerun_url=f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}/rerun",
        previous_attempt_url=None,
        workflow_url="https://api.github.com/repos/acme/demo/actions/workflows/1",
        head_commit={},
        repository={},
        head_repository={},
        head_repository_id=1,
        display_title="Periodic Flake Update",
    )


def _job_summary(
    job_id: int,
    name: str,
    status: str,
    conclusion: str | None = None,
) -> gha_client.JobSummary:
    started_at = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    completed_at = None
    if status == "completed":
        completed_at = datetime(2026, 4, 2, 16, 5, tzinfo=UTC)
    return gha_client.JobSummary(
        id=job_id,
        name=name,
        status=status,
        conclusion=conclusion,
        html_url=f"https://github.com/acme/demo/actions/runs/9/job/{job_id}",
        started_at=started_at,
        completed_at=completed_at,
    )


def _job_payload(
    job_id: int,
    name: str,
    status: str,
    conclusion: str | None = None,
) -> dict[str, object]:
    return {
        "id": job_id,
        "run_id": 9,
        "run_url": "https://api.github.com/repos/acme/demo/actions/runs/9",
        "run_attempt": 1,
        "node_id": f"JOB_{job_id}",
        "head_sha": "deadbeef",
        "url": f"https://api.github.com/repos/acme/demo/actions/jobs/{job_id}",
        "html_url": f"https://github.com/acme/demo/actions/runs/9/job/{job_id}",
        "status": status,
        "conclusion": conclusion,
        "created_at": "2026-04-02T16:00:00Z",
        "started_at": "2026-04-02T16:00:00Z",
        "completed_at": (None if status != "completed" else "2026-04-02T16:05:00Z"),
        "name": name,
        "check_run_url": f"https://api.github.com/repos/acme/demo/check-runs/{job_id}",
        "labels": ["macos-15-arm64"],
        "runner_id": 1,
        "runner_name": "GitHub Actions 1",
        "runner_group_id": 1,
        "runner_group_name": "GitHub Actions",
        "workflow_name": "Periodic Flake Update",
        "head_branch": "main",
        "steps": [
            {
                "number": 1,
                "name": "Set up job",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "number": 2,
                "name": "Pre-fetch flake inputs",
                "status": "pending",
                "conclusion": None,
            },
        ],
    }


def _job_list_payload(jobs: list[dict[str, object]] | None = None) -> dict[str, object]:
    jobs_payload = jobs or [_job_payload(42, "darwin-lock-smoke", "in_progress")]
    return {
        "total_count": len(jobs_payload),
        "jobs": jobs_payload,
    }


def test_parse_git_remote_url_supports_common_forms() -> None:
    """Handle SSH, ssh://, and HTTPS GitHub remotes."""
    ssh_remote = gha_client.parse_git_remote_url("git@github.com:acme/demo.git")
    assert ssh_remote.host == "github.com"
    assert ssh_remote.slug.full_name == "acme/demo"

    ssh_scheme_remote = gha_client.parse_git_remote_url(
        "ssh://git@github.example.com/acme/demo.git"
    )
    assert ssh_scheme_remote.host == "github.example.com"
    assert ssh_scheme_remote.slug.full_name == "acme/demo"

    https_remote = gha_client.parse_git_remote_url("https://github.com/acme/demo")
    assert https_remote.host == "github.com"
    assert https_remote.slug.full_name == "acme/demo"

    with pytest.raises(ValueError, match="Unsupported git remote URL"):
        gha_client.parse_git_remote_url("file:///tmp/demo")


def test_repository_slug_parse_rejects_extra_segments_and_empty_parts() -> None:
    """Explicit --repo values should require exactly one owner and one name."""
    assert gha_client.RepositorySlug.parse("acme/demo").full_name == "acme/demo"

    for invalid in ("acme/demo/extra", "acme//demo", "acme/", "/demo"):
        with pytest.raises(ValueError, match="owner/name"):
            gha_client.RepositorySlug.parse(invalid)


def test_parse_git_remote_origin_uses_git_config_and_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Read origin from git config and fail cleanly when it is missing."""

    def _success(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(
            args=[],
            returncode=0,
            stdout="git@github.com:acme/demo.git\n",
        )

    monkeypatch.setattr(gha_client.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(gha_client.subprocess, "run", _success)
    parsed = gha_client.parse_git_remote_origin()
    assert parsed.host == "github.com"
    assert parsed.slug.full_name == "acme/demo"

    def _failure(*_args: object, **_kwargs: object) -> CompletedProcess[str]:
        return CompletedProcess(args=[], returncode=1, stdout="")

    monkeypatch.setattr(gha_client.subprocess, "run", _failure)
    with pytest.raises(RuntimeError, match="remote.origin.url"):
        gha_client.parse_git_remote_origin()


def test_resolve_repository_context_prefers_explicit_repo_and_validates_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use the explicit repo when provided and validate conflicting hosts."""
    explicit = gha_client.resolve_repository_context(
        repo="acme/demo",
        server_url="https://github.example.com",
    )
    assert explicit.slug.full_name == "acme/demo"
    assert explicit.server_url == "https://github.example.com"

    monkeypatch.setattr(
        gha_client,
        "parse_git_remote_origin",
        lambda **_kwargs: gha_client.parse_git_remote_url(
            "git@github.com:acme/demo.git"
        ),
    )
    inferred = gha_client.resolve_repository_context(repo=None, server_url=None)
    assert inferred.slug.full_name == "acme/demo"
    assert inferred.server_url == "https://github.com"

    with pytest.raises(ValueError, match="remote.origin.url points at"):
        gha_client.resolve_repository_context(
            repo=None,
            server_url="https://github.example.com",
        )


def test_default_github_token_requires_known_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raise a helpful error when no GitHub token can be resolved."""
    monkeypatch.setattr(
        gha_client.http_utils,
        "resolve_github_token",
        lambda **_kwargs: None,
    )
    with pytest.raises(RuntimeError, match="Could not resolve a GitHub token"):
        gha_client.default_github_token()


def test_server_url_helpers_cover_public_and_enterprise() -> None:
    """Normalize GitHub web origins and derive the correct API base."""
    assert (
        gha_client.normalize_server_url("https://github.com/") == "https://github.com"
    )
    assert (
        gha_client.github_api_base_url("https://github.com") == "https://api.github.com"
    )
    assert (
        gha_client.github_api_base_url("https://github.example.com")
        == "https://github.example.com/api/v3"
    )

    with pytest.raises(ValueError, match="Expected an HTTPS GitHub server URL"):
        gha_client.normalize_server_url("http://github.com")
    with pytest.raises(ValueError, match="Expected a bare GitHub server origin"):
        gha_client.normalize_server_url("https://github.com/path")


def test_select_helpers_and_run_choice_cover_exact_fuzzy_and_ambiguous() -> None:
    """Resolve exact, fuzzy, and ambiguous workflow/job names."""
    workflows = (
        _workflow(1, "CI", ".github/workflows/ci.yml", "active"),
        _workflow(
            2,
            "Periodic Flake Update",
            ".github/workflows/update.yml",
            "active",
        ),
    )
    assert gha_client.select_named_workflow(workflows, "CI").id == 1
    assert gha_client.select_named_workflow(workflows, "periodic flake update").id == 2
    assert gha_client.select_named_workflow(workflows, "flake").id == 2
    with pytest.raises(ValueError, match="Unknown workflow"):
        gha_client.select_named_workflow(workflows, "missing")

    jobs = (
        _job_summary(1, "linux", "queued"),
        _job_summary(2, "linux smoke", "queued"),
    )
    with pytest.raises(ValueError, match="Ambiguous job name"):
        gha_client.select_named_job(jobs, "lin")

    runs = (
        _run(1, run_number=1, status="completed", conclusion="success"),
        _run(2, run_number=2, status="queued"),
    )
    assert gha_client.choose_live_run(runs).id == 2
    assert (
        gha_client.choose_next_live_job((
            _job_summary(10, "active", "in_progress"),
            _job_summary(11, "queued", "queued"),
        )).id
        == 10
    )
    assert gha_client.choose_next_live_job(jobs) is None
    assert (
        gha_client.choose_next_live_job((
            _job_summary(3, "done", "completed", "success"),
        ))
        is None
    )


def test_client_wraps_githubkit_actions_models(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return GitHubKit-generated models for workflows, runs, and jobs."""
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )
    workflow = _workflow(
        1, "Periodic Flake Update", ".github/workflows/update.yml", "active"
    )
    run = _run(9, run_number=683, status="in_progress")
    fake_actions = _FakeActions(
        workflows=gha_client.ReposOwnerRepoActionsWorkflowsGetResponse200.model_construct(
            total_count=1,
            workflows=[workflow],
        ),
        runs=gha_client.ReposOwnerRepoActionsWorkflowsWorkflowIdRunsGetResponse200.model_construct(
            total_count=1,
            workflow_runs=[run],
        ),
        run=run,
        jobs_payload=_job_list_payload(),
    )
    fake_github = _FakeGitHub(fake_actions)
    monkeypatch.setattr(
        gha_client,
        "build_github_client",
        lambda **_kwargs: fake_github,
    )

    client = gha_client.GitHubActionsClient(token="ghs_" + "test", context=context)
    workflows = client.list_workflows()
    assert workflows == (workflow,)

    runs = client.list_workflow_runs(1)
    assert runs[0].run_number == 683
    assert runs[0].created_at == datetime(2026, 4, 2, 16, 0, tzinfo=UTC)

    resolved_run = client.get_workflow_run(9)
    assert resolved_run.html_url.endswith("/actions/runs/9")

    jobs = client.list_run_jobs(9)
    assert jobs[0].name == "darwin-lock-smoke"
    assert jobs[0].status == "in_progress"
    assert jobs[0].html_url.endswith("/job/42")
    assert jobs[0].completed_at is None
    assert fake_actions.workflow_page_calls == [(1, 100)]
    assert fake_actions.run_page_calls == [(1, 20)]
    assert fake_actions.job_page_calls == [(1, 100)]
    assert fake_github.requested_versions == ["2022-11-28"]


def test_client_paginates_workflows_runs_and_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Collect multi-page workflow and job results while respecting run limits."""
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )
    workflow_page_1 = [
        _workflow(
            index, f"Workflow {index}", f".github/workflows/{index}.yml", "active"
        )
        for index in range(1, 101)
    ]
    workflow_page_2 = [
        _workflow(101, "Workflow 101", ".github/workflows/101.yml", "active")
    ]
    run_page_1 = [
        _run(index, run_number=index, status="completed", conclusion="success")
        for index in range(1, 101)
    ]
    run_page_2 = [
        _run(index, run_number=index, status="completed", conclusion="success")
        for index in range(101, 201)
    ]
    job_page_1 = [
        _job_payload(index, f"job-{index}", "completed", "success")
        for index in range(1, 101)
    ]
    job_page_2 = [_job_payload(101, "job-101", "completed", "success")]
    fake_actions = _FakeActions(
        workflows=gha_client.ReposOwnerRepoActionsWorkflowsGetResponse200.model_construct(
            total_count=len(workflow_page_1),
            workflows=workflow_page_1,
        ),
        runs=gha_client.ReposOwnerRepoActionsWorkflowsWorkflowIdRunsGetResponse200.model_construct(
            total_count=len(run_page_1),
            workflow_runs=run_page_1,
        ),
        run=_run(9, run_number=683, status="in_progress"),
        jobs_payload=_job_list_payload(job_page_1),
        workflow_pages=[
            gha_client.ReposOwnerRepoActionsWorkflowsGetResponse200.model_construct(
                total_count=101,
                workflows=workflow_page_1,
            ),
            gha_client.ReposOwnerRepoActionsWorkflowsGetResponse200.model_construct(
                total_count=101,
                workflows=workflow_page_2,
            ),
        ],
        run_pages=[
            gha_client.ReposOwnerRepoActionsWorkflowsWorkflowIdRunsGetResponse200.model_construct(
                total_count=200,
                workflow_runs=run_page_1,
            ),
            gha_client.ReposOwnerRepoActionsWorkflowsWorkflowIdRunsGetResponse200.model_construct(
                total_count=200,
                workflow_runs=run_page_2,
            ),
        ],
        jobs_payload_pages=[
            _job_list_payload(job_page_1),
            _job_list_payload(job_page_2),
        ],
    )
    fake_github = _FakeGitHub(fake_actions)
    monkeypatch.setattr(
        gha_client,
        "build_github_client",
        lambda **_kwargs: fake_github,
    )

    client = gha_client.GitHubActionsClient(token="ghs_" + "test", context=context)

    workflows = client.list_workflows()
    runs = client.list_workflow_runs(1, limit=150)
    jobs = client.list_run_jobs(9)

    assert len(workflows) == 101
    assert workflows[-1].name == "Workflow 101"
    assert len(runs) == 150
    assert runs[-1].run_number == 150
    assert len(jobs) == 101
    assert jobs[-1].name == "job-101"
    assert fake_actions.workflow_page_calls == [(1, 100), (2, 100)]
    assert fake_actions.run_page_calls == [(1, 100), (2, 100)]
    assert fake_actions.job_page_calls == [(1, 100), (2, 100)]


def test_build_github_client_uses_api_base_url(monkeypatch: pytest.MonkeyPatch) -> None:
    """Construct the shared GitHubKit client against the API origin."""
    called: dict[str, object] = {}

    class _ConstructedGitHub:
        pass

    def _fake_github(
        auth: str,
        *,
        base_url: str,
        user_agent: str,
    ) -> _ConstructedGitHub:
        called.update(auth=auth, base_url=base_url, user_agent=user_agent)
        return _ConstructedGitHub()

    monkeypatch.setattr(gha_client, "GitHub", _fake_github)
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.example.com",
    )

    github = gha_client.build_github_client(token="ghs_" + "test", context=context)

    assert isinstance(github, _ConstructedGitHub)
    assert called == {
        "auth": "ghs_test",
        "base_url": "https://github.example.com/api/v3",
        "user_agent": "nixcfg-github-actions/0.0.0",
    }
