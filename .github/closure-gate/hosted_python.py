# ruff: noqa: INP001 -- Actions runs this file as a script, not a package
"""Run a hosted Actions step with the interpreter realize resolves.

``shell: python`` on the macOS runner is
``/Library/Frameworks/Python.framework/Versions/Current/bin/python``. That
directory drops off PATH after the realize process is signalled
(45788923798), so a later step cannot look the word ``python`` up. This
module publishes the framework bin and the Determinate Nix bin through
``GITHUB_PATH`` before the long step, and runs the step body with that
interpreter. The workflow shell stays ``python``.

A realize child whose status is 129..159 was killed by a signal. macOS
SIGBUS is 138. Python reports that death as a negative signal number; this
module maps it back to the 128+signum status the runner logs and records
``runner_fault``. Any other status records ``runner_fault=false``. An empty
flag is a runner that died before this process could write.

This module stays on the runner's Python 3.12 and the standard library.
"""

from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path

FRAMEWORK_BIN = "/Library/Frameworks/Python.framework/Versions/Current/bin"
NIX_BIN = "/nix/var/nix/profiles/default/bin"
_SIGNAL_FLOOR = 128
_SIGNAL_CEILING = 160


def reported_status(status: int) -> int:
    """Return the 128+signum status for a Python signal death."""
    if status < 0:
        return _SIGNAL_FLOOR + (-status)
    return status


def is_runner_signal(status: int) -> bool:
    """Return whether *status* is a signalled process, statuses 129 through 159."""
    reported = reported_status(status)
    return _SIGNAL_FLOOR < reported < _SIGNAL_CEILING


def hosted_directories() -> tuple[str, ...]:
    """Return the framework, Nix profile, and user profile bins, in that order."""
    profile = str(Path.home() / ".nix-profile" / "bin")
    return (FRAMEWORK_BIN, NIX_BIN, profile)


def publish_hosted_path() -> None:
    """Put the hosted bins in front of PATH for this step and the ones after it."""
    entries = hosted_directories()
    current = os.environ.get("PATH", "")
    os.environ["PATH"] = (
        os.pathsep.join((*entries, current)) if current else os.pathsep.join(entries)
    )
    github_path = os.environ.get("GITHUB_PATH")
    if not github_path:
        return
    with Path(github_path).open("a", encoding="utf-8") as handle:
        handle.writelines(f"{entry}\n" for entry in entries)


def resolve_interpreter() -> str:
    """Return the framework interpreter when this runner has one.

    ``NIXCFG_PYTHON`` wins when it is executable. Otherwise the macOS
    framework interpreter wins. The interpreter that started this process is
    the fallback, which is what ``shell: python`` already resolved on Linux.
    """
    explicit = os.environ.get("NIXCFG_PYTHON", "")
    if explicit and os.access(explicit, os.X_OK):
        return explicit
    framework = str(Path(FRAMEWORK_BIN) / "python")
    if os.access(framework, os.X_OK):
        return framework
    return sys.executable


def write_output(name: str, value: str) -> None:
    """Append one Actions output when the runner provided the file."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"{name}={value}\n")


def note_unrecorded_realize_fault() -> None:
    """Record runner_fault when realize died before its own step could.

    The storage step calls this. A GateError has already written
    ``runner_fault=false``. A skipped realize is not a fault. Only a failed
    realize that left the flag empty is the shell dying with the child.
    """
    outcome = os.environ.get("NIXCFG_REALIZE_OUTCOME", "")
    flag = os.environ.get("NIXCFG_REALIZE_RUNNER_FAULT", "")
    if outcome == "failure" and flag == "":
        write_output("runner_fault", "true")


def run_target(interpreter: str, target: str) -> int:
    """Run *target* as a child and return its status, including a signal death."""
    completed = subprocess.run(  # noqa: S603 -- interpreter and target are repo paths
        [interpreter, target],
        check=False,
    )
    return completed.returncode


def main() -> None:
    """Publish PATH, then run ``NIXCFG_HOSTED_TARGET``."""
    publish_hosted_path()
    note_unrecorded_realize_fault()
    target = os.environ.get("NIXCFG_HOSTED_TARGET", "")
    if not target:
        msg = "NIXCFG_HOSTED_TARGET is required"
        raise SystemExit(msg)
    interpreter = resolve_interpreter()
    if os.environ.get("NIXCFG_RECORD_SIGNAL") == "1":
        status = run_target(interpreter, target)
        write_output(
            "runner_fault",
            "true" if is_runner_signal(status) else "false",
        )
        raise SystemExit(reported_status(status))
    if os.path.realpath(interpreter) != os.path.realpath(sys.executable):
        # Replace this process with the framework interpreter.
        os.execv(interpreter, [interpreter, target])  # noqa: S606
    runpy.run_path(target, run_name="__main__")


if __name__ == "__main__":
    main()
