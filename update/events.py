"""Event models and stream helpers for updater workflows."""

import asyncio
from collections.abc import AsyncGenerator, AsyncIterator
from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict, cast

from libnix.models.sources import SourceEntry, SourceHashes


def _is_nix_build_command(args: list[str] | None) -> bool:
    return bool(args) and args[:2] == ["nix", "build"]


class UpdateEventKind(StrEnum):
    """Kinds of events emitted by update tasks."""

    STATUS = "status"
    COMMAND_START = "command_start"
    LINE = "line"
    COMMAND_END = "command_end"
    VALUE = "value"
    RESULT = "result"
    ERROR = "error"


class RefUpdatePayload(TypedDict):
    """Payload emitted when a flake ref moves from current to latest."""

    current: str
    latest: str


type CommandArgs = list[str]
type PlatformHash = tuple[str, str]


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
    CommandArgs
    | CommandResult
    | SourceEntry
    | SourceHashes
    | PlatformHash
    | str
    | RefUpdatePayload
)


@dataclass(frozen=True)
class UpdateEvent:
    """Single event emitted during update processing."""

    source: str
    kind: UpdateEventKind
    message: str | None = None
    stream: str | None = None
    payload: UpdateEventPayload | None = None

    @classmethod
    def status(cls, source: str, message: str) -> UpdateEvent:
        """Create a status event."""
        return cls(source=source, kind=UpdateEventKind.STATUS, message=message)

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
) -> EventStream:
    """Yield non-VALUE events while storing VALUE payloads in ``drain``."""
    async for event in events:
        if event.kind == UpdateEventKind.VALUE:
            drain.value = cast("T", event.payload)
        else:
            yield event


def _require_value[T](drain: ValueDrain[T], error: str) -> T:
    if drain.value is None:
        raise RuntimeError(error)
    return drain.value


@dataclass(frozen=True)
class CapturedValue[T]:
    """Wrapper for a required value captured from an ``EventStream``."""

    captured: T


async def capture_stream_value[T](
    events: EventStream,
    *,
    error: str,
) -> AsyncGenerator[UpdateEvent | CapturedValue[T]]:
    """Yield non-VALUE events, then emit one :class:`CapturedValue`.

    This wraps the common ``ValueDrain`` + ``drain_value_events`` +
    ``_require_value`` sequence while preserving streaming behavior.
    """
    drain = ValueDrain[T]()
    async for event in drain_value_events(events, drain):
        yield event
    yield CapturedValue(_require_value(drain, error))


@dataclass(frozen=True)
class GatheredValues[K, V]:
    """Wrapper for the collected values from :func:`gather_event_streams`."""

    values: dict[K, V]


async def gather_event_streams[K, V](
    streams: dict[K, EventStream],
) -> AsyncGenerator[UpdateEvent | GatheredValues[K, V]]:
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
    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    results: dict[K, V] = {}

    async def _run(key: K, stream: EventStream) -> None:
        async for event in stream:
            if event.kind == UpdateEventKind.VALUE:
                results[key] = cast("V", event.payload)
            else:
                await queue.put(event)

    async def _wait(tasks: list[asyncio.Task[None]]) -> None:
        results = await asyncio.gather(*tasks, return_exceptions=True)
        await queue.put(None)  # sentinel
        for result in results:
            if isinstance(result, BaseException):
                raise result

    tasks = [asyncio.create_task(_run(k, s)) for k, s in streams.items()]
    waiter = asyncio.create_task(_wait(tasks))

    while True:
        event = await queue.get()
        if event is None:
            break
        yield event

    await waiter
    yield GatheredValues(results)
