"""Nix hash conversion and URL prefetching utilities."""

from pydantic import BaseModel

from lib.nix.models.hash import (
    NixHash,  # noqa: TC001 -- Pydantic resolves this at runtime
)

from .base import _resolve_timeout_alias, run_nix


class PrefetchResult(BaseModel):
    """The exact Nix store import produced by a URL prefetch."""

    hash: NixHash
    storePath: str  # noqa: N815 -- Nix's JSON protocol uses camelCase.


async def nix_hash_convert(
    hash_value: str,
    *,
    hash_algo: str = "sha256",
    to: str = "sri",
    command_timeout: float = 30.0,
    **kwargs: object,
) -> str:
    """Convert a hash to the specified representation (SRI by default)."""
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    result = await run_nix(
        ["nix", "hash", "convert", "--hash-algo", hash_algo, "--to", to, hash_value],
        timeout=timeout_seconds,
    )
    return result.stdout.strip()


async def nix_prefetch_url(
    url: str,
    *,
    hash_type: str = "sha256",
    name: str | None = None,
    command_timeout: float = 1200.0,
    **kwargs: object,
) -> str:
    """Download a URL and return its SRI hash."""
    return (
        await nix_prefetch_url_result(
            url,
            hash_type=hash_type,
            name=name,
            command_timeout=command_timeout,
            **kwargs,
        )
    ).hash


async def nix_prefetch_url_result(
    url: str,
    *,
    hash_type: str = "sha256",
    name: str | None = None,
    command_timeout: float = 1200.0,
    **kwargs: object,
) -> PrefetchResult:
    """Download a URL and return its SRI hash and exact store import."""
    timeout_seconds = _resolve_timeout_alias(
        command_timeout=command_timeout,
        kwargs=kwargs,
    )
    args = ["nix", "store", "prefetch-file", "--json", "--hash-type", hash_type]
    if name is not None:
        args.extend(["--name", name])
    args.append(url)
    result = await run_nix(
        args,
        timeout=timeout_seconds,
    )
    return PrefetchResult.model_validate_json(result.stdout)
