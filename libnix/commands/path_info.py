"""Typed wrapper for `nix path-info --json`."""

import json

from libnix.models.store_object_info import ImpureStoreObjectInfo

from .base import run_nix


async def nix_path_info(
    paths: list[str],
    *,
    closure_size: bool = False,
    timeout: float = 60.0,
) -> list[ImpureStoreObjectInfo]:
    """Query store path information.

    Returns a list of ImpureStoreObjectInfo (includes deriver, signatures, etc.).
    """
    args = ["nix", "path-info", "--json", *paths]
    if closure_size:
        args.append("--closure-size")
    result = await run_nix(args, timeout=timeout)
    raw = json.loads(result.stdout)
    if isinstance(raw, list):
        return [ImpureStoreObjectInfo.model_validate(item) for item in raw]
    # Some nix versions return a dict keyed by path
    return [ImpureStoreObjectInfo.model_validate(v) for v in raw.values()]
