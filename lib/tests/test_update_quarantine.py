"""Quarantine keeps one package failure from stopping the rest of Update.

The rot threshold is seven days because Update runs weekly, Monday 07:17 UTC.
``since + 7 days <= now`` lets the run that first quarantines a package
publish, and fails the next scheduled run before certify. That is one grace
publication, then a hard stop.
"""

import asyncio
import json
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from lib.nix.models.sources import SourcesFile
from lib.update import cli as update_cli
from lib.update.ci import candidate as pipeline
from lib.update.ci import jobs
from lib.update.ci.candidate import app as candidate_app
from lib.update.ci.coverage import (
    CoverageError,
    GateReport,
    held_installables_by_system,
    require_package_inventory,
)
from lib.update.cli import (
    OutputOptions,
    ResolvedTargets,
    _RunOutcome,
    _RunPlan,
    _RunPlanError,
)
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.quarantine import (
    EXCERPT_MAX,
    IssueSyncAction,
    OpenQuarantineIssue,
    QuarantineEntry,
    QuarantineListTruncatedError,
    QuarantinePhase,
    QuarantineRotError,
    append_job_summary,
    bound_excerpt,
    current_run_url,
    entries_for_package_failures,
    failure_is_infra,
    failures_are_package_scoped,
    merge_quarantine_entries,
    package_build_quarantines,
    parse_open_issue,
    parse_search_payload,
    plan_issue_sync,
    quarantine_log_line,
    render_issue_body,
    render_quarantine_summary,
    sync_quarantine_issues,
)
from lib.update.source_runner import (
    SourceTaskResult,
    UpdatePhaseResult,
    _failure_text,
    _summarize_source_results,
    _terminal_error_messages,
)

NOW = datetime(2026, 11, 4, 7, 17, tzinfo=UTC)
RUN = "https://github.com/example/repo/actions/runs/1"


def _entry(
    package: str = "demo",
    *,
    phase: QuarantinePhase = QuarantinePhase.UPDATER,
    excerpt: str = "boom",
    since: datetime = NOW,
    run_url: str = RUN,
) -> QuarantineEntry:
    return QuarantineEntry(
        package=package,
        phase=phase,
        error_excerpt=excerpt,
        run_url=run_url,
        since=since,
    )


def _issue(
    number: int,
    package: str = "demo",
    *,
    since: datetime = NOW,
    phase: QuarantinePhase | None = QuarantinePhase.UPDATER,
) -> OpenQuarantineIssue:
    return OpenQuarantineIssue(
        number=number,
        package=package,
        title=f"nixcfg quarantine: {package}",
        since=since,
        phase=phase,
    )


def _plan() -> _RunPlan:
    resolved = ResolvedTargets(
        all_source_names={"demo"},
        all_ref_inputs=[],
        all_ref_names=set(),
        all_known_names={"demo"},
        do_refs=False,
        do_sources=True,
        do_input_refresh=False,
        dry_run=True,
        native_only=True,
        ref_inputs=[],
        source_names=["demo"],
    )
    return _RunPlan(
        resolved=resolved,
        sources=SourcesFile(entries={}),
        item_meta={},
        order=["demo"],
    )


class _Capture:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def capture(
        self,
        _workspace: object,
        *,
        sources: tuple[str, ...],
        allowed_paths: tuple[Path, ...],
        succeeded: bool,
        quarantines: tuple[QuarantineEntry, ...] = (),
    ) -> None:
        self.calls.append({
            "sources": sources,
            "allowed_paths": allowed_paths,
            "succeeded": succeeded,
            "quarantines": quarantines,
        })


def _outcome(*errors: str, messages: Mapping[str, str] | None = None) -> _RunOutcome:
    outcome = _RunOutcome(had_errors=bool(errors))
    if errors:
        outcome.summary.accumulate(dict.fromkeys(errors, "error"))
    outcome.error_messages = dict(messages or {})
    return outcome


def test_package_text_is_not_infrastructure() -> None:
    """Builder, hash, and HTTP 5xx failures stay with the package."""
    assert failure_is_infra("Illegal byte sequence while reading the store")
    assert failure_is_infra("builder terminated by signal 10")
    assert failure_is_infra("The requested URL returned error: 401")
    assert failure_is_infra("Bad credentials")
    assert failure_is_infra("Operation cancelled")
    assert failure_is_infra("The runner has received a shutdown signal")
    assert failure_is_infra("Lost communication with the server")
    assert not failure_is_infra("error: builder for /nix/store/abc.drv failed")
    assert not failure_is_infra("hash mismatch in fixed-output derivation")
    assert not failure_is_infra("HTTP error 503 from the upstream")
    assert not failure_is_infra("")


def test_only_real_package_names_can_be_isolated() -> None:
    assert not failures_are_package_scoped((), {})
    assert not failures_are_package_scoped(("workspace",), {})
    assert not failures_are_package_scoped(("bad name",), {})
    assert not failures_are_package_scoped(("demo",), {"demo": "authentication failed"})
    assert failures_are_package_scoped(("demo",), {"demo": "error: builder for x"})
    assert failures_are_package_scoped(("demo", "other"), {})


def test_excerpt_is_redacted_and_bounded() -> None:
    credential_url = "https://user:secret@example.test/path"
    assert "secret" not in bound_excerpt(f"see {credential_url}")
    assert "'''" in bound_excerpt("before ``` after")
    assert len(bound_excerpt("x" * (EXCERPT_MAX + 20))) == EXCERPT_MAX


def test_entry_rejects_a_naive_clock_and_a_bad_name() -> None:
    with pytest.raises(ValidationError, match="package name"):
        _entry("bad name")
    with pytest.raises(ValidationError, match="timezone-aware"):
        QuarantineEntry(
            package="demo",
            phase=QuarantinePhase.UPDATER,
            error_excerpt="boom",
            since=datetime(2026, 11, 4, 7, 17),  # noqa: DTZ001 -- naive stamp is the rejection
        )
    with pytest.raises(ValidationError, match="timezone-aware"):
        OpenQuarantineIssue(
            number=1,
            package="demo",
            title="nixcfg quarantine: demo",
            since=datetime(2026, 11, 4, 7, 17),  # noqa: DTZ001 -- naive stamp is the rejection
        )


def test_merge_keeps_the_earliest_clock_and_the_latest_failure() -> None:
    early = _entry(excerpt="old", since=NOW - timedelta(days=3), run_url="https://old")
    late = _entry(
        phase=QuarantinePhase.BUILD,
        excerpt="new",
        since=NOW,
        run_url="https://new",
    )
    merged = merge_quarantine_entries((late, early))
    assert len(merged) == 1
    assert merged[0].since == early.since
    assert merged[0].phase is QuarantinePhase.BUILD
    assert merged[0].error_excerpt == "new"
    assert merged[0].run_url == "https://new"
    same = NOW - timedelta(days=1)
    again = merge_quarantine_entries((
        _entry(excerpt="first", since=same),
        _entry(excerpt="second", since=same),
    ))
    assert again[0].error_excerpt == "second"


def test_entries_include_failed_packages_and_withheld_companions() -> None:
    entries = entries_for_package_failures(
        ["demo"],
        {},
        {"companion": "coupled to demo"},
        now=NOW,
        run_url=RUN,
    )
    by_name = {entry.package: entry for entry in entries}
    assert by_name["demo"].error_excerpt == "updater failed"
    assert by_name["companion"].error_excerpt == "coupled to demo"
    assert by_name["demo"].phase is QuarantinePhase.UPDATER


def test_build_failures_skip_infrastructure_and_bad_names() -> None:
    entries = package_build_quarantines(
        (
            ("demo", "error: builder for /nix/store/abc.drv"),
            ("root-closures", "manifest failed"),
            ("other", "Lost communication with the server"),
            ("bad name", "error: builder for x"),
        ),
        now=NOW,
        run_url=RUN,
    )
    assert [entry.package for entry in entries] == ["demo"]
    assert entries[0].phase is QuarantinePhase.BUILD


def test_rot_boundary_is_exactly_seven_days() -> None:
    """One weekly grace publication, then the next Monday fails."""
    fresh = _entry(since=NOW)
    almost = _entry(since=NOW - timedelta(days=6, hours=23, minutes=59))
    exact = _entry(since=NOW - timedelta(days=7))
    open_actions, open_rotten = plan_issue_sync(
        quarantines=(fresh,),
        targets=(),
        failures=(),
        reports_present=True,
        now=NOW,
        issues=(),
        truncated=False,
    )
    assert open_actions[0].kind == "open"
    assert open_rotten == ()
    _actions, almost_rotten = plan_issue_sync(
        quarantines=(almost,),
        targets=(),
        failures=(),
        reports_present=True,
        now=NOW,
        issues=(_issue(4, since=almost.since),),
        truncated=False,
    )
    assert almost_rotten == ()
    actions, rotten = plan_issue_sync(
        quarantines=(exact,),
        targets=(),
        failures=(),
        reports_present=True,
        now=NOW,
        issues=(_issue(4, since=exact.since), _issue(9, since=NOW)),
        truncated=False,
    )
    assert rotten[0].since == exact.since
    assert [action.kind for action in actions] == ["update", "close"]
    assert actions[0].number == 4
    assert exact.since.isoformat() in actions[0].body
    assert actions[1].close_reason == "not planned"
    assert actions[1].number == 9


def test_issue_plan_opens_updates_and_closes_only_proven_packages() -> None:
    demo = _entry(excerpt="still broken", since=NOW)
    primary = _issue(3, since=NOW - timedelta(days=2))
    actions, rotten = plan_issue_sync(
        quarantines=(demo,),
        targets=(),
        failures=(("other", "error: builder for x"),),
        reports_present=True,
        now=NOW,
        issues=(primary, _issue(8, package="kept", since=NOW - timedelta(days=1))),
        truncated=False,
    )
    assert rotten == ()
    assert actions[0].kind == "update"
    assert primary.since.isoformat() in actions[0].body
    assert "still broken" in actions[0].body
    assert [action.package for action in actions if action.kind == "close"] == ["kept"]
    blocked, _rotten = plan_issue_sync(
        quarantines=(),
        targets=(),
        failures=(("root-closures", "closure failed"),),
        reports_present=True,
        now=NOW,
        issues=(_issue(1, package="kept"),),
        truncated=False,
    )
    assert blocked == ()
    infra, _rotten = plan_issue_sync(
        quarantines=(),
        targets=(),
        failures=(("demo", "authentication failed"),),
        reports_present=True,
        now=NOW,
        issues=(_issue(1, package="kept"),),
        truncated=False,
    )
    assert infra == ()
    unnamed, _rotten = plan_issue_sync(
        quarantines=(),
        targets=None,
        failures=(),
        reports_present=True,
        now=NOW,
        issues=(_issue(1, package="kept"),),
        truncated=False,
    )
    assert unnamed == ()
    no_reports, _rotten = plan_issue_sync(
        quarantines=(),
        targets=(),
        failures=(),
        reports_present=False,
        now=NOW,
        issues=(_issue(1, package="kept"),),
        truncated=False,
    )
    assert no_reports == ()
    partial, _rotten = plan_issue_sync(
        quarantines=(),
        targets=("demo",),
        failures=(),
        reports_present=True,
        now=NOW,
        issues=(_issue(1, package="kept"),),
        truncated=False,
    )
    assert partial == ()
    same_clock = NOW - timedelta(days=1)
    duplicates, _rotten = plan_issue_sync(
        quarantines=(),
        targets=(),
        failures=(),
        reports_present=True,
        now=NOW,
        issues=(
            _issue(5, package="kept", since=same_clock),
            _issue(3, package="kept", since=same_clock),
        ),
        truncated=False,
    )
    assert [action.number for action in duplicates] == [3, 5]
    assert duplicates[0].close_reason == "completed"
    assert duplicates[1].close_reason == "not planned"
    with pytest.raises(QuarantineListTruncatedError, match="truncated"):
        plan_issue_sync(
            quarantines=(),
            targets=(),
            failures=(),
            reports_present=True,
            now=NOW,
            issues=(),
            truncated=True,
        )


def test_sync_applies_the_plan_before_rot_fails_the_run() -> None:
    applied: list[str] = []
    entry = _entry(since=NOW - timedelta(days=7))

    def apply(action: IssueSyncAction) -> None:
        applied.append(action.kind)

    with pytest.raises(QuarantineRotError, match="demo"):
        sync_quarantine_issues(
            quarantines=(entry,),
            targets=(),
            failures=(),
            reports_present=True,
            now=NOW,
            list_issues=lambda: ((_issue(2, since=entry.since),), False),
            apply_action=apply,
        )
    assert applied == ["update"]
    applied.clear()
    with pytest.raises(QuarantineListTruncatedError):
        sync_quarantine_issues(
            quarantines=(),
            targets=(),
            failures=(),
            reports_present=False,
            now=NOW,
            list_issues=lambda: ((), True),
            apply_action=apply,
        )
    assert applied == []


def test_issue_parser_uses_the_marker_clock_and_fails_closed_on_a_full_page() -> None:
    created = "2026-10-18T07:17:00+00:00"
    marker = (
        '<!-- nixcfg-quarantine package="demo" phase="build" '
        f'since="{NOW.isoformat()}" -->'
    )
    parsed = parse_open_issue({
        "number": 7,
        "title": "nixcfg quarantine: demo",
        "created_at": created,
        "body": marker,
    })
    assert parsed is not None
    assert parsed.since == NOW
    assert parsed.phase is QuarantinePhase.BUILD
    fallback = parse_open_issue({
        "number": 8,
        "title": "nixcfg quarantine: demo",
        "createdAt": created,
        "body": None,
    })
    assert fallback is not None
    assert fallback.since == datetime.fromisoformat(created)
    assert fallback.phase is None
    unknown_phase = parse_open_issue({
        "number": 9,
        "title": "nixcfg quarantine: demo",
        "created_at": created,
        "body": (
            '<!-- nixcfg-quarantine package="demo" phase="nope" '
            f'since="{NOW.isoformat()}" -->'
        ),
    })
    assert unknown_phase is not None
    assert unknown_phase.phase is None
    assert unknown_phase.since == NOW
    naive_marker = parse_open_issue({
        "number": 10,
        "title": "nixcfg quarantine: demo",
        "created_at": created,
        "body": (
            '<!-- nixcfg-quarantine package="demo" phase="updater" '
            'since="2026-11-04T07:17:00" -->'
        ),
    })
    assert naive_marker is not None
    assert naive_marker.since == datetime.fromisoformat(created)
    assert parse_open_issue({"title": "nixcfg quarantine: demo"}) is None
    assert parse_open_issue("nope") is None
    assert (
        parse_open_issue({
            "number": 1,
            "title": "something else",
            "created_at": created,
        })
        is None
    )
    assert (
        parse_open_issue({
            "number": 1,
            "title": "nixcfg quarantine: demo",
            "created_at": "yesterday",
        })
        is None
    )
    assert (
        parse_open_issue({
            "number": 1,
            "title": "nixcfg quarantine: demo",
        })
        is None
    )
    assert (
        parse_open_issue({
            "number": 1,
            "title": "nixcfg quarantine: demo",
            "created_at": None,
        })
        is None
    )
    issues, truncated = parse_search_payload({
        "total_count": 1,
        "items": [
            {
                "number": 7,
                "title": "nixcfg quarantine: demo",
                "created_at": created,
                "body": "",
            }
        ],
    })
    assert truncated is False
    assert issues[0].number == 7
    _ignored, full = parse_search_payload({
        "total_count": 100,
        "items": [
            {"number": index, "title": "nope", "created_at": created}
            for index in range(100)
        ],
    })
    assert full is True
    _ignored, short = parse_search_payload({
        "total_count": 2,
        "items": [
            {
                "number": 1,
                "title": "nixcfg quarantine: demo",
                "created_at": created,
            }
        ],
    })
    assert short is True
    with pytest.raises(TypeError, match="unexpected"):
        parse_search_payload([])
    with pytest.raises(TypeError, match="unexpected"):
        parse_search_payload({"total_count": "1", "items": []})


def test_summary_markers_and_run_url(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    for name in ("GITHUB_SERVER_URL", "GITHUB_REPOSITORY", "GITHUB_RUN_ID"):
        monkeypatch.delenv(name, raising=False)
    assert current_run_url() == ""
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    assert current_run_url() == ""
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    assert current_run_url() == ""
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    assert current_run_url() == RUN
    text = render_quarantine_summary(())
    assert "Quarantined packages (0)" in text
    rendered = render_quarantine_summary((_entry(),))
    assert "phase=updater" in rendered
    assert "run=n/a" in render_quarantine_summary((_entry(run_url=""),))
    assert (
        quarantine_log_line(_entry()) == "NIXCFG_QUARANTINE package=demo phase=updater"
    )
    assert 'package="demo"' in render_issue_body(_entry(run_url=""))
    append_job_summary("orphan")
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    append_job_summary("line")
    append_job_summary("tail\n")
    assert summary.read_text(encoding="utf-8") == "line\ntail\n"
    actions, _rotten = plan_issue_sync(
        quarantines=(),
        targets=(),
        failures=(),
        reports_present=True,
        now=NOW,
        issues=(_issue(1, package="kept"),),
        truncated=False,
    )
    assert RUN in actions[0].comment


def test_source_failures_keep_their_diagnostic_text() -> None:
    assert _failure_text(None) == "updater failed"
    assert _failure_text(UpdateEvent.error("demo", "boom", detail="trace")) == "trace"
    assert _failure_text(UpdateEvent.error("demo", "boom")) == "boom"
    assert (
        _failure_text(UpdateEvent(source="demo", kind=UpdateEventKind.ERROR))
        == "updater failed"
    )
    messages = _terminal_error_messages((
        UpdateEvent.status("demo", "working"),
        UpdateEvent.error("demo", "ref failed"),
        UpdateEvent(source="other", kind=UpdateEventKind.ERROR),
    ))
    assert messages == {"demo": "ref failed", "other": "updater failed"}
    summarized = _summarize_source_results(
        ["demo", "missing"],
        {
            "demo": SourceTaskResult(
                completed=False,
                error=UpdateEvent.error("demo", "boom", detail="trace"),
            )
        },
    )
    assert summarized.error_messages == {
        "demo": "trace",
        "missing": "updater failed",
    }
    merged = UpdatePhaseResult(error_messages={"demo": "old"}).merged(
        UpdatePhaseResult(error_messages={"demo": "new", "other": "x"})
    )
    assert merged.error_messages == {"demo": "new", "other": "x"}


def test_prepare_quarantines_package_failures_and_keeps_infra_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    withheld: list[str] = []

    def withhold(
        _workspace: object,
        _plan: object,
        outcome: _RunOutcome,
        _updaters: object,
        _out: object,
        _monitor: object,
    ) -> None:
        withheld.append("called")
        outcome.dropped["companion"] = "coupled to demo"

    monkeypatch.setattr(update_cli, "_withhold_failed_clusters", withhold)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    context = SimpleNamespace(opts=None, updaters={}, out=OutputOptions(), monitor=None)

    def gate(
        outcome: _RunOutcome,
        plan: _RunPlan | _RunPlanError | None,
    ) -> _Capture:
        prep = _Capture()
        asyncio.run(
            update_cli._gate_candidate(
                None,  # type: ignore[arg-type]
                plan,
                outcome,
                context,  # type: ignore[arg-type]
                allowed_paths=(),
                preparation=prep,  # type: ignore[arg-type]
            )
        )
        return prep

    isolated = _outcome("demo", messages={"demo": "error: builder for x"})
    prep = gate(isolated, _plan())
    assert withheld == ["called"]
    assert isolated.had_errors is False
    assert {entry.package for entry in isolated.quarantines} == {"demo", "companion"}
    assert prep.calls[0]["succeeded"] is True
    assert prep.calls[0]["quarantines"] == isolated.quarantines
    assert prep.calls[0]["sources"] == ("demo",)

    withheld.clear()
    infra = _outcome("demo", messages={"demo": "Illegal byte sequence"})
    failed = gate(infra, _plan())
    assert withheld == []
    assert infra.had_errors is True
    assert failed.calls[0]["succeeded"] is False
    assert failed.calls[0]["quarantines"] == ()

    mixed = _outcome(
        "demo",
        "other",
        messages={"demo": "error: builder for x", "other": "authentication failed"},
    )
    assert gate(mixed, _plan()).calls[0]["succeeded"] is False

    invalid = _outcome("bad name", messages={"bad name": "error: builder for x"})
    assert gate(invalid, _plan()).calls[0]["succeeded"] is False

    clean = _outcome()
    assert gate(clean, _plan()).calls[0]["succeeded"] is True


def test_prepare_keeps_non_package_failures_unprepared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = SimpleNamespace(opts=None, updaters={}, out=OutputOptions(), monitor=None)

    def gate(
        outcome: _RunOutcome,
        plan: _RunPlan | _RunPlanError | None,
    ) -> _Capture:
        prep = _Capture()
        asyncio.run(
            update_cli._gate_candidate(
                None,  # type: ignore[arg-type]
                plan,
                outcome,
                context,  # type: ignore[arg-type]
                allowed_paths=(),
                preparation=prep,  # type: ignore[arg-type]
            )
        )
        return prep

    planned = _outcome("demo")
    planned.plan_error = _RunPlanError("missing", ("nope",), ("demo",))
    assert gate(planned, None).calls[0]["succeeded"] is False
    assert gate(planned, None).calls[0]["sources"] == ()

    workspace = _outcome("demo")
    workspace.workspace_error = "store vanished"
    assert gate(workspace, _plan()).calls[0]["succeeded"] is False

    validation = _outcome("demo")
    validation.validation_error = "validation incomplete"
    assert gate(validation, _plan()).calls[0]["succeeded"] is False

    unplanned = _outcome("demo", messages={"demo": "error: builder for x"})
    assert gate(unplanned, None).calls[0]["succeeded"] is False

    def bad_companion(*_args: object, **_kwargs: object) -> None:
        unplanned.dropped["bad name"] = "coupled"

    monkeypatch.setattr(update_cli, "_withhold_failed_clusters", bad_companion)
    with pytest.raises(ValidationError, match="package name"):
        gate(unplanned, _plan())
    assert unplanned.had_errors is True


def test_emit_reports_quarantines_without_adding_an_empty_key(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    outcome = _RunOutcome(had_errors=False)
    outcome.summary.accumulate({"demo": "error"})
    outcome.quarantines = (_entry(),)
    code = update_cli._emit_run_outcome(
        outcome,
        out=OutputOptions(json_output=True),
        dry_run=True,
    )
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["success"] is True
    assert payload["errors"] == ["demo"]
    assert payload["quarantined"][0]["package"] == "demo"
    assert "NIXCFG_QUARANTINE package=demo phase=updater" in captured.err
    assert "demo" in summary.read_text(encoding="utf-8")


class _Held:
    derivation_validations = (
        DerivationValidation(
            installable=".#{name}",
            systems=("aarch64-linux",),
        ),
    )


class _DarwinBuild:
    derivation_validations = (
        DerivationValidation(
            installable=".#{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )


def test_held_installables_follow_the_coverage_resolver() -> None:
    held = held_installables_by_system(
        ("demo",),
        updaters={"demo": _Held},
        systems=("aarch64-linux", "aarch64-darwin"),
    )
    assert held["aarch64-linux"] == frozenset({".#demo"})
    darwin_only = held_installables_by_system(
        ("demo",),
        updaters={"demo": _DarwinBuild},
        systems=("aarch64-linux",),
    )
    assert darwin_only["aarch64-linux"] == frozenset()


def _inventory(
    system: str, planned: tuple[str, ...], held: tuple[str, ...]
) -> GateReport:
    return GateReport(
        tree="tree",
        system=system,
        validate_all_packages=True,
        gates=("packages",),
        failures=(),
        planned_installables=planned,
        held_installables=held,
    )


def test_coverage_accepts_only_the_quarantined_gap() -> None:
    report = _inventory("aarch64-linux", (), (".#demo",))
    require_package_inventory(
        [report],
        {"aarch64-linux": frozenset({".#demo"})},
        {"aarch64-linux": frozenset({".#demo"})},
    )
    with pytest.raises(CoverageError, match="drifted"):
        require_package_inventory(
            [_inventory("aarch64-linux", (), ())],
            {"aarch64-linux": frozenset({".#demo", ".#other"})},
            {"aarch64-linux": frozenset({".#demo"})},
        )
    with pytest.raises(CoverageError, match="drifted"):
        require_package_inventory(
            [_inventory("aarch64-linux", (".#demo", ".#extra"), ())],
            {"aarch64-linux": frozenset({".#demo"})},
            {"aarch64-linux": frozenset()},
        )
    with pytest.raises(CoverageError, match="do not match"):
        require_package_inventory(
            [_inventory("aarch64-linux", (".#demo",), ())],
            {"aarch64-linux": frozenset({".#demo"})},
            {"aarch64-linux": frozenset({".#demo"})},
        )
    with pytest.raises(CoverageError, match="not in the declared"):
        require_package_inventory(
            [_inventory("aarch64-linux", (".#demo",), ())],
            {"aarch64-linux": frozenset({".#demo"})},
            {"aarch64-linux": frozenset({".#other"})},
        )


def _candidate_json(quarantines: tuple[QuarantineEntry, ...] = ()) -> str:
    from lib.update.candidate import Candidate

    return Candidate(
        base_tree="a" * 40,
        tree="b" * 40,
        targets=("demo",),
        sources=("demo",),
        systems=("aarch64-linux",),
        resolutions={},
        prepared=True,
        patch=b"",
        quarantines=quarantines,
    ).model_dump_json()


def test_sync_evidence_reads_reports_and_talks_to_github_through_files(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence = tmp_path / "evidence"
    prepared = evidence / "prepare-x86_64-linux"
    prepared.mkdir(parents=True)
    (prepared / "candidate.json").write_text(_candidate_json((_entry(),)))
    report_dir = evidence / "validate-aarch64-linux"
    report_dir.mkdir()
    from lib.update.ci.candidate import ValidationReport
    from lib.update.derivation_validation import DerivationValidationFailure

    (report_dir / "validation.json").write_text(
        ValidationReport(
            tree="b" * 40,
            system="aarch64-linux",
            failures=(
                DerivationValidationFailure(
                    source="demo",
                    installable=".#demo",
                    message="error: builder for x",
                ),
            ),
            quarantines=(_entry(phase=QuarantinePhase.BUILD, excerpt="build"),),
        ).model_dump_json()
    )
    applied: list[IssueSyncAction] = []
    pipeline.sync_quarantine_evidence(
        evidence,
        list_issues=lambda: ((), False),
        apply_action=applied.append,
        now=NOW,
    )
    captured = capsys.readouterr()
    assert applied[0].kind == "open"
    assert "NIXCFG_QUARANTINE package=demo phase=build" in captured.err
    monkeypatch.setattr(pipeline, "list_open_quarantine_issues", lambda: ((), False))
    monkeypatch.setattr(pipeline, "apply_issue_action", lambda _action: None)
    pipeline.sync_quarantine_evidence(tmp_path / "empty")


def test_github_adapters_keep_the_body_off_the_command_line(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, ...]] = []
    bodies: list[str] = []

    def run(args: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(args))
        if "--body-file" in args:
            bodies.append(Path(args[args.index("--body-file") + 1]).read_text())
        return subprocess.CompletedProcess(
            args, 0, stdout='{"total_count":0,"items":[]}'
        )

    monkeypatch.setattr(pipeline.subprocess, "run", run)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    issues, truncated = pipeline.list_open_quarantine_issues()
    assert issues == ()
    assert truncated is False
    assert calls[0][0:2] == ("gh", "api")
    pipeline.apply_issue_action(
        IssueSyncAction(
            kind="open",
            package="demo",
            title="nixcfg quarantine: demo",
            body="body text",
        )
    )
    pipeline.apply_issue_action(
        IssueSyncAction(
            kind="update",
            package="demo",
            title="nixcfg quarantine: demo",
            body="updated",
            number=4,
        )
    )
    pipeline.apply_issue_action(
        IssueSyncAction(
            kind="close",
            package="demo",
            title="nixcfg quarantine: demo",
            number=4,
            comment="clean",
        )
    )
    assert bodies == ["body text", "updated"]
    assert calls[-1][:3] == ("gh", "issue", "close")
    with pytest.raises(ValueError, match="closing"):
        pipeline.apply_issue_action(
            IssueSyncAction(
                kind="close",
                package="demo",
                title="nixcfg quarantine: demo",
            )
        )
    with pytest.raises(ValueError, match="updating"):
        pipeline.apply_issue_action(
            IssueSyncAction(
                kind="update",
                package="demo",
                title="nixcfg quarantine: demo",
                body="updated",
            )
        )


def test_sync_command_fails_on_rot_and_a_truncated_search(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail(_evidence: Path, **_kwargs: object) -> None:
        raise QuarantineRotError((_entry(since=NOW - timedelta(days=7)),))

    monkeypatch.setattr(pipeline, "sync_quarantine_evidence", fail)
    result = CliRunner().invoke(
        candidate_app,
        ["sync-quarantines", "--evidence", str(tmp_path)],
    )
    assert result.exit_code == 1
    assert "7 days" in result.stderr

    def truncated(_evidence: Path, **_kwargs: object) -> None:
        raise QuarantineListTruncatedError("open quarantine issue search was truncated")

    monkeypatch.setattr(pipeline, "sync_quarantine_evidence", truncated)
    again = CliRunner().invoke(
        candidate_app,
        ["sync-quarantines", "--evidence", str(tmp_path)],
    )
    assert again.exit_code == 1


def test_jobs_surface_stage_and_publication_count(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    calls: list[tuple[str, ...]] = []

    def run(*args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(jobs, "_run", run)
    assert jobs.main("sync-quarantines") == 0
    assert calls == []
    evidence = tmp_path / "evidence" / "validate-aarch64-linux"
    evidence.mkdir(parents=True)
    (evidence / "validation.json").write_text("{}\n")
    monkeypatch.setenv("NIXCFG_RUNTIME", str(tmp_path / "runtime"))
    assert jobs.main("sync-quarantines") == 0
    assert calls[0][1:4] == ("ci", "update", "sync-quarantines")
    prepared = tmp_path / "evidence" / "prepare-x86_64-linux"
    prepared.mkdir()
    (prepared / "candidate.json").write_text("{}\n")
    note = jobs._quarantine_publication_note()
    assert note.startswith("Quarantined packages held at last-good pins: 0")
    (prepared / "candidate.json").write_text(
        json.dumps({
            "quarantines": [
                {"package": "demo", "phase": "updater"},
                "ignore",
            ]
        })
    )
    listed = jobs._quarantine_publication_note()
    assert "Quarantined packages held at last-good pins: 2" in listed
    assert "- demo (updater)" in listed
