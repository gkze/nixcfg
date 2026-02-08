"""Typed wrapper for `nix derivation show`."""

import json

from libnix.models.derivation import Derivation

from .base import run_nix


async def nix_derivation_show(
    installable: str,
    *,
    timeout: float = 60.0,
) -> dict[str, Derivation]:
    """Show derivation information (always JSON output).

    Returns a dict mapping derivation store paths to Derivation models.
    """
    result = await run_nix(
        ["nix", "derivation", "show", installable],
        timeout=timeout,
    )
    raw = json.loads(result.stdout)
    return {path: Derivation.model_validate(drv) for path, drv in raw.items()}
