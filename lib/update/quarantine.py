"""Package quarantine for one Update run.

A package updater or package build can fail without discarding every other
package. The prepare job reverts that package to its last-good pin, records
one :class:`QuarantineEntry`, and seals the candidate. Runner, cache, and
authentication failures are not package failures: they still fail the job.

The rot clock is the open GitHub issue's original ``since``, not a fresh
timestamp written on each candidate. Update runs weekly (Monday 07:17 UTC).
``QUARANTINE_ROT_AFTER`` is seven days, compared with ``since + threshold <=
now``, so the run that first quarantines a package still publishes and the
next scheduled run fails before certify. That is one grace publication, then
a hard stop, so a package cannot ride isolation for a second week.

``jobs.py`` must not import this module. It runs on the runner's Python 3.12
before the packaged CLI exists. The publish job calls ``nixcfg ci update
sync-quarantines`` instead.
"""

import os
import re
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict, field_validator

from lib.diagnostics import redact_urls
from lib.update.failures import (
    is_package_scoped_daemon_crash,
    is_transient_store_interruption,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

QUARANTINE_ROT_AFTER = timedelta(days=7)
EXCERPT_MAX = 2000
PACKAGE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]*$")
_TITLE = re.compile(r"^nixcfg quarantine: ([A-Za-z0-9][A-Za-z0-9._+-]*)$")
_MARKER = re.compile(
    r'<!-- nixcfg-quarantine package="([^"]+)" phase="([^"]+)" since="([^"]+)" -->'
)
# Status names the summary uses for a failed run, not for one package.
NOT_PACKAGE_SOURCES = frozenset({"workspace", "validation", "plan", "root-closures"})
# A page of 100 is the search cap. A full page can hide an older issue, so
# the sync fails closed instead of opening a duplicate or missing a rot.
_SEARCH_PAGE_CAP = 100
_INFRA_PHRASES = (
    "authentication failed",
    "bad credentials",
    "invalid username or token",
    "permission denied (publickey)",
    "could not read username for",
    "the requested url returned error: 401",
    "the requested url returned error: 403",
    "http error 401",
    "401 unauthorized",
    "requires authentication",
    "error: unable to authenticate",
    "not authorized to access",
    "operation cancelled",
    "the runner has received a shutdown signal",
    "lost communication with the server",
)


class QuarantinePhase(StrEnum):
    """Where the package failure was observed."""

    UPDATER = "updater"
    BUILD = "build"


class QuarantineEntry(BaseModel):
    """One package held at its last-good pin for this run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    package: str
    phase: QuarantinePhase
    error_excerpt: str
    run_url: str = ""
    since: datetime

    @field_validator("package")
    @classmethod
    def package_name(cls, value: str) -> str:
        """Reject a name that cannot be a stable issue title."""
        if PACKAGE_NAME.fullmatch(value) is None:
            msg = "invalid quarantine package name"
            raise ValueError(msg)
        return value

    @field_validator("error_excerpt")
    @classmethod
    def bounded_excerpt(cls, value: str) -> str:
        """Store the redacted, length-capped diagnostic."""
        return bound_excerpt(value)

    @field_validator("run_url")
    @classmethod
    def redacted_run_url(cls, value: str) -> str:
        """Drop credentials if a run URL was copied from a log."""
        return redact_urls(value)

    @field_validator("since")
    @classmethod
    def aware_since(cls, value: datetime) -> datetime:
        """Require a timezone. Naive stamps cannot be compared with the rot clock."""
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "quarantine since must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)


class QuarantineHold(BaseModel):
    """Baseline state one preparation restored for its quarantined packages.

    ``paths`` are the repository-relative files of failed and withheld
    packages, plus ``flake.nix`` and ``flake.lock`` when the flake files were
    reverted whole. ``inputs`` are lock-only flake inputs whose root edge and
    node were rolled back to the baseline lock.

    A package quarantined on one platform cannot promote on another: its
    hashes would be missing for the platform that failed. Folding native
    candidates applies the union of every hold to every candidate before the
    3-way merge. The quarantine entry alone names the package, not the files
    and lock inputs the prepare restored.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    paths: tuple[str, ...] = ()
    inputs: tuple[str, ...] = ()

    @field_validator("paths")
    @classmethod
    def repository_relative(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject a path that could name a file outside the repository index."""
        for raw in value:
            path = PurePosixPath(raw)
            if path.is_absolute() or ".." in path.parts or str(path) != raw:
                msg = f"quarantine hold path must be repository-relative: {raw!r}"
                raise ValueError(msg)
        return value

    def union(self, other: QuarantineHold) -> QuarantineHold:
        """Return both holds as one sorted, duplicate-free hold."""
        return QuarantineHold(
            paths=tuple(sorted({*self.paths, *other.paths})),
            inputs=tuple(sorted({*self.inputs, *other.inputs})),
        )


class OpenQuarantineIssue(BaseModel):
    """One already-open issue whose title names exactly one package."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    number: int
    package: str
    title: str
    since: datetime
    phase: QuarantinePhase | None = None

    @field_validator("since")
    @classmethod
    def aware_since(cls, value: datetime) -> datetime:
        """Normalize the rot clock to UTC."""
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "quarantine issue since must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)


class IssueSyncAction(BaseModel):
    """One GitHub issue write the sync applies after the plan is complete."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["open", "update", "close"]
    package: str
    title: str
    body: str = ""
    number: int | None = None
    comment: str = ""
    close_reason: Literal["completed", "not planned"] = "completed"


class QuarantineRotError(RuntimeError):
    """A package has been quarantined since at least ``QUARANTINE_ROT_AFTER``."""

    def __init__(self, entries: tuple[QuarantineEntry, ...]) -> None:
        """Record the packages whose quarantine clock has expired."""
        self.entries = entries
        rendered = ", ".join(
            f"{entry.package} since {entry.since.isoformat()}" for entry in entries
        )
        super().__init__(
            f"quarantine exceeded {QUARANTINE_ROT_AFTER.days} days: {rendered}"
        )


class QuarantineListTruncatedError(RuntimeError):
    """The open-issue search hit its page cap, so a rot or close could be missed."""


def failure_is_infra(text: str) -> bool:
    """Return whether *text* is a runner, store, auth, or cancellation failure.

    A package builder failure, a hash mismatch, an HTTP 5xx, an empty
    message, or a daemon disconnect while evaluating one derivation stays
    package-scoped. Update 41796475737 was that last shape: only neutils's
    worker died, and the same daemon kept serving the rest of the shard.
    Any other infra phrase among package failures fails the whole prepare
    job: a partial publish is not safe while auth, the runner, or the store
    is broken. Those runner store faults retry on a fresh VM before
    sibling-stop; they are not quarantines.
    """
    if is_package_scoped_daemon_crash(text):
        return False
    if is_transient_store_interruption(text):
        return True
    folded = text.casefold()
    return any(phrase in folded for phrase in _INFRA_PHRASES)


def failures_are_package_scoped(
    names: Sequence[str],
    messages: Mapping[str, str],
) -> bool:
    """Return whether every named failure can be reverted on its own."""
    if not names:
        return False
    for name in names:
        if name in NOT_PACKAGE_SOURCES or PACKAGE_NAME.fullmatch(name) is None:
            return False
        if failure_is_infra(messages.get(name, "")):
            return False
    return True


def bound_excerpt(text: str) -> str:
    """Redact URLs and keep the diagnostic inside ``EXCERPT_MAX`` characters."""
    redacted = redact_urls(text).replace("```", "'''").strip()
    if len(redacted) <= EXCERPT_MAX:
        return redacted
    return redacted[:EXCERPT_MAX]


def current_run_url() -> str:
    """Return this Actions run URL, or ``""`` outside an Actions job."""
    server = os.environ.get("GITHUB_SERVER_URL", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    run_id = os.environ.get("GITHUB_RUN_ID", "")
    if not server or not repo or not run_id:
        return ""
    return f"{server}/{repo}/actions/runs/{run_id}"


def quarantine_issue_title(package: str) -> str:
    """Return the exact title used to dedupe one package's issue."""
    return f"nixcfg quarantine: {package}"


def merge_quarantine_entries(
    entries: Sequence[QuarantineEntry],
) -> tuple[QuarantineEntry, ...]:
    """Keep one entry per package.

    ``since`` is the earliest failure. ``phase`` is ``build`` when either
    report says so. The excerpt and run URL come from the later ``since``.
    """
    chosen: dict[str, QuarantineEntry] = {}
    for entry in entries:
        current = chosen.get(entry.package)
        if current is None:
            chosen[entry.package] = entry
            continue
        later = entry if entry.since >= current.since else current
        phase = (
            QuarantinePhase.BUILD
            if QuarantinePhase.BUILD in {current.phase, entry.phase}
            else QuarantinePhase.UPDATER
        )
        chosen[entry.package] = later.model_copy(
            update={"since": min(current.since, entry.since), "phase": phase}
        )
    return tuple(chosen[name] for name in sorted(chosen))


def entries_for_package_failures(
    failed: Sequence[str],
    messages: Mapping[str, str],
    dropped: Mapping[str, str],
    *,
    now: datetime,
    run_url: str,
) -> tuple[QuarantineEntry, ...]:
    """Build updater-phase entries for failed packages and withheld companions."""
    entries = [
        QuarantineEntry(
            package=name,
            phase=QuarantinePhase.UPDATER,
            error_excerpt=messages.get(name) or "updater failed",
            run_url=run_url,
            since=now,
        )
        for name in failed
    ]
    entries.extend(
        QuarantineEntry(
            package=name,
            phase=QuarantinePhase.UPDATER,
            error_excerpt=reason,
            run_url=run_url,
            since=now,
        )
        for name, reason in dropped.items()
    )
    return merge_quarantine_entries(entries)


def package_build_quarantines(
    failures: Sequence[tuple[str, str]],
    *,
    now: datetime,
    run_url: str,
) -> tuple[QuarantineEntry, ...]:
    """Record package-attributed build failures without rewriting the tree.

    Unattributed root-closure and infrastructure failures are omitted. Those
    still fail the validation job.
    """
    entries: list[QuarantineEntry] = []
    for source, message in failures:
        if source in NOT_PACKAGE_SOURCES or failure_is_infra(message):
            continue
        if PACKAGE_NAME.fullmatch(source) is None:
            continue
        entries.append(
            QuarantineEntry(
                package=source,
                phase=QuarantinePhase.BUILD,
                error_excerpt=message,
                run_url=run_url,
                since=now,
            )
        )
    return merge_quarantine_entries(entries)


def render_issue_body(entry: QuarantineEntry) -> str:
    """Render the issue body. The HTML comment is the rot-clock record."""
    since = entry.since.astimezone(UTC).isoformat()
    marker = (
        f'<!-- nixcfg-quarantine package="{entry.package}" '
        f'phase="{entry.phase.value}" since="{since}" -->'
    )
    run = entry.run_url or "(no run URL)"
    return (
        f"{marker}\n\n"
        f"Package `{entry.package}` is quarantined at its last-good pin.\n\n"
        f"- Phase: {entry.phase.value}\n"
        f"- Since: {since}\n"
        f"- Run: {run}\n\n"
        f"```\n{entry.error_excerpt}\n```\n"
    )


def render_quarantine_summary(entries: tuple[QuarantineEntry, ...]) -> str:
    """Render the job summary, including a zero count when nothing is held."""
    lines = [f"## Quarantined packages ({len(entries)})", ""]
    if not entries:
        lines.append("No packages were quarantined.")
        return "\n".join(lines) + "\n"
    for entry in entries:
        run = entry.run_url or "n/a"
        lines.append(
            f"- `{entry.package}` phase={entry.phase.value} "
            f"since={entry.since.isoformat()} run={run}"
        )
        lines.extend(("", "```", entry.error_excerpt, "```", ""))
    return "\n".join(lines)


def quarantine_log_line(entry: QuarantineEntry) -> str:
    """Return the stable stderr marker for one quarantined package."""
    return f"NIXCFG_QUARANTINE package={entry.package} phase={entry.phase.value}"


def append_job_summary(text: str) -> None:
    """Append *text* to ``GITHUB_STEP_SUMMARY`` when Actions provides it."""
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(text)
        if not text.endswith("\n"):
            handle.write("\n")


def _parse_since(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed.astimezone(UTC)


def _marker_since(body: str) -> tuple[datetime | None, QuarantinePhase | None]:
    match = _MARKER.search(body)
    if match is None:
        return None, None
    _package, phase_text, since_text = match.groups()
    try:
        phase = QuarantinePhase(phase_text)
    except ValueError:
        phase = None
    return _parse_since(since_text), phase


def parse_open_issue(payload: object) -> OpenQuarantineIssue | None:
    """Return a quarantine issue, or ``None`` when the title is not ours."""
    if not isinstance(payload, dict):
        return None
    title = payload.get("title")
    number = payload.get("number")
    if not isinstance(title, str) or not isinstance(number, int):
        return None
    match = _TITLE.fullmatch(title)
    if match is None:
        return None
    created_raw = payload.get("created_at", payload.get("createdAt"))
    if not isinstance(created_raw, str):
        return None
    created = _parse_since(created_raw)
    if created is None:
        return None
    body = payload.get("body")
    marker_since, phase = _marker_since(body if isinstance(body, str) else "")
    return OpenQuarantineIssue(
        number=number,
        package=match.group(1),
        title=title,
        since=marker_since or created,
        phase=phase,
    )


def parse_search_payload(
    payload: object,
) -> tuple[tuple[OpenQuarantineIssue, ...], bool]:
    """Parse one issue-search page.

    The boolean is true when the page is full or shorter than ``total_count``.
    """
    if not isinstance(payload, dict):
        msg = "quarantine issue search returned an unexpected payload"
        raise TypeError(msg)
    total = payload.get("total_count")
    items = payload.get("items")
    if not isinstance(total, int) or not isinstance(items, list):
        msg = "quarantine issue search returned an unexpected payload"
        raise TypeError(msg)
    parsed = tuple(
        issue for item in items if (issue := parse_open_issue(item)) is not None
    )
    truncated = total > len(items) or len(items) >= _SEARCH_PAGE_CAP
    return parsed, truncated


def _is_rotten(since: datetime, now: datetime) -> bool:
    return since.astimezone(UTC) + QUARANTINE_ROT_AFTER <= now.astimezone(UTC)


def _primary(issues: tuple[OpenQuarantineIssue, ...]) -> OpenQuarantineIssue:
    return min(issues, key=lambda issue: (issue.since, issue.number))


def _closes_blocked(failures: tuple[tuple[str, str], ...]) -> bool:
    return any(
        source in NOT_PACKAGE_SOURCES or failure_is_infra(message)
        for source, message in failures
    )


def plan_issue_sync(
    *,
    quarantines: tuple[QuarantineEntry, ...],
    targets: tuple[str, ...] | None,
    failures: tuple[tuple[str, str], ...],
    reports_present: bool,
    now: datetime,
    issues: tuple[OpenQuarantineIssue, ...],
    truncated: bool,
) -> tuple[tuple[IssueSyncAction, ...], tuple[QuarantineEntry, ...]]:
    """Plan opens, updates, and closes. Raise when the issue list is truncated.

    ``targets is None`` means the candidate was missing, so nothing is closed.
    An empty target tuple is a full run and may close every clean package.
    Closes require validation reports, and any infrastructure or root-closure
    failure blocks every close. A package named as a failure source stays open.
    """
    if truncated:
        msg = "open quarantine issue search was truncated at the page cap"
        raise QuarantineListTruncatedError(msg)
    grouped: dict[str, list[OpenQuarantineIssue]] = {}
    for issue in issues:
        grouped.setdefault(issue.package, []).append(issue)
    held = {entry.package: entry for entry in quarantines}
    actions: list[IssueSyncAction] = []
    rotten: list[QuarantineEntry] = []
    for package, entry in held.items():
        existing = tuple(grouped.get(package, ()))
        if existing:
            primary = _primary(existing)
            preserved = entry.model_copy(update={"since": primary.since})
            actions.append(
                IssueSyncAction(
                    kind="update",
                    package=package,
                    title=quarantine_issue_title(package),
                    body=render_issue_body(preserved),
                    number=primary.number,
                )
            )
            for extra in existing:
                if extra.number == primary.number:
                    continue
                actions.append(
                    IssueSyncAction(
                        kind="close",
                        package=package,
                        title=extra.title,
                        number=extra.number,
                        comment=f"Duplicate of #{primary.number}.",
                        close_reason="not planned",
                    )
                )
            clock = primary.since
        else:
            actions.append(
                IssueSyncAction(
                    kind="open",
                    package=package,
                    title=quarantine_issue_title(package),
                    body=render_issue_body(entry),
                )
            )
            clock = entry.since
        if _is_rotten(clock, now):
            rotten.append(entry.model_copy(update={"since": clock}))
    if reports_present and targets is not None and not _closes_blocked(failures):
        failed_sources = {source for source, _message in failures}
        for package, existing in grouped.items():
            if package in held or package in failed_sources:
                continue
            if targets and package not in targets:
                continue
            primary = _primary(tuple(existing))
            actions.append(
                IssueSyncAction(
                    kind="close",
                    package=package,
                    title=primary.title,
                    number=primary.number,
                    comment=(
                        f"Package {package} updated cleanly. "
                        f"{current_run_url() or 'No run URL recorded.'}"
                    ),
                )
            )
            for extra in existing:
                if extra.number == primary.number:
                    continue
                actions.append(
                    IssueSyncAction(
                        kind="close",
                        package=package,
                        title=extra.title,
                        number=extra.number,
                        comment=f"Duplicate of #{primary.number}.",
                        close_reason="not planned",
                    )
                )
    return tuple(actions), tuple(rotten)


def sync_quarantine_issues(
    *,
    quarantines: tuple[QuarantineEntry, ...],
    targets: tuple[str, ...] | None,
    failures: tuple[tuple[str, str], ...],
    reports_present: bool,
    now: datetime,
    list_issues: Callable[[], tuple[tuple[OpenQuarantineIssue, ...], bool]],
    apply_action: Callable[[IssueSyncAction], None],
) -> None:
    """Apply the issue plan, then fail the run when any package has rotted.

    Callables are injected so tests do not talk to GitHub. Production passes
    the ``gh`` adapters.
    """
    issues, truncated = list_issues()
    actions, rotten = plan_issue_sync(
        quarantines=quarantines,
        targets=targets,
        failures=failures,
        reports_present=reports_present,
        now=now,
        issues=issues,
        truncated=truncated,
    )
    for action in actions:
        apply_action(action)
    if rotten:
        raise QuarantineRotError(rotten)
