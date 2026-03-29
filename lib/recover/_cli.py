"""Shared CLI output helpers for recover commands."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import typer

if TYPE_CHECKING:
    from collections.abc import Mapping


def emit_error(*, json_output: bool, message: str) -> int:
    """Render a recover-command error and return its exit code."""
    if json_output:
        typer.echo(json.dumps({"success": False, "error": message}))
    else:
        typer.echo(f"Error: {message}", err=True)
    return 1


def emit_success(
    *, json_output: bool, payload: Mapping[str, object], plain: str
) -> int:
    """Render a recover-command success payload and return its exit code."""
    if json_output:
        typer.echo(json.dumps(payload))
    else:
        typer.echo(plain)
    return 0


def require_apply_for_stage(
    *, apply: bool, json_output: bool, stage: bool
) -> int | None:
    """Reject ``--stage`` without ``--apply`` when requested."""
    if not stage or apply:
        return None
    return emit_error(json_output=json_output, message="--stage requires --apply")


__all__ = ["emit_error", "emit_success", "require_apply_for_stage"]
