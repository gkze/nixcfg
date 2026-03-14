"""Wrappers for ``nix-store`` subcommands."""

from typing import TYPE_CHECKING

from .base import (
    CommandResult,
    NixCommandError,
    _resolve_timeout_alias,
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
    **kwargs: object,
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
    **kwargs:
        Supports legacy ``timeout=...`` alias.

    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    cmd = ["nix-store", "--realise", *drv_paths]

    if on_line is not None:
        async for line in stream_nix(cmd, timeout=timeout_seconds):
            on_line(line)
        # stream_nix raises on non-zero exit, so reaching here means success.
        return CommandResult(args=cmd, returncode=0, stdout="", stderr="")

    return await run_nix(
        cmd,
        check=check,
        capture=capture,
        timeout=timeout_seconds,
    )


async def nix_store_query_deriver(
    path: str,
    *,
    command_timeout: float = 30.0,
    **kwargs: object,
) -> str | None:
    """Return the derivation that produced a store path, if known.

    Parameters
    ----------
    path:
        A Nix store path or realised profile symlink target.
    command_timeout:
        Maximum wall-clock seconds before the process is killed.
    **kwargs:
        Supports legacy ``timeout=...`` alias.

    Returns
    -------
    str | None
        The producing ``.drv`` path, or ``None`` when Nix reports an
        unknown deriver.

    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    result = await run_nix(
        ["nix-store", "--query", "--deriver", path],
        check=False,
        timeout=timeout_seconds,
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
    **kwargs: object,
) -> list[str]:
    """Return the immediate references of a store path.

    Parameters
    ----------
    path:
        A Nix store path (e.g. ``/nix/store/...-foo``).
    command_timeout:
        Maximum wall-clock seconds before the process is killed.
    **kwargs:
        Supports legacy ``timeout=...`` alias.

    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    result = await run_nix(
        ["nix-store", "--query", "--references", path],
        check=True,
        timeout=timeout_seconds,
    )
    return [line for line in result.stdout.splitlines() if line]


async def nix_store_query_requisites(
    path: str,
    *,
    command_timeout: float = 30.0,
    **kwargs: object,
) -> list[str]:
    """Return the full build-time requisites of a store path.

    Parameters
    ----------
    path:
        A Nix store path, typically a ``.drv`` when tracing build provenance.
    command_timeout:
        Maximum wall-clock seconds before the process is killed.
    **kwargs:
        Supports legacy ``timeout=...`` alias.

    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    result = await run_nix(
        ["nix-store", "--query", "--requisites", path],
        check=True,
        timeout=timeout_seconds,
    )
    return [line for line in result.stdout.splitlines() if line]
