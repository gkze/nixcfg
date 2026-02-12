"""Typed wrapper for `nix derivation show`."""

from lib.nix.models.derivation import Derivation

from ._json import as_model_mapping, run_nix_json


async def nix_derivation_show(
    installable: str,
    *,
    timeout: float = 60.0,  # noqa: ASYNC109
) -> dict[str, Derivation]:
    """Show derivation information (always JSON output).

    Returns a dict mapping derivation store paths to Derivation models.
    """
    raw = await run_nix_json(
        ["nix", "derivation", "show", installable],
        timeout=timeout,
    )
    return as_model_mapping(raw, Derivation)
