"""Behavioral checks for the lightweight root CLI import boundary."""

import json
import subprocess
import sys

from lib.update.paths import REPO_ROOT


def test_root_cli_import_defers_command_subsystems() -> None:
    """Root help should not pay command-specific import cost before dispatch."""
    script = """
import json
import sys
from typer.testing import CliRunner

import nixcfg

result = CliRunner().invoke(nixcfg.app, ["--help"])
assert result.exit_code == 0, result.output

prefixes = (
    "lib.github_actions",
    "lib.nix.schemas",
    "lib.recover",
    "lib.schema_codegen",
    "lib.update.ci",
    "lib.update.cli",
)
print(json.dumps(sorted(
    name for name in sys.modules if name.startswith(prefixes)
)))
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == []


def test_schema_cli_loads_only_when_schema_is_dispatched() -> None:
    """The schema implementation should load only when its group is dispatched."""
    script = """
import json
import sys
from typer.testing import CliRunner

import nixcfg

before = "lib.schema_codegen.cli" in sys.modules
result = CliRunner().invoke(nixcfg.app, ["schema", "--help"])
assert result.exit_code == 0, result.output
print(json.dumps({
    "before": before,
    "after": "lib.schema_codegen.cli" in sys.modules,
    "commands": [
        command
        for command in ("codegen", "fetch", "generate", "lock", "targets", "verify")
        if command in result.output
    ],
}))
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        check=True,
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "before": False,
        "after": True,
        "commands": ["codegen", "fetch", "generate", "lock", "targets", "verify"],
    }
