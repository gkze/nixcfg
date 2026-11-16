"""Plan update pull requests without opening them.

The scheduled workflow reads this command's JSON. ``plan`` discovers upstream
movement and prints the groups it would open. It does not push a branch.
"""

import asyncio
import base64
import json
import sys
from pathlib import Path  # noqa: TC003 -- typer resolves Path annotations at runtime
from typing import Annotated

import typer

from lib.cli import make_typer_app
from lib.update.pull_request_discover import discover_live
from lib.update.pull_request_plan import (
    FailureResponse,
    PlanMarker,
    PlanResult,
    automerge_allowed,
    batch_success_response,
    checks_from_payload,
    failure_response,
    gate_failure_job_ids,
    matrix_branches,
    merge_check_run_pages,
    open_prs_from_payload,
    parse_marker,
    plan_to_json,
    publication_decision,
    render_failure_body,
    render_plan_text,
    select_plan,
)

app = make_typer_app(
    help_text="Plan one pull request per independent update.",
    no_args_is_help=True,
)


@app.command("plan")
def plan_command(
    json_path: Annotated[
        Path | None,
        typer.Option("--json", help="Write the machine-readable plan."),
    ] = None,
    matrix_path: Annotated[
        Path | None,
        typer.Option("--matrix", help="Write the branch list for Actions."),
    ] = None,
    open_prs: Annotated[
        Path | None,
        typer.Option(
            "--open-prs", help="gh pr list JSON. Empty means no open update PRs."
        ),
    ] = None,
    security: Annotated[
        list[str] | None,
        typer.Option("--security", help="Treat this target as a security update."),
    ] = None,
    main_fix: Annotated[
        list[str] | None,
        typer.Option("--main-fix", help="Treat this target as a main-fix update."),
    ] = None,
) -> None:
    """Discover upstream updates and print the pull requests that would open."""
    existing = (
        ()
        if open_prs is None
        else open_prs_from_payload(json.loads(open_prs.read_text()))
    )
    result = asyncio.run(
        discover_live(
            security=frozenset(security or ()),
            main_fix=frozenset(main_fix or ()),
            open_prs=existing,
        )
    )
    _emit_plan(result, json_path=json_path, matrix_path=matrix_path)


@app.command("accept-candidate")
def accept_candidate(
    candidate: Annotated[Path, typer.Argument(help="Prepared candidate JSON.")],
    patch_out: Annotated[
        Path | None,
        typer.Option("--patch-out", help="Write the decoded patch when publishing."),
    ] = None,
) -> None:
    """Print ``publish`` or ``noop``. Refuse a quarantined candidate."""
    payload = json.loads(candidate.read_text())
    decision = publication_decision(payload)
    if decision == "publish" and patch_out is not None:
        patch_out.write_bytes(base64.b64decode(str(payload["patch"])))
    typer.echo(decision)


@app.command("show")
def show_plan(
    plan: Annotated[Path, typer.Argument(help="Plan JSON from the planner.")],
    branch: Annotated[str, typer.Option("--branch", help="Branch to select.")],
) -> None:
    """Print one planned update as JSON."""
    selected = select_plan(json.loads(plan.read_text()), branch)
    sys.stdout.write(
        plan_to_json(PlanResult(updates=(selected,), deferred=(), failures=()))
    )


@app.command("normalize-checks")
def normalize_checks_command(
    source: Annotated[Path, typer.Argument(help="Paginated check-run JSON.")],
    destination: Annotated[Path, typer.Argument(help="One check-run document.")],
) -> None:
    """Write every paginated check page into one document."""
    payload = merge_check_run_pages(source.read_text(encoding="utf-8"))
    destination.write_text(json.dumps(payload), encoding="utf-8")


@app.command("failure")
def failure_command(
    checks: Annotated[Path, typer.Argument(help="Commit check-run JSON.")],
    *,
    branch: Annotated[str, typer.Option("--branch")],
    body_file: Annotated[
        Path | None,
        typer.Option("--body-file", help="Pull-request body, when it has a marker."),
    ] = None,
    label: Annotated[list[str] | None, typer.Option("--label")] = None,
) -> None:
    """Print the heal, bisect, or needs-human decision as JSON."""
    body = "" if body_file is None else body_file.read_text(encoding="utf-8")
    response = failure_response(
        branch=branch,
        labels=frozenset(label or ()),
        marker=parse_marker(body),
        checks=checks_from_payload(json.loads(checks.read_text())),
    )
    sys.stdout.write(_response_json(response))


@app.command("batch-success")
def batch_success_command(
    body_file: Annotated[Path, typer.Option("--body-file")],
) -> None:
    """Print the next batch slice after a green probe, or ``merge``."""
    response = batch_success_response(
        parse_marker(body_file.read_text(encoding="utf-8"))
    )
    if response is None:
        typer.echo("merge")
        return
    sys.stdout.write(_response_json(response))


@app.command("automerge")
def automerge_command(
    checks: Annotated[Path, typer.Argument(help="Commit check-run JSON.")],
    *,
    branch: Annotated[str, typer.Option("--branch")],
    label: Annotated[list[str] | None, typer.Option("--label")] = None,
) -> None:
    """Print ``merge`` or ``wait`` for one update pull request."""
    allowed = automerge_allowed(
        branch=branch,
        labels=frozenset(label or ()),
        checks=checks_from_payload(json.loads(checks.read_text())),
    )
    typer.echo("merge" if allowed else "wait")


@app.command("gate-jobs")
def gate_jobs_command(
    checks: Annotated[Path, typer.Argument(help="Commit check-run JSON.")],
) -> None:
    """Print failed closure-gate Actions job ids, one per line."""
    for job_id in gate_failure_job_ids(json.loads(checks.read_text())):
        typer.echo(job_id)


def _emit_plan(
    result: PlanResult, *, json_path: Path | None, matrix_path: Path | None
) -> None:
    text = render_plan_text(result)
    sys.stdout.write(text)
    if json_path is not None:
        json_path.write_text(plan_to_json(result), encoding="utf-8")
    if matrix_path is not None:
        matrix_path.write_text(
            json.dumps(list(matrix_branches(result))) + "\n", encoding="utf-8"
        )


def _response_json(response: FailureResponse) -> str:
    marker: PlanMarker | None = response.marker
    payload = {
        "action": response.action,
        "batchTargets": list(response.batch_targets),
        "splitTargets": list(response.split_targets),
        "marker": None if marker is None else marker.to_json(),
        "body": render_failure_body(response, run_url=""),
    }
    return json.dumps(payload, indent=2) + "\n"


__all__ = ["app"]
