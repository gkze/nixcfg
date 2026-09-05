"""Source-derived Bun toolchain contracts shared by package updaters."""

from types import MappingProxyType
from typing import TYPE_CHECKING

from lib import json_utils, system_policy
from lib.nix.models.sources import HashEntry
from lib.update.npm_semver import require_exact_semantic_version

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

BUN_RELEASE_ASSETS: Mapping[str, str] = MappingProxyType(
    system_policy.bun_artifact_names()
)


def require_bun_package_manager(payload: object, *, context: str) -> str:
    """Return the exact Bun version owned by a package manifest."""
    manifest = json_utils.as_object_dict(payload, context=context)
    package_manager = json_utils.get_required_str(
        manifest,
        "packageManager",
        context=context,
    )
    prefix = "bun@"
    if not package_manager.startswith(prefix):
        msg = (
            f"{context} requires an exact bun@<version> packageManager, "
            f"got {package_manager!r}"
        )
        raise RuntimeError(msg)
    return require_exact_semantic_version(
        package_manager.removeprefix(prefix),
        context=f"{context} Bun",
    )


def bun_release_url(version: str, system: str) -> str:
    """Return the official Bun release asset URL for one Nix system."""
    require_exact_semantic_version(version, context="Bun release")
    asset = BUN_RELEASE_ASSETS.get(system)
    if asset is None:
        msg = f"Bun has no configured release asset for Nix system {system!r}"
        raise RuntimeError(msg)
    return f"https://github.com/oven-sh/bun/releases/download/bun-v{version}/{asset}"


def bun_release_urls(version: str, systems: Iterable[str]) -> dict[str, str]:
    """Return official release URLs keyed by each requested Nix system."""
    return {system: bun_release_url(version, system) for system in systems}


def bun_runtime_hash_entries(
    version: str,
    systems: Iterable[str],
    hashes_by_url: Mapping[str, str],
) -> list[HashEntry]:
    """Build exact Bun runtime source entries for the requested systems."""
    urls = bun_release_urls(version, systems)
    entries: list[HashEntry] = []
    for system, url in urls.items():
        hash_value = hashes_by_url.get(url)
        if hash_value is None:
            msg = f"Missing Bun {version} runtime hash for {system}: {url}"
            raise RuntimeError(msg)
        entries.append(
            HashEntry.create(
                "bunRuntimeHash",
                hash_value,
                platform=system,
                url=url,
            )
        )
    return entries


__all__ = [
    "BUN_RELEASE_ASSETS",
    "bun_release_url",
    "bun_release_urls",
    "bun_runtime_hash_entries",
    "require_bun_package_manager",
]
