"""Tests for process task runners and error formatting helpers."""

from __future__ import annotations

import asyncio

import pytest

from lib.nix.tests._assertions import check
from lib.update.errors import format_exception
from lib.update.events import UpdateEvent, UpdateEventKind
from lib.update.process import run_queue_task


class _CustomProcessError(Exception):
    pass


BAD_INPUT_MESSAGE = "bad input"
TASK_ERROR_MESSAGE = "boom"
UNEXPECTED_ERROR_MESSAGE = "unexpected"


def _raise_bad_input(message: str = BAD_INPUT_MESSAGE) -> None:
    raise ValueError(message)


async def _raise_value_error(message: str = TASK_ERROR_MESSAGE) -> None:
    raise ValueError(message)


async def _raise_cancelled_error() -> None:
    raise asyncio.CancelledError


async def _raise_unexpected(message: str = UNEXPECTED_ERROR_MESSAGE) -> None:
    raise _CustomProcessError(message)


def test_format_exception_suppresses_traceback_by_default() -> None:
    """Keep user-facing errors concise by default."""
    with pytest.raises(ValueError, match=BAD_INPUT_MESSAGE) as exc_info:
        _raise_bad_input()

    formatted = format_exception(exc_info.value)

    check(formatted == "bad input")
    check("Traceback" not in formatted)


def test_format_exception_includes_traceback_when_enabled() -> None:
    """Support explicit traceback rendering for deep debugging."""
    with pytest.raises(ValueError, match=BAD_INPUT_MESSAGE) as exc_info:
        _raise_bad_input()

    formatted = format_exception(exc_info.value, include_traceback=True)

    check(formatted.startswith("bad input"))
    check("ValueError" in formatted)


def test_run_queue_task_reports_known_runtime_errors_to_events() -> None:
    """Translate known task exceptions into update error events."""

    async def _task() -> None:
        await _raise_value_error()

    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    asyncio.run(run_queue_task(source="demo", queue=queue, task=_task))

    event = queue.get_nowait()
    if not isinstance(event, UpdateEvent):
        msg = f"Expected UpdateEvent, got {type(event)}"
        raise TypeError(msg)
    check(event.kind == UpdateEventKind.ERROR)
    check(event.message == "boom")


def test_run_queue_task_reports_cancelled_as_error_event() -> None:
    """Keep cancellation visible in the queue consumer."""

    async def _task() -> None:
        await _raise_cancelled_error()

    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    asyncio.run(run_queue_task(source="demo", queue=queue, task=_task))

    event = queue.get_nowait()
    if not isinstance(event, UpdateEvent):
        msg = f"Expected UpdateEvent, got {type(event)}"
        raise TypeError(msg)
    check(event.message == "Operation cancelled")


def test_run_queue_task_re_raises_unknown_exceptions() -> None:
    """Propagate unexpected exceptions so bug-level failures stay visible."""

    async def _task() -> None:
        await _raise_unexpected()

    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    with pytest.raises(_CustomProcessError):
        asyncio.run(run_queue_task(source="demo", queue=queue, task=_task))

    check(queue.empty())
