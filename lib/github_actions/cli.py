"""Typer CLI for GitHub Actions workflow discovery and polling-based live tailing."""

from __future__ import annotations

import asyncio
import sys
from typing import Annotated

import click
import typer
from githubkit.exception import RequestError as GitHubKitRequestError
from rich.console import Console
from rich.table import Table

from lib import http_utils
from lib.cli import HELP_CONTEXT_SETTINGS
from lib.github_actions.client import (
    GitHubActionsClient,
    WorkflowListRow,
    WorkflowRun,
    choose_live_run,
    default_github_token,
    resolve_repository_context,
    select_named_workflow,
)
from lib.github_actions.tail import GitHubActionsLiveClient, GitHubActionsTailer
from lib.github_actions.web_auth import GitHubWebCookieProvider
from lib.update.ci._cli import make_main

app = typer.Typer(
    name="actions",
    help="GitHub Actions workflow discovery and live-tail helpers.",
    no_args_is_help=True,
    context_settings=HELP_CONTEXT_SETTINGS,
)


@app.command(name="workflows", help="List workflows for the current repository.")
def list_workflows(
    *,
    repo: Annotated[
        str | None,
        typer.Option("-R", "--repo", help="Repository in owner/name form."),
    ] = None,
    server_url: Annotated[
        str | None,
        typer.Option("-S", "--server-url", help="GitHub web origin."),
    ] = None,
) -> None:
    """Render all repository workflows with their latest known run state."""
    try:
        rows = _workflow_rows(repo=repo, server_url=server_url)
    except (
        GitHubKitRequestError,
        http_utils.RequestError,
        RuntimeError,
        ValueError,
    ) as exc:
        raise click.ClickException(str(exc)) from None

    table = Table(title="GitHub Actions workflows")
    table.add_column("Workflow")
    table.add_column("State")
    table.add_column("Path")
    table.add_column("Latest run")
    for row in rows:
        table.add_row(
            row.workflow.name,
            row.workflow.state,
            row.workflow.path,
            _latest_run_text(row.latest_run),
        )
    if not rows:
        Console().print("No workflows found")
        return
    Console().print(table)


@app.command(name="tail", help="Poll the latest active run for one workflow.")
def tail_workflow(
    workflow_name: Annotated[
        str,
        typer.Argument(
            help=(
                "Workflow name, path, or file basename as shown by "
                "`nixcfg actions workflows`."
            )
        ),
    ],
    job_name: Annotated[
        str | None,
        typer.Argument(
            help="Optional job name to tail instead of hopping across the run."
        ),
    ] = None,
    *,
    repo: Annotated[
        str | None,
        typer.Option("-R", "--repo", help="Repository in owner/name form."),
    ] = None,
    server_url: Annotated[
        str | None,
        typer.Option("-S", "--server-url", help="GitHub web origin."),
    ] = None,
    poll_interval: Annotated[
        float,
        typer.Option("-i", "--poll-interval", help="Polling interval in seconds."),
    ] = 1.0,
    chrome_debugging_url: Annotated[
        str | None,
        typer.Option(
            "-D",
            "--chrome-debugging-url",
            help="Chrome DevTools base URL or websocket URL for existing browser auth.",
        ),
    ] = None,
    allow_playwright_login: Annotated[
        bool,
        typer.Option(
            "-P",
            "--allow-playwright-login",
            help=(
                "If CDP cookies are unavailable, open Chrome via Playwright to "
                "bootstrap a logged-in GitHub web session."
            ),
        ),
    ] = False,
) -> None:
    """Poll live job logs for the latest active run of one workflow."""
    try:
        asyncio.run(
            _tail_workflow_async(
                workflow_name=workflow_name,
                job_name=job_name,
                repo=repo,
                server_url=server_url,
                poll_interval=poll_interval,
                chrome_debugging_url=chrome_debugging_url,
                allow_playwright_login=allow_playwright_login,
            )
        )
    except (
        GitHubKitRequestError,
        http_utils.RequestError,
        RuntimeError,
        ValueError,
    ) as exc:
        raise click.ClickException(str(exc)) from None


async def _tail_workflow_async(
    *,
    workflow_name: str,
    job_name: str | None,
    repo: str | None,
    server_url: str | None,
    poll_interval: float,
    chrome_debugging_url: str | None,
    allow_playwright_login: bool,
) -> None:
    api_client, live_client = _build_tail_clients(
        repo=repo,
        server_url=server_url,
        chrome_debugging_url=chrome_debugging_url,
        allow_playwright_login=allow_playwright_login,
    )
    try:
        try:
            workflow = select_named_workflow(api_client.list_workflows(), workflow_name)
        except ValueError as exc:
            raise typer.BadParameter(str(exc), param_hint="workflow_name") from None
        runs = api_client.list_workflow_runs(workflow.id, limit=20)
        run = choose_live_run(runs)
        if run is None:
            latest = runs[0] if runs else None
            if latest is None:
                message = f"Workflow {workflow.name!r} has no runs yet"
                raise typer.BadParameter(message, param_hint="workflow_name")
            message = (
                f"Workflow {workflow.name!r} has no active run; latest run is "
                f"#{latest.run_number} [{latest.conclusion or latest.status}]"
            )
            raise typer.BadParameter(message, param_hint="workflow_name")

        tailer = GitHubActionsTailer(
            api_client=api_client,
            live_client=live_client,
            output=sys.stdout,
            poll_interval=poll_interval,
        )
        await tailer.tail_workflow(
            workflow=workflow,
            run=run,
            requested_job_name=job_name,
        )
    finally:
        await live_client.aclose()


def _build_api_client(
    *, repo: str | None, server_url: str | None
) -> GitHubActionsClient:
    token = default_github_token()
    context = resolve_repository_context(repo=repo, server_url=server_url)
    return GitHubActionsClient(token=token, context=context)


def _build_tail_clients(
    *,
    repo: str | None,
    server_url: str | None,
    chrome_debugging_url: str | None,
    allow_playwright_login: bool,
) -> tuple[GitHubActionsClient, GitHubActionsLiveClient]:
    token = default_github_token()
    context = resolve_repository_context(repo=repo, server_url=server_url)
    cookie_provider = GitHubWebCookieProvider(
        server_url=context.server_url,
        output=sys.stderr,
        allow_playwright=allow_playwright_login,
        chrome_debugging_url=chrome_debugging_url,
    )
    return (
        GitHubActionsClient(token=token, context=context),
        GitHubActionsLiveClient(
            token=token,
            context=context,
            cookie_provider=cookie_provider,
        ),
    )


def _workflow_rows(
    *, repo: str | None, server_url: str | None
) -> tuple[WorkflowListRow, ...]:
    api_client = _build_api_client(repo=repo, server_url=server_url)
    rows: list[WorkflowListRow] = []
    for workflow in sorted(
        api_client.list_workflows(), key=lambda item: item.name.casefold()
    ):
        latest_runs = api_client.list_workflow_runs(workflow.id, limit=1)
        rows.append(
            WorkflowListRow(
                workflow=workflow,
                latest_run=latest_runs[0] if latest_runs else None,
            )
        )
    return tuple(rows)


def _latest_run_text(run: WorkflowRun | None) -> str:
    if run is None:
        return "-"
    return f"#{run.run_number} {run.conclusion or run.status}"


main = make_main(app, prog_name="nixcfg actions")

__all__ = ["app", "main"]
