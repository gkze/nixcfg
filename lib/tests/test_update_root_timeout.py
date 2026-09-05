"""CLI and configuration contracts for root-validation subprocess bounds."""

import json
import subprocess
from functools import partial
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lib.tests._run_updates_helpers import configure_isolated_run, make_run_plan
from lib.tests._update_workspace_helpers import init_update_workspace_repo
from lib.update.cli import UpdateSummary, app
from lib.update.config import resolve_active_config, resolve_config
from lib.update.derivation_validation import (
    ROOT_CLOSURE_VALIDATION_TIMEOUT_SECONDS,
    validate_root_closures,
)


@pytest.mark.parametrize(
    ("cli_timeout", "env_timeout", "expected_timeout"),
    [
        (None, None, ROOT_CLOSURE_VALIDATION_TIMEOUT_SECONDS),
        (42, None, 42),
        (None, "43", 43),
        (42, "43", 42),
        (2400, None, 2400),
        (None, "2400", 2400),
    ],
)
def test_cli_root_validation_honors_explicit_subprocess_bounds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    cli_timeout: int | None,
    env_timeout: str | None,
    expected_timeout: int,
) -> None:
    """Carry CLI/env bounds to manifest and build subprocesses during an update."""
    monkeypatch.delenv("UPDATE_SUBPROCESS_TIMEOUT", raising=False)
    if env_timeout is not None:
        monkeypatch.setenv("UPDATE_SUBPROCESS_TIMEOUT", env_timeout)
    live = tmp_path / "live"
    init_update_workspace_repo(live)

    async def _execute_result(*_args: object) -> SimpleNamespace:
        candidate = Path.cwd() / "tracked.txt"
        candidate.write_text("candidate\n", encoding="utf-8")
        return SimpleNamespace(
            summary=UpdateSummary(updated=["demo"]),
            candidate_updates=("demo",),
            had_errors=False,
            written_paths=(candidate,),
        )

    configure_isolated_run(
        monkeypatch,
        root=live,
        plan=make_run_plan(source_names=("demo",)),
        execute_result=_execute_result,
        planned_paths=("tracked.txt",),
    )
    monkeypatch.setattr("lib.update.cli._maybe_reexec_checkout_update", lambda: None)
    monkeypatch.setattr("lib.update.cli._handle_required_tool_check", lambda _: None)
    calls: list[tuple[list[str], object]] = []

    def _run(
        args: list[str],
        **kwargs: object,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args[:2], kwargs["timeout"]))
        manifest = {
            "schemaVersion": 2,
            "requiredKinds": ["darwin", "home"],
            "requiredRoots": [],
            "roots": [
                {"kind": "darwin", "name": "argus", "system": "aarch64-darwin"},
                {"kind": "home", "name": "george", "system": "aarch64-darwin"},
            ],
        }
        return subprocess.CompletedProcess(
            args,
            0,
            stdout=json.dumps(manifest) if args[:2] == ["nix", "eval"] else "",
            stderr="",
        )

    monkeypatch.setattr(
        "lib.update.derivation_validation.validate_root_closures",
        partial(validate_root_closures, run=_run),
    )
    arguments = ["--json"]
    if cli_timeout is not None:
        arguments.extend(["--subprocess-timeout", str(cli_timeout)])
    arguments.append("demo")

    result = CliRunner().invoke(app, arguments)

    assert result.exit_code == 0, result.output
    assert calls == [
        (["nix", "eval"], expected_timeout),
        (["nix", "build"], expected_timeout),
    ]
    assert (live / "tracked.txt").read_text(encoding="utf-8") == "candidate\n"


def test_config_preserves_explicit_bounds_equal_to_the_generic_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit forty-minute bound must not become an automatic root timeout."""
    monkeypatch.delenv("UPDATE_SUBPROCESS_TIMEOUT", raising=False)
    implicit = resolve_config()
    assert implicit.default_subprocess_timeout == 2400
    assert implicit.subprocess_timeout_override is None

    explicit = resolve_config(subprocess_timeout=2400)
    assert explicit.default_subprocess_timeout == 2400
    assert explicit.subprocess_timeout_override == 2400

    monkeypatch.setenv("UPDATE_SUBPROCESS_TIMEOUT", "2400")
    assert resolve_active_config(None).subprocess_timeout_override == 2400
