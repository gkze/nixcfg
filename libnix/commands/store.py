"""Wrappers for ``nix-store`` subcommands."""

from typing import TYPE_CHECKING

from .base import CommandResult, run_nix, stream_nix

if TYPE_CHECKING:
    from collections.abc import Callable


async def nix_store_realise(
    drv_paths: list[str],
    *,
    on_line: Callable[[str], None] | None = None,
    capture: bool = True,
    check: bool = True,
    timeout: float = 3600.0,
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
    timeout:
        Maximum wall-clock seconds before the process is killed.
    """
    cmd = ["nix-store", "--realise", *drv_paths]

    if on_line is not None:
        async for line in stream_nix(cmd, timeout=timeout):
            on_line(line)
        # stream_nix raises on non-zero exit, so reaching here means success.
        return CommandResult(args=cmd, returncode=0, stdout="", stderr="")

    return await run_nix(cmd, check=check, capture=capture, timeout=timeout)


async def nix_store_query_references(
    path: str,
    *,
    timeout: float = 30.0,
) -> list[str]:
    """Return the immediate references of a store path.

    Parameters
    ----------
    path:
        A Nix store path (e.g. ``/nix/store/...-foo``).
    timeout:
        Maximum wall-clock seconds before the process is killed.
    """
    result = await run_nix(
        ["nix-store", "--query", "--references", path],
        check=True,
        timeout=timeout,
    )
    return [line for line in result.stdout.splitlines() if line]
