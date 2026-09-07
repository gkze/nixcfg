"""Event models and stream helpers for updater workflows."""

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal, ReadOnly, TypedDict

from lib.nix.models.sources import SourceEntry
from lib.update.artifacts import GeneratedArtifact


def is_nix_build_command(args: list[str] | None) -> bool:
    """Return ``True`` if *args* looks like a ``nix build`` invocation."""
    return bool(args) and args[:2] == ["nix", "build"]


class UpdateEventKind(StrEnum):
    """Kinds of events emitted by update tasks."""

    STATUS = "status"
    COMMAND_START = "command_start"
    LINE = "line"
    COMMAND_END = "command_end"
    RESULT = "result"
    ARTIFACT = "artifact"
    ERROR = "error"


class RefUpdatePayload(TypedDict):
    """Payload emitted when a flake ref moves from current to latest."""

    current: ReadOnly[str]
    latest: ReadOnly[str]


class StatusKind(StrEnum):
    """Typed producer intent carried by status events."""

    CHECKING_CURRENT = "checking_current"
    LATEST_VERSION = "latest_version"
    UPDATE_AVAILABLE = "update_available"
    UP_TO_DATE = "up_to_date"
    UPDATED = "updated"
    UPDATING_REF = "updating_ref"
    REFRESH_LOCK = "refresh_lock"
    FETCHING_HASHES = "fetching_hashes"
    COMPUTING_HASH = "computing_hash"
    UNSUPPORTED_PLATFORM = "unsupported_platform"
    SKIPPED = "skipped"
    PRESERVED_HASH = "preserved_hash"
    PRESERVED_DRV_HASH = "preserved_drv_hash"
    PRESERVED_ARTIFACT = "preserved_artifact"
    PARTIAL_HASHES = "partial_hashes"
    RETRY = "retry"


type StatusScope = Literal["version", "hash", "artifacts", "ref"]


@dataclass(frozen=True)
class StatusInfo:
    """Structured status metadata describing what a status event reports.

    ``value`` carries the single interesting datum for most kinds (a version,
    platform, file, or attempt counter).  ``current``/``latest`` describe ref
    or version transitions, and ``scope`` qualifies ``UP_TO_DATE`` events.
    """

    kind: StatusKind
    value: str | None = None
    current: str | None = None
    latest: str | None = None
    scope: StatusScope | None = None


@dataclass(frozen=True)
class StatusPayload:
    """Typed payload attached to STATUS events."""

    operation: str | None = None
    info: StatusInfo | None = None


type CommandArgs = list[str]
type ArtifactUpdates = list[GeneratedArtifact]


@dataclass(frozen=True)
class CommandResult:
    """Result payload for a completed subprocess command."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    allow_failure: bool = False
    tail_lines: tuple[str, ...] = ()


type UpdateEventPayload = (
    ArtifactUpdates
    | CommandArgs
    | CommandResult
    | StatusPayload
    | SourceEntry
    | str
    | RefUpdatePayload
)


def expect_command_result(payload: object) -> CommandResult:
    """Return payload as :class:`CommandResult` or raise ``TypeError``."""
    if isinstance(payload, CommandResult):
        return payload
    msg = f"Expected CommandResult payload, got {type(payload).__name__}"
    raise TypeError(msg)


def raise_failed_command(action: str, result: CommandResult) -> None:
    """Raise a concise ``RuntimeError`` when *result* reports failure."""
    if result.returncode == 0:
        return
    detail = result.stderr.strip() or result.stdout.strip()
    message = f"{action} failed (exit {result.returncode})"
    raise RuntimeError(f"{message}: {detail}" if detail else message)


def expect_source_entry(payload: object) -> SourceEntry:
    """Return payload as :class:`SourceEntry` or raise ``TypeError``."""
    if isinstance(payload, SourceEntry):
        return payload
    msg = f"Expected SourceEntry payload, got {type(payload).__name__}"
    raise TypeError(msg)


def expect_artifact_updates(payload: object) -> ArtifactUpdates:
    """Return payload as ``list[GeneratedArtifact]`` or raise ``TypeError``."""
    if isinstance(payload, list):
        artifacts: list[GeneratedArtifact] = []
        for item in payload:
            if not isinstance(item, GeneratedArtifact):
                break
            artifacts.append(item)
        else:
            return artifacts
    msg = f"Expected GeneratedArtifact list payload, got {type(payload).__name__}"
    raise TypeError(msg)


@dataclass(frozen=True)
class UpdateEvent:
    """Single event emitted during update processing."""

    source: str
    kind: UpdateEventKind
    message: str | None = None
    stream: str | None = None
    payload: UpdateEventPayload | None = None

    @classmethod
    def status(
        cls,
        source: str,
        message: str,
        *,
        operation: str | None = None,
        status: StatusInfo | None = None,
    ) -> UpdateEvent:
        """Create a status event."""
        payload: StatusPayload | None = None
        if operation is not None or status is not None:
            payload = StatusPayload(operation=operation, info=status)
        return cls(
            source=source,
            kind=UpdateEventKind.STATUS,
            message=message,
            payload=payload,
        )

    @classmethod
    def error(cls, source: str, message: str) -> UpdateEvent:
        """Create an error event."""
        return cls(source=source, kind=UpdateEventKind.ERROR, message=message)

    @classmethod
    def result(
        cls,
        source: str,
        payload: UpdateEventPayload | None = None,
    ) -> UpdateEvent:
        """Create a result event."""
        return cls(source=source, kind=UpdateEventKind.RESULT, payload=payload)

    @classmethod
    def artifact(
        cls,
        source: str,
        payload: GeneratedArtifact | ArtifactUpdates,
    ) -> UpdateEvent:
        """Create an artifact event."""
        artifacts = (
            [payload] if isinstance(payload, GeneratedArtifact) else list(payload)
        )
        return cls(source=source, kind=UpdateEventKind.ARTIFACT, payload=artifacts)


type EventSink = Callable[[UpdateEvent], Awaitable[None]]


async def ignore_event(_event: UpdateEvent) -> None:
    """Discard progress for callers that only need an operation's result."""


async def gather_results[K, V](operations: Mapping[K, Awaitable[V]]) -> dict[K, V]:
    """Collect typed task results, cancelling siblings if an operation fails."""
    tasks: dict[K, asyncio.Task[V]] = {}
    try:
        async with asyncio.TaskGroup() as group:
            for key, operation in operations.items():
                tasks[key] = group.create_task(_keyed_result(key, operation))
    except ExceptionGroup as error:
        if len(error.exceptions) == 1:
            raise error.exceptions[0] from None
        message = "; ".join(str(exc) for exc in error.exceptions)
        msg = f"Multiple update operations failed: {message}"
        raise RuntimeError(msg) from error
    return {key: task.result() for key, task in tasks.items()}


async def _keyed_result[K, V](key: K, operation: Awaitable[V]) -> V:
    try:
        return await operation
    except Exception as error:
        error.add_note(f"update operation key: {key!r}")
        raise
