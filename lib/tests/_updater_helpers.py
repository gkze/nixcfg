"""Shared helpers for updater-focused tests."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable, AsyncIterator, Coroutine, Sequence
from pathlib import Path
from types import ModuleType
from typing import Final, Protocol

from lib.import_utils import load_module_from_path
from lib.update.events import UpdateEvent
from lib.update.paths import REPO_ROOT

NO_FIXED_HASH_VALUE: Final = object()


class _MonkeyPatch(Protocol):
    def setattr(self, target: str, value: object, *, raising: bool = True) -> None: ...


def run_async[T](coro: Coroutine[object, object, T]) -> T:
    """Run one coroutine to completion."""
    return asyncio.run(coro)


async def collect_events[T](stream: AsyncIterable[T]) -> list[T]:
    """Collect every event from an async stream."""
    return [event async for event in stream]


async def empty_event_stream() -> AsyncIterator[UpdateEvent]:
    """Return an empty async event stream for tests."""
    events: tuple[UpdateEvent, ...] = ()
    for event in events:
        yield event


def load_repo_module(path: str | Path, module_name: str) -> ModuleType:
    """Load a test module from a repository-relative path."""
    module_path = Path(path)
    if not module_path.is_absolute():
        module_path = REPO_ROOT / module_path
    return load_module_from_path(module_path, module_name)


def install_fixed_hash_stream(
    monkeypatch: _MonkeyPatch,
    outputs: Sequence[tuple[str | None, object]],
    *,
    target: str = "lib.update.updaters.base.compute_fixed_output_hash",
) -> list[dict[str, object]]:
    """Patch ``compute_fixed_output_hash`` with a configured async stream."""
    calls: list[dict[str, object]] = []
    output_steps = tuple(outputs)

    async def _fixed_hash(
        name: str,
        expr: str,
        *,
        env: object = None,
        config: object = None,
    ) -> AsyncIterator[UpdateEvent]:
        index = len(calls)
        calls.append({"name": name, "expr": expr, "env": env, "config": config})
        if index >= len(output_steps):
            return
        status, value = output_steps[index]
        if status is not None:
            yield UpdateEvent.status(name, status)
        if value is not NO_FIXED_HASH_VALUE:
            yield UpdateEvent.value(name, value)

    monkeypatch.setattr(target, _fixed_hash)
    return calls
