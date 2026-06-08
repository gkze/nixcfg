"""Deterministic policy helpers for update self-healing workflows."""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


CAMPAIGN_MAX_CYCLES: Final = 3
UPDATE_BRANCH: Final = "update_flake_lock_action"
REPAIR_BRANCH_PREFIX: Final = "agentic/update-self-heal/"
REPAIR_LABEL: Final = "agentic-update-repair"
LEDGER_ISSUE_TITLE: Final = "Agentic update self-healing ledger"
LEDGER_MARKER_NAME: Final = "nixcfg-self-heal"
LEDGER_MARKER_RE: Final = re.compile(
    r"<!--\s*nixcfg-self-heal:(?P<payload>\{.*?\})\s*-->",
    re.DOTALL,
)
CLASSIFIER_MARKER_NAME: Final = "nixcfg-self-heal-classifier"
CLASSIFIER_MARKER_RE: Final = re.compile(
    r"<!--\s*nixcfg-self-heal-classifier:(?P<payload>\{.*?\})\s*-->",
    re.DOTALL,
)
AUTO_FIX_ALLOWED_PATH_PREFIXES: Final = (
    "packages/",
    "overlays/",
    "lib/tests/",
    "tests/",
    "docs/",
    "misc/",
)
# Keep this list aligned with the PR CI surface; the workflow structure tests compare
# it against .github/workflows/ci.yml so branch-protection drift fails locally.
EXPECTED_REPAIR_PR_REQUIRED_CHECKS: Final = (
    "commitlint",
    "format-repo",
    "lint-editorconfig",
    "format-yaml-yamlfmt",
    "lint-yaml-yamllint",
    "format-web-oxfmt",
    "lint-web-oxlint",
    "format-python-pyupgrade",
    "format-python-ruff",
    "lint-python-compile",
    "lint-python-ruff",
    "lint-python-ty",
    "lint-workflows-actionlint",
    "test-nix-default-api",
    "test-nix-package-helpers",
    "test-nix-opencode-desktop",
    "cache-electron-runtimes",
    "test-python-pytest",
    "verify-workflow-artifacts-refresh",
    "verify-workflow-artifacts-certify",
    "verify-workflow-structure-refresh",
    "verify-workflow-structure-certify",
    "lint-pins-pinact",
    "verify-crate2nix",
)


class SelfHealPolicyError(ValueError):
    """Raised when self-healing policy data is invalid or unsafe."""


LEDGER_EVENT_PARSE_ERRORS: Final = (
    json.JSONDecodeError,
    SelfHealPolicyError,
    ValueError,
)


class Decision(StrEnum):
    """Allowed LLM classifier decisions."""

    RETRY = "retry"
    AUTO_FIX = "auto_fix"
    STOP = "stop"


class AttemptKind(StrEnum):
    """Machine-readable ledger action kinds."""

    RETRY = "retry"
    REPAIR = "repair"
    STOP = "stop"


@dataclass(frozen=True, kw_only=True)
class ClassifierDecision:
    """Validated classifier decision emitted by the LLM stage."""

    decision: Decision
    campaign_key: str
    run_id: str
    evidence: tuple[str, ...]
    reason: str
    failed_job_ids: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()


@dataclass(frozen=True, kw_only=True)
class LedgerEvent:
    """One machine-readable campaign entry from the evergreen issue ledger."""

    campaign_key: str
    attempt_kind: AttemptKind
    run_id: str
    status: str


@dataclass(frozen=True, kw_only=True)
class RepairPullRequestEvent:
    """Normalized pull request event facts used by deterministic GHA gates."""

    action: str
    merged: bool
    base_ref: str
    head_ref: str
    labels: tuple[str, ...]


def _json_object(value: object, *, context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        msg = f"{context} must be a JSON object"
        raise SelfHealPolicyError(msg)
    return {str(key): item for key, item in value.items()}


def _required_string(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        msg = f"{field} must be a non-empty string"
        raise SelfHealPolicyError(msg)
    return value.strip()


def _string_tuple(
    value: object, *, field: str, required: bool = False
) -> tuple[str, ...]:
    if value is None:
        if required:
            msg = f"{field} must contain at least one item"
            raise SelfHealPolicyError(msg)
        return ()
    if isinstance(value, str):
        items = (value.strip(),) if value.strip() else ()
    elif isinstance(value, list | tuple):
        items = tuple(
            item.strip() for item in value if isinstance(item, str) and item.strip()
        )
    else:
        msg = f"{field} must be a string or list of strings"
        raise SelfHealPolicyError(msg)
    if required and not items:
        msg = f"{field} must contain at least one item"
        raise SelfHealPolicyError(msg)
    return items


def parse_classifier_json(raw: str) -> ClassifierDecision:
    """Parse and validate LLM classifier JSON."""
    try:
        payload = _json_object(json.loads(raw), context="classifier output")
    except json.JSONDecodeError as exc:
        msg = "classifier output must be valid JSON"
        raise SelfHealPolicyError(msg) from exc

    try:
        decision = Decision(_required_string(payload.get("decision"), field="decision"))
    except ValueError as exc:
        msg = "decision must be one of: retry, auto_fix, stop"
        raise SelfHealPolicyError(msg) from exc

    classifier = ClassifierDecision(
        decision=decision,
        campaign_key=_required_string(
            payload.get("campaign_key"), field="campaign_key"
        ),
        run_id=_required_string(payload.get("run_id"), field="run_id"),
        evidence=_string_tuple(
            payload.get("evidence"), field="evidence", required=True
        ),
        reason=_required_string(payload.get("reason"), field="reason"),
        failed_job_ids=_string_tuple(
            payload.get("failed_job_ids", payload.get("job_ids")),
            field="failed_job_ids",
        ),
        affected_paths=_string_tuple(
            payload.get("affected_paths"), field="affected_paths"
        ),
    )
    _validate_decision_shape(classifier)
    return classifier


def render_classifier_marker(classifier: ClassifierDecision) -> str:
    """Render the machine-readable PR marker for a validated classifier decision."""
    marker = json.dumps(
        {
            "decision": classifier.decision.value,
            "campaign_key": classifier.campaign_key,
            "run_id": classifier.run_id,
            "evidence": list(classifier.evidence),
            "reason": classifier.reason,
            "failed_job_ids": list(classifier.failed_job_ids),
            "affected_paths": list(classifier.affected_paths),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"<!-- {CLASSIFIER_MARKER_NAME}:{marker} -->"


def parse_classifier_marker_from_text(text: str) -> ClassifierDecision:
    """Extract and validate the last classifier marker from PR body text."""
    matches = tuple(CLASSIFIER_MARKER_RE.finditer(text))
    if not matches:
        msg = f"missing {CLASSIFIER_MARKER_NAME} marker"
        raise SelfHealPolicyError(msg)
    return parse_classifier_json(matches[-1].group("payload"))


def _validate_auto_fix_path(path: str) -> None:
    if path.startswith("/") or ".." in Path(path).parts:
        msg = f"auto_fix path {path!r} must stay inside the repository"
        raise SelfHealPolicyError(msg)
    if not path.startswith(AUTO_FIX_ALLOWED_PATH_PREFIXES):
        allowed = ", ".join(AUTO_FIX_ALLOWED_PATH_PREFIXES)
        msg = f"auto_fix path {path!r} is outside allowed lanes: {allowed}"
        raise SelfHealPolicyError(msg)


def validate_auto_fix_paths(paths: Sequence[str]) -> None:
    """Fail closed unless actual repair PR paths stay in allowed lanes."""
    clean_paths = tuple(path.strip() for path in paths if path.strip())
    if not clean_paths:
        msg = "repair PR must change at least one path"
        raise SelfHealPolicyError(msg)
    for path in clean_paths:
        _validate_auto_fix_path(path)


def validate_auto_fix_classifier(classifier: ClassifierDecision) -> None:
    """Fail closed unless a classifier decision is eligible for repair auto-merge."""
    if classifier.decision is not Decision.AUTO_FIX:
        msg = "repair PR classifier marker must be an auto_fix decision"
        raise SelfHealPolicyError(msg)
    validate_auto_fix_paths(classifier.affected_paths)


def _validate_decision_shape(classifier: ClassifierDecision) -> None:
    if classifier.decision is Decision.RETRY and not classifier.failed_job_ids:
        msg = "retry decisions must include failed_job_ids"
        raise SelfHealPolicyError(msg)
    if classifier.decision is Decision.AUTO_FIX and not classifier.affected_paths:
        msg = "auto_fix decisions must include affected_paths"
        raise SelfHealPolicyError(msg)


def retry_selection(classifier: ClassifierDecision) -> tuple[str, ...]:
    """Return failed job ids for a retry decision."""
    if classifier.decision is not Decision.RETRY:
        msg = "retry selection requires a retry decision"
        raise SelfHealPolicyError(msg)
    return classifier.failed_job_ids


def ledger_marker_payload(
    *,
    campaign_key: str,
    attempt_kind: AttemptKind,
    run_id: str,
    status: str,
) -> dict[str, str]:
    """Create the stable machine-readable ledger payload."""
    return {
        "campaign_key": _required_string(campaign_key, field="campaign_key"),
        "attempt_kind": attempt_kind.value,
        "run_id": _required_string(run_id, field="run_id"),
        "status": _required_string(status, field="status"),
    }


def render_ledger_comment(
    *,
    campaign_key: str,
    attempt_kind: AttemptKind,
    run_id: str,
    status: str,
    reason: str,
    evidence: Sequence[str],
) -> str:
    """Render a ledger issue comment with a machine-readable marker."""
    payload = ledger_marker_payload(
        campaign_key=campaign_key,
        attempt_kind=attempt_kind,
        run_id=run_id,
        status=status,
    )
    marker = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    evidence_lines = "\n".join(f"- {item}" for item in evidence)
    return (
        f"<!-- {LEDGER_MARKER_NAME}:{marker} -->\n"
        f"### {attempt_kind.value}: {status}\n\n"
        f"Campaign: `{campaign_key}`\n\n"
        f"Run: `{run_id}`\n\n"
        f"Reason: {reason}\n\n"
        f"Evidence:\n{evidence_lines}\n"
    )


def parse_ledger_events_from_text(text: str) -> tuple[LedgerEvent, ...]:
    """Extract cycle-counting ledger events from issue comment text."""
    events: list[LedgerEvent] = []
    for match in LEDGER_MARKER_RE.finditer(text):
        try:
            payload = _json_object(
                json.loads(match.group("payload")), context="ledger marker"
            )
            events.append(
                LedgerEvent(
                    campaign_key=_required_string(
                        payload.get("campaign_key"),
                        field="campaign_key",
                    ),
                    attempt_kind=AttemptKind(
                        _required_string(
                            payload.get("attempt_kind"), field="attempt_kind"
                        )
                    ),
                    run_id=_required_string(payload.get("run_id"), field="run_id"),
                    status=_required_string(payload.get("status"), field="status"),
                )
            )
        except LEDGER_EVENT_PARSE_ERRORS:
            continue
    return tuple(events)


def parse_ledger_events_from_comments_json(raw: str) -> tuple[LedgerEvent, ...]:
    """Extract ledger events from GitHub issue comment JSON."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        msg = "comments JSON must be valid JSON"
        raise SelfHealPolicyError(msg) from exc
    comments = payload.get("comments", []) if isinstance(payload, dict) else payload
    if not isinstance(comments, list):
        msg = "comments JSON must be a list or contain comments"
        raise SelfHealPolicyError(msg)
    bodies: list[str] = []
    for comment in comments:
        if isinstance(comment, str):
            bodies.append(comment)
        elif isinstance(comment, dict) and isinstance(comment.get("body"), str):
            bodies.append(comment["body"])
    return parse_ledger_events_from_text("\n".join(bodies))


def cycles_used(events: Sequence[LedgerEvent], *, campaign_key: str) -> int:
    """Return automatic retry/repair cycles already consumed by a campaign."""
    return sum(
        1
        for event in events
        if event.campaign_key == campaign_key
        and event.attempt_kind in {AttemptKind.RETRY, AttemptKind.REPAIR}
    )


def remaining_cycles(
    events: Sequence[LedgerEvent],
    *,
    campaign_key: str,
    max_cycles: int = CAMPAIGN_MAX_CYCLES,
) -> int:
    """Return remaining automatic cycles for the campaign."""
    return max(0, max_cycles - cycles_used(events, campaign_key=campaign_key))


def require_cycle_budget(
    events: Sequence[LedgerEvent],
    *,
    campaign_key: str,
    max_cycles: int = CAMPAIGN_MAX_CYCLES,
) -> None:
    """Fail closed when the campaign has no automatic cycles left."""
    if remaining_cycles(events, campaign_key=campaign_key, max_cycles=max_cycles) <= 0:
        msg = f"campaign {campaign_key!r} exhausted its {max_cycles} automatic cycles"
        raise SelfHealPolicyError(msg)


def required_check_contexts(
    protection_payload: Mapping[str, Any] | None,
) -> frozenset[str]:
    """Return configured required status/check contexts from branch protection."""
    if not protection_payload:
        return frozenset()
    contexts: set[str] = set()
    raw_contexts = protection_payload.get("contexts")
    if isinstance(raw_contexts, list):
        contexts.update(
            item.strip()
            for item in raw_contexts
            if isinstance(item, str) and item.strip()
        )
    raw_checks = protection_payload.get("checks")
    if isinstance(raw_checks, list):
        for check in raw_checks:
            if isinstance(check, str) and check.strip():
                contexts.add(check.strip())
            elif isinstance(check, dict):
                context = check.get("context")
                if isinstance(context, str) and context.strip():
                    contexts.add(context.strip())
    return frozenset(contexts)


def missing_required_checks(
    protection_payload: Mapping[str, Any] | None,
    *,
    expected: Sequence[str] = EXPECTED_REPAIR_PR_REQUIRED_CHECKS,
) -> tuple[str, ...]:
    """Return expected CI gates absent from branch protection."""
    contexts = required_check_contexts(protection_payload)
    return tuple(check for check in expected if check not in contexts)


def required_checks_present(protection_payload: Mapping[str, Any] | None) -> bool:
    """Return whether branch protection requires every expected repair PR gate."""
    return not missing_required_checks(protection_payload)


def repair_pr_event_from_github(payload: Mapping[str, Any]) -> RepairPullRequestEvent:
    """Normalize a GitHub pull_request event payload."""
    pr = _json_object(payload.get("pull_request"), context="pull_request")
    base = _json_object(pr.get("base"), context="pull_request.base")
    head = _json_object(pr.get("head"), context="pull_request.head")
    labels = pr.get("labels", [])
    if not isinstance(labels, list):
        labels = []
    label_names = tuple(
        label["name"]
        for label in labels
        if isinstance(label, dict) and isinstance(label.get("name"), str)
    )
    return RepairPullRequestEvent(
        action=_required_string(payload.get("action"), field="action"),
        merged=bool(pr.get("merged")),
        base_ref=_required_string(base.get("ref"), field="pull_request.base.ref"),
        head_ref=_required_string(head.get("ref"), field="pull_request.head.ref"),
        labels=label_names,
    )


def is_agentic_repair_pr(event: RepairPullRequestEvent) -> bool:
    """Return whether a PR is eligible for deterministic repair handling."""
    return (
        event.base_ref == "main"
        and event.head_ref.startswith(REPAIR_BRANCH_PREFIX)
        and REPAIR_LABEL in event.labels
    )


def should_dispatch_update_after_merge(event: RepairPullRequestEvent) -> bool:
    """Return whether update.yml should be dispatched after a repair PR merge."""
    return event.action == "closed" and event.merged and is_agentic_repair_pr(event)


def _write_outputs(outputs: Mapping[str, str], output_path: Path) -> None:
    with output_path.open("a", encoding="utf-8") as handle:
        for key, value in outputs.items():
            handle.write(f"{key}={value}\n")


def _cmd_remaining_cycles(args: argparse.Namespace) -> int:
    raw = Path(args.comments_json).read_text(encoding="utf-8")
    events = parse_ledger_events_from_comments_json(raw)
    remaining = remaining_cycles(events, campaign_key=args.campaign_key)
    sys.stdout.write(f"{remaining}\n")
    return 0 if remaining > 0 else 2


def _cmd_render_ledger_comment(args: argparse.Namespace) -> int:
    evidence = args.evidence or ()
    sys.stdout.write(
        render_ledger_comment(
            campaign_key=args.campaign_key,
            attempt_kind=AttemptKind(args.attempt_kind),
            run_id=args.run_id,
            status=args.status,
            reason=args.reason,
            evidence=evidence,
        )
    )
    return 0


def _cmd_pr_event_outputs(args: argparse.Namespace) -> int:
    payload = _json_object(
        json.loads(Path(args.event_path).read_text(encoding="utf-8")),
        context="event payload",
    )
    event = repair_pr_event_from_github(payload)
    outputs = {
        "is_repair_pr": str(is_agentic_repair_pr(event)).lower(),
        "should_dispatch_update": str(
            should_dispatch_update_after_merge(event)
        ).lower(),
        "head_ref": event.head_ref,
        "base_ref": event.base_ref,
    }
    if args.github_output:
        _write_outputs(outputs, Path(args.github_output))
    else:
        sys.stdout.write(f"{json.dumps(outputs, sort_keys=True)}\n")
    return 0


def _cmd_required_checks_present(args: argparse.Namespace) -> int:
    payload = _json_object(
        json.loads(Path(args.protection_json).read_text(encoding="utf-8")),
        context="branch protection payload",
    )
    missing = missing_required_checks(payload)
    if not missing:
        return 0
    sys.stderr.write(
        f"branch protection is missing required repair checks: {', '.join(missing)}\n"
    )
    return 2


def _cmd_validate_auto_fix_paths(args: argparse.Namespace) -> int:
    paths = tuple(
        line.strip()
        for line in Path(args.paths_file).read_text(encoding="utf-8").splitlines()
    )
    validate_auto_fix_paths(paths)
    return 0


def _cmd_parse_classifier(args: argparse.Namespace) -> int:
    classifier = parse_classifier_json(Path(args.path).read_text(encoding="utf-8"))
    rendered = json.dumps(
        {
            "decision": classifier.decision.value,
            "campaign_key": classifier.campaign_key,
            "run_id": classifier.run_id,
            "evidence": list(classifier.evidence),
            "reason": classifier.reason,
            "failed_job_ids": list(classifier.failed_job_ids),
            "affected_paths": list(classifier.affected_paths),
        },
        sort_keys=True,
    )
    sys.stdout.write(f"{rendered}\n")
    return 0


def _cmd_render_classifier_marker(args: argparse.Namespace) -> int:
    classifier = parse_classifier_json(Path(args.path).read_text(encoding="utf-8"))
    if args.require_auto_fix:
        validate_auto_fix_classifier(classifier)
    sys.stdout.write(f"{render_classifier_marker(classifier)}\n")
    return 0


def _cmd_verify_auto_fix_classifier(args: argparse.Namespace) -> int:
    classifier = parse_classifier_marker_from_text(
        Path(args.body_path).read_text(encoding="utf-8")
    )
    validate_auto_fix_classifier(classifier)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    remaining = subparsers.add_parser("remaining-cycles")
    remaining.add_argument("--comments-json", required=True)
    remaining.add_argument("--campaign-key", required=True)
    remaining.set_defaults(func=_cmd_remaining_cycles)

    comment = subparsers.add_parser("render-ledger-comment")
    comment.add_argument("--campaign-key", required=True)
    comment.add_argument(
        "--attempt-kind",
        choices=[kind.value for kind in AttemptKind],
        required=True,
    )
    comment.add_argument("--run-id", required=True)
    comment.add_argument("--status", required=True)
    comment.add_argument("--reason", required=True)
    comment.add_argument("--evidence", action="append")
    comment.set_defaults(func=_cmd_render_ledger_comment)

    pr_event = subparsers.add_parser("pr-event-outputs")
    pr_event.add_argument("--event-path", required=True)
    pr_event.add_argument("--github-output")
    pr_event.set_defaults(func=_cmd_pr_event_outputs)

    checks = subparsers.add_parser("required-checks-present")
    checks.add_argument("--protection-json", required=True)
    checks.set_defaults(func=_cmd_required_checks_present)

    paths = subparsers.add_parser("validate-auto-fix-paths")
    paths.add_argument("--paths-file", required=True)
    paths.set_defaults(func=_cmd_validate_auto_fix_paths)

    classifier = subparsers.add_parser("parse-classifier")
    classifier.add_argument("path")
    classifier.set_defaults(func=_cmd_parse_classifier)

    marker = subparsers.add_parser("render-classifier-marker")
    marker.add_argument("path")
    marker.add_argument("--require-auto-fix", action="store_true")
    marker.set_defaults(func=_cmd_render_classifier_marker)

    repair_classifier = subparsers.add_parser("verify-auto-fix-classifier")
    repair_classifier.add_argument("--body-path", required=True)
    repair_classifier.set_defaults(func=_cmd_verify_auto_fix_classifier)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the self-healing helper CLI."""
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (SelfHealPolicyError, json.JSONDecodeError, ValueError) as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "AUTO_FIX_ALLOWED_PATH_PREFIXES",
    "CAMPAIGN_MAX_CYCLES",
    "CLASSIFIER_MARKER_NAME",
    "EXPECTED_REPAIR_PR_REQUIRED_CHECKS",
    "LEDGER_ISSUE_TITLE",
    "REPAIR_BRANCH_PREFIX",
    "REPAIR_LABEL",
    "AttemptKind",
    "ClassifierDecision",
    "Decision",
    "LedgerEvent",
    "RepairPullRequestEvent",
    "SelfHealPolicyError",
    "cycles_used",
    "is_agentic_repair_pr",
    "ledger_marker_payload",
    "main",
    "missing_required_checks",
    "parse_classifier_json",
    "parse_classifier_marker_from_text",
    "parse_ledger_events_from_comments_json",
    "parse_ledger_events_from_text",
    "remaining_cycles",
    "render_classifier_marker",
    "render_ledger_comment",
    "repair_pr_event_from_github",
    "require_cycle_budget",
    "required_check_contexts",
    "required_checks_present",
    "retry_selection",
    "should_dispatch_update_after_merge",
    "validate_auto_fix_classifier",
    "validate_auto_fix_paths",
]
