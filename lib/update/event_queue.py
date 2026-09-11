"""Bounded event delivery with joint ownership of producers and consumers."""

import asyncio
from contextlib import suppress
from typing import TYPE_CHECKING

from lib.update.runtime import await_cleanup

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from lib.update.events import UpdateEvent

EVENT_QUEUE_SIZE = 512


async def run_event_pipeline[T](
    queue: asyncio.Queue[UpdateEvent | None],
    *,
    produce: Callable[[], Awaitable[T]],
    consume: Callable[[], Awaitable[None]],
) -> T:
    """Drain normal completion and stop blocked producers if rendering fails."""

    async def produce_and_close() -> T:
        try:
            return await produce()
        finally:
            await queue.put(None)

    async def consume_events() -> None:
        await consume()

    producer = asyncio.create_task(produce_and_close())
    consumer = asyncio.create_task(consume_events())
    try:
        done, _pending = await asyncio.wait(
            (producer, consumer), return_when=asyncio.FIRST_COMPLETED
        )
        if consumer in done:
            await consumer
            if not producer.done():
                msg = "Update event consumer stopped before the producer completed"
                raise RuntimeError(msg)
            return await producer
        try:
            result = await producer
        except Exception:
            with suppress(Exception):
                await asyncio.shield(consumer)
            raise
        await asyncio.shield(consumer)
        return result
    finally:
        # Cancellation belongs here, including when interrupted during draining.
        # Wake blocked puts before cancellation cleanup attempts to emit events.
        queue.shutdown(immediate=True)
        for task in (producer, consumer):
            if not task.done():
                task.cancel()
        await await_cleanup(asyncio.gather(producer, consumer, return_exceptions=True))
