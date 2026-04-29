"""Subprocess execution and hash conversion helpers for updates."""

from __future__ import annotations

import asyncio
import logging
import posixpath
import re
import shlex
from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from rich.text import Text

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable, Mapping

from lib.nix.commands.base import (
    NixCommandError,
    ProcessDone,
    ProcessLine,
    stream_process,
)
from lib.nix.commands.hash import nix_hash_convert as libnix_hash_convert
from lib.nix.commands.hash import nix_prefetch_url as libnix_prefetch_url
from lib.update.config import UpdateConfig, resolve_active_config
from lib.update.constants import NIX_BUILD_FAILURE_TAIL_LINES, resolve_timeout_alias
from lib.update.errors import format_exception
from lib.update.events import (
    CommandResult,
    EventStream,
    GatheredValues,
    UpdateEvent,
    UpdateEventKind,
    ValueDrain,
    expect_str,
    gather_event_streams,
    is_nix_build_command,
    require_value,
)

_TASK_ERROR_TYPES: tuple[type[Exception], ...] = (
    RuntimeError,
    ValueError,
    TypeError,
    OSError,
    KeyError,
    NixCommandError,
)
_LOG = logging.getLogger(__name__)
_NIX_STORE_NAME_UNSAFE_RE = re.compile(r"[^A-Za-z0-9+._?=-]+")


@dataclass(frozen=True)
class StreamCommandOptions:
    """Options controlling streamed subprocess execution."""

    source: str
    command_timeout: float | None = None
    env: Mapping[str, str] | None = None
    allow_failure: bool = False
    suppress_patterns: tuple[str, ...] | None = None
    config: UpdateConfig | None = None


@dataclass(frozen=True)
class RunCommandOptions:
    """Options controlling buffered subprocess execution."""

    source: str
    error: str
    env: Mapping[str, str] | None = None
    allow_failure: bool = False
    suppress_patterns: tuple[str, ...] | None = None
    config: UpdateConfig | None = None


@dataclass(frozen=True)
class NixBuildOptions:
    """Options controlling fixed-output ``nix build`` execution."""

    source: str
    allow_failure: bool = False
    suppress_patterns: tuple[str, ...] | None = None
    env: Mapping[str, str] | None = None
    verbose: bool = False
    config: UpdateConfig | None = None


async def run_queue_task(
    *,
    source: str,
    queue: asyncio.Queue[UpdateEvent | None],
    task: Callable[[], Awaitable[None]],
) -> None:
    """Run ``task`` and translate failures into queued error events."""
    try:
        await task()
    except asyncio.CancelledError:
        await queue.put(UpdateEvent.error(source, "Operation cancelled"))
    except Exception as exc:
        if not isinstance(exc, _TASK_ERROR_TYPES):
            _LOG.exception("Unexpected task failure for %s", source)
            raise
        _LOG.debug("Handled task failure for %s", source, exc_info=exc)
        await queue.put(UpdateEvent.error(source, format_exception(exc)))


def _sanitize_log_line(line: str) -> str:
    """Strip control characters and ANSI styling from a process line."""
    line = line.replace("\r", "")
    return Text.from_ansi(line).plain


def _truncate_command(text: str, max_len: int = 80) -> str:
    escaped = text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    if len(escaped) <= max_len:
        return escaped
    suffix = " [...]"
    trimmed = escaped[: max(0, max_len - len(suffix))].rstrip()
    return f"{trimmed}{suffix}"


def _resolve_timeout_alias(
    *,
    command_timeout: float | None,
    kwargs: dict[str, object],
) -> float | None:
    return resolve_timeout_alias(
        named_timeout=command_timeout,
        named_timeout_label="command_timeout",
        kwargs=kwargs,
    )


async def stream_command(
    args: list[str],
    *,
    options: StreamCommandOptions,
    **kwargs: object,
) -> EventStream:
    """Stream subprocess lifecycle events and output lines."""
    command_timeout = _resolve_timeout_alias(
        command_timeout=options.command_timeout,
        kwargs=kwargs,
    )
    config = resolve_active_config(options.config)
    if command_timeout is None:
        command_timeout = config.default_subprocess_timeout
    command_text = _truncate_command(shlex.join(args))
    yield UpdateEvent(
        source=options.source,
        kind=UpdateEventKind.COMMAND_START,
        message=command_text,
        payload=args,
    )

    tail_lines: deque[str] | None = None
    if is_nix_build_command(args):
        tail_lines = deque(maxlen=NIX_BUILD_FAILURE_TAIL_LINES)
    result: ProcessDone | None = None
    try:
        async for event in stream_process(
            args,
            timeout=command_timeout,
            env=options.env,
        ):
            if isinstance(event, ProcessLine):
                label = event.stream
                text = event.text
                sanitized = _sanitize_log_line(text.rstrip("\n"))
                if sanitized:
                    if options.suppress_patterns and any(
                        pattern in sanitized for pattern in options.suppress_patterns
                    ):
                        continue
                    line_text = f"[{label}] {sanitized}" if label else sanitized
                    if tail_lines is not None:
                        tail_lines.append(line_text)
                    yield UpdateEvent(
                        source=options.source,
                        kind=UpdateEventKind.LINE,
                        message=sanitized,
                        stream=label,
                    )
            else:
                result = event
    except TimeoutError:
        msg = f"Command timed out after {command_timeout}s: {shlex.join(args)}"
        raise RuntimeError(msg) from None

    if result is None:
        msg = f"Command exited without result: {shlex.join(args)}"
        raise RuntimeError(msg)

    payload = CommandResult(
        args=args,
        returncode=result.result.returncode,
        stdout=result.result.stdout,
        stderr=result.result.stderr,
        allow_failure=options.allow_failure,
        tail_lines=tuple(tail_lines) if tail_lines else (),
    )
    yield UpdateEvent(
        source=options.source,
        kind=UpdateEventKind.COMMAND_END,
        payload=payload,
    )


async def run_command(
    args: list[str],
    *,
    options: RunCommandOptions,
) -> EventStream:
    """Run a command and emit both command events and final VALUE result."""
    result_drain = ValueDrain[CommandResult]()
    stream_options = StreamCommandOptions(
        source=options.source,
        env=options.env,
        allow_failure=options.allow_failure,
        suppress_patterns=options.suppress_patterns,
        config=options.config,
    )
    async for event in stream_command(
        args,
        options=stream_options,
    ):
        if event.kind == UpdateEventKind.COMMAND_END and isinstance(
            event.payload,
            CommandResult,
        ):
            result_drain.value = event.payload
        yield event
    result = require_value(result_drain, options.error)
    yield UpdateEvent.value(options.source, result)


async def run_nix_build(
    expr: str,
    *,
    options: NixBuildOptions,
) -> EventStream:
    """Run ``nix build`` and stream command events."""
    args = ["nix", "build", "-L"]
    if options.verbose:
        args.append("--verbose")
    args.extend(["--no-link", "--impure", "--expr", expr])
    run_options = RunCommandOptions(
        source=options.source,
        error="nix build did not return output",
        env=options.env,
        allow_failure=options.allow_failure,
        suppress_patterns=options.suppress_patterns,
        config=options.config,
    )
    async for event in run_command(
        args,
        options=run_options,
    ):
        yield event


async def _emit_successful_command(
    *,
    source: str,
    args: list[str],
    message: str,
    runner: Callable[[], Awaitable[str]],
) -> EventStream:
    """Emit COMMAND_START/END + VALUE events for an async command helper."""
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_START,
        message=message,
    )
    stdout = await runner()
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_END,
        payload=CommandResult(
            args=args,
            returncode=0,
            stdout=stdout,
            stderr="",
        ),
    )
    yield UpdateEvent.value(source, stdout)


async def convert_nix_hash_to_sri(source: str, hash_value: str) -> EventStream:
    """Convert a hash to SRI format via :func:`lib.nix.commands.hash.nix_hash_convert`."""
    args = [
        "nix",
        "hash",
        "convert",
        "--hash-algo",
        "sha256",
        "--to",
        "sri",
        hash_value,
    ]
    async for event in _emit_successful_command(
        source=source,
        args=args,
        message=f"nix hash convert --hash-algo sha256 --to sri {hash_value}",
        runner=lambda: libnix_hash_convert(hash_value),
    ):
        yield event


def _nix_prefetch_name(url: str) -> str | None:
    """Return a safe override name when ``nix-prefetch-url`` would infer a bad one."""
    basename = posixpath.basename(urlparse(url).path)
    if not basename:
        return None
    decoded = unquote(basename)
    safe_name = _NIX_STORE_NAME_UNSAFE_RE.sub("-", decoded).strip("-")
    if not safe_name or safe_name == decoded:
        return None
    return safe_name


async def compute_sri_hash(source: str, url: str) -> EventStream:
    """Prefetch a URL and return its SRI hash via :func:`lib.nix.commands.hash.nix_prefetch_url`."""
    args = ["nix-prefetch-url", "--type", "sha256"]
    prefetch_name = _nix_prefetch_name(url)
    if prefetch_name is not None:
        args.extend(["--name", prefetch_name])
    args.append(url)
    async for event in _emit_successful_command(
        source=source,
        args=args,
        message=shlex.join(args),
        runner=lambda: libnix_prefetch_url(url, name=prefetch_name),
    ):
        yield event


async def compute_url_hashes(source: str, urls: Iterable[str]) -> EventStream:
    """Compute SRI hashes for URLs and emit a final URL-to-hash mapping."""
    streams: dict[str, EventStream] = {
        url: compute_sri_hash(source, url) for url in dict.fromkeys(urls)
    }
    async for item in gather_event_streams(streams):
        if isinstance(item, GatheredValues):
            hash_mapping: dict[str, str] = {}
            for url, hash_value in item.values.items():
                if not isinstance(url, str):
                    msg = f"Expected URL key to be str, got {type(url)}"
                    raise TypeError(msg)
                hash_mapping[url] = expect_str(hash_value)
            yield UpdateEvent.value(source, hash_mapping)
        else:
            yield item
