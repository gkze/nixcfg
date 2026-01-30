"""Event streaming infrastructure for async update operations.

Provides a structured way to emit progress events during long-running
operations while still returning a final result value.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, AsyncIterator, Generic, TypeVar

T = TypeVar("T")


class EventKind(StrEnum):
    """Types of events emitted during updates."""

    STATUS = "status"  # Status message update
    COMMAND_START = "command_start"  # Command execution started
    LINE = "line"  # Output line from command
    COMMAND_END = "command_end"  # Command execution finished
    VALUE = "value"  # Intermediate value (extracted by collectors)
    RESULT = "result"  # Final result of operation
    ERROR = "error"  # Error occurred


@dataclass(frozen=True)
class UpdateEvent:
    """Event emitted during an update operation."""

    source: str
    kind: EventKind
    message: str | None = None
    stream: str | None = None  # "stdout" or "stderr" for LINE events
    payload: Any | None = None

    # Factory methods for common event types
    @classmethod
    def status(cls, source: str, message: str) -> UpdateEvent:
        return cls(source=source, kind=EventKind.STATUS, message=message)

    @classmethod
    def error(cls, source: str, message: str) -> UpdateEvent:
        return cls(source=source, kind=EventKind.ERROR, message=message)

    @classmethod
    def result(cls, source: str, payload: Any = None) -> UpdateEvent:
        return cls(source=source, kind=EventKind.RESULT, payload=payload)

    @classmethod
    def value(cls, source: str, payload: Any) -> UpdateEvent:
        return cls(source=source, kind=EventKind.VALUE, payload=payload)

    @classmethod
    def line(cls, source: str, message: str, stream: str = "stdout") -> UpdateEvent:
        return cls(source=source, kind=EventKind.LINE, message=message, stream=stream)

    @classmethod
    def command_start(
        cls, source: str, command_text: str, args: list[str]
    ) -> UpdateEvent:
        return cls(
            source=source,
            kind=EventKind.COMMAND_START,
            message=command_text,
            payload=args,
        )

    @classmethod
    def command_end(cls, source: str, result: "CommandResult") -> UpdateEvent:
        return cls(source=source, kind=EventKind.COMMAND_END, payload=result)


@dataclass(frozen=True)
class CommandResult:
    """Result of a command execution."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def success(self) -> bool:
        return self.returncode == 0


# Type alias for async event streams
EventStream = AsyncIterator[UpdateEvent]


# =============================================================================
# Event Collection Utilities
# =============================================================================


@dataclass
class EventCollector(Generic[T]):
    """Collects events from a stream, extracting VALUE payloads.

    This replaces the awkward ValueDrain + drain_value_events pattern
    with a more intuitive interface.

    Usage:
        collector = EventCollector[str]()
        async for event in collector.collect(some_event_stream()):
            yield event  # Pass through non-VALUE events
        result = collector.require_value("Expected a hash")
    """

    value: T | None = None
    events: list[UpdateEvent] = field(default_factory=list)

    async def collect(self, stream: EventStream) -> EventStream:
        """Consume stream, capturing VALUE events and yielding others."""
        async for event in stream:
            if event.kind == EventKind.VALUE:
                self.value = event.payload
            else:
                self.events.append(event)
                yield event

    def require_value(self, error_message: str) -> T:
        """Get the collected value, raising if none was captured."""
        if self.value is None:
            raise RuntimeError(error_message)
        return self.value

    def get_value(self, default: T | None = None) -> T | None:
        """Get the collected value or a default."""
        return self.value if self.value is not None else default


async def collect_value(
    stream: EventStream, error_message: str
) -> tuple[Any, list[UpdateEvent]]:
    """Convenience function to collect a stream and extract its value.

    Returns (value, list_of_other_events).
    Raises RuntimeError if no VALUE event was emitted.
    """
    collector: EventCollector[Any] = EventCollector()
    events = [event async for event in collector.collect(stream)]
    return collector.require_value(error_message), events


async def forward_events(stream: EventStream) -> EventStream:
    """Simply forward all events from a stream (identity transform)."""
    async for event in stream:
        yield event


async def forward_collecting_value(
    stream: EventStream, collector: EventCollector[Any]
) -> EventStream:
    """Forward events while collecting VALUE into a collector."""
    async for event in collector.collect(stream):
        yield event
