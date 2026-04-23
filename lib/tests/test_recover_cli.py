"""Tests for shared recover CLI helpers."""

from __future__ import annotations

import json

import pytest

from lib.recover import _cli as rc


def test_emit_error_supports_plain_and_json_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render recover errors in both supported output formats."""
    assert rc.emit_error(json_output=False, message="broken") == 1
    assert capsys.readouterr().err == "Error: broken\n"

    assert rc.emit_error(json_output=True, message="broken") == 1
    assert json.loads(capsys.readouterr().out) == {
        "success": False,
        "error": "broken",
    }


def test_emit_success_supports_plain_and_json_output(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Render recover successes in both supported output formats."""
    payload = {"success": True, "plan": {"snapshot": "/nix/store/source"}}

    assert rc.emit_success(json_output=False, payload=payload, plain="done") == 0
    assert capsys.readouterr().out == "done\n"

    assert rc.emit_success(json_output=True, payload=payload, plain="ignored") == 0
    assert json.loads(capsys.readouterr().out) == payload


def test_require_apply_for_stage_handles_valid_and_invalid_requests(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Accept valid flag combinations and reject staging-only requests."""
    assert (
        rc.require_apply_for_stage(apply=False, json_output=False, stage=False) is None
    )
    assert rc.require_apply_for_stage(apply=True, json_output=False, stage=True) is None

    assert rc.require_apply_for_stage(apply=False, json_output=False, stage=True) == 1
    assert capsys.readouterr().err == "Error: --stage requires --apply\n"
