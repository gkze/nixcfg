"""Behavioral tests for bounded event delivery and task ownership."""

import asyncio

import pytest

from lib.update.event_queue import run_event_pipeline
from lib.update.events import UpdateEvent


def test_pipeline_drains_backpressure_and_returns_result() -> None:
    """A slow renderer receives every event while memory stays bounded."""

    async def run() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue(maxsize=1)
        received: list[str] = []

        async def produce() -> str:
            for index in range(20):
                await queue.put(UpdateEvent.status("source", str(index)))
            return "finished"

        async def consume() -> None:
            while (event := await queue.get()) is not None:
                received.append(event.message or "")
                await asyncio.sleep(0)

        assert (
            await run_event_pipeline(queue, produce=produce, consume=consume)
            == "finished"
        )
        assert received == [str(index) for index in range(20)]

    asyncio.run(run())


def test_pipeline_drains_before_propagating_phase_error() -> None:
    """The original phase error survives successful renderer cleanup."""

    async def run() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue(maxsize=1)
        closed = asyncio.Event()

        async def produce() -> None:
            raise ValueError("phase failed")

        async def consume() -> None:
            assert await queue.get() is None
            await asyncio.sleep(0)
            closed.set()

        with pytest.raises(ValueError, match="phase failed"):
            await run_event_pipeline(queue, produce=produce, consume=consume)
        assert closed.is_set()

    asyncio.run(run())


@pytest.mark.parametrize("fail", [False, True])
def test_pipeline_stops_producer_when_renderer_exits(*, fail: bool) -> None:
    """Renderer failure or early return cannot strand a blocked queue put."""

    async def run() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue(maxsize=1)
        closed = asyncio.Event()
        started = asyncio.Event()

        async def produce() -> None:
            try:
                await queue.put(UpdateEvent.status("source", "first"))
                started.set()
                await queue.put(UpdateEvent.status("source", "blocked"))
            finally:
                closed.set()

        async def consume() -> None:
            await started.wait()
            if fail:
                raise RuntimeError("renderer failed")

        with pytest.raises(RuntimeError, match="renderer failed|consumer stopped"):
            async with asyncio.timeout(1):
                await run_event_pipeline(queue, produce=produce, consume=consume)
        assert closed.is_set()

    asyncio.run(run())


def test_pipeline_cancellation_joins_both_tasks() -> None:
    """Cancellation wakes blocked puts and joins consumer cleanup."""

    async def run() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue(maxsize=1)
        started = asyncio.Event()
        producer_closed = asyncio.Event()
        consumer_closed = asyncio.Event()

        async def produce() -> None:
            try:
                started.set()
                await asyncio.Future()
            finally:
                producer_closed.set()

        async def consume() -> None:
            try:
                await queue.get()
            finally:
                consumer_closed.set()

        pipeline = asyncio.create_task(
            run_event_pipeline(queue, produce=produce, consume=consume)
        )
        await started.wait()
        pipeline.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pipeline
        assert producer_closed.is_set()
        assert consumer_closed.is_set()

    asyncio.run(run())


def test_pipeline_repeated_cancellation_waits_for_producer_cleanup() -> None:
    """Repeated stop requests must not interrupt restoration by the producer."""

    async def run() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue(maxsize=1)
        started = asyncio.Event()
        cleaning = asyncio.Event()
        finish = asyncio.Event()
        restored = asyncio.Event()

        async def produce() -> None:
            started.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await finish.wait()
                restored.set()

        async def consume() -> None:
            await queue.get()

        pipeline = asyncio.create_task(
            run_event_pipeline(queue, produce=produce, consume=consume)
        )
        await started.wait()
        pipeline.cancel()
        await cleaning.wait()
        pipeline.cancel()
        try:
            await asyncio.sleep(0)
            assert not pipeline.done()
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await pipeline
        assert restored.is_set()

    asyncio.run(run())


@pytest.mark.parametrize("failed", [False, True])
def test_pipeline_repeated_cancellation_joins_draining_renderer(
    *, failed: bool
) -> None:
    """Drain cancellation reaches renderer cleanup once even after a phase error."""

    async def run() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue(maxsize=1)
        draining = asyncio.Event()
        cleaning = asyncio.Event()
        finish = asyncio.Event()
        restored = asyncio.Event()

        async def produce() -> None:
            if failed:
                raise RuntimeError("phase failed")

        async def consume() -> None:
            assert await queue.get() is None
            draining.set()
            try:
                await asyncio.Future()
            finally:
                cleaning.set()
                await finish.wait()
                restored.set()

        pipeline = asyncio.create_task(
            run_event_pipeline(queue, produce=produce, consume=consume)
        )
        await draining.wait()
        await asyncio.sleep(0)
        pipeline.cancel()
        await cleaning.wait()
        try:
            for _ in range(3):
                pipeline.cancel()
                await asyncio.sleep(0)
            assert not pipeline.done()
        finally:
            finish.set()
        with pytest.raises(asyncio.CancelledError):
            await pipeline
        assert restored.is_set()

    asyncio.run(run())


@pytest.mark.parametrize("failed", [False, True])
def test_pipeline_waits_for_delayed_renderer_after_production(*, failed: bool) -> None:
    """A completed phase keeps its result or original failure while rendering drains."""

    async def run() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue(maxsize=1)
        draining = asyncio.Event()
        finish = asyncio.Event()

        async def produce() -> str:
            if failed:
                raise ValueError("phase failed")
            return "finished"

        async def consume() -> None:
            assert await queue.get() is None
            draining.set()
            await finish.wait()
            if failed:
                raise RuntimeError("renderer cleanup failed")

        pipeline = asyncio.create_task(
            run_event_pipeline(queue, produce=produce, consume=consume)
        )
        await draining.wait()
        await asyncio.sleep(0)
        assert not pipeline.done()
        finish.set()
        if failed:
            with pytest.raises(ValueError, match="phase failed"):
                await pipeline
        else:
            assert await pipeline == "finished"

    asyncio.run(run())


def test_pipeline_cancellation_interrupts_failed_phase_drain() -> None:
    """Cancellation while draining an earlier phase error must remain cancellation."""

    async def run() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue(maxsize=1)
        draining = asyncio.Event()
        consumer_closed = asyncio.Event()

        async def produce() -> None:
            raise ValueError("earlier phase failed")

        async def consume() -> None:
            try:
                assert await queue.get() is None
                draining.set()
                await asyncio.Future()
            finally:
                consumer_closed.set()

        pipeline = asyncio.create_task(
            run_event_pipeline(queue, produce=produce, consume=consume)
        )
        await draining.wait()
        await asyncio.sleep(0)
        pipeline.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pipeline
        assert consumer_closed.is_set()

    asyncio.run(run())


def test_pipeline_renderer_failure_wakes_producer_cleanup_event() -> None:
    """Producer cleanup cannot deadlock by emitting into the renderer's full queue."""

    async def run() -> None:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue(maxsize=1)
        started = asyncio.Event()
        closed = asyncio.Event()

        async def produce() -> None:
            try:
                await queue.put(UpdateEvent.status("source", "first"))
                started.set()
                await queue.put(UpdateEvent.status("source", "blocked"))
            finally:
                try:
                    await queue.put(UpdateEvent.status("source", "cleanup"))
                finally:
                    closed.set()

        async def consume() -> None:
            await started.wait()
            raise RuntimeError("renderer failed")

        with pytest.raises(RuntimeError, match="renderer failed"):
            async with asyncio.timeout(1):
                await run_event_pipeline(queue, produce=produce, consume=consume)
        assert closed.is_set()

    asyncio.run(run())
