"""Tests for deterministic update self-healing policy helpers."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from lib.update.ci import self_heal


def _classifier_payload(**overrides: object) -> str:
    payload: dict[str, object] = {
        "decision": "retry",
        "campaign_key": "update_flake_lock_action-123",
        "run_id": "123",
        "evidence": ["network timeout while fetching GitHub release asset"],
        "reason": "transient network failure",
        "failed_job_ids": ["456"],
    }
    payload.update(overrides)
    return json.dumps(payload)


def _github_outputs(path: Path) -> dict[str, str]:
    outputs: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, value = line.split("=", 1)
        outputs[key] = value
    return outputs


def test_parse_classifier_json_accepts_retry_decisions() -> None:
    """Retry decisions carry a campaign key, evidence, and failed job ids."""
    parsed = self_heal.parse_classifier_json(_classifier_payload())

    assert parsed.decision is self_heal.Decision.RETRY
    assert parsed.campaign_key == "update_flake_lock_action-123"
    assert parsed.run_id == "123"
    assert parsed.evidence == ("network timeout while fetching GitHub release asset",)
    assert parsed.failed_job_ids == ("456",)
    assert self_heal.retry_selection(parsed) == ("456",)


def test_parse_classifier_json_accepts_auto_fix_decisions() -> None:
    """Auto-fix decisions must name the affected path lane."""
    parsed = self_heal.parse_classifier_json(
        _classifier_payload(
            decision="auto_fix",
            affected_paths=["packages/codex/sources.json"],
            failed_job_ids=None,
            reason="upstream package source drift",
        )
    )

    assert parsed.decision is self_heal.Decision.AUTO_FIX
    assert parsed.affected_paths == ("packages/codex/sources.json",)


def test_parse_classifier_json_accepts_stop_decisions_with_string_evidence() -> None:
    """Stop decisions preserve refusal reason and evidence."""
    parsed = self_heal.parse_classifier_json(
        _classifier_payload(
            decision="stop",
            evidence="failure is in lib/update core",
            failed_job_ids=[],
            reason="updater core failure",
        )
    )

    assert parsed.decision is self_heal.Decision.STOP
    assert parsed.evidence == ("failure is in lib/update core",)


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("[]", "classifier output must be a JSON object"),
        ("{", "classifier output must be valid JSON"),
        (_classifier_payload(decision="maybe"), "decision must be one of"),
        (_classifier_payload(campaign_key=""), "campaign_key must be"),
        (_classifier_payload(evidence=None), "evidence must contain"),
        (_classifier_payload(evidence=[]), "evidence must contain"),
        (
            _classifier_payload(failed_job_ids=[]),
            "retry decisions must include failed_job_ids",
        ),
        (
            _classifier_payload(decision="auto_fix", failed_job_ids=[]),
            "auto_fix decisions must include affected_paths",
        ),
        (
            _classifier_payload(evidence={"bad": "shape"}),
            "evidence must be a string or list",
        ),
    ],
)
def test_parse_classifier_json_rejects_unsafe_shapes(raw: str, message: str) -> None:
    """Bad or ambiguous classifier output fails closed."""
    with pytest.raises(self_heal.SelfHealPolicyError, match=message):
        self_heal.parse_classifier_json(raw)


def test_retry_selection_rejects_non_retry_decisions() -> None:
    """Retry selection is only valid for retry decisions."""
    parsed = self_heal.parse_classifier_json(
        _classifier_payload(
            decision="stop",
            failed_job_ids=[],
            reason="unclear failure",
        )
    )

    with pytest.raises(self_heal.SelfHealPolicyError, match="requires a retry"):
        self_heal.retry_selection(parsed)


def test_classifier_marker_validates_auto_fix_pr_body() -> None:
    """Repair PRs carry a machine-readable auto-fix classifier marker."""
    classifier = self_heal.parse_classifier_json(
        _classifier_payload(
            decision="auto_fix",
            affected_paths=[
                "packages/codex/sources.json",
                "lib/tests/test_codex_package_nix.py",
            ],
            failed_job_ids=[],
            reason="upstream package source drift",
        )
    )
    body = f"Repair details\n\n{self_heal.render_classifier_marker(classifier)}\n"

    parsed = self_heal.parse_classifier_marker_from_text(body)

    assert parsed.decision is self_heal.Decision.AUTO_FIX
    self_heal.validate_auto_fix_classifier(parsed)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            _classifier_payload(
                decision="retry",
                failed_job_ids=["456"],
            ),
            "must be an auto_fix",
        ),
        (
            _classifier_payload(
                decision="auto_fix",
                affected_paths=["lib/update/cli.py"],
                failed_job_ids=[],
            ),
            "outside allowed lanes",
        ),
        (
            _classifier_payload(
                decision="auto_fix",
                affected_paths=["packages/../lib/update/cli.py"],
                failed_job_ids=[],
            ),
            "inside the repository",
        ),
    ],
)
def test_classifier_marker_rejects_unsafe_auto_fix_pr_bodies(
    payload: str,
    message: str,
) -> None:
    """Unsafe repair PR classifier markers fail closed before auto-merge."""
    body = f"{self_heal.CLASSIFIER_MARKER_NAME}:{payload}"
    marker = f"<!-- {body} -->"

    classifier = self_heal.parse_classifier_marker_from_text(marker)

    with pytest.raises(self_heal.SelfHealPolicyError, match=message):
        self_heal.validate_auto_fix_classifier(classifier)


def test_classifier_marker_requires_machine_readable_pr_body_marker() -> None:
    """Missing classifier markers are policy failures."""
    with pytest.raises(self_heal.SelfHealPolicyError, match="missing"):
        self_heal.parse_classifier_marker_from_text("classifier: {}")


def test_ledger_comments_count_campaign_cycles() -> None:
    """Machine-readable ledger comments are the campaign budget source."""
    first = self_heal.render_ledger_comment(
        campaign_key="update_flake_lock_action-123",
        attempt_kind=self_heal.AttemptKind.RETRY,
        run_id="123",
        status="rerun",
        reason="network timeout",
        evidence=["failed job 456"],
    )
    second = self_heal.render_ledger_comment(
        campaign_key="update_flake_lock_action-123",
        attempt_kind=self_heal.AttemptKind.REPAIR,
        run_id="124",
        status="pr-created",
        reason="source drift",
        evidence=["packages/codex/sources.json changed"],
    )
    stopped = self_heal.render_ledger_comment(
        campaign_key="update_flake_lock_action-123",
        attempt_kind=self_heal.AttemptKind.STOP,
        run_id="125",
        status="stopped",
        reason="workflow logic failure",
        evidence=[".github/workflows/update.yml failed"],
    )
    comments = json.dumps({
        "comments": [
            {"body": first},
            {"body": "<!-- nixcfg-self-heal:not-json --> ignored"},
            {"body": second},
            {"body": stopped},
            {"body": None},
            {"body": '<!-- nixcfg-self-heal:{"attempt_kind":"bad"} -->'},
        ]
    })

    events = self_heal.parse_ledger_events_from_comments_json(comments)

    assert [event.attempt_kind for event in events] == [
        self_heal.AttemptKind.RETRY,
        self_heal.AttemptKind.REPAIR,
        self_heal.AttemptKind.STOP,
    ]
    assert (
        self_heal.cycles_used(events, campaign_key="update_flake_lock_action-123") == 2
    )
    assert (
        self_heal.remaining_cycles(events, campaign_key="update_flake_lock_action-123")
        == 1
    )
    self_heal.require_cycle_budget(events, campaign_key="update_flake_lock_action-123")


def test_ledger_budget_exhaustion_fails_closed() -> None:
    """The fourth automatic action in a campaign is rejected."""
    body = "\n".join(
        self_heal.render_ledger_comment(
            campaign_key="update_flake_lock_action-123",
            attempt_kind=self_heal.AttemptKind.RETRY,
            run_id=str(run_id),
            status="rerun",
            reason="transient",
            evidence=[f"job {run_id}"],
        )
        for run_id in range(3)
    )
    events = self_heal.parse_ledger_events_from_text(body)

    assert (
        self_heal.remaining_cycles(events, campaign_key="update_flake_lock_action-123")
        == 0
    )
    with pytest.raises(self_heal.SelfHealPolicyError, match="exhausted"):
        self_heal.require_cycle_budget(
            events, campaign_key="update_flake_lock_action-123"
        )


@pytest.mark.parametrize(
    ("raw", "message"),
    [
        ("{", "comments JSON must be valid JSON"),
        (json.dumps({"comments": "nope"}), "comments JSON must be a list"),
    ],
)
def test_parse_ledger_events_from_comments_json_rejects_bad_json(
    raw: str,
    message: str,
) -> None:
    """Malformed issue comment payloads fail before policy decisions."""
    with pytest.raises(self_heal.SelfHealPolicyError, match=message):
        self_heal.parse_ledger_events_from_comments_json(raw)


def test_required_checks_present_is_fail_closed() -> None:
    """Auto-merge needs branch protection with at least one required check."""
    assert not self_heal.required_checks_present(None)
    assert not self_heal.required_checks_present({})
    assert not self_heal.required_checks_present({"contexts": [], "checks": []})
    assert self_heal.required_checks_present({"contexts": ["ci"]})
    assert self_heal.required_checks_present({"checks": [{"context": "quality"}]})


def _pull_request_event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "action": "closed",
        "pull_request": {
            "merged": True,
            "base": {"ref": "main"},
            "head": {"ref": "agentic/update-self-heal/update_flake_lock_action-123"},
            "labels": [{"name": self_heal.REPAIR_LABEL}],
        },
    }
    event.update(overrides)
    return event


def test_repair_pr_event_gates_post_merge_dispatch() -> None:
    """Only merged labeled repair PRs dispatch the update workflow."""
    event = self_heal.repair_pr_event_from_github(_pull_request_event())

    assert self_heal.is_agentic_repair_pr(event)
    assert self_heal.should_dispatch_update_after_merge(event)


@pytest.mark.parametrize(
    "event_payload",
    [
        _pull_request_event(action="opened"),
        _pull_request_event(
            pull_request={
                "merged": True,
                "base": {"ref": "main"},
                "head": {"ref": "feature/manual"},
                "labels": [{"name": self_heal.REPAIR_LABEL}],
            }
        ),
        _pull_request_event(
            pull_request={
                "merged": True,
                "base": {"ref": "main"},
                "head": {
                    "ref": "agentic/update-self-heal/update_flake_lock_action-123"
                },
                "labels": [{"name": "manual"}],
            }
        ),
        _pull_request_event(
            pull_request={
                "merged": False,
                "base": {"ref": "main"},
                "head": {
                    "ref": "agentic/update-self-heal/update_flake_lock_action-123"
                },
                "labels": [{"name": self_heal.REPAIR_LABEL}],
            }
        ),
    ],
)
def test_repair_pr_event_rejects_non_repair_or_unmerged_prs(
    event_payload: dict[str, object],
) -> None:
    """The companion workflow ignores unrelated or still-open PRs."""
    event = self_heal.repair_pr_event_from_github(event_payload)

    assert not self_heal.should_dispatch_update_after_merge(event)


def test_repair_pr_event_rejects_missing_shapes() -> None:
    """Missing PR event fields are policy errors."""
    with pytest.raises(self_heal.SelfHealPolicyError, match="pull_request"):
        self_heal.repair_pr_event_from_github({"action": "closed"})


def test_repair_pr_event_treats_non_list_labels_as_empty() -> None:
    """Malformed label payloads cannot make a PR repair-eligible."""
    event = self_heal.repair_pr_event_from_github(
        _pull_request_event(
            pull_request={
                "merged": True,
                "base": {"ref": "main"},
                "head": {
                    "ref": "agentic/update-self-heal/update_flake_lock_action-123"
                },
                "labels": "agentic-update-repair",
            }
        )
    )

    assert event.labels == ()
    assert not self_heal.is_agentic_repair_pr(event)


def test_cli_parse_classifier_outputs_normalized_json(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The helper CLI can normalize a classifier JSON file for workflows."""
    classifier = tmp_path / "classifier.json"
    classifier.write_text(_classifier_payload(), encoding="utf-8")

    assert self_heal.main(["parse-classifier", str(classifier)]) == 0

    output = json.loads(capsys.readouterr().out)
    assert output["decision"] == "retry"
    assert output["failed_job_ids"] == ["456"]


def test_cli_render_classifier_marker_allows_non_repair_decisions_without_gate(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Marker rendering can serialize retry decisions unless the repair gate is required."""
    classifier = tmp_path / "classifier.json"
    classifier.write_text(_classifier_payload(), encoding="utf-8")

    assert self_heal.main(["render-classifier-marker", str(classifier)]) == 0

    parsed = self_heal.parse_classifier_marker_from_text(capsys.readouterr().out)
    assert parsed.decision is self_heal.Decision.RETRY


def test_cli_render_classifier_marker_requires_auto_fix(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The marker CLI can require a repair-eligible classifier."""
    classifier = tmp_path / "classifier.json"
    classifier.write_text(
        _classifier_payload(
            decision="auto_fix",
            affected_paths=["overlays/opencode/sources.json"],
            failed_job_ids=[],
        ),
        encoding="utf-8",
    )

    assert (
        self_heal.main([
            "render-classifier-marker",
            str(classifier),
            "--require-auto-fix",
        ])
        == 0
    )

    assert self_heal.CLASSIFIER_MARKER_NAME in capsys.readouterr().out


def test_cli_verify_auto_fix_classifier_rejects_invalid_pr_body(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The PR companion gate fails closed on invalid classifier markers."""
    classifier = self_heal.parse_classifier_json(
        _classifier_payload(
            decision="auto_fix",
            affected_paths=["home/george/default.nix"],
            failed_job_ids=[],
        )
    )
    body = tmp_path / "body.md"
    body.write_text(self_heal.render_classifier_marker(classifier), encoding="utf-8")

    assert (
        self_heal.main([
            "verify-auto-fix-classifier",
            "--body-path",
            str(body),
        ])
        == 2
    )

    assert "outside allowed lanes" in capsys.readouterr().err


def test_cli_verify_auto_fix_classifier_accepts_valid_pr_body(tmp_path: Path) -> None:
    """The PR companion gate accepts repair markers confined to allowed lanes."""
    classifier = self_heal.parse_classifier_json(
        _classifier_payload(
            decision="auto_fix",
            affected_paths=["packages/codex/sources.json"],
            failed_job_ids=[],
        )
    )
    body = tmp_path / "body.md"
    body.write_text(self_heal.render_classifier_marker(classifier), encoding="utf-8")

    assert (
        self_heal.main([
            "verify-auto-fix-classifier",
            "--body-path",
            str(body),
        ])
        == 0
    )


def test_cli_remaining_cycles_returns_nonzero_when_exhausted(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The budget CLI returns 2 after the campaign cap is spent."""
    comments = tmp_path / "comments.json"
    body = "\n".join(
        self_heal.render_ledger_comment(
            campaign_key="campaign",
            attempt_kind=self_heal.AttemptKind.REPAIR,
            run_id=str(index),
            status="pr-created",
            reason="drift",
            evidence=["path"],
        )
        for index in range(self_heal.CAMPAIGN_MAX_CYCLES)
    )
    comments.write_text(json.dumps([body]), encoding="utf-8")

    assert (
        self_heal.main([
            "remaining-cycles",
            "--comments-json",
            str(comments),
            "--campaign-key",
            "campaign",
        ])
        == 2
    )

    assert capsys.readouterr().out.strip() == "0"


def test_cli_render_ledger_comment(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The ledger comment CLI renders the machine-readable marker."""
    assert (
        self_heal.main([
            "render-ledger-comment",
            "--campaign-key",
            "campaign",
            "--attempt-kind",
            "retry",
            "--run-id",
            "123",
            "--status",
            "rerun",
            "--reason",
            "network timeout",
            "--evidence",
            "job 456",
        ])
        == 0
    )

    output = capsys.readouterr().out
    assert "<!-- nixcfg-self-heal:" in output
    assert "job 456" in output


def test_cli_pr_event_outputs_writes_github_output(tmp_path: Path) -> None:
    """The PR event CLI emits deterministic job outputs."""
    event_path = tmp_path / "event.json"
    output_path = tmp_path / "github-output"
    event_path.write_text(json.dumps(_pull_request_event()), encoding="utf-8")

    assert (
        self_heal.main([
            "pr-event-outputs",
            "--event-path",
            str(event_path),
            "--github-output",
            str(output_path),
        ])
        == 0
    )

    assert _github_outputs(output_path)["should_dispatch_update"] == "true"


def test_cli_pr_event_outputs_prints_json_without_github_output(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The PR event CLI can emit JSON directly for local diagnostics."""
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_pull_request_event()), encoding="utf-8")

    assert (
        self_heal.main([
            "pr-event-outputs",
            "--event-path",
            str(event_path),
        ])
        == 0
    )

    outputs = json.loads(capsys.readouterr().out)
    assert outputs["is_repair_pr"] == "true"
    assert outputs["base_ref"] == "main"


def test_cli_required_checks_present(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The branch-protection CLI returns 2 when checks are absent."""
    protection = tmp_path / "protection.json"
    protection.write_text(json.dumps({"contexts": []}), encoding="utf-8")

    assert (
        self_heal.main([
            "required-checks-present",
            "--protection-json",
            str(protection),
        ])
        == 2
    )

    assert "no required checks" in capsys.readouterr().err


def test_cli_required_checks_present_accepts_branch_protection(
    tmp_path: Path,
) -> None:
    """The branch-protection CLI succeeds when required checks are configured."""
    protection = tmp_path / "protection.json"
    protection.write_text(json.dumps({"contexts": ["CI"]}), encoding="utf-8")

    assert (
        self_heal.main([
            "required-checks-present",
            "--protection-json",
            str(protection),
        ])
        == 0
    )


def test_cli_errors_return_policy_failure(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """CLI policy exceptions are normalized to exit code 2."""
    classifier = tmp_path / "classifier.json"
    classifier.write_text(_classifier_payload(decision="bad"), encoding="utf-8")

    assert self_heal.main(["parse-classifier", str(classifier)]) == 2

    assert "decision must be one of" in capsys.readouterr().err


def test_module_entrypoint_exits_with_main_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The module script entrypoint delegates to the CLI main function."""
    classifier = tmp_path / "classifier.json"
    classifier.write_text(_classifier_payload(), encoding="utf-8")
    module_path = Path(self_heal.__file__)
    monkeypatch.setattr(
        sys,
        "argv",
        ["self_heal", "parse-classifier", str(classifier)],
    )

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(module_path), run_name="__main__")

    assert exc_info.value.code == 0
