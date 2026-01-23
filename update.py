#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "aiohttp",
#   "pydantic>=2.0",
#   "rich>=13.0",
# ]
# ///
"""Update source versions and hashes in sources.json.

Usage:
    ./update.py <source>                   Update a specific source
    ./update.py <source> --update-input    Update and refresh flake input
    ./update.py --all                      Update all sources
    ./update.py --all --continue-on-error  Continue if individual sources fail
    ./update.py --list                     List available sources
    ./update.py --validate                 Validate sources.json
    ./update.py --schema                   Output JSON schema for sources.json
    ./update.py --json                     Output results as JSON
    ./update.py --quiet                    Suppress progress output

Environment Variables:
    UPDATE_LOG_TAIL_LINES     Number of log lines to show (default: 10)
    UPDATE_PANEL_HEIGHT       Terminal panel height override

Sources are defined with custom update logic for fetching latest versions
and computing hashes from upstream release channels.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import select
import shutil
import shlex
import time
import sys
import termios
import tty
import urllib.parse
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Literal, Mapping

import aiohttp
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


# =============================================================================
# Configuration
# =============================================================================


def get_repo_file(filename: str) -> Path:
    """Resolve repo file location (handles nix store paths)."""
    script_path = Path(__file__)
    base_dir = Path.cwd() if "/nix/store" in str(script_path) else script_path.parent
    return base_dir / filename


SOURCES_FILE = get_repo_file("sources.json")
FLAKE_LOCK_FILE = get_repo_file("flake.lock")

# =============================================================================
# Utilities
# =============================================================================


DEFAULT_TIMEOUT = 30
DEFAULT_LOG_TAIL_LINES = 10
DEFAULT_RENDER_INTERVAL = 0.05
DEFAULT_USER_AGENT = "update.py"
DEFAULT_RETRIES = 3
DEFAULT_RETRY_BACKOFF = 1.0


def _resolve_log_tail_lines(lines: int | None) -> int:
    if lines is not None:
        return max(1, lines)
    try:
        configured = int(
            os.environ.get("UPDATE_LOG_TAIL_LINES", DEFAULT_LOG_TAIL_LINES)
        )
    except ValueError:
        configured = DEFAULT_LOG_TAIL_LINES
    return max(1, configured)


def _read_cursor_row() -> int | None:
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    fd = sys.stdin.fileno()
    try:
        original = termios.tcgetattr(fd)
    except termios.error:
        return None
    try:
        tty.setcbreak(fd)
        sys.stdout.write("\x1b[6n")
        sys.stdout.flush()
        response = ""
        start = time.monotonic()
        while time.monotonic() - start < 0.05:
            ready, _, _ = select.select([fd], [], [], 0.05)
            if not ready:
                continue
            response += os.read(fd, 32).decode(errors="ignore")
            if "R" in response:
                break
        match = re.search(r"\x1b\[(\d+);(\d+)R", response)
        if match:
            return int(match.group(1))
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)
    return None


class TerminalInfo:
    @staticmethod
    def size() -> os.terminal_size:
        return shutil.get_terminal_size(fallback=(120, 20))

    @classmethod
    def width(cls) -> int:
        return cls.size().columns

    @classmethod
    def height(cls) -> int:
        return cls.size().lines

    @classmethod
    def panel_height(cls) -> int:
        override = os.environ.get("UPDATE_PANEL_HEIGHT")
        if override:
            try:
                return max(1, int(override))
            except ValueError:
                pass
        height = cls.height()
        row = _read_cursor_row()
        if row is None:
            return max(1, height - 1)
        return max(1, height - row + 1)


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def _sanitize_log_line(line: str) -> str:
    line = line.replace("\r", "")
    line = _ANSI_ESCAPE_RE.sub("", line)
    return line


def _truncate_command(text: str, max_len: int = 80) -> str:
    escaped = text.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")
    if len(escaped) <= max_len:
        return escaped
    suffix = " [...]"
    trimmed = escaped[: max(0, max_len - len(suffix))].rstrip()
    return f"{trimmed}{suffix}"


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


class UpdateEventKind(StrEnum):
    STATUS = "status"
    COMMAND_START = "command_start"
    LINE = "line"
    COMMAND_END = "command_end"
    VALUE = "value"
    RESULT = "result"
    ERROR = "error"


@dataclass(frozen=True)
class UpdateEvent:
    source: str
    kind: UpdateEventKind
    message: str | None = None
    stream: str | None = None
    payload: Any | None = None


@dataclass
class ValueDrain:
    value: Any | None = None


async def drain_value_events(
    events: AsyncIterator[UpdateEvent], drain: ValueDrain
) -> AsyncIterator[UpdateEvent]:
    async for event in events:
        if event.kind == UpdateEventKind.VALUE:
            drain.value = event.payload
        else:
            yield event


def _require_value(drain: ValueDrain, error: str) -> Any:
    if drain.value is None:
        raise RuntimeError(error)
    return drain.value


def _check_github_rate_limit(headers: Mapping[str, str], url: str) -> None:
    remaining = headers.get("X-RateLimit-Remaining")
    if remaining is None:
        return
    try:
        remaining_value = int(remaining)
    except ValueError:
        return
    if remaining_value > 0:
        return
    reset = headers.get("X-RateLimit-Reset")
    reset_time = "unknown"
    if reset and reset.isdigit():
        reset_time = datetime.fromtimestamp(int(reset), tz=timezone.utc).isoformat()
    raise RuntimeError(
        f"GitHub API rate limit exceeded for {url}. Resets at {reset_time}."
    )


async def _request(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
    method: str = "GET",
    retries: int = DEFAULT_RETRIES,
    backoff: float = DEFAULT_RETRY_BACKOFF,
) -> tuple[bytes, Mapping[str, str]]:
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    timeout_config = aiohttp.ClientTimeout(total=timeout or DEFAULT_TIMEOUT)

    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                timeout=timeout_config,
                allow_redirects=True,
            ) as response:
                payload = await response.read()
                if response.status >= 400:
                    error_body = payload.decode(errors="ignore").strip()
                    detail = f"HTTP {response.status} {response.reason}"
                    if error_body:
                        detail = f"{detail}\n{error_body}"
                    # Don't retry client errors (4xx), only server errors (5xx)
                    if response.status < 500:
                        raise RuntimeError(f"Request to {url} failed: {detail}")
                    last_error = RuntimeError(f"Request to {url} failed: {detail}")
                else:
                    return payload, response.headers
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = e

        # Exponential backoff before retry
        if attempt < retries - 1:
            await asyncio.sleep(backoff * (2**attempt))

    raise RuntimeError(
        f"Request to {url} failed after {retries} attempts: {last_error}"
    )


async def fetch_url(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
) -> bytes:
    payload, _headers = await _request(
        session, url, user_agent=user_agent, timeout=timeout
    )
    return payload


async def fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
) -> dict:
    """Fetch and parse JSON from a URL."""
    if url.startswith("https://api.github.com/"):
        payload, headers = await _request(
            session, url, user_agent=user_agent, timeout=timeout
        )
        _check_github_rate_limit(headers, url)
    else:
        payload = await fetch_url(session, url, user_agent=user_agent, timeout=timeout)
    try:
        return json.loads(payload.decode())
    except json.JSONDecodeError as err:
        raise RuntimeError(f"Invalid JSON response from {url}: {err}") from err


async def stream_command(
    args: list[str],
    *,
    source: str,
) -> AsyncIterator[UpdateEvent]:
    command_text = _truncate_command(shlex.join(args))
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_START,
        message=command_text,
        payload=args,
    )

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

    done_streams = 0
    while done_streams < len(tasks):
        label, text = await queue.get()
        if text is None:
            done_streams += 1
            continue
        sanitized = _sanitize_log_line(text.rstrip("\n"))
        if sanitized:
            yield UpdateEvent(
                source=source,
                kind=UpdateEventKind.LINE,
                message=sanitized,
                stream=label,
            )

    await asyncio.gather(*tasks)
    returncode = await process.wait()
    result = CommandResult(
        args=args,
        returncode=returncode,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
    )
    yield UpdateEvent(source=source, kind=UpdateEventKind.COMMAND_END, payload=result)


async def run_command(
    args: list[str], *, source: str, error: str
) -> AsyncIterator[UpdateEvent]:
    result_drain = ValueDrain()
    async for event in stream_command(args, source=source):
        if event.kind == UpdateEventKind.COMMAND_END:
            result_drain.value = event.payload
        yield event
    result = _require_value(result_drain, error)
    yield UpdateEvent(source=source, kind=UpdateEventKind.VALUE, payload=result)


async def convert_nix_hash_to_sri(
    source: str, hash_value: str
) -> AsyncIterator[UpdateEvent]:
    """Convert any nix hash format (base32, hex) to SRI format."""
    result_drain = ValueDrain()
    async for event in drain_value_events(
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
            error="nix hash convert did not return output",
        ),
        result_drain,
    ):
        yield event
    result = _require_value(result_drain, "nix hash convert did not return output")
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.VALUE,
        payload=result.stdout.strip(),
    )


async def compute_sri_hash(source: str, url: str) -> AsyncIterator[UpdateEvent]:
    """Compute SRI hash for a URL using nix-prefetch-url."""
    result_drain = ValueDrain()
    async for event in drain_value_events(
        run_command(
            ["nix-prefetch-url", "--type", "sha256", url],
            source=source,
            error="nix-prefetch-url did not return output",
        ),
        result_drain,
    ):
        yield event
    result = _require_value(result_drain, "nix-prefetch-url did not return output")
    base32_hash = result.stdout.strip().split("\n")[-1]
    async for event in convert_nix_hash_to_sri(source, base32_hash):
        yield event


async def compute_url_hashes(
    source: str, urls: Iterable[str]
) -> AsyncIterator[UpdateEvent]:
    hashes: dict[str, str] = {}
    for url in dict.fromkeys(urls):
        sri_drain = ValueDrain()
        async for event in drain_value_events(compute_sri_hash(source, url), sri_drain):
            yield event
        sri_value = _require_value(sri_drain, "Missing hash output")
        hashes[url] = sri_value
    yield UpdateEvent(source=source, kind=UpdateEventKind.VALUE, payload=hashes)


def load_flake_lock() -> dict:
    """Load flake.lock nodes."""
    if not FLAKE_LOCK_FILE.exists():
        raise FileNotFoundError(f"flake.lock not found at {FLAKE_LOCK_FILE}")
    return json.loads(FLAKE_LOCK_FILE.read_text())["nodes"]


def get_flake_input_node(input_name: str) -> dict:
    """Return flake.lock node for an input."""
    lock = load_flake_lock()
    if input_name not in lock:
        raise KeyError(f"flake input '{input_name}' not found in flake.lock")
    return lock[input_name]


def get_root_input_name(input_name: str) -> str:
    """Return the node name for a root input."""
    lock = load_flake_lock()
    root_inputs = lock.get("root", {}).get("inputs", {})
    return root_inputs.get(input_name, input_name)


def get_flake_input_version(node: dict) -> str:
    """Best-effort version string for a flake input."""
    original = node.get("original", {})
    return (
        original.get("ref")
        or original.get("rev")
        or node.get("locked", {}).get("rev")
        or "unknown"
    )


def flake_fetch_expr(node: dict) -> str:
    """Build a nix expression to fetch a flake input."""
    locked = node.get("locked", {})
    if locked.get("type") not in {"github", "gitlab"}:
        raise ValueError(f"Unsupported flake input type: {locked.get('type')}")
    return (
        "builtins.fetchTree { "
        f'type = "{locked["type"]}"; '
        f'owner = "{locked["owner"]}"; '
        f'repo = "{locked["repo"]}"; '
        f'rev = "{locked["rev"]}"; '
        f'narHash = "{locked["narHash"]}"; '
        "}"
    )


def nixpkgs_expr() -> str:
    node_name = get_root_input_name("nixpkgs")
    node = get_flake_input_node(node_name)
    return f"import ({flake_fetch_expr(node)}) {{ system = builtins.currentSystem; }}"


async def update_flake_input(
    input_name: str, *, source: str
) -> AsyncIterator[UpdateEvent]:
    """Update a flake input in flake.lock."""
    async for event in stream_command(
        ["nix", "flake", "lock", "--update-input", input_name],
        source=source,
    ):
        yield event


def _extract_nix_hash(output: str) -> str:
    sri_match = re.search(r"got:\s*(sha256-[0-9A-Za-z+/=]+)", output)
    if sri_match:
        return sri_match.group(1)
    fallback_match = re.search(
        r"got:\s*(sha256:[0-9a-fA-F]{64}|[0-9a-fA-F]{64}|[0-9a-z]{52})",
        output,
    )
    if fallback_match:
        return fallback_match.group(1)
    raise RuntimeError(f"Could not find hash in nix output:\n{output.strip()}")


async def compute_fixed_output_hash(
    source: str, expr: str
) -> AsyncIterator[UpdateEvent]:
    """Compute hash by running a nix expression with lib.fakeHash."""
    result_drain = ValueDrain()
    async for event in drain_value_events(
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
            error="nix build did not return output",
        ),
        result_drain,
    ):
        yield event
    result = _require_value(result_drain, "nix build did not return output")
    if result.returncode == 0:
        raise RuntimeError(
            "Expected nix build to fail with hash mismatch, but it succeeded"
        )
    hash_value = _extract_nix_hash(result.stderr + result.stdout)
    if hash_value.startswith("sha256-"):
        yield UpdateEvent(source=source, kind=UpdateEventKind.VALUE, payload=hash_value)
        return
    async for event in convert_nix_hash_to_sri(source, hash_value):
        yield event


def _build_nix_expr(body: str) -> str:
    """Build a nix expression with nixpkgs prelude."""
    return f"""
      let
        pkgs = {nixpkgs_expr()};
      in
        {body}
    """


async def _compute_nixpkgs_hash(
    source: str, expr_body: str
) -> AsyncIterator[UpdateEvent]:
    """Compute hash for a nixpkgs expression that uses lib.fakeHash."""
    expr = _build_nix_expr(expr_body)
    async for event in compute_fixed_output_hash(source, expr):
        yield event


async def compute_go_vendor_hash(
    source: str,
    input_name: str,
    *,
    pname: str,
    version: str,
    subpackages: list[str] | None = None,
    proxy_vendor: bool = False,
) -> AsyncIterator[UpdateEvent]:
    subpackages_expr = ""
    if subpackages:
        quoted = " ".join(f'"{sp}"' for sp in subpackages)
        subpackages_expr = f"subPackages = [ {quoted} ];"
    proxy_expr = "proxyVendor = true;" if proxy_vendor else ""
    src_expr = flake_fetch_expr(get_flake_input_node(input_name))
    async for event in _compute_nixpkgs_hash(
        source,
        f"""pkgs.buildGoModule {{
        pname = "{pname}";
        version = "{version}";
        src = {src_expr};
        {subpackages_expr}
        {proxy_expr}
        vendorHash = pkgs.lib.fakeHash;
      }}""",
    ):
        yield event


async def compute_cargo_vendor_hash(
    source: str, input_name: str, *, subdir: str | None = None
) -> AsyncIterator[UpdateEvent]:
    src_expr = flake_fetch_expr(get_flake_input_node(input_name))
    if subdir:
        src_expr = f'"${{{src_expr}}}/{subdir}"'
    async for event in _compute_nixpkgs_hash(
        source,
        f"""pkgs.rustPlatform.fetchCargoVendor {{
        src = {src_expr};
        hash = pkgs.lib.fakeHash;
      }}""",
    ):
        yield event


async def compute_npm_deps_hash(
    source: str, input_name: str
) -> AsyncIterator[UpdateEvent]:
    src_expr = flake_fetch_expr(get_flake_input_node(input_name))
    async for event in _compute_nixpkgs_hash(
        source,
        f"""pkgs.fetchNpmDeps {{
        src = {src_expr};
        hash = pkgs.lib.fakeHash;
      }}""",
    ):
        yield event


def github_raw_url(owner: str, repo: str, rev: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{path}"


async def fetch_github_default_branch(
    session: aiohttp.ClientSession, owner: str, repo: str
) -> str:
    data = await fetch_json(
        session,
        f"https://api.github.com/repos/{owner}/{repo}",
        user_agent=DEFAULT_USER_AGENT,
        timeout=DEFAULT_TIMEOUT,
    )
    return data["default_branch"]


async def fetch_github_latest_commit(
    session: aiohttp.ClientSession, owner: str, repo: str, path: str, branch: str
) -> str:
    encoded_path = urllib.parse.quote(path)
    url = (
        f"https://api.github.com/repos/{owner}/{repo}/commits"
        f"?path={encoded_path}&sha={branch}&per_page=1"
    )
    data = await fetch_json(
        session, url, user_agent=DEFAULT_USER_AGENT, timeout=DEFAULT_TIMEOUT
    )
    if not data:
        raise RuntimeError(f"No commits found for {owner}/{repo}:{path}")
    return data[0]["sha"]


def make_hash_entry(
    drv_type: str,
    hash_type: str,
    hash_value: str,
    *,
    url: str | None = None,
    urls: dict[str, str] | None = None,
) -> HashEntry:
    return HashEntry(
        drv_type=drv_type,
        hash_type=hash_type,
        hash=hash_value,
        url=url,
        urls=urls,
    )


# =============================================================================
# Updater Base Class
# =============================================================================

# Registry populated automatically via __init_subclass__
UPDATERS: dict[str, type["Updater"]] = {}

# SRI hash pattern for validation
_SRI_HASH_PATTERN = re.compile(r"^sha256-[A-Za-z0-9+/]+=*$")

# Valid Nix platforms
NixPlatform = Literal[
    "aarch64-darwin", "x86_64-darwin", "aarch64-linux", "x86_64-linux", "darwin"
]

# Valid derivation types
DrvType = Literal["buildGoModule", "fetchCargoVendor", "fetchNpmDeps", "fetchurl"]

# Valid hash types
HashType = Literal["vendorHash", "cargoHash", "npmDepsHash", "sha256"]


def _validate_sri_hash(value: str) -> str:
    """Validate that a hash is in SRI format."""
    if not _SRI_HASH_PATTERN.match(value):
        raise ValueError(f"Hash must be in SRI format (sha256-...): {value!r}")
    return value


class HashEntry(BaseModel):
    """Single hash entry for sources.json (flake-input-based sources)."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    drv_type: DrvType = Field(alias="drvType")
    hash_type: HashType = Field(alias="hashType")
    hash: str
    url: str | None = None
    urls: dict[str, str] | None = None

    @field_validator("hash")
    @classmethod
    def validate_hash(cls, v: str) -> str:
        return _validate_sri_hash(v)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict with camelCase keys."""
        result: dict[str, Any] = {
            "drvType": self.drv_type,
            "hashType": self.hash_type,
            "hash": self.hash,
        }
        if self.url is not None:
            result["url"] = self.url
        if self.urls is not None:
            result["urls"] = self.urls
        return result


# Type for hashes - either list of entries or platform->hash mapping
SourceHashes = dict[str, str] | list[HashEntry]


class HashCollection(BaseModel):
    """Collection of hashes - either structured entries or platform mapping."""

    model_config = ConfigDict(extra="forbid")

    entries: list[HashEntry] | None = None
    mapping: dict[str, str] | None = None

    @model_validator(mode="before")
    @classmethod
    def parse_input(cls, data: Any) -> dict[str, Any]:
        """Parse raw input into entries or mapping."""
        if isinstance(data, dict):
            if "entries" in data or "mapping" in data:
                return data
            # Platform -> hash mapping
            for platform, hash_value in data.items():
                _validate_sri_hash(hash_value)
            return {"mapping": data}
        if isinstance(data, list):
            return {"entries": data}
        if isinstance(data, HashCollection):
            return {"entries": data.entries, "mapping": data.mapping}
        raise ValueError("Hashes must be a list or dict")

    def to_json(self) -> dict[str, Any] | list[dict[str, Any]]:
        """Serialize to JSON-compatible format."""
        if self.entries is not None:
            return [entry.to_dict() for entry in self.entries]
        if self.mapping is not None:
            return dict(self.mapping)
        return {}

    def primary_hash(self) -> str | None:
        """Return the first/primary hash for display purposes."""
        if self.entries and len(self.entries) == 1:
            return self.entries[0].hash
        if self.mapping:
            values = list(self.mapping.values())
            if len(set(values)) == 1:
                return values[0]
        return None

    @classmethod
    def from_value(cls, data: "SourceHashes") -> "HashCollection":
        """Create HashCollection from raw hashes data."""
        return cls.model_validate(data)


class SourceEntry(BaseModel):
    """A source package entry in sources.json."""

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    hashes: HashCollection
    version: str | None = None
    input: str | None = None
    urls: dict[str, str] | None = None
    commit: str | None = Field(default=None, pattern=r"^[0-9a-f]{40}$")

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict with camelCase keys."""
        result: dict[str, Any] = {"hashes": self.hashes.to_json()}
        if self.version is not None:
            result["version"] = self.version
        if self.input is not None:
            result["input"] = self.input
        if self.urls is not None:
            result["urls"] = self.urls
        if self.commit is not None:
            result["commit"] = self.commit
        return result


class SourcesFile(BaseModel):
    """Container for sources.json entries."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, SourceEntry]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourcesFile":
        """Parse raw JSON data into SourcesFile."""
        entries = {}
        for name, entry in data.items():
            if name == "$schema":
                continue
            entries[name] = SourceEntry.model_validate(entry)
        return cls(entries=entries)

    @classmethod
    def load(cls, path: Path) -> "SourcesFile":
        """Load from file path."""
        if not path.exists():
            return cls(entries={})
        return cls.from_dict(json.loads(path.read_text()))

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {name: entry.to_dict() for name, entry in self.entries.items()}

    def save(self, path: Path) -> None:
        """Save to file path."""
        data = self.to_dict()
        path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")

    @classmethod
    def json_schema(cls) -> dict[str, Any]:
        """Generate JSON schema for sources.json file format.

        Post-processes the Pydantic schema to match the actual serialization
        format (hashes as list or dict, not wrapped in entries/mapping).
        """
        entry_schema = SourceEntry.model_json_schema()
        defs = dict(entry_schema.get("$defs", {}))

        # Extract SourceEntry definition from root schema (pydantic puts it there)
        source_entry_def = {
            k: v for k, v in entry_schema.items() if k not in ("$defs", "$schema")
        }
        defs["SourceEntry"] = source_entry_def

        # Replace HashCollection with the actual serialization format
        defs["HashCollection"] = {
            "title": "Hashes",
            "description": "Hashes as list of entries or platform-to-hash mapping",
            "oneOf": [
                {
                    "type": "array",
                    "items": {"$ref": "#/$defs/HashEntry"},
                    "description": "List of structured hash entries",
                },
                {
                    "type": "object",
                    "additionalProperties": {
                        "type": "string",
                        "pattern": "^sha256-[A-Za-z0-9+/]+=*$",
                    },
                    "description": "Platform to SRI hash mapping",
                },
            ],
        }

        return {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "Nix Sources",
            "description": "Source package versions and hashes for Nix derivations",
            "type": "object",
            "additionalProperties": {
                "$ref": "#/$defs/SourceEntry",
            },
            "$defs": defs,
        }


@dataclass
class VersionInfo:
    """Version and metadata fetched from upstream."""

    version: str
    metadata: dict[
        str, Any
    ]  # Updater-specific data (URLs, checksums, release info, etc.)


class Updater(ABC):
    """Base class for source updaters. Subclasses auto-register via `name` attribute."""

    name: str  # Source name (e.g., "google-chrome")

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        if hasattr(cls, "name"):
            UPDATERS[cls.name] = cls

    @abstractmethod
    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch the latest version and any metadata needed for hashes."""

    @abstractmethod
    def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> AsyncIterator[UpdateEvent]:
        """Fetch hashes for the source."""

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build SourceEntry from version info and hashes. Override to customize."""
        return SourceEntry(
            version=info.version, hashes=HashCollection.from_value(hashes)
        )

    def _build_result_with_urls(
        self,
        info: VersionInfo,
        hashes: SourceHashes,
        urls: dict[str, str],
        *,
        commit: str | None = None,
    ) -> SourceEntry:
        """Helper for updaters that include download URLs in the result."""
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            urls=urls,
            commit=commit,
        )

    def _is_latest(self, current: SourceEntry | None, info: VersionInfo) -> bool:
        """Check if current entry matches latest version info.

        Compares version and optionally commit hash for sources that track commits.
        """
        if current is None:
            return False
        if current.version != info.version:
            return False
        # For sources with commit tracking, also compare commits
        upstream_commit = info.metadata.get("commit")
        if upstream_commit and current.commit:
            return current.commit == upstream_commit
        return True

    async def update_stream(
        self, current: SourceEntry | None, session: aiohttp.ClientSession
    ) -> AsyncIterator[UpdateEvent]:
        """Check for updates. Yields UpdateEvent stream and final result."""
        yield UpdateEvent(
            source=self.name,
            kind=UpdateEventKind.STATUS,
            message=f"Fetching latest {self.name} version...",
        )
        info = await self.fetch_latest(session)

        yield UpdateEvent(
            source=self.name,
            kind=UpdateEventKind.STATUS,
            message=f"Latest version: {info.version}",
        )
        if self._is_latest(current, info):
            yield UpdateEvent(
                source=self.name,
                kind=UpdateEventKind.STATUS,
                message="Already at latest version",
            )
            yield UpdateEvent(
                source=self.name,
                kind=UpdateEventKind.RESULT,
                payload=None,
            )
            return

        yield UpdateEvent(
            source=self.name,
            kind=UpdateEventKind.STATUS,
            message="Fetching hashes for all platforms...",
        )
        hashes_drain = ValueDrain()
        async for event in drain_value_events(
            self.fetch_hashes(info, session), hashes_drain
        ):
            yield event
        hashes = _require_value(hashes_drain, "Missing hash output")
        result = self.build_result(info, hashes)
        if current is not None and result == current:
            yield UpdateEvent(
                source=self.name,
                kind=UpdateEventKind.STATUS,
                message="No updates needed",
            )
            yield UpdateEvent(
                source=self.name,
                kind=UpdateEventKind.RESULT,
                payload=None,
            )
            return
        yield UpdateEvent(
            source=self.name,
            kind=UpdateEventKind.RESULT,
            payload=result,
        )


# =============================================================================
# Specialized Updater Base Classes
# =============================================================================


class ChecksumProvidedUpdater(Updater):
    """Base for sources that provide checksums in their API (no download needed)."""

    PLATFORMS: dict[str, str]  # nix_platform -> api_key

    @abstractmethod
    async def fetch_checksums(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> dict[str, str]:
        """Return {nix_platform: hex_hash} from API metadata."""

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> AsyncIterator[UpdateEvent]:
        """Convert API checksums to SRI format."""
        hashes: dict[str, str] = {}
        checksums = await self.fetch_checksums(info, session)
        for platform, hex_hash in checksums.items():
            sri_drain = ValueDrain()
            async for event in drain_value_events(
                convert_nix_hash_to_sri(self.name, hex_hash), sri_drain
            ):
                yield event
            sri_value = _require_value(sri_drain, "Missing checksum conversion output")
            hashes[platform] = sri_value
        yield UpdateEvent(
            source=self.name,
            kind=UpdateEventKind.VALUE,
            payload=hashes,
        )


class DownloadHashUpdater(Updater):
    """Base for sources requiring download to compute hash, with URL deduplication."""

    PLATFORMS: dict[str, str]  # nix_platform -> download_url or template

    @abstractmethod
    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return download URL for a platform."""

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> AsyncIterator[UpdateEvent]:
        """Compute hashes by downloading, deduplicating identical URLs."""
        platform_urls = {
            platform: self.get_download_url(platform, info)
            for platform in self.PLATFORMS
        }
        hashes_drain = ValueDrain()
        async for event in drain_value_events(
            compute_url_hashes(self.name, platform_urls.values()), hashes_drain
        ):
            yield event
        hashes_by_url = _require_value(hashes_drain, "Missing hash output")

        hashes: dict[str, str] = {
            platform: hashes_by_url[platform_urls[platform]]
            for platform in self.PLATFORMS
        }
        yield UpdateEvent(
            source=self.name,
            kind=UpdateEventKind.VALUE,
            payload=hashes,
        )


class HashEntryUpdater(Updater):
    """Base for sources that emit hash entries in sources.json."""

    input_name: str | None = None

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return SourceEntry(
            hashes=HashCollection.from_value(hashes), input=self.input_name
        )

    async def _emit_single_hash_entry(
        self,
        events: AsyncIterator[UpdateEvent],
        *,
        error: str,
        drv_type: str,
        hash_type: str,
    ) -> AsyncIterator[UpdateEvent]:
        hash_drain = ValueDrain()
        async for event in drain_value_events(events, hash_drain):
            yield event
        hash_value = _require_value(hash_drain, error)
        yield UpdateEvent(
            source=self.name,
            kind=UpdateEventKind.VALUE,
            payload=[make_hash_entry(drv_type, hash_type, hash_value)],
        )


class FlakeInputHashUpdater(HashEntryUpdater):
    """Base for hashes derived from flake inputs."""

    input_name: str | None = None
    drv_type: str
    hash_type: str

    def __init__(self):
        if self.input_name is None:
            self.input_name = self.name

    @property
    def _input(self) -> str:
        """Return input_name, guaranteed non-None after __init__."""
        assert self.input_name is not None
        return self.input_name

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        node = get_flake_input_node(self._input)
        version = get_flake_input_version(node)
        return VersionInfo(version=version, metadata={"node": node})

    @abstractmethod
    def _compute_hash(self, info: VersionInfo) -> AsyncIterator[UpdateEvent]:
        """Return async iterator that yields hash computation events."""

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> AsyncIterator[UpdateEvent]:
        async for event in self._emit_single_hash_entry(
            self._compute_hash(info),
            error=f"Missing {self.hash_type} output",
            drv_type=self.drv_type,
            hash_type=self.hash_type,
        ):
            yield event


class GoVendorHashUpdater(FlakeInputHashUpdater):
    drv_type = "buildGoModule"
    hash_type = "vendorHash"
    pname: str | None = None
    subpackages: list[str] | None = None
    proxy_vendor: bool = False

    def _compute_hash(self, info: VersionInfo) -> AsyncIterator[UpdateEvent]:
        return compute_go_vendor_hash(
            self.name,
            self._input,
            pname=self.pname or self.name,
            version=info.version,
            subpackages=self.subpackages,
            proxy_vendor=self.proxy_vendor,
        )


class CargoVendorHashUpdater(FlakeInputHashUpdater):
    drv_type = "fetchCargoVendor"
    hash_type = "cargoHash"
    subdir: str | None = None

    def _compute_hash(self, info: VersionInfo) -> AsyncIterator[UpdateEvent]:
        return compute_cargo_vendor_hash(self.name, self._input, subdir=self.subdir)


class NpmDepsHashUpdater(FlakeInputHashUpdater):
    drv_type = "fetchNpmDeps"
    hash_type = "npmDepsHash"

    def _compute_hash(self, info: VersionInfo) -> AsyncIterator[UpdateEvent]:
        return compute_npm_deps_hash(self.name, self._input)


# =============================================================================
# Updater Factory Functions
# =============================================================================


def go_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    pname: str | None = None,
    subpackages: list[str] | None = None,
    proxy_vendor: bool = False,
) -> type[GoVendorHashUpdater]:
    """Create and register a Go vendor hash updater."""
    attrs = {
        "name": name,
        "input_name": input_name,
        "pname": pname,
        "subpackages": subpackages,
        "proxy_vendor": proxy_vendor,
    }
    return type(f"{name}Updater", (GoVendorHashUpdater,), attrs)


def cargo_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    subdir: str | None = None,
) -> type[CargoVendorHashUpdater]:
    """Create and register a Cargo vendor hash updater."""
    attrs = {"name": name, "input_name": input_name, "subdir": subdir}
    return type(f"{name}Updater", (CargoVendorHashUpdater,), attrs)


def npm_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[NpmDepsHashUpdater]:
    """Create and register an npm deps hash updater."""
    attrs = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (NpmDepsHashUpdater,), attrs)


def github_raw_file_updater(
    name: str,
    *,
    owner: str,
    repo: str,
    path: str,
) -> type[GitHubRawFileUpdater]:
    """Create and register a GitHub raw file updater."""
    attrs = {"name": name, "owner": owner, "repo": repo, "path": path}
    return type(f"{name}Updater", (GitHubRawFileUpdater,), attrs)


# =============================================================================
# Source Updaters
# =============================================================================


class GitHubRawFileUpdater(HashEntryUpdater):
    """Fetch latest raw file from GitHub and compute hash."""

    owner: str
    repo: str
    path: str

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        branch = await fetch_github_default_branch(session, self.owner, self.repo)
        rev = await fetch_github_latest_commit(
            session, self.owner, self.repo, self.path, branch
        )
        return VersionInfo(version=rev, metadata={"rev": rev, "branch": branch})

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> AsyncIterator[UpdateEvent]:
        url = github_raw_url(self.owner, self.repo, info.metadata["rev"], self.path)
        hashes_drain = ValueDrain()
        async for event in drain_value_events(
            compute_url_hashes(self.name, [url]), hashes_drain
        ):
            yield event
        hashes_by_url = _require_value(hashes_drain, "Missing hash output")
        hash_value = hashes_by_url[url]
        yield UpdateEvent(
            source=self.name,
            kind=UpdateEventKind.VALUE,
            payload=[make_hash_entry("fetchurl", "sha256", hash_value, url=url)],
        )


github_raw_file_updater(
    "homebrew-zsh-completion",
    owner="Homebrew",
    repo="brew",
    path="completions/zsh/_brew",
)
github_raw_file_updater(
    "gitui-key-config",
    owner="extrawurst",
    repo="gitui",
    path="vim_style_key_config.ron",
)


class GoogleChromeUpdater(DownloadHashUpdater):
    """Update Google Chrome to latest stable version."""

    name = "google-chrome"

    # nix platform -> download url
    PLATFORMS = {
        "aarch64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
        "x86_64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
        "x86_64-linux": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        url = "https://chromiumdash.appspot.com/fetch_releases?channel=Stable&platform=Mac&num=1"
        data = await fetch_json(session, url)
        return VersionInfo(version=data[0]["version"], metadata={})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return self.PLATFORMS[platform]

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return self._build_result_with_urls(info, hashes, dict(self.PLATFORMS))


class DataGripUpdater(ChecksumProvidedUpdater):
    """Update DataGrip to latest stable version."""

    name = "datagrip"

    API_URL = "https://data.services.jetbrains.com/products/releases?code=DG&latest=true&type=release"

    # nix platform -> JetBrains download key
    PLATFORMS = {
        "aarch64-darwin": "macM1",
        "x86_64-darwin": "mac",
        "aarch64-linux": "linuxARM64",
        "x86_64-linux": "linux",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        data = await fetch_json(session, self.API_URL)
        release = data["DG"][0]
        return VersionInfo(version=release["version"], metadata={"release": release})

    async def fetch_checksums(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> dict[str, str]:
        release = info.metadata["release"]
        checksums = {}
        for nix_platform, jb_key in self.PLATFORMS.items():
            checksum_url = release["downloads"][jb_key]["checksumLink"]
            # Format: "hexhash *filename"
            payload = await fetch_url(session, checksum_url, timeout=DEFAULT_TIMEOUT)
            hex_hash = payload.decode().split()[0]
            checksums[nix_platform] = hex_hash
        return checksums

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        release = info.metadata["release"]
        urls = {
            nix_platform: release["downloads"][jb_key]["link"]
            for nix_platform, jb_key in self.PLATFORMS.items()
        }
        return self._build_result_with_urls(info, hashes, urls)


class ChatGPTUpdater(DownloadHashUpdater):
    """Update ChatGPT desktop app to latest version using Sparkle appcast."""

    name = "chatgpt"

    APPCAST_URL = (
        "https://persistent.oaistatic.com/sidekick/public/sparkle_public_appcast.xml"
    )

    # Both darwin platforms use the same universal binary
    PLATFORMS = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch version and download URL from Sparkle appcast XML."""
        # Use Sparkle user agent to avoid 403
        xml_payload = await fetch_url(
            session,
            self.APPCAST_URL,
            user_agent="Sparkle/2.0",
            timeout=DEFAULT_TIMEOUT,
        )
        xml_data = xml_payload.decode()

        root = ET.fromstring(xml_data)
        # Get the first (latest) item
        item = root.find(".//item")
        if item is None:
            raise RuntimeError("No items found in appcast")

        # Sparkle namespace
        ns = {"sparkle": "http://www.andymatuschak.org/xml-namespaces/sparkle"}

        version_elem = item.find("sparkle:shortVersionString", ns)
        if version_elem is None or version_elem.text is None:
            raise RuntimeError("No version found in appcast")

        enclosure = item.find("enclosure")
        if enclosure is None:
            raise RuntimeError("No enclosure found in appcast")

        url = enclosure.get("url")
        if url is None:
            raise RuntimeError("No URL found in enclosure")

        return VersionInfo(version=version_elem.text, metadata={"url": url})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return info.metadata["url"]

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return self._build_result_with_urls(
            info, hashes, {"darwin": info.metadata["url"]}
        )


class ConductorUpdater(DownloadHashUpdater):
    """Update Conductor to latest version from CrabNebula CDN."""

    name = "conductor"

    # nix platform -> CrabNebula platform
    PLATFORMS = {
        "aarch64-darwin": "dmg-aarch64",
        "x86_64-darwin": "dmg-x86_64",
    }

    BASE_URL = "https://cdn.crabnebula.app/download/melty/conductor/latest/platform"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch version from Content-Disposition header of the download."""
        url = f"{self.BASE_URL}/dmg-aarch64"
        _payload, headers = await _request(
            session, url, method="HEAD", timeout=DEFAULT_TIMEOUT
        )
        disposition = headers.get("Content-Disposition", "")

        # Parse version from filename like "Conductor_0.31.1_aarch64.dmg"
        match = re.search(r"Conductor_([0-9.]+)_", disposition)
        if not match:
            raise RuntimeError(f"Could not parse version from: {disposition}")

        return VersionInfo(version=match.group(1), metadata={})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return f"{self.BASE_URL}/{self.PLATFORMS[platform]}"

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {
            nix_platform: f"{self.BASE_URL}/{cn_platform}"
            for nix_platform, cn_platform in self.PLATFORMS.items()
        }
        return self._build_result_with_urls(info, hashes, urls)


class VSCodeInsidersUpdater(ChecksumProvidedUpdater):
    """Update VS Code Insiders to latest version."""

    name = "vscode-insiders"

    # nix platform -> vscode api platform
    PLATFORMS = {
        "aarch64-darwin": "darwin-arm64",
        "aarch64-linux": "linux-arm64",
        "x86_64-darwin": "darwin",
        "x86_64-linux": "linux-x64",
    }

    async def _fetch_platform_info(
        self, session: aiohttp.ClientSession, api_platform: str
    ) -> dict:
        url = f"https://update.code.visualstudio.com/api/update/{api_platform}/insider/latest"
        return await fetch_json(session, url)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        # Fetch info for all platforms upfront to avoid repeated API calls
        platform_info = {}
        for nix_platform, api_platform in self.PLATFORMS.items():
            platform_info[nix_platform] = await self._fetch_platform_info(
                session, api_platform
            )

        versions = {
            platform: info["productVersion"] for platform, info in platform_info.items()
        }
        unique_versions = set(versions.values())
        if len(unique_versions) != 1:
            raise RuntimeError(f"VS Code Insiders version mismatch: {versions}")
        version = unique_versions.pop()
        return VersionInfo(version=version, metadata={"platform_info": platform_info})

    async def fetch_checksums(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> dict[str, str]:
        platform_info = info.metadata["platform_info"]
        return {
            platform: platform_info[platform]["sha256hash"]
            for platform in self.PLATFORMS
        }

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {
            nix_platform: f"https://update.code.visualstudio.com/{info.version}/{api_platform}/insider"
            for nix_platform, api_platform in self.PLATFORMS.items()
        }
        return self._build_result_with_urls(info, hashes, urls)


go_vendor_updater("axiom-cli", subpackages=["cmd/axiom"])
go_vendor_updater("beads", subpackages=["cmd/bd"], proxy_vendor=True)
go_vendor_updater("crush")
go_vendor_updater("gogcli", subpackages=["cmd/gog"])
cargo_vendor_updater("codex", subdir="codex-rs")
cargo_vendor_updater("sentry-cli")
npm_deps_updater("gemini-cli")


class CodeCursorUpdater(DownloadHashUpdater):
    """Update Cursor editor to latest stable version."""

    name = "code-cursor"

    API_BASE = "https://www.cursor.com/api/download"

    # nix platform -> Cursor API platform
    PLATFORMS = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-x64",
        "aarch64-linux": "linux-arm64",
        "x86_64-linux": "linux-x64",
    }

    async def _fetch_platform_info(
        self, session: aiohttp.ClientSession, api_platform: str
    ) -> dict:
        url = f"{self.API_BASE}?platform={api_platform}&releaseTrack=stable"
        return await fetch_json(session, url)

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        # Fetch info for all platforms - they share version but have different URLs
        platform_info = {}
        for nix_platform, api_platform in self.PLATFORMS.items():
            platform_info[nix_platform] = await self._fetch_platform_info(
                session, api_platform
            )

        # Verify all platforms have the same version and commit
        versions = {info["version"] for info in platform_info.values()}
        commits = {info["commitSha"] for info in platform_info.values()}
        if len(versions) != 1:
            raise RuntimeError(f"Cursor version mismatch across platforms: {versions}")
        if len(commits) != 1:
            raise RuntimeError(f"Cursor commit mismatch across platforms: {commits}")

        version = versions.pop()
        commit = commits.pop()
        return VersionInfo(
            version=version,
            metadata={"commit": commit, "platform_info": platform_info},
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return info.metadata["platform_info"][platform]["downloadUrl"]

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        platform_info = info.metadata["platform_info"]
        urls = {
            nix_platform: platform_info[nix_platform]["downloadUrl"]
            for nix_platform in self.PLATFORMS
        }
        return self._build_result_with_urls(
            info, hashes, urls, commit=info.metadata["commit"]
        )


# =============================================================================
# Main
# =============================================================================


@dataclass
class OutputOptions:
    """Control output format and verbosity."""

    json_output: bool = False
    quiet: bool = False

    def print(self, message: str, *, file: Any = None) -> None:
        """Print message unless in quiet or json mode."""
        if not self.quiet and not self.json_output:
            print(message, file=file or sys.stdout)

    def print_error(self, message: str) -> None:
        """Print error message (always shown unless json mode)."""
        if not self.json_output:
            print(message, file=sys.stderr)


def load_sources() -> SourcesFile:
    return SourcesFile.load(SOURCES_FILE)


def save_sources(sources: SourcesFile) -> None:
    sources.save(SOURCES_FILE)


@dataclass
class SourceState:
    status: str = "pending"
    tail: deque[str] = field(default_factory=deque)
    active_commands: int = 0


def _is_tty() -> bool:
    term = os.environ.get("TERM", "")
    return sys.stdout.isatty() and term.lower() not in {"", "dumb"}


def _fit_to_width(text: str, width: int) -> str:
    if width <= 0:
        return text
    # Reserve the last column to avoid line wrapping on some terminals.
    return text[: max(0, width - 1)]


class Renderer:
    """Rich-based live renderer for update progress.

    Uses Rich's Live display for automatic terminal resize handling and
    clean rendering without manual ANSI escape sequences.
    """

    def __init__(
        self,
        states: dict[str, SourceState],
        order: list[str],
        *,
        is_tty: bool,
        panel_height: int | None = None,
        quiet: bool = False,
    ) -> None:
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text

        self.states = states
        self.order = order
        self.is_tty = is_tty
        self.quiet = quiet
        self._initial_panel_height = panel_height
        self.panel_height = panel_height or TerminalInfo.panel_height()
        self.last_render = 0.0
        self.needs_render = False

        # Rich components - only initialize for TTY mode
        self._console: Any = None
        self._live: Any = None
        if is_tty and not quiet:
            self._console = Console(force_terminal=True)
            self._live = Live(
                Text(""),
                console=self._console,
                refresh_per_second=10,
                transient=True,  # Clear display when stopped
            )
            self._live.start()

    def _build_display(self) -> Any:
        """Build the Rich renderable for current state."""
        from rich.console import Group
        from rich.text import Text

        if not self._console:
            return Text("")

        width = self._console.width
        height = self._console.height
        max_visible = min(self.panel_height, height - 1)

        lines: list[Text] = []
        for name in self.order:
            if len(lines) >= max_visible:
                break
            state = self.states[name]
            status = state.status or "pending"

            # Build header line: "name: status" with name in bold
            header = Text()
            header.append(name, style="bold")
            header.append(f": {status}")
            header.truncate(width - 1)
            lines.append(header)

            if len(lines) >= max_visible:
                break

            # Add tail lines for active commands
            if state.active_commands > 0:
                for tail_line in state.tail:
                    if len(lines) >= max_visible:
                        break
                    detail = Text(f"  {tail_line}", style="dim")
                    detail.truncate(width - 1)
                    lines.append(detail)

        return Group(*lines)

    def log(self, source: str, message: str, *, stream: str | None = None) -> None:
        """Log a message to stdout when not in TTY mode."""
        if self.is_tty or self.quiet:
            return
        if stream:
            print(f"[{source}][{stream}] {message}")
        else:
            print(f"[{source}] {message}")

    def log_error(self, source: str, message: str) -> None:
        """Log an error message to stderr (always shown unless quiet)."""
        if self.is_tty or self.quiet:
            return
        print(f"[{source}] Error: {message}", file=sys.stderr)

    def request_render(self) -> None:
        if self.is_tty:
            self.needs_render = True

    def render_if_due(self, now: float) -> None:
        if not self.is_tty or not self.needs_render:
            return
        if now - self.last_render >= DEFAULT_RENDER_INTERVAL:
            self.render()
            self.last_render = now
            self.needs_render = False

    def finalize(self) -> None:
        """Stop live display and print final status."""
        if self._live:
            self._live.stop()
            self._live = None
        if self.is_tty and not self.quiet:
            self._print_final_status()

    def _print_final_status(self) -> None:
        """Print final status summary after stopping live display."""
        from rich.console import Console
        from rich.text import Text

        console = Console()
        for name in self.order:
            state = self.states[name]
            status = state.status or "done"
            line = Text()
            line.append(name, style="bold")
            line.append(f": {status}")
            console.print(line)

    def render(self) -> None:
        """Update the live display with current state."""
        if not self._live:
            return
        self._live.update(self._build_display())


async def _consume_events(
    queue: asyncio.Queue[UpdateEvent | None],
    order: list[str],
    sources: SourcesFile,
    *,
    max_lines: int,
    is_tty: bool,
    quiet: bool = False,
) -> tuple[bool, int, dict[str, str]]:
    states = {
        name: SourceState(status="pending", tail=deque(maxlen=max_lines))
        for name in order
    }
    updated = False
    errors = 0
    update_details: dict[str, str] = {}  # name -> "updated" | "error" | "no_change"
    renderer = Renderer(states, order, is_tty=is_tty, quiet=quiet)

    while True:
        event = await queue.get()
        if event is None:
            break
        state = states.get(event.source)
        if state is None:
            continue

        match event.kind:
            case UpdateEventKind.STATUS:
                state.status = event.message or state.status
                if event.message:
                    renderer.log(event.source, event.message)

            case UpdateEventKind.COMMAND_START:
                state.active_commands += 1
                if event.message:
                    state.status = event.message
                    renderer.log(event.source, event.message)
                if state.active_commands == 1:
                    state.tail.clear()

            case UpdateEventKind.LINE:
                label = event.stream or "stdout"
                message = event.message or ""
                line_text = f"[{label}] {message}" if label else message
                if state.active_commands > 0:
                    if not state.tail or state.tail[-1] != line_text:
                        state.tail.append(line_text)
                renderer.log(event.source, message, stream=label)

            case UpdateEventKind.COMMAND_END:
                state.active_commands = max(0, state.active_commands - 1)
                if state.active_commands == 0:
                    state.tail.clear()
                result = event.payload
                if isinstance(result, CommandResult):
                    renderer.log(
                        event.source, f"command finished (exit {result.returncode})"
                    )

            case UpdateEventKind.RESULT:
                result = event.payload
                if result is not None:
                    old_entry = sources.entries.get(event.source)
                    old_version = old_entry.version if old_entry else None
                    new_version = result.version
                    sources.entries[event.source] = result
                    updated = True
                    update_details[event.source] = "updated"
                    if old_version and new_version and old_version != new_version:
                        state.status = f"Updated :: {old_version} => {new_version}"
                    else:
                        # Fall back to showing hash change if no version
                        old_hash = (
                            old_entry.hashes.primary_hash() if old_entry else None
                        )
                        new_hash = result.hashes.primary_hash()
                        if old_hash and new_hash and old_hash != new_hash:
                            state.status = f"Updated :: {old_hash} => {new_hash}"
                        else:
                            state.status = "Updated."
                else:
                    update_details[event.source] = "no_change"
                    if not state.status:
                        state.status = "No updates needed."

            case UpdateEventKind.ERROR:
                errors += 1
                update_details[event.source] = "error"
                message = event.message or "Unknown error"
                state.status = f"Error: {message}"
                state.active_commands = 0
                state.tail.clear()
                renderer.log_error(event.source, message)

        renderer.request_render()
        renderer.render_if_due(time.monotonic())

    renderer.finalize()

    return updated, errors, update_details


async def _update_source_task(
    name: str,
    sources: SourcesFile,
    *,
    update_input: bool,
    session: aiohttp.ClientSession,
    update_input_lock: asyncio.Lock,
    queue: asyncio.Queue[UpdateEvent | None],
) -> None:
    current = sources.entries.get(name)
    updater = UPDATERS[name]()
    input_name = getattr(updater, "input_name", None)

    try:
        await queue.put(
            UpdateEvent(
                source=name,
                kind=UpdateEventKind.STATUS,
                message="Starting update",
            )
        )
        if update_input and input_name:
            await queue.put(
                UpdateEvent(
                    source=name,
                    kind=UpdateEventKind.STATUS,
                    message=f"Updating flake input '{input_name}'...",
                )
            )
            async with update_input_lock:
                async for event in update_flake_input(input_name, source=name):
                    await queue.put(event)

        async for event in updater.update_stream(current, session):
            await queue.put(event)
    except Exception as exc:
        await queue.put(
            UpdateEvent(source=name, kind=UpdateEventKind.ERROR, message=str(exc))
        )


@dataclass
class UpdateSummary:
    """Summary of update run for JSON output."""

    updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    no_change: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "updated": self.updated,
            "errors": self.errors,
            "noChange": self.no_change,
            "success": len(self.errors) == 0,
        }


async def _run_updates(args: argparse.Namespace) -> int:
    out = OutputOptions(json_output=args.json, quiet=args.quiet)

    if args.schema:
        print(json.dumps(SourcesFile.json_schema(), indent=2))
        return 0

    if args.list:
        if args.json:
            print(json.dumps({"sources": list(UPDATERS.keys())}))
        else:
            print("Available sources:")
            for name in UPDATERS:
                print(f"  {name}")
        return 0

    # Validate mode: just load and validate sources.json
    if args.validate:
        try:
            sources = load_sources()
            if args.json:
                print(json.dumps({"valid": True, "count": len(sources.entries)}))
            else:
                print(f"Validated {SOURCES_FILE}: {len(sources.entries)} sources OK")
            return 0
        except Exception as exc:
            if args.json:
                print(json.dumps({"valid": False, "error": str(exc)}))
            else:
                print(f"Validation failed: {exc}", file=sys.stderr)
            return 1

    if not args.source and not args.all:
        out.print_error("No source specified. Use --all or provide a source name.")
        return 1

    if not args.all and args.source not in UPDATERS:
        out.print_error(f"Error: Unknown source '{args.source}'")
        out.print_error(f"Available sources: {', '.join(UPDATERS.keys())}")
        return 1

    sources = load_sources()
    names = list(UPDATERS.keys()) if args.all else [args.source]
    summary = UpdateSummary()

    if not names:
        if args.json:
            print(json.dumps(summary.to_dict()))
        else:
            out.print("No sources to update.")
        return 0

    max_lines = _resolve_log_tail_lines(None)
    # Disable TTY rendering in quiet or JSON mode
    is_tty = _is_tty() and not args.quiet and not args.json

    async with aiohttp.ClientSession() as session:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        update_input_lock = asyncio.Lock()
        tasks = [
            asyncio.create_task(
                _update_source_task(
                    name,
                    sources,
                    update_input=args.update_input,
                    session=session,
                    update_input_lock=update_input_lock,
                    queue=queue,
                )
            )
            for name in names
        ]
        consumer = asyncio.create_task(
            _consume_events(
                queue,
                names,
                sources,
                max_lines=max_lines,
                is_tty=is_tty,
                quiet=args.quiet or args.json,
            )
        )
        await asyncio.gather(*tasks)
        await queue.put(None)
        updated, errors, update_details = await consumer

    # Populate summary from update details
    for name, detail in update_details.items():
        if detail == "updated":
            summary.updated.append(name)
        elif detail == "error":
            summary.errors.append(name)
        else:
            summary.no_change.append(name)

    if updated:
        save_sources(sources)

    # JSON output mode
    if args.json:
        print(json.dumps(summary.to_dict()))
        return 1 if summary.errors else 0

    # Standard output
    if updated:
        out.print(f"\nUpdated {SOURCES_FILE}")
    else:
        out.print("\nNo updates needed.")

    # With --continue-on-error, return success if any updates succeeded
    if args.continue_on_error and updated:
        if errors:
            out.print(f"\nWarning: {errors} source(s) failed but continuing.")
        return 0

    return 1 if errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update source versions and hashes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available sources: {', '.join(UPDATERS.keys())}",
    )
    parser.add_argument("source", nargs="?", help="Source to update")
    parser.add_argument("-a", "--all", action="store_true", help="Update all sources")
    parser.add_argument(
        "-l", "--list", action="store_true", help="List available sources"
    )
    parser.add_argument(
        "--update-input",
        action="store_true",
        help="Update flake input(s) before hashing",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue updating other sources if one fails",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="Validate sources.json and exit",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Output JSON schema for sources.json and exit",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output results as JSON (for scripting/automation)",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress progress output, only show errors and final summary",
    )
    args = parser.parse_args()

    raise SystemExit(asyncio.run(_run_updates(args)))


if __name__ == "__main__":
    main()
