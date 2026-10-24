"""Typed wrapper for `nix path-info --json`."""

from lib.nix.models.store_object_info import ImpureStoreObjectInfo

from ._json import as_model_list, run_nix_json


async def nix_path_info(
    paths: list[str],
    *,
    closure_size: bool = False,
    command_timeout: float = 60.0,
) -> list[ImpureStoreObjectInfo]:
    """Query store path information.

    Returns a list of ImpureStoreObjectInfo (includes deriver, signatures, etc.).
    """
    args = ["nix", "path-info", "--json", *paths]
    if closure_size:
        args.append("--closure-size")
    raw = await run_nix_json(args, command_timeout=command_timeout)
    if isinstance(raw, dict):
        mapped = []
        for path, value in raw.items():
            if not isinstance(value, dict):
                mapped.append(value)
                continue
            explicit_path = value.get("path")
            if explicit_path is None:
                mapped.append({**value, "path": path})
                continue
            if isinstance(explicit_path, str) and explicit_path != path:
                msg = (
                    f"nix path-info map key {path!r} conflicts with "
                    f"explicit path {explicit_path!r}"
                )
                raise ValueError(msg)
            mapped.append(value)
        raw = mapped
    return as_model_list(raw, ImpureStoreObjectInfo)
