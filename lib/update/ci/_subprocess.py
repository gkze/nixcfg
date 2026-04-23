"""Shared subprocess helpers for CI command modules."""

import asyncio
import shlex
import subprocess
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


def _emit_hidden_output(*, args: list[str], stdout: str, stderr: str) -> None:
    rendered = shlex.join(args)
    if stdout:
        sys.stderr.write(f"Command failed with hidden stdout: {rendered}\n{stdout}")
        if not stdout.endswith("\n"):
            sys.stderr.write("\n")
    if stderr:
        sys.stderr.write(f"Command failed with hidden stderr: {rendered}\n{stderr}")
        if not stderr.endswith("\n"):
            sys.stderr.write("\n")


async def run_command_async(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    cwd: str | Path | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with optional output capture and check semantics."""
    hidden_stdout = check and not capture_output and stdout == subprocess.DEVNULL
    hidden_stderr = check and not capture_output and stderr == subprocess.DEVNULL

    process_stdout = stdout
    process_stderr = stderr
    if capture_output or hidden_stdout:
        process_stdout = asyncio.subprocess.PIPE
    if capture_output or hidden_stderr:
        process_stderr = asyncio.subprocess.PIPE

    process = await asyncio.create_subprocess_exec(
        *args,
        cwd=None if cwd is None else str(cwd),
        stdout=process_stdout,
        stderr=process_stderr,
    )
    stdout_data, stderr_data = await process.communicate()
    result = subprocess.CompletedProcess(
        args=args,
        returncode=int(process.returncode or 0),
        stdout=(stdout_data or b"").decode(errors="replace"),
        stderr=(stderr_data or b"").decode(errors="replace"),
    )
    if check and result.returncode != 0:
        if hidden_stdout or hidden_stderr:
            _emit_hidden_output(
                args=args,
                stdout=result.stdout if hidden_stdout else "",
                stderr=result.stderr if hidden_stderr else "",
            )
        raise subprocess.CalledProcessError(
            result.returncode,
            args,
            output=result.stdout,
            stderr=result.stderr,
        )
    return result


def run_command(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    cwd: str | Path | None = None,
    stdout: int | None = None,
    stderr: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command synchronously via ``asyncio.run``."""
    return asyncio.run(
        run_command_async(
            args,
            check=check,
            capture_output=capture_output,
            cwd=cwd,
            stdout=stdout,
            stderr=stderr,
        )
    )
