"""Typed wrapper for `nix path-info --json`."""

from lib.nix.models.store_object_info import ImpureStoreObjectInfo

from ._json import as_model_list, run_nix_json
from .base import _resolve_timeout_alias


async def nix_path_info(
    paths: list[str],
    *,
    closure_size: bool = False,
    command_timeout: float = 60.0,
    **kwargs: object,
) -> list[ImpureStoreObjectInfo]:
    """Query store path information.

    Returns a list of ImpureStoreObjectInfo (includes deriver, signatures, etc.).
    """
    args = ["nix", "path-info", "--json", *paths]
    if closure_size:
        args.append("--closure-size")
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    raw = await run_nix_json(args, timeout=timeout_seconds)
    return as_model_list(raw, ImpureStoreObjectInfo)
