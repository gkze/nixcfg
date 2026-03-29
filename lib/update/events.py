"""Event models and stream helpers for updater workflows."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import NotRequired, TypedDict, TypeGuard, cast

from lib.nix.models.sources import HashEntry, HashMapping, SourceEntry, SourceHashes
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
    VALUE = "value"
    RESULT = "result"
    ARTIFACT = "artifact"
    ERROR = "error"


class RefUpdatePayload(TypedDict):
    """Payload emitted when a flake ref moves from current to latest."""

    current: str
    latest: str


class StatusPayload(TypedDict):
    """Optional typed status metadata for UI/event consumers."""

    operation: NotRequired[str]
    status: NotRequired[str]
    detail: NotRequired[object]


type CommandArgs = list[str]
type PlatformHash = tuple[str, str]
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
    | SourceHashes
    | PlatformHash
    | str
    | RefUpdatePayload
)


def _is_hash_mapping(value: object) -> TypeGuard[HashMapping]:
    if not isinstance(value, dict):
        return False
    return all(isinstance(k, str) and isinstance(v, str) for k, v in value.items())


def expect_command_result(payload: object) -> CommandResult:
    """Return payload as :class:`CommandResult` or raise ``TypeError``."""
    if isinstance(payload, CommandResult):
        return payload
    msg = f"Expected CommandResult payload, got {type(payload).__name__}"
    raise TypeError(msg)


def expect_str(payload: object) -> str:
    """Return payload as ``str`` or raise ``TypeError``."""
    if isinstance(payload, str):
        return payload
    msg = f"Expected string payload, got {type(payload).__name__}"
    raise TypeError(msg)


def expect_hash_mapping(payload: object) -> HashMapping:
    """Return payload as ``dict[str, str]`` or raise ``TypeError``."""
    if _is_hash_mapping(payload):
        return dict(payload)
    msg = f"Expected hash mapping payload, got {type(payload).__name__}"
    raise TypeError(msg)


def expect_source_hashes(payload: object) -> SourceHashes:
    """Return payload as ``SourceHashes`` or raise ``TypeError``."""
    if _is_hash_mapping(payload):
        return dict(payload)
    if isinstance(payload, list):
        entries: list[HashEntry] = []
        for item in payload:
            if not isinstance(item, HashEntry):
                break
            entries.append(item)
        else:
            return entries
    msg = f"Expected SourceHashes payload, got {type(payload).__name__}"
    raise TypeError(msg)


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
        status: str | None = None,
        detail: object | None = None,
    ) -> UpdateEvent:
        """Create a status event."""
        payload: StatusPayload | None = None
        if operation is not None or status is not None or detail is not None:
            payload_dict: dict[str, object] = {}
            if operation is not None:
                payload_dict["operation"] = operation
            if status is not None:
                payload_dict["status"] = status
            if detail is not None:
                payload_dict["detail"] = detail
            payload = cast("StatusPayload", payload_dict)
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

    @classmethod
    def value(cls, source: str, payload: UpdateEventPayload) -> UpdateEvent:
        """Create a value event."""
        return cls(source=source, kind=UpdateEventKind.VALUE, payload=payload)


type EventStream = AsyncIterator[UpdateEvent]


@dataclass
class ValueDrain[T]:
    """Mutable holder used to capture VALUE payloads from streams."""

    value: T | None = None


async def drain_value_events[T](
    events: EventStream,
    drain: ValueDrain[T],
    *,
    parse: Callable[[UpdateEventPayload], T],
) -> EventStream:
    """Yield non-VALUE events while storing VALUE payloads in ``drain``."""
    async for event in events:
        if event.kind == UpdateEventKind.VALUE:
            payload = event.payload
            if payload is None:
                msg = f"Value event from {event.source!r} is missing payload"
                raise RuntimeError(msg)
            drain.value = parse(payload)
        else:
            yield event


def require_value[T](drain: ValueDrain[T], error: str) -> T:
    """Extract the captured value from *drain*, raising on ``None``."""
    if drain.value is None:
        raise RuntimeError(error)
    return drain.value


@dataclass(frozen=True)
class CapturedValue[T]:
    """Wrapper for a required value captured from an ``EventStream``."""

    captured: T


async def capture_stream_value(
    events: EventStream,
    *,
    error: str,
) -> AsyncGenerator[UpdateEvent | CapturedValue[UpdateEventPayload]]:
    """Yield non-VALUE events, then emit one :class:`CapturedValue`.

    This wraps the common ``ValueDrain`` + ``drain_value_events`` +
    ``require_value`` sequence while preserving streaming behavior.
    """
    drain = ValueDrain[UpdateEventPayload]()
    async for event in drain_value_events(events, drain, parse=lambda payload: payload):
        yield event
    yield CapturedValue(require_value(drain, error))


@dataclass(frozen=True)
class GatheredValues[K]:
    """Wrapper for the collected values from :func:`gather_event_streams`."""

    values: dict[K, UpdateEventPayload]


@dataclass(frozen=True)
class _GatherDone:
    pass


async def gather_event_streams[K](
    streams: dict[K, EventStream],
) -> AsyncGenerator[UpdateEvent | GatheredValues[K]]:
    """Run multiple ``EventStream`` generators concurrently.

    Non-VALUE events are yielded as they arrive.  Each stream's VALUE payload
    is captured keyed by its dict key.  Once every stream finishes, the
    collected values are yielded as a :class:`GatheredValues` instance.

    Usage::

        async for item in gather_event_streams({"a": gen_a, "b": gen_b}):
            if isinstance(item, GatheredValues):
                hashes = item.values  # dict[str, str] with all results
            else:
                yield item  # forward UpdateEvent to caller
    """
    done = _GatherDone()
    queue: asyncio.Queue[UpdateEvent | Exception | _GatherDone] = asyncio.Queue()
    results: dict[K, UpdateEventPayload] = {}

    def _required_payload(event: UpdateEvent) -> UpdateEventPayload:
        payload = event.payload
        if payload is None:
            msg = f"Value event from {event.source!r} is missing payload"
            raise RuntimeError(msg)
        return payload

    async def _run(key: K, stream: EventStream) -> None:
        try:
            async for event in stream:
                if event.kind == UpdateEventKind.VALUE:
                    results[key] = _required_payload(event)
                else:
                    await queue.put(event)
        except Exception as exc:  # noqa: BLE001
            await queue.put(exc)
        finally:
            await queue.put(done)

    errors: list[Exception] = []
    remaining = len(streams)
    async with asyncio.TaskGroup() as group:
        tasks: list[asyncio.Task[None]] = []
        for key, stream in streams.items():
            tasks.append(group.create_task(_run(key, stream)))

        while remaining > 0:
            item = await queue.get()
            if item == done:
                remaining -= 1
                continue
            if isinstance(item, Exception):
                errors.append(item)
                for task in tasks:
                    if not task.done():
                        task.cancel()
                continue
            yield cast("UpdateEvent", item)

    if errors:
        if len(errors) == 1:
            raise errors[0]
        message = "; ".join(str(error) for error in errors)
        aggregate = RuntimeError(f"Multiple event streams failed: {message}")
        for error in errors:
            aggregate.add_note(repr(error))
        raise aggregate
    yield GatheredValues(results)
