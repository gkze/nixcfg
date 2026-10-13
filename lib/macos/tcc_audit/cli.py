"""Typer CLI for auditing and cleaning macOS TCC privacy permissions.

TCC (Transparency, Consent, and Control) is the subsystem behind the
System Settings > Privacy & Security lists (Full Disk Access,
Accessibility, Screen Recording, Input Monitoring, ...).
"""

import json
import re
import sqlite3
import subprocess
import sys
from dataclasses import asdict, dataclass
from functools import lru_cache
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from lib.cli import make_main, make_typer_app
from lib.macos.tcc_audit import removal as removal_module
from lib.macos.tcc_audit import settings as settings_module
from lib.macos.tcc_audit.accessibility import SystemSettingsProcess
from lib.macos.tcc_audit.audit import (
    Auditor,
    DeadPath,
    HiddenOrphan,
    Live,
    NameAmbiguous,
    PolicyKept,
    SystemEntry,
    UnrecognizedService,
)
from lib.macos.tcc_audit.bundles import BundleIndex
from lib.macos.tcc_audit.db import DEFAULT_DB, TccSnapshot, read_snapshot
from lib.macos.tcc_audit.model import (
    PANE_BY_SERVICE,
    Auth,
    Pane,
    TCCEntry,
    page_label,
    pane_label,
)

app = make_typer_app(
    help_text=(
        "Audit and clean macOS TCC (Transparency, Consent, and Control) "
        "privacy permissions - the lists behind System Settings > Privacy "
        "& Security: Full Disk Access, Accessibility, Screen Recording, "
        "Input Monitoring, and friends."
    ),
)

_LSREGISTER = (
    "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
    "LaunchServices.framework/Support/lsregister"
)
# The LaunchServices dump spells each record's bundle id on an "identifier:" line.
_IDENTIFIER_RE = re.compile(r"^[ \t]*identifier:[ \t]+(\S+)[ \t]*$", re.MULTILINE)


@lru_cache(maxsize=1)
def _registered_bundle_ids() -> frozenset[str]:
    """Return the bundle ids LaunchServices records, matched exactly.

    The dump is the store ``tccutil`` consults, but it is a human-readable
    listing: a substring test treats an unregistered id as live whenever it
    happens to prefix a registered one, silently hiding a dead row from the
    audit instead of reporting it.
    """
    completed = subprocess.run(  # noqa: S603 -- fixed local Apple LS binary, argv is constant
        [_LSREGISTER, "-dump"],
        capture_output=True,
        text=True,
        check=False,
    )
    return frozenset(_IDENTIFIER_RE.findall(completed.stdout))


def _registered(bundle_id: str) -> bool:
    """Return whether LaunchServices still registers this bundle id."""
    return bundle_id in _registered_bundle_ids()


def _auth_label(entry: TCCEntry) -> str:
    return "ON" if entry.auth is Auth.ALLOWED else "off"


def _load_snapshot() -> TccSnapshot:
    """Read the TCC database, reporting an unreadable one as a tool failure.

    The database looks world-readable but its open is gated by Full Disk
    Access, so "not readable" is the expected failure rather than corruption.
    Callers must not have to read a driver traceback to learn which it is.
    """
    try:
        return read_snapshot()
    except sqlite3.Error as error:
        typer.echo(
            f"Cannot read {DEFAULT_DB}: {error}\n"
            "hint: grant the invoking terminal Full Disk Access (System Settings"
            " > Privacy & Security). A corrupt database reports differently, so"
            " read this line before assuming corruption.",
            err=True,
        )
        raise typer.Exit(code=2) from error


def _render_rows(rows: list[dict[str, str]]) -> None:
    no_color = not sys.stdout.isatty()
    console = Console(no_color=no_color, highlight=not no_color)

    table = Table(title="macOS TCC privacy lists", show_lines=False)
    table.add_column("section", style="cyan")
    table.add_column("auth", style="magenta")
    table.add_column("status", style="green")
    table.add_column("client")
    table.add_column("reason")
    for row in rows:
        table.add_row(
            row["section"],
            row["auth"],
            row["status"],
            row["client"],
            row["reason"] or "<none>",
        )
    console.print(table)


@app.command(name="audit")
def audit_command(
    *,
    show_all: Annotated[
        bool,
        typer.Option(
            "--all",
            "-a",
            help="Include valid, system, and unrecognized rows.",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            "-j",
            help="Emit machine-readable JSON instead of a table.",
        ),
    ] = False,
) -> None:
    """Classify every macOS TCC privacy-list entry.

    Lists stale entries by default: rows left behind by uninstalled apps,
    dead binary paths, and nix-store churn, plus rows this model does not
    yet interpret. Pass --all to also see valid and Apple-system rows.
    """
    index = BundleIndex.scan()
    auditor = Auditor(index=index, registered=_registered)
    snapshot = _load_snapshot()
    verdicts = auditor.classify(snapshot.entries, snapshot.unrecognized)

    rows: list[dict[str, str]] = []
    for verdict in verdicts:
        match verdict:
            case SystemEntry():
                if show_all:
                    rows.append(
                        {
                            "section": page_label(verdict.entry.service),
                            "auth": "n/a",
                            "status": "system",
                            "client": verdict.entry.client,
                            "reason": "",
                        },
                    )
            case Live():
                if show_all:
                    rows.append(
                        {
                            "section": page_label(verdict.entry.service),
                            "auth": _auth_label(verdict.entry),
                            "status": "live",
                            "client": verdict.entry.client,
                            "reason": "",
                        },
                    )
            case DeadPath():
                rows.append(
                    {
                        "section": page_label(verdict.entry.service),
                        "auth": _auth_label(verdict.entry),
                        "status": "dead-path",
                        "client": verdict.entry.client,
                        "reason": "binary path gone",
                    },
                )
            case HiddenOrphan():
                rows.append(
                    {
                        "section": page_label(verdict.entry.service),
                        "auth": _auth_label(verdict.entry),
                        "status": "hidden-orphan",
                        "client": verdict.entry.client,
                        "reason": "orphan bundle id (hidden from panes, inert)",
                    },
                )
            case NameAmbiguous():
                rows.append(
                    {
                        "section": page_label(verdict.entry.service),
                        "auth": _auth_label(verdict.entry),
                        "status": "name-ambiguous",
                        "client": verdict.entry.client,
                        "reason": f"ambiguous display name ({verdict.occurrences} rows)",
                    },
                )
            case PolicyKept():
                rows.append(
                    {
                        "section": page_label(verdict.entry.service),
                        "auth": _auth_label(verdict.entry),
                        "status": "policy-kept",
                        "client": verdict.entry.client,
                        "reason": f"policy keep ({verdict.reason})",
                    },
                )
            case UnrecognizedService():
                rows.append(
                    {
                        "section": verdict.entry.service_key,
                        "auth": verdict.entry.auth_value,
                        "status": "unrecognized",
                        "client": verdict.entry.client,
                        "reason": verdict.entry.reason,
                    },
                )
            case _:
                # A verdict class added upstream must surface in the report: an
                # audit that silently drops a row reads as complete when it is not.
                rows.append(
                    {
                        "section": "unknown",
                        "auth": "n/a",
                        "status": "unknown-verdict",
                        "client": type(verdict).__name__,
                        "reason": "verdict this renderer does not know - upgrade tcctool",
                    },
                )

    actionable = auditor.actionable(verdicts)
    if as_json:
        typer.echo(json.dumps({"rows": rows, "actionable": len(actionable)}, indent=2))
        return

    _render_rows(rows)
    if actionable:
        typer.echo(
            f"{len(actionable)} actionable row(s) - run 'tcctool clean' to remove them.",
        )


@dataclass(frozen=True, slots=True)
class _CleanResult:
    """One `clean` attempt on one actionable row, classified for reporting."""

    pane: str
    name: str
    outcome: str
    handled: bool


# A row is handled once it is selected (dry run) or its removal is verified.
# `Pressed` never reaches this layer: `remove_and_verify` polls the row until it
# disappears, so a press the user has not authenticated yet reports
# `StillPresent`. Every other outcome leaves an actionable row in place, so
# `clean` reports a partial failure and exits non-zero rather than reading as
# success.
_HANDLED_OUTCOMES = (
    removal_module.Selection,
    removal_module.Removed,
)


def _unhandled(pane: str, name: str, outcome: str) -> _CleanResult:
    """Classify a row `clean` never handed to the removal path at all."""
    return _CleanResult(pane=pane, name=name, outcome=outcome, handled=False)


@app.command(name="clean")
def clean_command(
    *,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Select rows and verify, without pressing Remove.",
        ),
    ] = False,
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            "-j",
            help="Emit machine-readable JSON instead of text.",
        ),
    ] = False,
) -> None:
    """Remove stale macOS TCC privacy-list rows via the System Settings panes.

    Drives the native Accessibility path: each actionable row is selected
    in its pane and the Remove button is pressed; macOS then demands your
    Touch ID/password to complete it. Ambiguous rows (where a dead and a
    live entry share a display name) are refused, not guessed at. Every row
    is classified in the output, and the command exits 1 when any row was
    left undone; nothing to do or a fully handled run exits 0.
    """
    index = BundleIndex.scan()
    auditor = Auditor(index=index, registered=_registered)
    snapshot = _load_snapshot()
    verdicts = auditor.classify(snapshot.entries, snapshot.unrecognized)
    dead = auditor.actionable(verdicts)
    skipped = [
        v for v in verdicts if isinstance(v, (HiddenOrphan, NameAmbiguous, PolicyKept))
    ]
    results: list[_CleanResult] = []
    if not dead:
        payload = {"results": [], "failed": 0, "skipped": len(skipped)}
        typer.echo(
            json.dumps(payload, indent=2)
            if as_json
            else "No actionable rows - the lists are clean."
        )
        return

    processes: dict[Pane, SystemSettingsProcess] = {}
    for dead_path in dead:
        pane = PANE_BY_SERVICE.get(dead_path.entry.service)
        if pane is None:
            results.append(
                _unhandled("unknown", dead_path.name, "skipped (no pane mapping)"),
            )
            continue
        process = processes.get(pane)
        if process is None:
            if not settings_module.ensure_running(pane):
                results.append(
                    _unhandled(
                        pane.value,
                        dead_path.name,
                        "failed (System Settings did not start)",
                    ),
                )
                continue
            process = SystemSettingsProcess.attach()
            if process is None:
                results.append(
                    _unhandled(
                        pane.value,
                        dead_path.name,
                        "failed (System Settings not addressable via AX)",
                    ),
                )
                continue
            processes[pane] = process
        outcome = removal_module.remove_and_verify(
            process,
            pane,
            dead_path.name,
            dry_run=dry_run,
        )
        results.append(
            _CleanResult(
                pane=pane.value,
                name=dead_path.name,
                outcome=type(outcome).__name__,
                handled=isinstance(outcome, _HANDLED_OUTCOMES),
            ),
        )
        if isinstance(outcome, removal_module.WrongPane):
            typer.echo(
                f"{dead_path.name!r} was left alone: asked for the "
                f"{pane_label(pane)} pane ({pane.value}), but System Settings "
                f"shows {outcome.observed or '<untitled>'!r}.",
                err=True,
            )
        if isinstance(outcome, removal_module.StillPresent):
            typer.echo(
                f"{dead_path.name!r} is still listed - the prompt was likely "
                "cancelled.",
                err=True,
            )

    failed = sum(1 for result in results if not result.handled)
    payload = {
        "results": [asdict(result) for result in results],
        "failed": failed,
        "skipped": len(skipped),
    }
    if as_json:
        typer.echo(json.dumps(payload, indent=2))
    else:
        for result in results:
            typer.echo(
                f"{result.pane:<18} {result.name:<30} {result.outcome}",
            )
        if skipped:
            typer.echo(
                f"{len(skipped)} inert or ambiguous row(s) skipped - see 'tcctool audit'.",
            )
    if failed:
        typer.echo(
            f"{failed} of {len(results)} row(s) did not complete - "
            "resolve the reported outcomes and rerun 'tcctool clean'.",
            err=True,
        )
        raise typer.Exit(code=1)


@app.command(name="remove")
def remove_command(
    pane: Annotated[
        Pane,
        typer.Argument(
            help=(
                "macOS privacy pane to act on: Privacy_AllFiles, "
                "Privacy_Accessibility, Privacy_ScreenCapture, "
                "Privacy_ListenEvent, Privacy_PostEvent, Privacy_DeveloperTool."
            ),
        ),
    ],
    name: Annotated[
        str,
        typer.Argument(help="Exact display name of the row."),
    ],
    *,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Select the row and verify, without pressing Remove.",
        ),
    ] = False,
) -> None:
    """Select and Remove a row from a macOS privacy pane.

    macOS will demand Touch ID (or your password) to complete the removal.
    """
    if not settings_module.ensure_running(pane):
        typer.echo("System Settings did not start.", err=True)
        raise typer.Exit(code=1)
    process = SystemSettingsProcess.attach()
    if process is None:
        typer.echo("System Settings is not addressable via AX.", err=True)
        raise typer.Exit(code=1)
    outcome = removal_module.remove_and_verify(process, pane, name, dry_run=dry_run)
    match outcome:
        case removal_module.Removed():
            typer.echo(f"Removed {name!r} - verified gone from the pane.")
        case removal_module.StillPresent():
            typer.echo(
                f"{name!r} is still listed - the prompt was likely cancelled.",
                err=True,
            )
            raise typer.Exit(code=1)
        case removal_module.WrongPane():
            typer.echo(
                f"Wrong pane: asked for the {pane_label(pane)} pane "
                f"({pane.value}), but System Settings shows "
                f"{outcome.observed or '<untitled>'!r} - nothing was "
                "selected or removed.",
                err=True,
            )
            raise typer.Exit(code=1)
        case removal_module.NotFound():
            typer.echo(
                f"No row named {name!r} in the {pane.value} pane.",
                err=True,
            )
            raise typer.Exit(code=1)
        case removal_module.Refused():
            typer.echo(
                f"Refused: {outcome.occurrences} rows named {name!r} - "
                "cannot tell dead from live.",
                err=True,
            )
            raise typer.Exit(code=1)
        case removal_module.SelectFailed():
            typer.echo("The row could not be selected via AX.", err=True)
            raise typer.Exit(code=1)
        case _:
            typer.echo("Removal did not complete.", err=True)
            raise typer.Exit(code=1)


main = make_main(app, prog_name="tcctool")

__all__ = ["app", "main"]
