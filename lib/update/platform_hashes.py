"""Helpers for preserving platform hashes after non-native build failures."""

from __future__ import annotations

from dataclasses import dataclass

from lib.update.events import UpdateEvent


@dataclass(frozen=True, slots=True)
class PreservedPlatformHash:
    """Existing hash reused because a non-native platform probe failed."""

    platform: str
    hash: str
    error: str


def preserve_existing_platform_hash(
    platform: str,
    existing_hashes: dict[str, str],
    failure: RuntimeError,
) -> PreservedPlatformHash:
    """Return the existing hash for *platform* or fail on incomplete results."""
    existing = existing_hashes.get(platform)
    if existing is None:
        msg = (
            f"Build failed for {platform} and no existing hash is available "
            f"to preserve: {failure}"
        )
        raise RuntimeError(msg) from failure
    return PreservedPlatformHash(
        platform=platform,
        hash=existing,
        error=str(failure),
    )


def preserved_platform_hash_status(
    source: str,
    preserved: PreservedPlatformHash,
) -> UpdateEvent:
    """Build a status event for one preserved platform hash."""
    detail = {"platform": preserved.platform, "error": preserved.error}
    return UpdateEvent.status(
        source,
        f"Build failed for {preserved.platform}, preserving existing hash",
        operation="compute_hash",
        status="preserved_hash",
        detail=detail,
    )


def preserved_platform_hash_warning(
    source: str,
    preserved: list[PreservedPlatformHash],
) -> UpdateEvent:
    """Build the summary status for a partial but safely preserved hash set."""
    platforms = tuple(item.platform for item in preserved)
    return UpdateEvent.status(
        source,
        f"Warning: {len(platforms)} platform(s) failed, "
        f"preserved existing hashes for: {', '.join(platforms)}",
        operation="compute_hash",
        status="partial_hashes",
        detail=platforms,
    )


__all__ = [
    "PreservedPlatformHash",
    "preserve_existing_platform_hash",
    "preserved_platform_hash_status",
    "preserved_platform_hash_warning",
]
