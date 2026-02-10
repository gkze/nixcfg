"""Typed wrapper for `nix path-info --json`."""

from libnix.models.store_object_info import ImpureStoreObjectInfo

from ._json import as_model_list, run_nix_json


async def nix_path_info(
    paths: list[str],
    *,
    closure_size: bool = False,
    timeout: float = 60.0,  # noqa: ASYNC109
) -> list[ImpureStoreObjectInfo]:
    """Query store path information.

    Returns a list of ImpureStoreObjectInfo (includes deriver, signatures, etc.).
    """
    args = ["nix", "path-info", "--json", *paths]
    if closure_size:
        args.append("--closure-size")
    raw = await run_nix_json(args, timeout=timeout)
    return as_model_list(raw, ImpureStoreObjectInfo)
