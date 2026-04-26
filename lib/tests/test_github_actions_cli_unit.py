"""Focused unit coverage for the GitHub Actions CLI helpers."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lib.github_actions import cli as gha_cli
from lib.github_actions import client as gha_client


def _workflow(name: str = "Periodic Flake Update") -> gha_client.Workflow:
    timestamp = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    return gha_client.Workflow.model_construct(
        id=1,
        node_id="WF_1",
        name=name,
        path=".github/workflows/update.yml",
        state="active",
        created_at=timestamp,
        updated_at=timestamp,
        url="https://api.github.com/repos/acme/demo/actions/workflows/1",
        html_url="https://github.com/acme/demo/actions/workflows/1",
        badge_url="https://github.com/acme/demo/actions/workflows/1/badge.svg",
        deleted_at=None,
    )


def _run(
    status: str, conclusion: str | None = None, run_number: int = 683
) -> gha_client.WorkflowRun:
    timestamp = datetime(2026, 4, 2, 16, 0, tzinfo=UTC)
    return gha_client.WorkflowRun.model_construct(
        id=9,
        name="update.yml",
        node_id="WR_9",
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
        display_title="Periodic Flake Update",
    )


def test_actions_workflows_prints_no_workflows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(gha_cli, "_workflow_rows", lambda **_kwargs: ())

    result = CliRunner().invoke(gha_cli.app, ["workflows"])

    assert result.exit_code == 0
    assert "No workflows found" in result.output


def test_tail_workflow_async_converts_missing_workflow_and_run_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _aclose() -> None:
        return None

    workflow = _workflow()

    class _ApiClient:
        def list_workflows(self) -> tuple[gha_client.Workflow, ...]:
            return (workflow,)

        def list_workflow_runs(
            self, _workflow_id: int, *, limit: int = 20
        ) -> tuple[gha_client.WorkflowRun, ...]:
            assert limit == 20
            return ()

    monkeypatch.setattr(
        gha_cli,
        "_build_tail_clients",
        lambda **_kwargs: (_ApiClient(), SimpleNamespace(aclose=_aclose)),
    )
    monkeypatch.setattr(
        gha_cli,
        "select_named_workflow",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("unknown workflow")),
    )

    with pytest.raises(gha_cli.typer.BadParameter, match="unknown workflow"):
        asyncio.run(
            gha_cli._tail_workflow_async(
                workflow_name="missing",
                job_name=None,
                repo=None,
                server_url=None,
                poll_interval=1.0,
                chrome_debugging_url=None,
                allow_playwright_login=False,
            )
        )

    monkeypatch.setattr(
        gha_cli, "select_named_workflow", lambda *_args, **_kwargs: workflow
    )

    with pytest.raises(gha_cli.typer.BadParameter, match="has no runs yet"):
        asyncio.run(
            gha_cli._tail_workflow_async(
                workflow_name=workflow.name,
                job_name=None,
                repo=None,
                server_url=None,
                poll_interval=1.0,
                chrome_debugging_url=None,
                allow_playwright_login=False,
            )
        )

    class _ApiClientWithLatest(_ApiClient):
        def list_workflow_runs(
            self, _workflow_id: int, *, limit: int = 20
        ) -> tuple[gha_client.WorkflowRun, ...]:
            assert limit == 20
            return (_run("completed", "success", 12),)

    monkeypatch.setattr(
        gha_cli,
        "_build_tail_clients",
        lambda **_kwargs: (_ApiClientWithLatest(), SimpleNamespace(aclose=_aclose)),
    )
    monkeypatch.setattr(gha_cli, "choose_live_run", lambda _runs: None)

    with pytest.raises(
        gha_cli.typer.BadParameter,
        match=r"has no active run; latest run is #12 \[success\]",
    ):
        asyncio.run(
            gha_cli._tail_workflow_async(
                workflow_name=workflow.name,
                job_name=None,
                repo=None,
                server_url=None,
                poll_interval=1.0,
                chrome_debugging_url=None,
                allow_playwright_login=False,
            )
        )


def test_build_client_helpers_and_workflow_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, object] = {}
    context = gha_client.RepositoryContext(
        slug=gha_client.RepositorySlug(owner="acme", name="demo"),
        server_url="https://github.com",
    )

    class _FakeApiClient:
        def __init__(
            self, *, token: str, context: gha_client.RepositoryContext
        ) -> None:
            created["api"] = (token, context)

        def list_workflows(self) -> tuple[gha_client.Workflow, ...]:
            return (_workflow("zeta"), _workflow("Alpha").model_copy(update={"id": 2}))

        def list_workflow_runs(
            self, workflow_id: int, *, limit: int = 1
        ) -> tuple[gha_client.WorkflowRun, ...]:
            assert limit == 1
            return () if workflow_id == 1 else (_run("completed", "success"),)

    class _FakeCookieProvider:
        def __init__(self, **kwargs: object) -> None:
            created["cookie_provider"] = kwargs

    class _FakeLiveClient:
        def __init__(self, **kwargs: object) -> None:
            created["live"] = kwargs

    monkeypatch.setattr(gha_cli, "default_github_token", lambda: "token")
    monkeypatch.setattr(
        gha_cli, "resolve_repository_context", lambda **_kwargs: context
    )
    monkeypatch.setattr(gha_cli, "GitHubActionsClient", _FakeApiClient)
    monkeypatch.setattr(gha_cli, "GitHubWebCookieProvider", _FakeCookieProvider)
    monkeypatch.setattr(gha_cli, "GitHubActionsLiveClient", _FakeLiveClient)

    api_client = gha_cli._build_api_client(
        repo="acme/demo", server_url="https://github.com"
    )
    built_api, built_live = gha_cli._build_tail_clients(
        repo="acme/demo",
        server_url="https://github.com",
        chrome_debugging_url="http://127.0.0.1:9222",
        allow_playwright_login=True,
    )
    rows = gha_cli._workflow_rows(repo="acme/demo", server_url="https://github.com")

    assert isinstance(api_client, _FakeApiClient)
    assert isinstance(built_api, _FakeApiClient)
    assert isinstance(built_live, _FakeLiveClient)
    assert [row.workflow.name for row in rows] == ["Alpha", "zeta"]
    assert rows[0].latest_run is not None
    assert rows[1].latest_run is None
    assert gha_cli._latest_run_text(None) == "-"
    assert gha_cli._latest_run_text(_run("queued")) == "#683 queued"
    assert created["cookie_provider"] == {
        "server_url": "https://github.com",
        "output": gha_cli.sys.stderr,
        "allow_playwright": True,
        "chrome_debugging_url": "http://127.0.0.1:9222",
    }
