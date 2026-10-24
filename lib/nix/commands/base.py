"""Core async subprocess infrastructure for running Nix commands.

Provides :class:`CommandResult` for structured output, :func:`run_nix` for
collected execution, and :func:`stream_nix` for line-by-line streaming.
Hash-mismatch errors from fixed-output derivations are captured as
:class:`HashMismatchError` with pre-extracted hashes (SRI when available,
raw otherwise — use :attr:`is_sri` to check and :meth:`to_sri` to convert).
"""

import asyncio
import importlib
import os
import re
import shlex
import sys
from collections import deque
from contextlib import suppress
from dataclasses import dataclass, field
from functools import partial
from typing import TYPE_CHECKING

from lib.diagnostics import redact_urls

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
    from typing import Self

# ---------------------------------------------------------------------------
# Result / error types
# ---------------------------------------------------------------------------

# Matches a well-formed SRI string: <algo>-<base64digest>
_SRI_RE = re.compile(r"^(?:blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$")

# --- Hash algorithm names Nix can produce ---
_HASH_ALGOS = r"(?:blake3|md5|sha1|sha256|sha512)"

# --- Primary: SRI format (algorithm-base64digest) ---
# Example: sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=
# --- Fallback: prefixed hex (algo:hex), bare hex, or Nix32 ---
# Nix32 uses the alphabet 0123456789abcdfghijklmnpqrsvwxyz (32 chars)
# sha256 lengths: hex = 64 chars, Nix32 = 52 chars
# sha512 lengths: hex = 128 chars, Nix32 = 103 chars
# sha1   lengths: hex = 40 chars, Nix32 = 32 chars
_HASH_VALUE = (
    _HASH_ALGOS
    + r"-[A-Za-z0-9+/]+=*|"
    + _HASH_ALGOS
    + r":[0-9a-fA-F]+"  # algo:hex (e.g., sha256:abc123...)
    + r"|[0-9a-fA-F]{40,128}"  # bare hex (40=sha1, 64=sha256, 128=sha512)
    + r"|[0-9a-df-np-sv-z]{32,103}"  # Nix32 encoding
)
_RE_GOT = re.compile(r"got:\s*(" + _HASH_VALUE + r")")
_RE_SPECIFIED = re.compile(r"specified:\s*(" + _HASH_VALUE + r")")

# --- Derivation path extraction ---
# Matches both "hash mismatch in fixed-output derivation" and
# "ca hash mismatch importing path" from local-store.cc
_RE_DRV_PATH = re.compile(
    r"(?:hash mismatch in fixed-output derivation|"
    r"(?:ca )?hash mismatch importing path)"
    r"\s+'([^']+)'",
)

_STDERR_TAIL_LINES = 20
_STREAM_QUEUE_SIZE = 128


def _raise_timeout() -> None:
    """Raise ``TimeoutError`` for timeout control-flow paths."""
    raise TimeoutError


@dataclass(frozen=True)
class CommandResult:
    """Immutable record of a completed subprocess invocation."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class NixCommandError(Exception):
    """A Nix command exited with a non-zero return code."""

    def __init__(self, result: CommandResult, message: str | None = None) -> None:
        """Create a command error from a failed subprocess result."""
        self.result = result
        self.message = message or f"command failed with exit code {result.returncode}"
        super().__init__(self.message)

    def __reduce__(self) -> tuple[Callable[[], Self], tuple[()], dict[str, object]]:
        """Preserve command diagnostics and exception notes across step replay."""
        return partial(type(self), self.result, self.message), (), self.__dict__

    def __str__(self) -> str:
        """Render a readable error message with command context."""
        cmd = shlex.join(self.result.args)
        parts = [
            f"NixCommandError: {self.message}",
            f"  command:     {cmd}",
            f"  returncode:  {self.result.returncode}",
        ]
        stderr_tail = self.result.stderr.strip()
        if stderr_tail:
            # Show at most the last 20 lines to keep output readable.
            lines = stderr_tail.splitlines()
            if len(lines) > _STDERR_TAIL_LINES:
                lines = lines[-_STDERR_TAIL_LINES:]
                parts.append(f"  stderr (last {_STDERR_TAIL_LINES} lines):")
            else:
                parts.append("  stderr:")
            parts.extend(f"    {line}" for line in lines)
        return redact_urls("\n".join(parts))


class HashMismatchError(NixCommandError):
    """A fixed-output derivation reported a hash mismatch.

    This is the **single** regex extraction point for Nix hash-mismatch
    output.  All call-sites should go through :meth:`from_stderr` rather
    than re-implementing the regex logic.

    The extracted :attr:`hash` preserves the original format from Nix output
    (SRI, ``algo:hex``, bare hex, or Nix32).  Use :attr:`is_sri` to check
    whether conversion is needed and :meth:`to_sri` to obtain an SRI string.
    """

    def __init__(
        self,
        result: CommandResult,
        *,
        got_hash: str,
        specified: str | None = None,
        drv_path: str | None = None,
    ) -> None:
        """Create an error with parsed hash-mismatch details."""
        self.hash = got_hash
        self.specified = specified
        self.drv_path = drv_path
        msg = f"hash mismatch — got {got_hash}"
        if drv_path:
            msg += f" (derivation: {drv_path})"
        super().__init__(result, msg)

    def __reduce__(self) -> tuple[Callable[[], Self], tuple[()], dict[str, object]]:
        """Reconstruct the keyword-only hash contract before restoring error state."""
        return (
            partial(
                type(self),
                self.result,
                got_hash=self.hash,
                specified=self.specified,
                drv_path=self.drv_path,
            ),
            (),
            self.__dict__,
        )

    @property
    def is_sri(self) -> bool:
        """``True`` when :attr:`hash` is already in SRI format."""
        return bool(_SRI_RE.match(self.hash))

    async def to_sri(self, *, hash_algo: str = "sha256") -> str:
        """Return :attr:`hash` as an SRI string, converting if necessary.

        When the captured hash is already SRI this is a no-op.  Otherwise
        ``nix hash convert`` is called (requires a running event loop).

        Parameters
        ----------
        hash_algo:
            Algorithm hint passed to ``nix hash convert`` when the captured
            hash has no embedded algorithm prefix (bare hex / Nix32).
            Ignored when the hash is already SRI or prefixed (``algo:hex``).

        """
        if self.is_sri:
            return self.hash

        hash_module = importlib.import_module("lib.nix.commands.hash")
        return await hash_module.nix_hash_convert(self.hash, hash_algo=hash_algo)

    @classmethod
    def from_output(
        cls,
        output: str,
        result: CommandResult,
    ) -> HashMismatchError | None:
        """Try to parse a hash mismatch from Nix command output.

        Parameters
        ----------
        output:
            Combined stderr+stdout text from a Nix command.  The hash
            mismatch may appear in either stream depending on Nix version
            and output routing.
        result:
            The :class:`CommandResult` for context in the exception.

        Selects the last hash and associates only the path and specified hash
        in that same diagnostic block. Nested failures cannot cross-pair one
        derivation's identity with another derivation's hash.

        Returns ``None`` when *output* does not contain a recognisable
        hash-mismatch message.

        Covers all known Nix output formats:

        - ``derivation-check.cc``:  FOD hash mismatch (SRI format)
        - ``local-store.cc``:       NAR import hash mismatch (Nix32 or hex)
        - ``local-store.cc``:       CA hash mismatch importing path (Nix32 or hex)

        ``prefetch-yarn-deps`` prints ``hash mismatch, expected …, got …`` for
        one tarball that disagrees with ``yarn.lock``. That digest belongs to
        the tarball, not to any fixed-output derivation, so it is not parsed
        here; ``lib.update.nix`` classifies it as a fetch failure.

        """
        got_matches = list(_RE_GOT.finditer(output))
        if not got_matches:
            return None
        got = got_matches[-1]
        block_start = got_matches[-2].end() if len(got_matches) > 1 else 0
        paths = list(_RE_DRV_PATH.finditer(output, block_start, got.start()))
        drv_path = paths[-1].group(1) if paths else None
        if paths:
            block_start = paths[-1].end()
        specified_matches = list(
            _RE_SPECIFIED.finditer(output, block_start, got.start())
        )
        specified = specified_matches[-1].group(1) if specified_matches else None
        return cls(
            result,
            got_hash=got.group(1),
            specified=specified,
            drv_path=drv_path,
        )

    @classmethod
    def from_stderr(
        cls,
        stderr: str,
        result: CommandResult,
    ) -> HashMismatchError | None:
        """Alias for :meth:`from_output` (backward-compatible name)."""
        return cls.from_output(stderr, result)


# ---------------------------------------------------------------------------
# Async runners
# ---------------------------------------------------------------------------


def _merge_env(env: Mapping[str, str] | None) -> dict[str, str]:
    merged = {**os.environ, "TERM": "dumb"}
    if env:
        merged.update(env)
    return merged


@dataclass(frozen=True)
class ProcessLine:
    """A single decoded line emitted by a subprocess stream."""

    stream: str
    text: str


@dataclass(frozen=True)
class ProcessDone:
    """Terminal event containing the completed subprocess result."""

    result: CommandResult


type ProcessEvent = ProcessLine | ProcessDone


async def _stop_stream_process(
    proc: asyncio.subprocess.Process,
    tasks: list[asyncio.Task[None]],
) -> None:
    """Stop and reap a streamed subprocess and its pipe readers."""
    for task in tasks:
        task.cancel()
    try:
        await _stop_process(proc)
    finally:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _finish_stream_cleanup(
    proc: asyncio.subprocess.Process,
    tasks: list[asyncio.Task[None]],
    *,
    primary_error: BaseException | None,
) -> None:
    """Finish cleanup before preserving the primary error or deferred cancellation."""
    cleanup = asyncio.create_task(_stop_stream_process(proc, tasks))
    completion = asyncio.gather(cleanup, return_exceptions=True)
    cancelled: asyncio.CancelledError | None = None
    while not completion.done():
        try:
            await asyncio.shield(completion)
        except asyncio.CancelledError as error:
            cancelled = error
    try:
        cleanup.result()
    except BaseException as cleanup_error:
        if cancelled is not None:
            cancelled.add_note(f"stream cleanup failed: {cleanup_error}")
            raise cancelled from cleanup_error
        if primary_error is None or isinstance(primary_error, GeneratorExit):
            raise
        primary_error.add_note(f"stream cleanup failed: {cleanup_error}")
        return
    if cancelled is not None:
        raise cancelled


async def _stop_process(proc: asyncio.subprocess.Process) -> None:
    """Stop and reap a child, including the race where it has already exited."""
    with suppress(ProcessLookupError):
        proc.kill()
    await proc.wait()


@dataclass
class _OutputCapture:
    """Keep complete parser output or an explicitly requested diagnostic tail."""

    limit: int | None
    chunks: deque[str] = field(default_factory=deque)
    size: int = 0

    def append(self, text: str) -> None:
        self.chunks.append(text)
        self.size += len(text)
        if self.limit is not None:
            while self.size > self.limit:
                first = self.chunks.popleft()
                excess = self.size - self.limit
                self.size -= len(first)
                if len(first) > excess:
                    remainder = first[excess:]
                    self.chunks.appendleft(remainder)
                    self.size += len(remainder)

    def text(self) -> str:
        return "".join(self.chunks)


async def _pump_process_stream(
    stream: asyncio.StreamReader | None,
    label: str,
    store: _OutputCapture,
    queue: asyncio.Queue[tuple[str, str | None] | Exception],
) -> None:
    if stream is None:
        await queue.put((label, None))
        return
    try:
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace")
            store.append(text)
            await queue.put((label, text))
    except (OSError, ValueError) as error:
        await queue.put(error)
        return
    await queue.put((label, None))


async def stream_process(
    args: list[str],
    *,
    command_timeout: float = 2400.0,
    env: Mapping[str, str] | None = None,
    output_limit: int | None = None,
) -> AsyncGenerator[ProcessEvent]:
    """Stream with backpressure; optionally retain only each stream's tail.

    ``output_limit`` bounds captured characters per stream, not emitted lines.
    Parser consumers must retain the default complete output or parse line events
    before opting into bounded diagnostic capture.
    """
    merged_env = _merge_env(env)
    if output_limit is not None and output_limit < 1:
        msg = "output_limit must be positive"
        raise ValueError(msg)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
    )

    stdout_chunks = _OutputCapture(output_limit)
    stderr_chunks = _OutputCapture(output_limit)
    queue: asyncio.Queue[tuple[str, str | None] | Exception] = asyncio.Queue(
        maxsize=_STREAM_QUEUE_SIZE,
    )
    loop = asyncio.get_running_loop()
    deadline = loop.time() + command_timeout

    tasks = [
        asyncio.create_task(
            _pump_process_stream(proc.stdout, "stdout", stdout_chunks, queue)
        ),
        asyncio.create_task(
            _pump_process_stream(proc.stderr, "stderr", stderr_chunks, queue)
        ),
    ]

    process_finished = False
    try:
        done_streams = 0
        while done_streams < len(tasks):
            remaining = deadline - loop.time()
            if remaining <= 0:
                _raise_timeout()
            event = await asyncio.wait_for(queue.get(), timeout=remaining)
            if isinstance(event, Exception):
                raise event
            label, text = event
            if text is None:
                done_streams += 1
                continue
            yield ProcessLine(label, text)
        await asyncio.gather(*tasks)
        returncode = await asyncio.wait_for(proc.wait(), timeout=deadline - loop.time())
        process_finished = True
    except TimeoutError:
        raise TimeoutError from None
    finally:
        if not process_finished:
            await _finish_stream_cleanup(
                proc,
                tasks,
                primary_error=sys.exc_info()[1],
            )

    result = CommandResult(
        args=args,
        returncode=returncode,
        stdout=stdout_chunks.text(),
        stderr=stderr_chunks.text(),
    )
    yield ProcessDone(result)


async def run_nix(
    args: list[str],
    *,
    command_timeout: float = 2400.0,
    check: bool = True,
    capture: bool = True,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run a Nix command and return the collected output.

    Parameters
    ----------
    args:
        Full argument list (e.g. ``["nix", "build", "-L", ...]``).
    command_timeout:
        Maximum wall-clock seconds before the process is killed.
    check:
        If ``True`` (default) and the process exits non-zero, raise
        :class:`NixCommandError` (or :class:`HashMismatchError` when
        applicable).
    capture:
        If ``True`` (default), stdout and stderr are captured into the
        returned :class:`CommandResult`.  When ``False`` they are
        inherited from the parent process (useful for interactive output).
    env:
        Extra environment variables merged on top of :data:`os.environ`.

    """
    merged_env = _merge_env(env)

    if capture:
        stdout_opt = asyncio.subprocess.PIPE
        stderr_opt = asyncio.subprocess.PIPE
    else:
        stdout_opt = None
        stderr_opt = None

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=stdout_opt,
        stderr=stderr_opt,
        env=merged_env,
    )

    process_finished = False
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=command_timeout,
        )
        process_finished = True
    except TimeoutError:
        raise NixCommandError(
            CommandResult(args=args, returncode=-1, stdout="", stderr=""),
            message=f"command timed out after {command_timeout}s",
        ) from None
    finally:
        if not process_finished:
            await _stop_process(proc)

    result = CommandResult(
        args=args,
        returncode=proc.returncode or 0,
        stdout=(stdout_bytes or b"").decode(errors="replace"),
        stderr=(stderr_bytes or b"").decode(errors="replace"),
    )

    if check and result.returncode != 0:
        combined = result.stderr + "\n" + result.stdout
        hash_err = HashMismatchError.from_output(combined, result)
        if hash_err is not None:
            raise hash_err
        raise NixCommandError(result)

    return result


async def _create_stream_process(
    args: list[str],
    *,
    env: Mapping[str, str],
) -> tuple[
    asyncio.subprocess.Process,
    asyncio.StreamReader,
    asyncio.StreamReader,
]:
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    if proc.stderr is None or proc.stdout is None:
        msg = "Subprocess was not created with piped stdout/stderr"
        raise RuntimeError(msg)
    return proc, proc.stdout, proc.stderr


async def _drain_stderr_stream(
    stderr_stream: asyncio.StreamReader,
    *,
    chunks: list[bytes],
) -> None:
    while True:
        chunk = await stderr_stream.read(8192)
        if not chunk:
            break
        chunks.append(chunk)


async def _iter_timed_stdout_lines(
    stdout_stream: asyncio.StreamReader,
    *,
    deadline: float,
    loop: asyncio.AbstractEventLoop,
) -> AsyncIterator[str]:
    while True:
        remaining = deadline - loop.time()
        if remaining <= 0:
            _raise_timeout()
        line_bytes = await asyncio.wait_for(stdout_stream.readline(), timeout=remaining)
        if not line_bytes:
            break
        yield line_bytes.decode(errors="replace").rstrip("\n")


def _raise_stream_timeout_error(
    *,
    args: list[str],
    timeout_seconds: float,
) -> None:
    raise NixCommandError(
        CommandResult(args=args, returncode=-1, stdout="", stderr=""),
        message=f"command timed out after {timeout_seconds}s",
    ) from None


def _raise_stream_command_failure(
    *,
    args: list[str],
    returncode: int,
    stderr_text: str,
) -> None:
    if returncode == 0:
        return
    result = CommandResult(
        args=args,
        returncode=returncode,
        stdout="",
        stderr=stderr_text,
    )
    hash_err = HashMismatchError.from_output(stderr_text, result)
    if hash_err is not None:
        raise hash_err
    raise NixCommandError(result)


async def stream_nix(
    args: list[str],
    *,
    command_timeout: float = 2400.0,
    env: Mapping[str, str] | None = None,
) -> AsyncGenerator[str]:
    """Yield stdout lines from a Nix command as they arrive.

    Stderr is collected internally and included in the
    :class:`NixCommandError` raised on non-zero exit.

    Parameters
    ----------
    args:
        Full argument list.
    command_timeout:
        Maximum wall-clock seconds before the process is killed.
    env:
        Extra environment variables merged on top of :data:`os.environ`.

    """
    merged_env = _merge_env(env)

    proc, stdout_stream, stderr_stream = await _create_stream_process(
        args,
        env=merged_env,
    )

    stderr_chunks: list[bytes] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + command_timeout
    stderr_task = asyncio.create_task(
        _drain_stderr_stream(stderr_stream, chunks=stderr_chunks)
    )
    returncode = 0
    process_finished = False

    try:
        async for line in _iter_timed_stdout_lines(
            stdout_stream,
            deadline=deadline,
            loop=loop,
        ):
            yield line

        # Wait for the process and stderr drain to finish.
        await stderr_task
        returncode = await proc.wait()
        process_finished = True

    except TimeoutError:
        _raise_stream_timeout_error(args=args, timeout_seconds=command_timeout)
    finally:
        if not process_finished:
            await _finish_stream_cleanup(
                proc,
                [stderr_task],
                primary_error=sys.exc_info()[1],
            )

    stderr_text = b"".join(stderr_chunks).decode(errors="replace")
    _raise_stream_command_failure(
        args=args,
        returncode=returncode,
        stderr_text=stderr_text,
    )
