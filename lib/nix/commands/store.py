"""Wrappers for ``nix-store`` subcommands."""

import asyncio
from typing import TYPE_CHECKING

from .base import (
    CommandResult,
    NixCommandError,
    run_nix,
    stream_nix,
)

if TYPE_CHECKING:
    from collections.abc import Callable


async def nix_store_realise(
    drv_paths: list[str],
    *,
    on_line: Callable[[str], None] | None = None,
    capture: bool = True,
    check: bool = True,
    command_timeout: float = 3600.0,
) -> CommandResult:
    """Build derivations in the store.

    Parameters
    ----------
    drv_paths:
        One or more ``.drv`` store paths to realise.
    on_line:
        Optional callback invoked with each stdout line during the build.
        When provided the command is streamed; otherwise output is collected.
        Mutually exclusive with *capture*.
    capture:
        If ``True`` (default), stdout and stderr are captured into the
        returned :class:`CommandResult`.  When ``False`` they are inherited
        from the parent process (useful for CI log visibility).
        Ignored when *on_line* is provided.
    check:
        If ``True`` (default) and the process exits non-zero, raise
        :class:`NixCommandError`.  Ignored when *on_line* is provided
        (``stream_nix`` always raises on failure).
    command_timeout:
        Maximum wall-clock seconds before the process is killed.

    """
    cmd = ["nix-store", "--realise", *drv_paths]

    if on_line is not None:
        stream = stream_nix(cmd, command_timeout=command_timeout)
        try:
            async for line in stream:
                on_line(line)
        except BaseException as error:
            try:
                await stream.aclose()
            except asyncio.CancelledError:
                raise
            except Exception as cleanup_error:  # noqa: BLE001 -- preserve primary error
                error.add_note(f"stream cleanup failed: {cleanup_error}")
            raise
        else:
            await stream.aclose()
        # stream_nix raises on non-zero exit, so reaching here means success.
        return CommandResult(args=cmd, returncode=0, stdout="", stderr="")

    return await run_nix(
        cmd,
        check=check,
        capture=capture,
        command_timeout=command_timeout,
    )


async def nix_store_query_deriver(
    path: str,
    *,
    command_timeout: float = 30.0,
) -> str | None:
    """Return the derivation that produced a store path, if known.

    Parameters
    ----------
    path:
        A Nix store path or realised profile symlink target.
    command_timeout:
        Maximum wall-clock seconds before the process is killed.

    Returns
    -------
    str | None
        The producing ``.drv`` path, or ``None`` when Nix reports an
        unknown deriver.

    """
    result = await run_nix(
        ["nix-store", "--query", "--deriver", path],
        check=False,
        command_timeout=command_timeout,
    )
    if result.returncode != 0:
        raise NixCommandError(result)

    deriver = result.stdout.strip()
    if not deriver or deriver == "unknown-deriver":
        return None
    return deriver


async def nix_store_query_references(
    path: str,
    *,
    command_timeout: float = 30.0,
) -> list[str]:
    """Return the immediate references of a store path.

    Parameters
    ----------
    path:
        A Nix store path (e.g. ``/nix/store/...-foo``).
    command_timeout:
        Maximum wall-clock seconds before the process is killed.

    """
    result = await run_nix(
        ["nix-store", "--query", "--references", path],
        check=True,
        command_timeout=command_timeout,
    )
    return [line for line in result.stdout.splitlines() if line]


async def nix_store_query_requisites(
    path: str,
    *,
    command_timeout: float = 30.0,
) -> list[str]:
    """Return the full build-time requisites of a store path.

    Parameters
    ----------
    path:
        A Nix store path, typically a ``.drv`` when tracing build provenance.
    command_timeout:
        Maximum wall-clock seconds before the process is killed.

    """
    result = await run_nix(
        ["nix-store", "--query", "--requisites", path],
        check=True,
        command_timeout=command_timeout,
    )
    return [line for line in result.stdout.splitlines() if line]
