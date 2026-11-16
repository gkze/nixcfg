"""The pull-request planner command prints decisions and does not push."""

import json
from pathlib import Path

from typer.testing import CliRunner

from lib.update.ci.pull_requests import app
from lib.update.pull_request_plan import (
    BATCH_BRANCH,
    BatchState,
    PlanMarker,
    PlanResult,
    UpdateOffer,
    UpdatePriority,
    render_marker,
)

runner = CliRunner()


def test_plan_writes_json_and_the_branch_matrix(
    monkeypatch: object, tmp_path: Path
) -> None:
    result = PlanResult(
        updates=(),
        deferred=(),
        failures=(),
    )

    async def fake(**_kwargs: object) -> PlanResult:
        return result

    monkeypatch.setattr("lib.update.ci.pull_requests.discover_live", fake)  # type: ignore[attr-defined]
    json_path = tmp_path / "plan.json"
    matrix_path = tmp_path / "matrix.json"
    invoked = runner.invoke(
        app,
        [
            "plan",
            "--json",
            str(json_path),
            "--matrix",
            str(matrix_path),
            "--security",
            "solo",
            "--main-fix",
            "nixpkgs",
        ],
    )
    assert invoked.exit_code == 0, invoked.output
    assert "No update pull requests." in invoked.output
    assert json.loads(json_path.read_text())["updates"] == []
    assert json.loads(matrix_path.read_text()) == []
    bare = runner.invoke(app, ["plan"])
    assert bare.exit_code == 0
    assert "No update pull requests." in bare.output


def test_candidate_show_failure_and_merge_commands(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.json"
    candidate.write_text(json.dumps({"prepared": True, "quarantines": [], "patch": ""}))
    assert (
        runner.invoke(app, ["accept-candidate", str(candidate)]).output.strip()
        == "noop"
    )
    published = tmp_path / "published.patch"
    candidate.write_text(json.dumps({"prepared": True, "patch": "ZGF0YQ=="}))
    accepted = runner.invoke(
        app,
        ["accept-candidate", str(candidate), "--patch-out", str(published)],
    )
    assert accepted.output.strip() == "publish"
    assert published.read_bytes() == b"data"
    untouched = tmp_path / "untouched.patch"
    assert (
        runner.invoke(
            app, ["accept-candidate", str(candidate), "--patch-out", str(untouched)]
        ).output.strip()
        == "publish"
    )
    candidate.write_text(json.dumps({"prepared": True, "patch": ""}))
    assert (
        runner.invoke(
            app, ["accept-candidate", str(candidate), "--patch-out", str(untouched)]
        ).output.strip()
        == "noop"
    )
    candidate.write_text(
        json.dumps({"prepared": True, "quarantines": [{"package": "solo"}]})
    )
    refused = runner.invoke(app, ["accept-candidate", str(candidate)])
    assert refused.exit_code != 0
    assert refused.exception is not None
    assert "quarantined" in str(refused.exception)

    from lib.update.pull_request_plan import PlannedUpdate, plan_to_json

    plan = tmp_path / "plan.json"
    marker = PlanMarker(
        branch="update/solo",
        kind="package",
        priority=UpdatePriority.ROUTINE,
        members=("solo",),
    )
    planned = PlannedUpdate(
        branch="update/solo",
        kind="package",
        priority=UpdatePriority.ROUTINE,
        action="open",
        members=("solo",),
        targets=("solo",),
        touches_darwin=False,
        title="chore(update): solo 1 to 2",
        labels=("routine", "nixcfg-update"),
        existing_number=None,
        marker=marker,
        offers=(UpdateOffer(name="solo", kind="source", current="1", latest="2"),),
    )
    document = json.loads(
        plan_to_json(PlanResult(updates=(planned,), deferred=(), failures=()))
    )
    plan.write_text(json.dumps(document))
    shown = runner.invoke(app, ["show", str(plan), "--branch", "update/solo"])
    assert shown.exit_code == 0, shown.output
    assert "update/solo" in shown.output

    checks = tmp_path / "checks.json"
    checks.write_text(
        json.dumps({
            "check_runs": [
                {
                    "name": "closure-gate",
                    "status": "completed",
                    "conclusion": "failure",
                    "details_url": "https://github.com/gkze/nixcfg/actions/runs/1/job/7",
                }
            ]
        })
    )
    body = tmp_path / "body.md"
    body.write_text(render_marker(marker))
    failed = runner.invoke(
        app,
        ["failure", str(checks), "--branch", "update/solo", "--body-file", str(body)],
    )
    assert failed.exit_code == 0, failed.output
    assert json.loads(failed.output)["action"] == "heal"
    jobs = runner.invoke(app, ["gate-jobs", str(checks)])
    assert jobs.output.strip() == "7"
    waiting = runner.invoke(
        app,
        ["automerge", str(checks), "--branch", "update/solo", "--label", "needs-human"],
    )
    assert waiting.output.strip() == "wait"
    batch = PlanMarker(
        branch=BATCH_BRANCH,
        kind="batch",
        priority=UpdatePriority.ROUTINE,
        members=("a", "b"),
        batch=BatchState(
            pending=("a", "b"), known_good=(), culprits=(), probe=("a", "b"), steps=1
        ),
    )
    body.write_text(render_marker(batch))
    advanced = runner.invoke(app, ["batch-success", "--body-file", str(body)])
    assert advanced.exit_code == 0, advanced.output
    assert json.loads(advanced.output)["action"] == "bisect"
    fresh = render_marker(
        PlanMarker(
            branch=BATCH_BRANCH,
            kind="batch",
            priority=UpdatePriority.ROUTINE,
            members=("a",),
            batch=BatchState(
                pending=("a",), known_good=(), culprits=(), probe=("a",), steps=0
            ),
        )
    )
    body.write_text(fresh)
    assert (
        runner.invoke(app, ["batch-success", "--body-file", str(body)]).output.strip()
        == "merge"
    )


def test_normalize_checks_folds_paginated_documents(tmp_path: Path) -> None:
    raw = tmp_path / "pages.json"
    raw.write_text(
        json.dumps({"check_runs": [{"name": "one"}]})
        + "\n"
        + json.dumps({"check_runs": [{"name": "two"}]})
    )
    folded = tmp_path / "folded.json"
    normalized = runner.invoke(app, ["normalize-checks", str(raw), str(folded)])
    assert normalized.exit_code == 0, normalized.output
    assert json.loads(folded.read_text())["check_runs"] == [
        {"name": "one"},
        {"name": "two"},
    ]
