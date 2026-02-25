"""Typed wrapper for `nix derivation show`."""

from lib.nix.models.derivation import Derivation

from ._json import as_model_mapping, run_nix_json
from .base import _resolve_timeout_alias


async def nix_derivation_show(
    installable: str,
    *,
    command_timeout: float = 60.0,
    **kwargs: object,
) -> dict[str, Derivation]:
    """Show derivation information (always JSON output).

    Returns a dict mapping derivation store paths to Derivation models.
    """
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    raw = await run_nix_json(
        ["nix", "derivation", "show", installable],
        timeout=timeout_seconds,
    )
    return as_model_mapping(raw, Derivation)
