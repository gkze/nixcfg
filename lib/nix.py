"""Nix command wrappers and flake lock utilities."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

from lib.config import SRI_PREFIX, get_config
from lib.events import (
    CommandResult,
    EventCollector,
    EventKind,
    EventStream,
    UpdateEvent,
)
from lib.exceptions import (
    FlakeLockError,
    HashExtractionError,
    NixCommandError,
)

if TYPE_CHECKING:
    pass


# =============================================================================
# Text Processing Utilities
# =============================================================================


def sanitize_log_line(line: str) -> str:
    """Remove carriage returns and ANSI escape sequences from log line."""
    # Lazy import to avoid dependency at module load time
    from rich.text import Text

    line = line.replace("\r", "")
    return Text.from_ansi(line).plain


def truncate_command(text: str, max_len: int = 80) -> str:
    """Truncate a command string for display."""
    escaped = text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    if len(escaped) <= max_len:
        return escaped
    suffix = " [...]"
    trimmed = escaped[: max(0, max_len - len(suffix))].rstrip()
    return f"{trimmed}{suffix}"


# =============================================================================
# Command Execution
# =============================================================================


async def stream_command(
    args: list[str],
    *,
    source: str,
    timeout: float | None = None,
) -> EventStream:
    """Execute a command and stream its output as events.

    Yields:
        COMMAND_START event with command text
        LINE events for each output line
        COMMAND_END event with CommandResult
    """
    config = get_config()
    timeout = timeout if timeout is not None else config.timeouts.subprocess

    command_text = truncate_command(shlex.join(args))
    yield UpdateEvent.command_start(source, command_text, args)

    # Use TERM=dumb to prevent subprocesses (especially nix) from writing
    # fancy progress output directly to /dev/tty, which corrupts cursor tracking.
    env = {**os.environ, "TERM": "dumb"}
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )

    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
    deadline = asyncio.get_event_loop().time() + timeout

    async def pump(
        stream: asyncio.StreamReader | None, label: str, store: list[str]
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
        asyncio.create_task(pump(process.stdout, "stdout", stdout_chunks)),
        asyncio.create_task(pump(process.stderr, "stderr", stderr_chunks)),
    ]

    try:
        done_streams = 0
        while done_streams < len(tasks):
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"Command timed out after {timeout}s")
            label, text = await asyncio.wait_for(queue.get(), timeout=remaining)
            if text is None:
                done_streams += 1
                continue
            sanitized = sanitize_log_line(text.rstrip("\n"))
            if sanitized:
                yield UpdateEvent.line(source, sanitized, label)
        await asyncio.gather(*tasks)
        returncode = await process.wait()
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        raise NixCommandError(
            f"Command timed out after {timeout}s: {shlex.join(args)}",
            command=args,
            source=source,
        )

    result = CommandResult(
        args=args,
        returncode=returncode,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
    )
    yield UpdateEvent.command_end(source, result)


async def run_command(
    args: list[str],
    *,
    source: str,
    error_message: str,
) -> EventStream:
    """Run a command and yield its VALUE as a CommandResult.

    This is a convenience wrapper that streams the command and emits
    the final CommandResult as a VALUE event.
    """
    result: CommandResult | None = None
    async for event in stream_command(args, source=source):
        if event.kind == EventKind.COMMAND_END:
            result = event.payload
        yield event
    if result is None:
        raise NixCommandError(error_message, command=args, source=source)
    yield UpdateEvent.value(source, result)


# =============================================================================
# Hash Computation
# =============================================================================

# Regex patterns for extracting hashes from nix output
_SRI_HASH_RE = re.compile(r"got:\s*(sha256-[0-9A-Za-z+/=]+)")
_FALLBACK_HASH_RE = re.compile(
    r"got:\s*(sha256:[0-9a-fA-F]{64}|[0-9a-fA-F]{64}|[0-9a-z]{52})"
)


def extract_nix_hash(output: str) -> str:
    """Extract hash from nix build output (hash mismatch error).

    Raises HashExtractionError if no hash found.
    """
    sri_match = _SRI_HASH_RE.search(output)
    if sri_match:
        return sri_match.group(1)

    fallback_match = _FALLBACK_HASH_RE.search(output)
    if fallback_match:
        return fallback_match.group(1)

    raise HashExtractionError(
        f"Could not find hash in nix output:\n{output.strip()[:500]}",
        output=output,
    )


async def convert_hash_to_sri(source: str, hash_value: str) -> EventStream:
    """Convert any nix hash format (base32, hex) to SRI format."""
    collector: EventCollector[CommandResult] = EventCollector()
    async for event in collector.collect(
        run_command(
            [
                "nix",
                "hash",
                "convert",
                "--hash-algo",
                "sha256",
                "--to",
                "sri",
                hash_value,
            ],
            source=source,
            error_message="nix hash convert did not return output",
        )
    ):
        yield event
    result = collector.require_value("nix hash convert did not return output")
    yield UpdateEvent.value(source, result.stdout.strip())


async def compute_sri_hash(source: str, url: str) -> EventStream:
    """Compute SRI hash for a URL using nix-prefetch-url."""
    collector: EventCollector[CommandResult] = EventCollector()
    async for event in collector.collect(
        run_command(
            ["nix-prefetch-url", "--type", "sha256", url],
            source=source,
            error_message="nix-prefetch-url did not return output",
        )
    ):
        yield event
    result = collector.require_value("nix-prefetch-url did not return output")
    base32_hash = result.stdout.strip().split("\n")[-1]

    # Convert to SRI
    async for event in convert_hash_to_sri(source, base32_hash):
        yield event


async def compute_url_hashes(source: str, urls: Iterable[str]) -> EventStream:
    """Compute SRI hashes for multiple URLs (deduplicating identical URLs)."""
    hashes: dict[str, str] = {}
    for url in dict.fromkeys(urls):  # Deduplicate while preserving order
        collector: EventCollector[str] = EventCollector()
        async for event in collector.collect(compute_sri_hash(source, url)):
            yield event
        hashes[url] = collector.require_value("Missing hash output")
    yield UpdateEvent.value(source, hashes)


async def compute_fixed_output_hash(source: str, expr: str) -> EventStream:
    """Compute hash by running a nix expression with lib.fakeHash.

    The expression should use lib.fakeHash for the hash field, causing
    nix to fail with the correct hash in the error message.
    """
    collector: EventCollector[CommandResult] = EventCollector()
    async for event in collector.collect(
        run_command(
            [
                "nix",
                "build",
                "-L",
                "--verbose",
                "--no-link",
                "--impure",
                "--expr",
                expr,
            ],
            source=source,
            error_message="nix build did not return output",
        )
    ):
        yield event

    result = collector.require_value("nix build did not return output")
    if result.returncode == 0:
        raise NixCommandError(
            "Expected nix build to fail with hash mismatch, but it succeeded",
            command=result.args,
            source=source,
        )

    hash_value = extract_nix_hash(result.stderr + result.stdout)
    if hash_value.startswith(SRI_PREFIX):
        yield UpdateEvent.value(source, hash_value)
        return

    # Convert to SRI if needed
    async for event in convert_hash_to_sri(source, hash_value):
        yield event


# =============================================================================
# Flake Lock Management
# =============================================================================


@dataclass
class FlakeLock:
    """Parsed flake.lock with utility methods."""

    nodes: dict
    path: Path

    @classmethod
    def load(cls, path: Path | None = None) -> FlakeLock:
        """Load flake.lock from path or default location."""
        if path is None:
            path = get_config().paths.flake_lock
        if not path.exists():
            raise FlakeLockError(f"flake.lock not found at {path}")
        data = json.loads(path.read_text())
        if "nodes" not in data:
            raise FlakeLockError(f"Invalid flake.lock: missing 'nodes' key in {path}")
        nodes = data["nodes"]
        if "root" not in nodes:
            raise FlakeLockError(f"Invalid flake.lock: missing 'root' node in {path}")
        return cls(nodes=nodes, path=path)

    def get_node(self, name: str) -> dict:
        """Get a node by name, following root input references."""
        if name not in self.nodes:
            raise FlakeLockError(
                f"Node '{name}' not found in flake.lock", input_name=name
            )
        return self.nodes[name]

    def get_root_input_name(self, input_name: str) -> str:
        """Get the actual node name for a root input."""
        root_inputs = self.nodes.get("root", {}).get("inputs", {})
        return root_inputs.get(input_name, input_name)

    def get_input_node(self, input_name: str) -> dict:
        """Get the node for a root input."""
        node_name = self.get_root_input_name(input_name)
        return self.get_node(node_name)

    def get_input_version(self, input_name: str) -> str:
        """Best-effort version string for a flake input."""
        node = self.get_input_node(input_name)
        original = node.get("original", {})
        return (
            original.get("ref")
            or original.get("rev")
            or node.get("locked", {}).get("rev")
            or "unknown"
        )

    def build_fetch_expr(self, input_name: str) -> str:
        """Build a nix expression to fetch a flake input."""
        node = self.get_input_node(input_name)
        locked = node.get("locked", {})
        input_type = locked.get("type")
        if input_type not in {"github", "gitlab"}:
            raise FlakeLockError(
                f"Unsupported flake input type: {input_type}",
                input_name=input_name,
            )
        return (
            "builtins.fetchTree { "
            f'type = "{input_type}"; '
            f'owner = "{locked["owner"]}"; '
            f'repo = "{locked["repo"]}"; '
            f'rev = "{locked["rev"]}"; '
            f'narHash = "{locked["narHash"]}"; '
            "}"
        )

    def nixpkgs_expr(self) -> str:
        """Build expression to import nixpkgs from flake input."""
        node_name = self.get_root_input_name("nixpkgs")
        return f"import ({self.build_fetch_expr(node_name)}) {{ system = builtins.currentSystem; }}"


async def update_flake_input(input_name: str, *, source: str) -> EventStream:
    """Update a flake input in flake.lock."""
    async for event in stream_command(
        ["nix", "flake", "lock", "--update-input", input_name],
        source=source,
    ):
        yield event


# =============================================================================
# Tool Availability
# =============================================================================


def check_required_tools() -> list[str]:
    """Check that required external tools are available.

    Returns list of missing tools (empty if all present).
    """
    from lib.config import REQUIRED_TOOLS

    return [tool for tool in REQUIRED_TOOLS if shutil.which(tool) is None]


def check_optional_tools() -> dict[str, bool]:
    """Check availability of optional tools.

    Returns dict of tool name -> is_available.
    """
    from lib.config import OPTIONAL_TOOLS

    return {tool: shutil.which(tool) is not None for tool in OPTIONAL_TOOLS}
