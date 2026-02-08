"""High-level async wrappers for ``nix flake`` subcommands."""

import json
from typing import Any

from .base import run_nix


async def nix_flake_metadata(
    flake_ref: str = ".",
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Return parsed JSON metadata for a flake.

    Runs ``nix flake metadata --json <flake_ref>`` and returns the
    deserialised dictionary containing locked refs, revision info, etc.
    """
    result = await run_nix(
        ["nix", "flake", "metadata", "--json", flake_ref],
        timeout=timeout,
    )
    return json.loads(result.stdout)


async def nix_flake_lock_update(
    input_name: str,
    *,
    flake_ref: str = ".",
    timeout: float = 300.0,
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
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Return the parsed JSON output tree of a flake.

    Runs ``nix flake show --json <flake_ref>`` and returns the
    deserialised dictionary describing all outputs.
    """
    result = await run_nix(
        ["nix", "flake", "show", "--json", flake_ref],
        timeout=timeout,
    )
    return json.loads(result.stdout)
