"""Nix hash conversion and URL prefetching utilities."""

from .base import run_nix


async def nix_hash_convert(
    hash_value: str,
    *,
    hash_algo: str = "sha256",
    to: str = "sri",
    timeout: float = 30.0,
) -> str:
    """Convert a hash to the specified representation (SRI by default)."""
    result = await run_nix(
        ["nix", "hash", "convert", "--hash-algo", hash_algo, "--to", to, hash_value],
        timeout=timeout,
    )
    return result.stdout.strip()


async def nix_prefetch_url(
    url: str,
    *,
    hash_type: str = "sha256",
    timeout: float = 300.0,
) -> str:
    """Download a URL and return its SRI hash."""
    result = await run_nix(
        ["nix-prefetch-url", "--type", hash_type, url],
        timeout=timeout,
    )
    raw_hash = result.stdout.strip().split("\n")[-1]
    return await nix_hash_convert(raw_hash)
