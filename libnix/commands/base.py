"""Core async subprocess infrastructure for running Nix commands.

Provides :class:`CommandResult` for structured output, :func:`run_nix` for
collected execution, and :func:`stream_nix` for line-by-line streaming.
Hash-mismatch errors from fixed-output derivations are captured as
:class:`HashMismatchError` with pre-extracted hashes (SRI when available,
raw otherwise — use :attr:`is_sri` to check and :meth:`to_sri` to convert).
"""

from __future__ import annotations

import asyncio
import importlib
import os
import re
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

# ---------------------------------------------------------------------------
# Result / error types
# ---------------------------------------------------------------------------

# Matches a well-formed SRI string: <algo>-<base64digest>
_SRI_RE = re.compile(r"^(?:blake3|md5|sha1|sha256|sha512)-[A-Za-z0-9+/]+=*$")

# --- Hash algorithm names Nix can produce ---
_HASH_ALGOS = r"(?:blake3|md5|sha1|sha256|sha512)"

# --- Primary: SRI format (algorithm-base64digest) ---
# Example: sha256-ungWv48Bz+pBQUDeXa4iI7ADYaOWF3qctBD/YfIAFa0=
_RE_SRI_GOT = re.compile(r"got:\s*(" + _HASH_ALGOS + r"-[A-Za-z0-9+/]+=*)")
_RE_SRI_SPECIFIED = re.compile(r"specified:\s*(" + _HASH_ALGOS + r"-[A-Za-z0-9+/]+=*)")

# --- Fallback: prefixed hex (algo:hex), bare hex, or Nix32 ---
# Nix32 uses the alphabet 0123456789abcdfghijklmnpqrsvwxyz (32 chars)
# sha256 lengths: hex = 64 chars, Nix32 = 52 chars
# sha512 lengths: hex = 128 chars, Nix32 = 103 chars
# sha1   lengths: hex = 40 chars, Nix32 = 32 chars
_RE_FALLBACK_GOT = re.compile(
    r"got:\s*("
    + _HASH_ALGOS
    + r":[0-9a-fA-F]+"  # algo:hex (e.g., sha256:abc123...)
    + r"|[0-9a-fA-F]{40,128}"  # bare hex (40=sha1, 64=sha256, 128=sha512)
    + r"|[0-9a-df-np-sv-z]{32,103}"  # Nix32 encoding
    + r")",
)
_RE_FALLBACK_SPECIFIED = re.compile(
    r"specified:\s*("
    + _HASH_ALGOS
    + r":[0-9a-fA-F]+"
    + r"|[0-9a-fA-F]{40,128}"
    + r"|[0-9a-df-np-sv-z]{32,103}"
    + r")",
)

# --- Derivation path extraction ---
# Matches both "hash mismatch in fixed-output derivation" and
# "ca hash mismatch importing path" from local-store.cc
_RE_DRV_PATH = re.compile(
    r"(?:hash mismatch in fixed-output derivation|"
    r"(?:ca )?hash mismatch importing path)"
    r"\s+'([^']+)'",
)

_STDERR_TAIL_LINES = 20


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
        return "\n".join(parts)


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

        hash_module = importlib.import_module("libnix.commands.hash")
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

        Uses ``findall`` and takes the **last** match so that nested
        derivation failures resolve to the innermost (most relevant) hash.

        Returns ``None`` when *output* does not contain a recognisable
        hash-mismatch message.

        Covers all known Nix output formats:

        - ``derivation-check.cc``:  FOD hash mismatch (SRI format)
        - ``local-store.cc``:       NAR import hash mismatch (Nix32 or hex)
        - ``local-store.cc``:       CA hash mismatch importing path (Nix32 or hex)

        """
        # Primary: SRI-encoded hash (e.g. sha256-ABC...=, sha512-XYZ...=)
        sri_matches = _RE_SRI_GOT.findall(output)
        if sri_matches:
            got_hash = sri_matches[-1]
        else:
            # Fallback: hex, prefixed-hex, or Nix32 representations
            fallback_matches = _RE_FALLBACK_GOT.findall(output)
            if fallback_matches:
                got_hash = fallback_matches[-1]
            else:
                return None

        # Optional: extract the "specified" hash (try SRI first, then fallback)
        specified: str | None = None
        spec_sri = _RE_SRI_SPECIFIED.findall(output)
        if spec_sri:
            specified = spec_sri[-1]
        else:
            spec_fallback = _RE_FALLBACK_SPECIFIED.findall(output)
            if spec_fallback:
                specified = spec_fallback[-1]

        # Optional: extract the derivation/store path
        drv_match = _RE_DRV_PATH.search(output)
        drv_path = drv_match.group(1) if drv_match else None

        return cls(
            result,
            got_hash=got_hash,
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


async def stream_process(
    args: list[str],
    *,
    timeout: float = 1200.0,  # noqa: ASYNC109
    env: Mapping[str, str] | None = None,
) -> AsyncIterator[ProcessEvent]:
    """Yield line events from both stdout and stderr until process completion."""
    merged_env = _merge_env(env)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    async def pump(
        stream: asyncio.StreamReader | None,
        label: str,
        store: list[str],
    ) -> None:
        if stream is None:
            await queue.put((label, None))
            return
        while True:
            line = await stream.readline()
            if not line:
                break
            text = line.decode(errors="replace")
            store.append(text)
            await queue.put((label, text))
        await queue.put((label, None))

    tasks = [
        asyncio.create_task(pump(proc.stdout, "stdout", stdout_chunks)),
        asyncio.create_task(pump(proc.stderr, "stderr", stderr_chunks)),
    ]

    try:
        done_streams = 0
        while done_streams < len(tasks):
            remaining = deadline - loop.time()
            if remaining <= 0:
                _raise_timeout()
            label, text = await asyncio.wait_for(queue.get(), timeout=remaining)
            if text is None:
                done_streams += 1
                continue
            yield ProcessLine(label, text)
        await asyncio.gather(*tasks)
        returncode = await proc.wait()
    except TimeoutError:
        proc.kill()
        await proc.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise TimeoutError from None

    result = CommandResult(
        args=args,
        returncode=returncode,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
    )
    yield ProcessDone(result)


async def run_nix(
    args: list[str],
    *,
    timeout: float = 1200.0,  # noqa: ASYNC109
    check: bool = True,
    capture: bool = True,
    env: Mapping[str, str] | None = None,
) -> CommandResult:
    """Run a Nix command and return the collected output.

    Parameters
    ----------
    args:
        Full argument list (e.g. ``["nix", "build", "-L", ...]``).
    timeout:
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

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=timeout,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise NixCommandError(
            CommandResult(args=args, returncode=-1, stdout="", stderr=""),
            message=f"command timed out after {timeout}s",
        ) from None

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


async def stream_nix(  # noqa: C901
    args: list[str],
    *,
    timeout: float = 1200.0,  # noqa: ASYNC109
    env: Mapping[str, str] | None = None,
) -> AsyncIterator[str]:
    """Yield stdout lines from a Nix command as they arrive.

    Stderr is collected internally and included in the
    :class:`NixCommandError` raised on non-zero exit.

    Parameters
    ----------
    args:
        Full argument list.
    timeout:
        Maximum wall-clock seconds before the process is killed.
    env:
        Extra environment variables merged on top of :data:`os.environ`.

    """
    merged_env = _merge_env(env)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=merged_env,
    )

    stderr_chunks: list[bytes] = []
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

    if proc.stderr is None or proc.stdout is None:
        msg = "Subprocess was not created with piped stdout/stderr"
        raise RuntimeError(msg)
    stderr_stream = proc.stderr
    stdout_stream = proc.stdout

    async def _drain_stderr() -> None:
        while True:
            chunk = await stderr_stream.read(8192)
            if not chunk:
                break
            stderr_chunks.append(chunk)

    stderr_task = asyncio.create_task(_drain_stderr())

    try:
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                _raise_timeout()

            line_bytes = await asyncio.wait_for(
                stdout_stream.readline(),
                timeout=remaining,
            )

            if not line_bytes:
                break
            yield line_bytes.decode(errors="replace").rstrip("\n")

        # Wait for the process and stderr drain to finish.
        await stderr_task
        returncode = await proc.wait()

    except TimeoutError:
        proc.kill()
        await proc.wait()
        stderr_task.cancel()
        raise NixCommandError(
            CommandResult(args=args, returncode=-1, stdout="", stderr=""),
            message=f"command timed out after {timeout}s",
        ) from None

    stderr_text = b"".join(stderr_chunks).decode(errors="replace")
    if returncode != 0:
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
