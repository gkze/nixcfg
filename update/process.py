from __future__ import annotations

import shlex
from collections import deque
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Awaitable, Callable, Iterable, Mapping

from libnix.commands.base import ProcessDone, ProcessLine, stream_process
from libnix.commands.hash import nix_hash_convert as libnix_hash_convert
from libnix.commands.hash import nix_prefetch_url as libnix_prefetch_url
from libnix.update.events import (
    CommandResult,
    EventStream,
    GatheredValues,
    UpdateEvent,
    UpdateEventKind,
    ValueDrain,
    _is_nix_build_command,
    _require_value,
    gather_event_streams,
)
from update.config import UpdateConfig, _resolve_active_config
from update.constants import NIX_BUILD_FAILURE_TAIL_LINES
from update.errors import format_exception


async def _run_queue_task(
    *,
    source: str,
    queue: asyncio.Queue[UpdateEvent | None],
    task: Callable[[], Awaitable[None]],
) -> None:
    try:
        await task()
    except Exception as exc:
        await queue.put(UpdateEvent.error(source, format_exception(exc)))


def _sanitize_log_line(line: str) -> str:
    from rich.text import Text

    line = line.replace("\r", "")
    return Text.from_ansi(line).plain


def _truncate_command(text: str, max_len: int = 80) -> str:
    escaped = text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    if len(escaped) <= max_len:
        return escaped
    suffix = " [...]"
    trimmed = escaped[: max(0, max_len - len(suffix))].rstrip()
    return f"{trimmed}{suffix}"


async def stream_command(
    args: list[str],
    *,
    source: str,
    timeout: float | None = None,
    env: Mapping[str, str] | None = None,
    allow_failure: bool = False,
    suppress_patterns: tuple[str, ...] | None = None,
    config: UpdateConfig | None = None,
) -> EventStream:
    config = _resolve_active_config(config)
    if timeout is None:
        timeout = config.default_subprocess_timeout
    command_text = _truncate_command(shlex.join(args))
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_START,
        message=command_text,
        payload=args,
    )

    tail_lines: deque[str] | None = None
    if _is_nix_build_command(args):
        tail_lines = deque(maxlen=NIX_BUILD_FAILURE_TAIL_LINES)
    result: ProcessDone | None = None
    try:
        async for event in stream_process(args, timeout=timeout, env=env):
            if isinstance(event, ProcessLine):
                label = event.stream
                text = event.text
                sanitized = _sanitize_log_line(text.rstrip("\n"))
                if sanitized:
                    if suppress_patterns and any(
                        pattern in sanitized for pattern in suppress_patterns
                    ):
                        continue
                    line_text = f"[{label}] {sanitized}" if label else sanitized
                    if tail_lines is not None:
                        tail_lines.append(line_text)
                    yield UpdateEvent(
                        source=source,
                        kind=UpdateEventKind.LINE,
                        message=sanitized,
                        stream=label,
                    )
            else:
                result = event
    except TimeoutError:
        msg = f"Command timed out after {timeout}s: {shlex.join(args)}"
        raise RuntimeError(msg) from None

    if result is None:
        msg = f"Command exited without result: {shlex.join(args)}"
        raise RuntimeError(msg)

    payload = CommandResult(
        args=args,
        returncode=result.result.returncode,
        stdout=result.result.stdout,
        stderr=result.result.stderr,
        allow_failure=allow_failure,
        tail_lines=tuple(tail_lines) if tail_lines else (),
    )
    yield UpdateEvent(source=source, kind=UpdateEventKind.COMMAND_END, payload=payload)


async def run_command(
    args: list[str],
    *,
    source: str,
    error: str,
    env: Mapping[str, str] | None = None,
    allow_failure: bool = False,
    suppress_patterns: tuple[str, ...] | None = None,
    config: UpdateConfig | None = None,
) -> EventStream:
    result_drain = ValueDrain[CommandResult]()
    async for event in stream_command(
        args,
        source=source,
        env=env,
        allow_failure=allow_failure,
        suppress_patterns=suppress_patterns,
        config=config,
    ):
        if event.kind == UpdateEventKind.COMMAND_END and isinstance(
            event.payload, CommandResult
        ):
            result_drain.value = event.payload
        yield event
    result = _require_value(result_drain, error)
    yield UpdateEvent.value(source, result)


async def run_nix_build(
    source: str,
    expr: str,
    *,
    allow_failure: bool = False,
    suppress_patterns: tuple[str, ...] | None = None,
    env: Mapping[str, str] | None = None,
    verbose: bool = False,
    config: UpdateConfig | None = None,
) -> EventStream:
    args = ["nix", "build", "-L"]
    if verbose:
        args.append("--verbose")
    args.extend(["--no-link", "--impure", "--expr", expr])
    async for event in run_command(
        args,
        source=source,
        error="nix build did not return output",
        env=env,
        allow_failure=allow_failure,
        suppress_patterns=suppress_patterns,
        config=config,
    ):
        yield event


async def convert_nix_hash_to_sri(source: str, hash_value: str) -> EventStream:
    """Convert a hash to SRI format via :func:`libnix.commands.hash.nix_hash_convert`."""
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_START,
        message=f"nix hash convert --hash-algo sha256 --to sri {hash_value}",
    )
    sri = await libnix_hash_convert(hash_value)
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_END,
        payload=CommandResult(
            args=[
                "nix",
                "hash",
                "convert",
                "--hash-algo",
                "sha256",
                "--to",
                "sri",
                hash_value,
            ],
            returncode=0,
            stdout=sri,
            stderr="",
        ),
    )
    yield UpdateEvent.value(source, sri)


async def compute_sri_hash(source: str, url: str) -> EventStream:
    """Prefetch a URL and return its SRI hash via :func:`libnix.commands.hash.nix_prefetch_url`."""
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_START,
        message=f"nix-prefetch-url --type sha256 {url}",
    )
    sri = await libnix_prefetch_url(url)
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_END,
        payload=CommandResult(
            args=["nix-prefetch-url", "--type", "sha256", url],
            returncode=0,
            stdout=sri,
            stderr="",
        ),
    )
    yield UpdateEvent.value(source, sri)


async def compute_url_hashes(source: str, urls: Iterable[str]) -> EventStream:
    streams = {url: compute_sri_hash(source, url) for url in dict.fromkeys(urls)}
    async for item in gather_event_streams(streams):
        if isinstance(item, GatheredValues):
            yield UpdateEvent.value(source, cast("dict[str, str]", item.values))
        else:
            yield item
