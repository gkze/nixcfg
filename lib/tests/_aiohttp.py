"""Shared aiohttp session helpers for test modules."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import aiohttp

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


def run_with_session[T](run: Callable[[aiohttp.ClientSession], Awaitable[T]]) -> T:
    """Run an async callable with a temporary ``aiohttp`` client session."""

    async def _runner() -> T:
        async with aiohttp.ClientSession() as session:
            return await run(session)

    return asyncio.run(_runner())


def async_result[T](value: T) -> Callable[..., Awaitable[T]]:
    """Return a callable that resolves to ``value`` when awaited."""
    return lambda *_a, **_k: asyncio.sleep(0, result=value)
