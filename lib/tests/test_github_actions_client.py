"""Focused tests for GitHub Actions API helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from lib.github_actions import client as gha_client


def _workflow(name: str, workflow_id: int = 1) -> gha_client.Workflow:
    timestamp = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    return gha_client.Workflow.model_construct(
        id=workflow_id,
        node_id=f"WF_{workflow_id}",
        name=name,
        path=f".github/workflows/{workflow_id}.yml",
        state="active",
        created_at=timestamp,
        updated_at=timestamp,
        url=f"https://api.github.com/repos/acme/demo/actions/workflows/{workflow_id}",
        html_url=f"https://github.com/acme/demo/actions/workflows/{workflow_id}",
        badge_url=f"https://github.com/acme/demo/actions/workflows/{workflow_id}/badge.svg",
        deleted_at=None,
    )


def _run(run_id: int, status: str = "queued") -> gha_client.WorkflowRun:
    timestamp = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    return gha_client.WorkflowRun.model_construct(
        id=run_id,
        name="workflow.yml",
        node_id=f"WR_{run_id}",
        check_suite_id=run_id,
        check_suite_node_id=f"CS_{run_id}",
        head_branch="main",
        head_sha="deadbeef",
        path=".github/workflows/update.yml@refs/heads/main",
        run_number=run_id,
        run_attempt=1,
        referenced_workflows=[],
        event="workflow_dispatch",
        status=status,
        conclusion=None if status != "completed" else "success",
        workflow_id=1,
        url=f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}",
        html_url=f"https://github.com/acme/demo/actions/runs/{run_id}",
        pull_requests=[],
        created_at=timestamp,
        updated_at=timestamp,
        actor=None,
        triggering_actor=None,
        run_started_at=timestamp,
        jobs_url=f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}/jobs",
        logs_url=f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}/logs",
        check_suite_url=f"https://api.github.com/check-suites/{run_id}",
        artifacts_url=f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}/artifacts",
        cancel_url=f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}/cancel",
        rerun_url=f"https://api.github.com/repos/acme/demo/actions/runs/{run_id}/rerun",
        previous_attempt_url=None,
        workflow_url="https://api.github.com/repos/acme/demo/actions/workflows/1",
        head_commit={},
        repository={},
        head_repository={},
        head_repository_id=1,
        display_title="Workflow",
    )


def _job_model(
    *,
    job_id: int = 1,
    name: str = "build",
    html_url: str | None = "https://github.com/acme/demo/actions/runs/1/job/1",
) -> gha_client.Job:
    timestamp = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    return gha_client.Job.model_construct(
        id=job_id,
        run_id=1,
        run_url="https://api.github.com/repos/acme/demo/actions/runs/1",
        run_attempt=1,
        node_id=f"JOB_{job_id}",
        head_branch="main",
        head_sha="deadbeef",
        url=f"https://api.github.com/repos/acme/demo/actions/jobs/{job_id}",
        html_url=html_url,
        status="in_progress",
        conclusion=None,
        created_at=timestamp,
        started_at=timestamp,
        completed_at=None,
        name=name,
        steps=[],
        check_run_url=f"https://api.github.com/repos/acme/demo/check-runs/{job_id}",
        labels=[],
        runner_id=None,
        runner_name=None,
        runner_group_id=None,
        runner_group_name=None,
        workflow_name="Workflow",
    )


class _FakeActions:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def list_repo_workflows(self, *args: object, **kwargs: object) -> SimpleNamespace:
        self.calls.append(("workflows", args, kwargs))
        page = int(kwargs["page"])
        workflows = () if page > 1 else (_workflow("One", 1), _workflow("Two", 2))
        return SimpleNamespace(parsed_data=SimpleNamespace(workflows=workflows))

    def list_workflow_runs(self, *args: object, **kwargs: object) -> SimpleNamespace:
        self.calls.append(("runs", args, kwargs))
        page = int(kwargs["page"])
        runs = () if page > 1 else (_run(1), _run(2))
        return SimpleNamespace(parsed_data=SimpleNamespace(workflow_runs=runs))

    def get_workflow_run(self, *args: object) -> SimpleNamespace:
        self.calls.append(("run", args, {}))
        return SimpleNamespace(parsed_data=_run(9, status="completed"))

    def list_jobs_for_workflow_run(
        self, *args: object, **kwargs: object
    ) -> SimpleNamespace:
        self.calls.append(("jobs", args, kwargs))
        return SimpleNamespace(json=lambda: {"jobs": [{"ignored": True}]})


def test_repository_slug_parse_and_full_name() -> None:
    slug = gha_client.RepositorySlug.parse(" acme/demo ")
    assert slug.full_name == "acme/demo"

    with pytest.raises(ValueError, match="owner/name"):
        gha_client.RepositorySlug.parse("acme")


def test_github_actions_client_wraps_actions_endpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = "token"
    actions = _FakeActions()
    github = SimpleNamespace(rest=lambda _version: SimpleNamespace(actions=actions))
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )
    client = gha_client.GitHubActionsClient(
        token=auth,
        context=context,
        github=github,
    )

    monkeypatch.setattr(
        gha_client,
        "_parse_job_list_response",
        lambda _payload: SimpleNamespace(jobs=(_job_model(job_id=7),)),
    )

    workflows = client.list_workflows()
    runs = client.list_workflow_runs(1, limit=2)
    run = client.get_workflow_run(9)
    jobs = client.list_run_jobs(9)

    assert [workflow.name for workflow in workflows] == ["One", "Two"]
    assert [item.run_number for item in runs] == [1, 2]
    assert run.run_number == 9
    assert jobs == (
        gha_client.JobSummary(
            id=7,
            name="build",
            status="in_progress",
            conclusion=None,
            html_url="https://github.com/acme/demo/actions/runs/1/job/1",
            started_at=datetime(2026, 4, 2, 16, 0, tzinfo=UTC),
            completed_at=None,
        ),
    )
    assert [call[0] for call in actions.calls] == ["workflows", "runs", "run", "jobs"]


def test_build_github_client_uses_github_api_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    auth = "secret"
    captured: dict[str, object] = {}

    class _FakeGitHub:
        def __init__(self, token: str, *, base_url: str, user_agent: str) -> None:
            captured.update(token=token, base_url=base_url, user_agent=user_agent)

    monkeypatch.setattr(gha_client, "GitHub", _FakeGitHub)

    created = gha_client.build_github_client(
        token=auth,
        context=gha_client.RepositoryContext(
            slug=gha_client.RepositorySlug(owner="acme", name="demo"),
            server_url="https://ghe.example.com",
        ),
    )

    assert isinstance(created, _FakeGitHub)
    assert captured == {
        "token": auth,
        "base_url": "https://ghe.example.com/api/v3",
        "user_agent": "nixcfg-github-actions/0.0.0",
    }


def test_resolve_repository_context_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gha_client,
        "parse_git_remote_origin",
        lambda cwd=None: gha_client._ParsedRemote(
            host="github.example.com",
            slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        ),
    )

    explicit = gha_client.resolve_repository_context(
        repo="acme/demo",
        server_url="https://github.com",
    )
    matched = gha_client.resolve_repository_context(
        repo=None,
        server_url="https://github.example.com",
    )
    implicit = gha_client.resolve_repository_context(repo=None, server_url=None)

    assert explicit.slug.full_name == "acme/demo"
    assert explicit.server_url == "https://github.com"
    assert matched.server_url == "https://github.example.com"
    assert implicit.server_url == "https://github.example.com"

    with pytest.raises(ValueError, match="remote.origin.url points at"):
        gha_client.resolve_repository_context(
            repo=None,
            server_url="https://github.com",
        )


def test_default_github_token_returns_value_or_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        gha_client.http_utils,
        "resolve_github_token",
        lambda **_kwargs: "token",
    )
    assert gha_client.default_github_token() == "token"

    monkeypatch.setattr(
        gha_client.http_utils,
        "resolve_github_token",
        lambda **_kwargs: None,
    )
    with pytest.raises(RuntimeError, match="Could not resolve a GitHub token"):
        gha_client.default_github_token()


def test_server_url_normalization_and_api_base() -> None:
    assert (
        gha_client.normalize_server_url(" https://github.com/ ") == "https://github.com"
    )
    assert (
        gha_client.github_api_base_url("https://github.com") == "https://api.github.com"
    )
    assert (
        gha_client.github_api_base_url("https://ghe.example.com")
        == "https://ghe.example.com/api/v3"
    )

    with pytest.raises(ValueError, match="HTTPS"):
        gha_client.normalize_server_url("http://github.com")
    with pytest.raises(ValueError, match="bare GitHub server origin"):
        gha_client.normalize_server_url("https://github.com/path")


def test_parse_git_remote_origin_handles_missing_git_and_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gha_client, "get_repo_root", lambda: Path("/tmp/repo"))
    monkeypatch.setattr(gha_client.shutil, "which", lambda _name: None)
    with pytest.raises(RuntimeError, match="Could not find `git`"):
        gha_client.parse_git_remote_origin()

    monkeypatch.setattr(gha_client.shutil, "which", lambda _name: "/usr/bin/git")
    monkeypatch.setattr(
        gha_client.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout=""),
    )
    with pytest.raises(RuntimeError, match="Could not read remote.origin.url"):
        gha_client.parse_git_remote_origin()

    monkeypatch.setattr(
        gha_client.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout="git@github.com:acme/demo.git\n",
        ),
    )
    parsed = gha_client.parse_git_remote_origin()
    assert parsed.host == "github.com"
    assert parsed.slug.full_name == "acme/demo"


@pytest.mark.parametrize(
    ("remote_url", "host", "slug"),
    [
        ("git@github.com:acme/demo.git", "github.com", "acme/demo"),
        ("https://github.com/acme/demo.git", "github.com", "acme/demo"),
        ("ssh://git@github.example.com/acme/demo", "github.example.com", "acme/demo"),
    ],
)
def test_parse_git_remote_url_supported_forms(
    remote_url: str,
    host: str,
    slug: str,
) -> None:
    parsed = gha_client.parse_git_remote_url(remote_url)
    assert parsed.host == host
    assert parsed.slug.full_name == slug


def test_parse_git_remote_url_and_path_reject_invalid_values() -> None:
    with pytest.raises(ValueError, match="Unsupported git remote URL"):
        gha_client.parse_git_remote_url("file:///tmp/repo")
    with pytest.raises(ValueError, match="Unsupported git remote URL"):
        gha_client.parse_git_remote_url("git@github.com/acme/demo")
    with pytest.raises(ValueError, match="owner/name"):
        gha_client._parse_remote_path("/too/many/parts")


def test_selection_and_live_helpers_cover_matching_modes() -> None:
    workflows = (
        _workflow("Build"),
        _workflow("BUILD", 2),
        _workflow("Deploy", 3),
    )
    jobs = (
        gha_client.JobSummary(
            1, "lint", "queued", None, "https://example/1", None, None
        ),
        gha_client.JobSummary(
            2, "build-macos", "completed", "success", "https://example/2", None, None
        ),
        gha_client.JobSummary(
            3, "build-linux", "in_progress", None, "https://example/3", None, None
        ),
    )

    assert (
        gha_client.select_named_workflow((_workflow("Build"),), "Build").name == "Build"
    )
    assert (
        gha_client.select_named_workflow((_workflow("Build"),), "build").name == "Build"
    )
    update_workflow = _workflow("Periodic Flake Update").model_copy(
        update={"path": ".github/workflows/update.yml"}
    )
    assert gha_client.select_named_workflow((update_workflow,), "update.yml").name == (
        "Periodic Flake Update"
    )
    assert (
        gha_client.select_named_workflow(
            (update_workflow,), ".github/workflows/update.yml"
        ).name
        == "Periodic Flake Update"
    )
    assert gha_client.select_named_job(jobs, "macos").id == 2
    assert gha_client.choose_live_run((_run(1, "completed"), _run(2, "queued"))).id == 2
    assert gha_client.choose_live_run((_run(1, "completed"),)) is None
    assert gha_client.choose_next_live_job(jobs).id == 3
    assert gha_client.choose_next_live_job((jobs[0], jobs[1])) is None

    no_path_workflow = _workflow("No Path").model_copy(update={"path": None})
    assert gha_client._workflow_aliases(no_path_workflow) == ("No Path",)
    assert gha_client._workflow_display_name(no_path_workflow) == "No Path"
    root_path_workflow = _workflow("Root Path").model_copy(update={"path": "root.yml"})
    assert gha_client._workflow_aliases(root_path_workflow) == (
        "Root Path",
        "root.yml",
    )
    assert gha_client.select_named_job((jobs[0],), "LINT").id == 1
    assert (
        gha_client.select_named_workflow((update_workflow,), "flake").name
        == "Periodic Flake Update"
    )

    with pytest.raises(ValueError, match="Expected a non-empty workflow name"):
        gha_client.select_named_workflow((_workflow("Build"),), "   ")
    with pytest.raises(ValueError, match="Expected a non-empty job name"):
        gha_client.select_named_job(jobs, "   ")
    with pytest.raises(ValueError, match="Ambiguous workflow name"):
        gha_client.select_named_workflow(workflows, "BuIlD")
    with pytest.raises(ValueError, match="Ambiguous workflow name"):
        gha_client.select_named_workflow(
            (
                _workflow("Build"),
                _workflow("Build", 2),
            ),
            "Build",
        )
    with pytest.raises(ValueError, match="Ambiguous workflow name"):
        gha_client.select_named_workflow(
            (
                _workflow("lint-linux"),
                _workflow("lint-macos", 2),
            ),
            "lint",
        )
    with pytest.raises(ValueError, match="Ambiguous job name"):
        gha_client.select_named_job(
            (
                gha_client.JobSummary(
                    1, "Build", "queued", None, "https://example/1", None, None
                ),
                gha_client.JobSummary(
                    2, "BUILD", "queued", None, "https://example/2", None, None
                ),
            ),
            "build",
        )
    with pytest.raises(ValueError, match="Unknown workflow 'ship'"):
        gha_client.select_named_workflow((_workflow("Build"),), "ship")
    with pytest.raises(ValueError, match="Unknown job 'ship'"):
        gha_client.select_named_job(jobs, "ship")


def test_collect_paginated_and_name_message_helpers() -> None:
    seen_calls: list[tuple[int, int]] = []

    def _fetch_page(*, page: int, per_page: int) -> tuple[int, ...]:
        seen_calls.append((page, per_page))
        if page == 1:
            return tuple(range(1, per_page + 1))
        if page == 2:
            return (per_page + 1,)
        return ()

    assert gha_client._collect_paginated(_fetch_page, limit=0) == ()
    assert gha_client._collect_paginated(_fetch_page, limit=2) == (1, 2)
    assert gha_client._collect_paginated(_fetch_page) == tuple(range(1, 102))
    assert seen_calls == [(1, 2), (1, 100), (2, 100)]

    empty_seen: list[tuple[int, int]] = []

    def _empty_page(*, page: int, per_page: int) -> tuple[int, ...]:
        empty_seen.append((page, per_page))
        return ()

    assert gha_client._collect_paginated(_empty_page) == ()
    assert empty_seen == [(1, 100)]
    assert (
        gha_client._ambiguous_name_message(
            [_workflow("Build")], label="workflow", value_getter=lambda item: item.name
        )
        == "Ambiguous workflow name; matches: Build"
    )

    with pytest.raises(ValueError, match="Ambiguous job name"):
        gha_client.select_named_job(
            (
                gha_client.JobSummary(
                    1, "lint-linux", "queued", None, "https://example/1", None, None
                ),
                gha_client.JobSummary(
                    2, "lint-macos", "queued", None, "https://example/2", None, None
                ),
            ),
            "lint",
        )


def test_parse_job_payload_and_summary_validation_errors() -> None:
    timestamp = "2026-04-02T16:00:00Z"
    payload = _job_model().model_dump(mode="json")
    payload["status"] = "completed"
    payload["conclusion"] = "success"
    payload["completed_at"] = timestamp
    payload["steps"] = [{"bad": "shape"}]
    parsed = gha_client._parse_job_list_response({"total_count": 1, "jobs": [payload]})
    assert parsed.jobs[0].name == "build"
    assert "steps" not in parsed.jobs[0].model_dump(exclude_unset=True)

    with pytest.raises(TypeError, match="response object"):
        gha_client._parse_job_list_response([])
    with pytest.raises(TypeError, match="jobs list"):
        gha_client._parse_job_list_response({"jobs": "nope"})
    with pytest.raises(TypeError, match="job to be an object"):
        gha_client._parse_job_list_response({"jobs": ["bad"]})
    with pytest.raises(TypeError, match="html_url"):
        gha_client._job_to_summary(_job_model(html_url=None))
