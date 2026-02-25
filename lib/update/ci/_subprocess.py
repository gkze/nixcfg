"""Shared subprocess helpers for CI command modules."""

from __future__ import annotations

import asyncio
import subprocess


async def run_command_async(
    args: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    stdout: int | None = None,
    stderr: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command with optional output capture and check semantics."""
    process_stdout = stdout
    process_stderr = stderr
    if capture_output:
        process_stdout = asyncio.subprocess.PIPE
        process_stderr = asyncio.subprocess.PIPE

    process = await asyncio.create_subprocess_exec(
        *args,
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
    stdout: int | None = None,
    stderr: int | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command synchronously via ``asyncio.run``."""
    return asyncio.run(
        run_command_async(
            args,
            check=check,
            capture_output=capture_output,
            stdout=stdout,
            stderr=stderr,
        )
    )
