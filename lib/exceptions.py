"""Exception hierarchy for update tools."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class UpdateError(Exception):
    """Base exception for all update-related errors."""

    message: str
    source: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        if self.source:
            return f"[{self.source}] {self.message}"
        return self.message


@dataclass
class NetworkError(UpdateError):
    """Error during network operations."""

    url: str | None = None
    status_code: int | None = None


@dataclass
class RateLimitError(NetworkError):
    """API rate limit exceeded."""

    reset_time: str | None = None


@dataclass
class NixCommandError(UpdateError):
    """Error executing a Nix command."""

    command: list[str] | None = None
    returncode: int | None = None
    stderr: str | None = None


@dataclass
class HashExtractionError(UpdateError):
    """Could not extract hash from Nix output."""

    output: str | None = None


@dataclass
class ValidationError(UpdateError):
    """Data validation failed."""

    field_name: str | None = None
    value: str | None = None


@dataclass
class FlakeLockError(UpdateError):
    """Error with flake.lock parsing or operations."""

    input_name: str | None = None


@dataclass
class CommandTimeoutError(UpdateError):
    """Operation timed out."""

    timeout_seconds: float | None = None
