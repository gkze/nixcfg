"""Behavior of the per-target update summary and its final rendering."""

import json
from typing import TYPE_CHECKING

import pytest

from lib.update.cli import OutputOptions
from lib.update.summary import UpdateSummary, emit_summary

if TYPE_CHECKING:
    from lib.update.outcomes import SummaryStatus


def test_update_summary_accumulates_without_downgrading_failures() -> None:
    """Keep first-seen target order and the strongest observed outcome."""
    summary = UpdateSummary()
    summary.accumulate({"a": "no_change", "b": "updated"})
    summary.accumulate({"a": "error", "c": "dropped"})
    summary.accumulate({"a": "updated"})

    assert list(summary.statuses) == ["a", "b", "c"]
    assert summary.updated == ["b"]
    assert summary.errors == ["a"]
    assert summary.dropped == ["c"]
    assert summary.no_change == []
    assert summary.to_dict()["success"] is False


def test_update_summary_projection_redacts_target_names() -> None:
    """Credential-bearing target names never reach the JSON projection."""
    target = "https://user:credential@example.invalid/repo"
    payload = UpdateSummary(statuses={target: "error"}).to_dict()

    assert "credential" not in json.dumps(payload)
    assert payload["errors"] != [target]


def test_emit_summary_json_outputs_payload(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Write summary payload to stdout in json mode."""
    summary = UpdateSummary(statuses={"demo": "updated", "stable": "no_change"})

    exit_code = emit_summary(
        summary,
        had_errors=False,
        out=OutputOptions(json_output=True, quiet=True),
        dry_run=False,
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload == {
        "updated": ["demo"],
        "dropped": [],
        "errors": [],
        "noChange": ["stable"],
        "success": True,
        "withheld": {},
    }


def test_emit_summary_json_uses_overall_failure_state(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Do not report success when a non-source failure sets the exit status."""
    exit_code = emit_summary(
        UpdateSummary(),
        had_errors=True,
        out=OutputOptions(json_output=True, quiet=True),
        dry_run=False,
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["success"] is False


@pytest.mark.parametrize(
    ("statuses", "dry_run", "had_errors", "discarded", "indeterminate", "headline"),
    [
        ({"a": "updated"}, True, False, (), (), "Available updates: a"),
        ({}, True, False, (), (), "No updates available."),
        ({"a": "updated"}, False, False, ("a",), (), "Candidate updates discarded: a"),
        (
            {"a": "updated"},
            False,
            False,
            (),
            ("a",),
            "Candidate updates have unknown promotion state: a",
        ),
        ({"a": "updated"}, False, False, (), (), "Updated: a"),
        ({}, False, True, (), (), "No updates promoted."),
        ({"a": "no_change"}, False, False, (), (), "No updates needed."),
    ],
)
def test_emit_summary_human_headline(
    statuses: dict[str, SummaryStatus],
    *,
    dry_run: bool,
    had_errors: bool,
    discarded: tuple[str, ...],
    indeterminate: tuple[str, ...],
    headline: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Pick exactly one headline describing what happened to the candidate."""
    exit_code = emit_summary(
        UpdateSummary(statuses=statuses),
        had_errors=had_errors,
        out=OutputOptions(),
        dry_run=dry_run,
        discarded_updates=discarded,
        indeterminate_updates=indeterminate,
    )

    assert exit_code == (1 if had_errors else 0)
    assert capsys.readouterr().out.strip() == headline


def test_emit_summary_dry_run_updates_and_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Print dry-run update list and failed sources in human mode."""
    summary = UpdateSummary(statuses={"a": "updated", "b": "error"})
    code = emit_summary(
        summary,
        had_errors=True,
        out=OutputOptions(json_output=False, quiet=False),
        dry_run=True,
    )
    assert code == 1
    captured = capsys.readouterr()
    assert "Available updates" in captured.out
    assert "Failed: b" in captured.err


def test_emit_summary_quiet_mode_still_reports_failures(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Quiet output suppresses progress but never the failed-target list."""
    code = emit_summary(
        UpdateSummary(statuses={"a": "updated", "b": "error"}),
        had_errors=True,
        out=OutputOptions(quiet=True),
        dry_run=False,
    )

    assert code == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Failed: b" in captured.err


def test_emit_summary_reports_withheld_and_unpromoted_runs(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Text and JSON summaries name withheld candidates and empty promotions."""
    summary = UpdateSummary(statuses={"bad": "error", "child": "dropped"})
    exit_code = emit_summary(
        summary,
        had_errors=True,
        out=OutputOptions(),
        dry_run=False,
        dropped={"child": "coupled to failed target bad"},
    )
    assert exit_code == 1
    captured = capsys.readouterr()
    assert "No updates promoted." in captured.out
    assert (
        "Withheld from promotion: child (coupled to failed target bad)" in captured.out
    )
    assert "Failed: bad" in captured.err

    exit_code = emit_summary(
        summary,
        had_errors=True,
        out=OutputOptions(json_output=True),
        dry_run=False,
        dropped={"child": "coupled to failed target bad"},
    )
    assert exit_code == 1
    assert json.loads(capsys.readouterr().out) == {
        "updated": [],
        "dropped": ["child"],
        "errors": ["bad"],
        "noChange": [],
        "success": False,
        "withheld": {"child": "coupled to failed target bad"},
    }
