"""Subprocess execution and hash conversion helpers for updates."""

import asyncio
import logging
import posixpath
import re
import shlex
from collections import deque
from contextlib import AsyncExitStack, aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from rich.text import Text

from lib.diagnostics import redact_urls
from lib.update.events import EventSink, ignore_event

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
    StatusInfo,
    StatusKind,
    UpdateEvent,
    UpdateEventKind,
    gather_results,
    is_nix_build_command,
)
from lib.update.runtime import (
    command_resource,
    measure,
    resource_slot,
    runtime_scope,
    workspace_access,
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
_NIX_PREFETCH_TRANSIENT_MARKERS = (
    "Could not resolve host",
    "Failure when receiving data from the peer",
    "Failed to connect",
    "HTTP error 502",
    "HTTP error 503",
    "HTTP error 504",
    "HTTP protocol violation",
    "HTTP/2 framing layer",
    "HTTP/2 stream",
    "Operation timed out",
    "Operation too slow",
    "Temporary failure in name resolution",
    "connection reset",
    "timed out",
)


@dataclass(frozen=True)
class RunCommandOptions:
    """Options controlling subprocess execution and progress reporting."""

    source: str
    command_timeout: float | None = None
    env: Mapping[str, str] | None = None
    allow_failure: bool = False
    suppress_patterns: tuple[str, ...] | None = None
    config: UpdateConfig | None = None
    uses_workspace: bool = True
    output_limit: int | None = None


@dataclass(frozen=True)
class NixBuildOptions:
    """Options controlling fixed-output ``nix build`` execution."""

    source: str
    derivation_path: str | None = None
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
        raise
    except Exception as exc:
        if not isinstance(exc, _TASK_ERROR_TYPES):
            _LOG.error(
                "Unexpected task failure for %s:\n%s",
                source,
                format_exception(exc, include_traceback=True),
            )
            raise
        _LOG.debug(
            "Handled task failure for %s:\n%s",
            source,
            format_exception(exc, include_traceback=True),
        )
        await queue.put(UpdateEvent.error(source, format_exception(exc)))


def _sanitize_log_line(line: str) -> str:
    """Strip control characters and ANSI styling from a process line."""
    line = line.replace("\r", "")
    return Text.from_ansi(line).plain if "\x1b" in line else line


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


async def _run_command(
    args: list[str],
    *,
    options: RunCommandOptions,
    emit: EventSink = ignore_event,
    **kwargs: object,
) -> CommandResult:
    """Stream subprocess progress and return its outcome after cleanup."""
    command_timeout = _resolve_timeout_alias(
        command_timeout=options.command_timeout,
        kwargs=kwargs,
    )
    config = resolve_active_config(options.config)
    if command_timeout is None:
        command_timeout = config.default_subprocess_timeout
    command_text = _truncate_command(redact_urls(shlex.join(args)))
    await emit(
        UpdateEvent(
            source=options.source,
            kind=UpdateEventKind.COMMAND_START,
            message=command_text,
            payload=args,
        )
    )

    tail_lines: deque[str] | None = None
    if is_nix_build_command(args):
        tail_lines = deque(maxlen=NIX_BUILD_FAILURE_TAIL_LINES)
    result: ProcessDone | None = None
    try:
        async with aclosing(
            stream_process(
                args,
                timeout=command_timeout,
                env=options.env,
                output_limit=options.output_limit,
            )
        ) as process_events:
            async for event in process_events:
                if isinstance(event, ProcessLine):
                    label = event.stream
                    text = event.text
                    sanitized = _sanitize_log_line(text.rstrip("\n"))
                    if sanitized:
                        if options.suppress_patterns and any(
                            pattern in sanitized
                            for pattern in options.suppress_patterns
                        ):
                            continue
                        line_text = f"[{label}] {sanitized}" if label else sanitized
                        if tail_lines is not None:
                            tail_lines.append(line_text)
                        await emit(
                            UpdateEvent(
                                source=options.source,
                                kind=UpdateEventKind.LINE,
                                message=sanitized,
                                stream=label,
                            )
                        )
                else:
                    result = event
    except TimeoutError:
        msg = f"Command timed out after {command_timeout}s: {shlex.join(args)}"
        raise RuntimeError(redact_urls(msg)) from None

    if result is None:
        msg = f"Command exited without result: {shlex.join(args)}"
        raise RuntimeError(redact_urls(msg))

    payload = CommandResult(
        args=args,
        returncode=result.result.returncode,
        stdout=result.result.stdout,
        stderr=result.result.stderr,
        allow_failure=options.allow_failure,
        tail_lines=tuple(tail_lines) if tail_lines else (),
    )
    await emit(
        UpdateEvent(
            source=options.source,
            kind=UpdateEventKind.COMMAND_END,
            payload=payload,
        )
    )
    return payload


async def run_command(
    args: list[str],
    *,
    options: RunCommandOptions,
    emit: EventSink = ignore_event,
    **kwargs: object,
) -> CommandResult:
    """Apply run-owned budgets at the process boundary, not around source work."""
    config = resolve_active_config(options.config)
    kind = command_resource(args)
    async with runtime_scope(config), AsyncExitStack() as stack:
        # Workspace admission must precede resource admission: an artifact owner
        # may need an evaluation slot while unrelated readers wait for its files.
        if kind is not None and options.uses_workspace:
            await stack.enter_async_context(workspace_access())
        if kind is None:
            timing = stack.enter_context(measure(options.source, "command"))
        else:
            timing = await stack.enter_async_context(
                resource_slot(kind, source=options.source, config=config)
            )
        result = await _run_command(args, options=options, emit=emit, **kwargs)
    timing.stdout_bytes += len(result.stdout.encode())
    timing.stderr_bytes += len(result.stderr.encode())
    timing.nonzero_exits += result.returncode != 0
    return result


async def run_nix_build(
    expr: str, *, options: NixBuildOptions, emit: EventSink = ignore_event
) -> CommandResult:
    """Run ``nix build`` and stream command events."""
    args = ["nix", "build", "-L"]
    if options.verbose:
        args.append("--verbose")
    args.append("--no-link")
    if options.derivation_path is None:
        args.extend(["--impure", "--expr", expr])
    else:
        args.append(f"{options.derivation_path}^out")
    run_options = RunCommandOptions(
        source=options.source,
        env=options.env,
        allow_failure=options.allow_failure,
        suppress_patterns=options.suppress_patterns,
        config=options.config,
        uses_workspace=options.derivation_path is None,
    )
    return await run_command(args, options=run_options, emit=emit)


async def _emit_successful_command(
    *,
    source: str,
    args: list[str],
    message: str,
    runner: Callable[[], Awaitable[str]],
    emit: EventSink = ignore_event,
) -> str:
    """Emit command lifecycle progress and return the helper result."""
    await emit(
        UpdateEvent(
            source=source,
            kind=UpdateEventKind.COMMAND_START,
            message=message,
        )
    )
    stdout = await runner()
    await emit(
        UpdateEvent(
            source=source,
            kind=UpdateEventKind.COMMAND_END,
            payload=CommandResult(
                args=args,
                returncode=0,
                stdout=stdout,
                stderr="",
            ),
        )
    )
    return stdout


async def convert_nix_hash_to_sri(
    source: str, hash_value: str, *, emit: EventSink = ignore_event
) -> str:
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
    return await _emit_successful_command(
        source=source,
        args=args,
        message=f"nix hash convert --hash-algo sha256 --to sri {hash_value}",
        runner=lambda: libnix_hash_convert(hash_value),
        emit=emit,
    )


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


def _is_retryable_prefetch_error(exc: NixCommandError) -> bool:
    output = str(exc).casefold()
    return any(
        marker.casefold() in output for marker in _NIX_PREFETCH_TRANSIENT_MARKERS
    )


async def compute_sri_hash(
    source: str, url: str, *, config: UpdateConfig, emit: EventSink = ignore_event
) -> str:
    """Prefetch a URL and return its SRI hash via :func:`lib.nix.commands.hash.nix_prefetch_url`."""
    args = ["nix", "store", "prefetch-file", "--json", "--hash-type", "sha256"]
    prefetch_name = _nix_prefetch_name(url)
    if prefetch_name is not None:
        args.extend(["--name", prefetch_name])
    args.append(url)
    attempts = max(1, config.default_retries)
    attempt = 1
    while True:
        try:
            async with resource_slot("download", source=source, config=config):
                return await _emit_successful_command(
                    source=source,
                    args=args,
                    message=shlex.join(args),
                    runner=lambda: libnix_prefetch_url(
                        url,
                        name=prefetch_name,
                        command_timeout=config.default_subprocess_timeout,
                    ),
                    emit=emit,
                )
        except NixCommandError as exc:
            if attempt >= attempts or not _is_retryable_prefetch_error(exc):
                raise
            await emit(
                UpdateEvent(
                    source=source,
                    kind=UpdateEventKind.COMMAND_END,
                    payload=CommandResult(
                        args=args,
                        returncode=exc.result.returncode,
                        stdout=exc.result.stdout,
                        stderr=exc.result.stderr,
                        allow_failure=True,
                    ),
                )
            )
            next_attempt = attempt + 1
            await emit(
                UpdateEvent.status(
                    source,
                    "URL prefetch hit a transient failure; retrying...",
                    operation="compute_hash",
                    status=StatusInfo(
                        kind=StatusKind.RETRY,
                        value=f"attempt {next_attempt}/{attempts}",
                    ),
                )
            )
            await asyncio.sleep(max(0.0, config.default_retry_backoff))
            attempt += 1


async def compute_url_hashes(
    source: str,
    urls: Iterable[str],
    *,
    config: UpdateConfig,
    emit: EventSink = ignore_event,
) -> dict[str, str]:
    """Compute SRI hashes for URLs and emit a final URL-to-hash mapping."""
    async with runtime_scope(config):
        return await gather_results({
            url: compute_sri_hash(source, url, config=config, emit=emit)
            for url in dict.fromkeys(urls)
        })
