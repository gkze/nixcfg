"""Hand-extended models for Nix build results.

Aligned with build-result-v1 schema from NixOS/nix.

Provides clean, discriminated-union models for successful and failed builds,
replacing the auto-generated BuildResult1/BuildResult2/Status/Status1 types
with ergonomic names and a proper tagged union.
"""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SuccessStatus(StrEnum):
    """Status codes for successful builds.

    Aligned with build-result-v1 schema from NixOS/nix.
    """

    Built = "Built"
    Substituted = "Substituted"
    AlreadyValid = "AlreadyValid"
    ResolvesToAlreadyValid = "ResolvesToAlreadyValid"


class FailureStatus(StrEnum):
    """Status codes for failed builds.

    Aligned with build-result-v1 schema from NixOS/nix.
    """

    PermanentFailure = "PermanentFailure"
    InputRejected = "InputRejected"
    OutputRejected = "OutputRejected"
    TransientFailure = "TransientFailure"
    CachedFailure = "CachedFailure"
    TimedOut = "TimedOut"
    MiscFailure = "MiscFailure"
    DependencyFailed = "DependencyFailed"
    LogLimitExceeded = "LogLimitExceeded"
    NotDeterministic = "NotDeterministic"
    NoSubstituters = "NoSubstituters"
    HashMismatch = "HashMismatch"


class SuccessfulBuild(BaseModel):
    """A successful build result.

    Aligned with build-result-v1 schema from NixOS/nix.
    Corresponds to the ``success: true`` variant of ``BuildResult``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    success: Literal[True] = Field(
        default=True,
        description="Always true for successful build results.",
    )
    status: SuccessStatus = Field(
        description="Status code indicating how the build succeeded.",
    )
    built_outputs: dict[str, Any] = Field(
        alias="builtOutputs",
        default_factory=dict,
        description="Mapping from output names to their build trace entries.",
    )
    times_built: int | None = Field(
        alias="timesBuilt",
        default=None,
        ge=0,
        description="How many times this build was performed.",
    )
    start_time: int | None = Field(
        alias="startTime",
        default=None,
        ge=0,
        description="Start time of the build as a Unix timestamp.",
    )
    stop_time: int | None = Field(
        alias="stopTime",
        default=None,
        ge=0,
        description="Stop time of the build as a Unix timestamp.",
    )
    cpu_user: int | None = Field(
        alias="cpuUser",
        default=None,
        ge=0,
        description="User CPU time the build took, in microseconds.",
    )
    cpu_system: int | None = Field(
        alias="cpuSystem",
        default=None,
        ge=0,
        description="System CPU time the build took, in microseconds.",
    )


class FailedBuild(BaseModel):
    """A failed build result.

    Aligned with build-result-v1 schema from NixOS/nix.
    Corresponds to the ``success: false`` variant of ``BuildResult``.
    """

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    success: Literal[False] = Field(
        default=False,
        description="Always false for failed build results.",
    )
    status: FailureStatus = Field(
        description="Status code indicating why the build failed.",
    )
    error_msg: str = Field(
        alias="errorMsg",
        description="Information about the error.",
    )
    is_non_deterministic: bool | None = Field(
        alias="isNonDeterministic",
        default=None,
        description=(
            "If timesBuilt > 1, whether some builds did not produce the same "
            "result. Note that False does not prove determinism."
        ),
    )
    times_built: int | None = Field(
        alias="timesBuilt",
        default=None,
        ge=0,
        description="How many times this build was performed.",
    )
    start_time: int | None = Field(
        alias="startTime",
        default=None,
        ge=0,
        description="Start time of the build as a Unix timestamp.",
    )
    stop_time: int | None = Field(
        alias="stopTime",
        default=None,
        ge=0,
        description="Stop time of the build as a Unix timestamp.",
    )
    cpu_user: int | None = Field(
        alias="cpuUser",
        default=None,
        ge=0,
        description="User CPU time the build took, in microseconds.",
    )
    cpu_system: int | None = Field(
        alias="cpuSystem",
        default=None,
        ge=0,
        description="System CPU time the build took, in microseconds.",
    )


type BuildResult = SuccessfulBuild | FailedBuild
"""Discriminated union of build outcomes.

Aligned with build-result-v1 schema from NixOS/nix.
Pydantic will discriminate on the ``success`` field (``True`` vs ``False``).
"""


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def is_hash_mismatch(result: FailedBuild) -> bool:
    """Return True if the failure is due to a hash mismatch.

    Useful for detecting fixed-output derivation integrity errors.
    """
    return result.status is FailureStatus.HashMismatch
