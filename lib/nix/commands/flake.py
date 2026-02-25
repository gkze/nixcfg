"""High-level async wrappers for ``nix flake`` subcommands."""

from ._json import JsonValue, run_nix_json
from .base import _resolve_timeout_alias, run_nix


def _as_json_object(value: JsonValue, *, command: str) -> dict[str, JsonValue]:
    if isinstance(value, dict):
        return value
    msg = f"Expected JSON object from `{command}`, got {type(value).__name__}"
    raise TypeError(msg)


async def nix_flake_metadata(
    flake_ref: str = ".",
    *,
    command_timeout: float = 60.0,
    **kwargs: object,
) -> dict[str, JsonValue]:
    """Return parsed JSON metadata for a flake.

    Runs ``nix flake metadata --json <flake_ref>`` and returns the
    deserialised dictionary containing locked refs, revision info, etc.
    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    raw = await run_nix_json(
        ["nix", "flake", "metadata", "--json", flake_ref],
        timeout=timeout_seconds,
    )
    return _as_json_object(raw, command="nix flake metadata --json")


async def nix_flake_lock_update(
    input_name: str,
    *,
    flake_ref: str = ".",
    command_timeout: float = 300.0,
    **kwargs: object,
) -> None:
    """Update a single flake input in the lock file.

    Runs ``nix flake lock --update-input <input_name>`` inside the flake
    directory indicated by *flake_ref*.  This is a side-effect-only
    operation; the lock file is modified in place.
    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    cmd = ["nix", "flake", "lock", "--update-input", input_name]
    if flake_ref != ".":
        cmd.append(flake_ref)
    await run_nix(cmd, timeout=timeout_seconds)


async def nix_flake_show(
    flake_ref: str = ".",
    *,
    command_timeout: float = 60.0,
    **kwargs: object,
) -> dict[str, JsonValue]:
    """Return the parsed JSON output tree of a flake.

    Runs ``nix flake show --json <flake_ref>`` and returns the
    deserialised dictionary describing all outputs.
    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    raw = await run_nix_json(
        ["nix", "flake", "show", "--json", flake_ref],
        timeout=timeout_seconds,
    )
    return _as_json_object(raw, command="nix flake show --json")
