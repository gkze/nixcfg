"""Shared helpers for updater-focused tests."""

import asyncio
from collections.abc import (
    Awaitable,
    Callable,
    Coroutine,
    Sequence,
)
from pathlib import Path
from types import ModuleType
from typing import Final, Protocol

from lib.import_utils import load_module_from_path
from lib.update.events import EventSink, UpdateEvent, ignore_event
from lib.update.paths import REPO_ROOT

NO_FIXED_HASH_VALUE: Final = object()


class _MonkeyPatch(Protocol):
    def setattr(self, target: str, value: object, *, raising: bool = True) -> None: ...


def run_async[T](coro: Coroutine[object, object, T]) -> T:
    """Run one coroutine to completion."""
    return asyncio.run(coro)


class CapturedEvents[T](list[UpdateEvent]):
    """Real progress events and the independently returned operation value."""

    def __init__(self, events: list[UpdateEvent], result: T) -> None:
        super().__init__(events)
        self.result = result


async def collect_events[T](
    operation: Callable[[EventSink], Awaitable[T]],
) -> CapturedEvents[T]:
    """Run an operation with an explicit sink and capture its typed return."""
    events: list[UpdateEvent] = []

    async def emit(event: UpdateEvent) -> None:
        events.append(event)

    result = await operation(emit)
    return CapturedEvents(events, result)


async def empty_event_stream(*, emit: EventSink = ignore_event) -> None:
    """Complete an operation that has no result or progress to report."""
    _ = emit


def load_repo_module(path: str | Path, module_name: str) -> ModuleType:
    """Load a test module from a repository-relative path."""
    module_path = Path(path)
    if not module_path.is_absolute():
        module_path = REPO_ROOT / module_path
    return load_module_from_path(module_path, module_name)


def module_name_for_path(path: str | Path, *, prefix: str) -> str:
    """Build a stable throwaway module name from a repo-relative path."""
    safe_path = str(path).replace("/", "_").replace("-", "_").replace(".", "_")
    return f"{prefix}_{safe_path}"


def load_repo_module_for_test(path: str | Path, *, prefix: str) -> ModuleType:
    """Load a repo module with a path-derived test module name."""
    return load_repo_module(path, module_name_for_path(path, prefix=prefix))


def updater_from_module(module: ModuleType) -> object:
    """Instantiate the updater class defined by a loaded updater module."""
    for value in vars(module).values():
        if (
            isinstance(value, type)
            and value.__name__.endswith("Updater")
            and value.__module__ == module.__name__
        ):
            return value()
    msg = f"No updater class found in {module.__name__}"
    raise AssertionError(msg)


def install_fixed_hash_stream(
    monkeypatch: _MonkeyPatch,
    outputs: Sequence[tuple[str | None, object]],
    *,
    target: str = "lib.update.nix.compute_fixed_output_hash",
) -> list[dict[str, object]]:
    """Patch ``compute_fixed_output_hash`` with a configured async stream."""
    calls: list[dict[str, object]] = []
    output_steps = tuple(outputs)

    async def _fixed_hash(
        name: str,
        expr: str,
        *,
        isolate_by_drv_hash: bool = False,
        env: object = None,
        config: object = None,
        emit: EventSink = ignore_event,
    ) -> object:
        index = len(calls)
        call = {
            "name": name,
            "expr": expr,
            "env": env,
            "config": config,
        }
        if isolate_by_drv_hash:
            call["isolate_by_drv_hash"] = True
        calls.append(call)
        if index >= len(output_steps):
            return None
        status, value = output_steps[index]
        if status is not None:
            await emit(UpdateEvent.status(name, status))
        if value is not NO_FIXED_HASH_VALUE:
            return value
        return None

    monkeypatch.setattr(target, _fixed_hash)
    return calls
