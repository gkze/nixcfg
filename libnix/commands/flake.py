"""High-level async wrappers for ``nix flake`` subcommands."""

from typing import Any, cast

from ._json import run_nix_json
from .base import run_nix


async def nix_flake_metadata(
    flake_ref: str = ".",
    *,
    timeout: float = 60.0,  # noqa: ASYNC109
) -> dict[str, Any]:
    """Return parsed JSON metadata for a flake.

    Runs ``nix flake metadata --json <flake_ref>`` and returns the
    deserialised dictionary containing locked refs, revision info, etc.
    """
    raw = await run_nix_json(
        ["nix", "flake", "metadata", "--json", flake_ref],
        timeout=timeout,
    )
    return cast("dict[str, Any]", raw)


async def nix_flake_lock_update(
    input_name: str,
    *,
    flake_ref: str = ".",
    timeout: float = 300.0,  # noqa: ASYNC109
) -> None:
    """Update a single flake input in the lock file.

    Runs ``nix flake lock --update-input <input_name>`` inside the flake
    directory indicated by *flake_ref*.  This is a side-effect-only
    operation; the lock file is modified in place.
    """
    cmd = ["nix", "flake", "lock", "--update-input", input_name]
    if flake_ref != ".":
        cmd.append(flake_ref)
    await run_nix(cmd, timeout=timeout)


async def nix_flake_show(
    flake_ref: str = ".",
    *,
    timeout: float = 60.0,  # noqa: ASYNC109
) -> dict[str, Any]:
    """Return the parsed JSON output tree of a flake.

    Runs ``nix flake show --json <flake_ref>`` and returns the
    deserialised dictionary describing all outputs.
    """
    raw = await run_nix_json(
        ["nix", "flake", "show", "--json", flake_ref],
        timeout=timeout,
    )
    return cast("dict[str, Any]", raw)
