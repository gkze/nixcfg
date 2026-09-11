"""Platform probe diagnostics and complete-computation checks."""

from dataclasses import dataclass

from lib.update.events import UpdateEvent


@dataclass(frozen=True, slots=True)
class PlatformHashFailure:
    """A requested platform whose dependency hash could not be computed."""

    platform: str
    error: str


def platform_hash_failure_status(
    source: str,
    failure: PlatformHashFailure,
) -> UpdateEvent:
    """Report one failed probe while the remaining platforms are checked."""
    return UpdateEvent.status(
        source,
        f"Hash probe failed for {failure.platform}: {failure.error}",
        operation="compute_hash",
    )


def require_complete_platform_hashes(
    source: str,
    failures: list[PlatformHashFailure],
) -> None:
    """Reject failed probes after collecting platform-specific diagnostics."""
    if failures:
        details = "\n".join(
            f"{failure.platform}: {failure.error}" for failure in failures
        )
        msg = (
            f"Failed to compute all requested platform hashes for {source}:\n{details}"
        )
        raise RuntimeError(msg)


__all__ = [
    "PlatformHashFailure",
    "platform_hash_failure_status",
    "require_complete_platform_hashes",
]
