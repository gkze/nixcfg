#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "aiohttp",
#   "pydantic>=2.0",
#   "rich>=13.0",
# ]
# ///
"""Update source versions and hashes in sources.json and flake input refs.

By default, updates everything: flake input refs, flake input locks, and
source hashes. Use --no-* flags to skip specific phases.

Usage:
    ./update.py                            Update everything
    ./update.py codex                      Update a specific source/input
    ./update.py -R  | --no-refs            Skip flake input ref updates
    ./update.py -S  | --no-sources         Skip sources.json hash updates
    ./update.py -I  | --no-input           Skip flake input lock refresh
    ./update.py -c  | --check              Dry run: check for updates only
    ./update.py -c codex                   Check a specific source/input
    ./update.py -k  | --continue-on-error  Continue if individual sources fail
    ./update.py -l  | --list               List available sources
    ./update.py -v  | --validate           Validate sources.json
    ./update.py --schema                   Output JSON schema for sources.json
    ./update.py -j  | --json               Output results as JSON
    ./update.py -q  | --quiet              Suppress progress output

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
import netrc
import os
import re
import select
import shlex
import sys
import termios
import time
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
    _console: Any = None

    @classmethod
    def _get_console(cls) -> Any:
        """Lazily initialize Rich Console."""
        if cls._console is None:
            from rich.console import Console

            cls._console = Console()
        return cls._console

    @classmethod
    def width(cls) -> int:
        return cls._get_console().width

    @classmethod
    def height(cls) -> int:
        return cls._get_console().height

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


def _sanitize_log_line(line: str) -> str:
    """Remove carriage returns and ANSI escape sequences from log line."""
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


def _get_github_token() -> str | None:
    """Get GitHub token from GITHUB_TOKEN env var or ~/.netrc.

    Returns None if no token is available (will use unauthenticated requests).
    """
    # First try GITHUB_TOKEN environment variable
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token

    # Then try ~/.netrc (check both api.github.com and github.com)
    netrc_path = Path.home() / ".netrc"
    if netrc_path.exists():
        try:
            nrc = netrc.netrc(str(netrc_path))
            # Try api.github.com first (common for API tokens), then github.com
            for host in ("api.github.com", "github.com"):
                auth = nrc.authenticators(host)
                if auth:
                    # netrc returns (login, account, password) - token is in password
                    return auth[2]
        except (netrc.NetrcParseError, OSError):
            pass

    return None


# Cache the token lookup
_GITHUB_TOKEN: str | None = _get_github_token()


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
    headers: dict[str, str] = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    # Add GitHub API authentication if available
    if url.startswith("https://api.github.com/") and _GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {_GITHUB_TOKEN}"
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
DrvType = Literal[
    "buildGoModule", "fetchCargoVendor", "fetchFromGitHub", "fetchNpmDeps", "fetchurl"
]

# Valid hash types
HashType = Literal["vendorHash", "cargoHash", "npmDepsHash", "sha256", "srcHash"]


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
        """Serialize to JSON-compatible dict with camelCase keys, sorted alphabetically."""
        result: dict[str, Any] = {
            "drvType": self.drv_type,
            "hash": self.hash,
            "hashType": self.hash_type,
        }
        if self.url is not None:
            result["url"] = self.url
        if self.urls is not None:
            result["urls"] = self.urls
        return dict(sorted(result.items()))


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
        """Serialize to JSON-compatible dict with camelCase keys, sorted alphabetically."""
        result: dict[str, Any] = {"hashes": self.hashes.to_json()}
        if self.commit is not None:
            result["commit"] = self.commit
        if self.input is not None:
            result["input"] = self.input
        if self.urls is not None:
            result["urls"] = self.urls
        if self.version is not None:
            result["version"] = self.version
        return dict(sorted(result.items()))


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


class DroidUpdater(ChecksumProvidedUpdater):
    """Update Factory Droid CLI to latest version."""

    name = "droid"

    INSTALL_SCRIPT_URL = "https://app.factory.ai/cli"
    BASE_URL = "https://downloads.factory.ai/factory-cli/releases"

    # nix platform -> (factory_platform, factory_arch)
    PLATFORMS = {
        "aarch64-darwin": "arm64",
        "x86_64-darwin": "x64",
        "aarch64-linux": "arm64",
        "x86_64-linux": "x64",
    }

    # Factory platform names
    _FACTORY_OS = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
        "aarch64-linux": "linux",
        "x86_64-linux": "linux",
    }

    def _download_url(self, nix_platform: str, version: str) -> str:
        factory_os = self._FACTORY_OS[nix_platform]
        factory_arch = self.PLATFORMS[nix_platform]
        return f"{self.BASE_URL}/{version}/{factory_os}/{factory_arch}/droid"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Parse version from the install script."""
        script = await fetch_url(
            session, self.INSTALL_SCRIPT_URL, timeout=DEFAULT_TIMEOUT
        )
        match = re.search(r'VER="([^"]+)"', script.decode())
        if not match:
            raise RuntimeError(
                "Could not parse version from Factory CLI install script"
            )
        return VersionInfo(version=match.group(1), metadata={})

    async def fetch_checksums(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> dict[str, str]:
        checksums = {}
        for nix_platform in self.PLATFORMS:
            sha_url = f"{self._download_url(nix_platform, info.version)}.sha256"
            payload = await fetch_url(session, sha_url, timeout=DEFAULT_TIMEOUT)
            hex_hash = payload.decode().strip()
            checksums[nix_platform] = hex_hash
        return checksums

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {
            nix_platform: self._download_url(nix_platform, info.version)
            for nix_platform in self.PLATFORMS
        }
        return self._build_result_with_urls(info, hashes, urls)


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


class SculptorUpdater(DownloadHashUpdater):
    """Update Sculptor to latest version from Imbue S3 bucket.

    Sculptor doesn't have a version API, so we use the Last-Modified header
    as a pseudo-version (date format: YYYY-MM-DD).
    """

    name = "sculptor"

    # S3 base URL (discovered via redirect from tryimbue.link shortlinks)
    BASE_URL = "https://imbue-sculptor-releases.s3.us-west-2.amazonaws.com/sculptor"

    # nix platform -> S3 path suffix
    PLATFORMS = {
        "aarch64-darwin": "Sculptor.dmg",
        "x86_64-darwin": "Sculptor-x86_64.dmg",
        "x86_64-linux": "AppImage/x64/Sculptor.AppImage",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Fetch version from Last-Modified header (no version API available)."""
        url = f"{self.BASE_URL}/Sculptor.dmg"
        _payload, headers = await _request(
            session, url, method="HEAD", timeout=DEFAULT_TIMEOUT
        )
        last_modified = headers.get("Last-Modified", "")

        # Parse Last-Modified header: "Tue, 20 Jan 2026 21:11:45 GMT"
        if not last_modified:
            raise RuntimeError("No Last-Modified header from Sculptor download")

        # Convert to date-based version: "2026-01-20"
        try:
            dt = datetime.strptime(last_modified, "%a, %d %b %Y %H:%M:%S %Z")
            version = dt.strftime("%Y-%m-%d")
        except ValueError:
            # Fallback: use raw header if parsing fails
            version = last_modified[:10]

        return VersionInfo(version=version, metadata={})

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return f"{self.BASE_URL}/{self.PLATFORMS[platform]}"

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {
            nix_platform: f"{self.BASE_URL}/{path}"
            for nix_platform, path in self.PLATFORMS.items()
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
npm_deps_updater("gemini-cli")


class SentryCliUpdater(Updater):
    """Update sentry-cli to latest GitHub release.

    Builds from source using fetchFromGitHub with postFetch to strip .xcarchive
    test fixtures (macOS code-signed bundles that break nix-store --optimise).
    """

    name = "sentry-cli"

    GITHUB_OWNER = "getsentry"
    GITHUB_REPO = "sentry-cli"
    XCARCHIVE_FILTER = "find $out -name '*.xcarchive' -type d -exec rm -rf {} +"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        data = await fetch_json(
            session,
            f"https://api.github.com/repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest",
            user_agent=DEFAULT_USER_AGENT,
            timeout=DEFAULT_TIMEOUT,
        )
        return VersionInfo(version=data["tag_name"], metadata={})

    def _src_nix_expr(self, version: str, hash_value: str = "pkgs.lib.fakeHash") -> str:
        """Build nix expression for fetchFromGitHub with xcarchive filtering."""
        return (
            f"pkgs.fetchFromGitHub {{\n"
            f'  owner = "{self.GITHUB_OWNER}";\n'
            f'  repo = "{self.GITHUB_REPO}";\n'
            f'  tag = "{version}";\n'
            f"  hash = {hash_value};\n"
            f'  postFetch = "{self.XCARCHIVE_FILTER}";\n'
            f"}}"
        )

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> AsyncIterator[UpdateEvent]:
        # Step 1: Compute source hash (fetchFromGitHub with xcarchive filtering)
        src_hash_drain = ValueDrain()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                _build_nix_expr(self._src_nix_expr(info.version)),
            ),
            src_hash_drain,
        ):
            yield event
        src_hash = _require_value(src_hash_drain, "Missing srcHash output")

        # Step 2: Compute cargo vendor hash using the filtered source
        cargo_hash_drain = ValueDrain()
        src_expr = self._src_nix_expr(info.version, f'"{src_hash}"')
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                _build_nix_expr(
                    f"pkgs.rustPlatform.fetchCargoVendor {{\n"
                    f"  src = {src_expr};\n"
                    f"  hash = pkgs.lib.fakeHash;\n"
                    f"}}"
                ),
            ),
            cargo_hash_drain,
        ):
            yield event
        cargo_hash = _require_value(cargo_hash_drain, "Missing cargoHash output")

        yield UpdateEvent(
            source=self.name,
            kind=UpdateEventKind.VALUE,
            payload=[
                make_hash_entry("fetchFromGitHub", "srcHash", src_hash),
                make_hash_entry("fetchCargoVendor", "cargoHash", cargo_hash),
            ],
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
        )


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
# Flake Input Ref Updates
# =============================================================================

# Patterns that indicate a branch or commit ref (not a version)
_BRANCH_REF_PATTERNS = {
    "master",
    "main",
    "nixos-unstable",
    "nixos-stable",
    "nixpkgs-unstable",
}

# Minimum length for a hex string to be considered a commit hash
_MIN_COMMIT_HEX_LEN = 7


def _is_version_ref(ref: str) -> bool:
    """Check if a ref looks like a version tag (not a branch or commit)."""
    if ref in _BRANCH_REF_PATTERNS:
        return False
    if ref.startswith("nixos-") or ref.startswith("nixpkgs-"):
        return False
    # Hex-only strings that look like commit hashes
    if re.fullmatch(r"[0-9a-f]+", ref) and len(ref) >= _MIN_COMMIT_HEX_LEN:
        return False
    # Must contain at least one digit to look like a version
    if not re.search(r"\d", ref):
        return False
    return True


@dataclass(frozen=True)
class FlakeInputRef:
    """A flake input that has a version-like ref."""

    name: str
    owner: str
    repo: str
    ref: str
    input_type: str  # "github", "gitlab"


def get_flake_inputs_with_refs() -> list[FlakeInputRef]:
    """Parse flake.lock to find inputs with version-like refs."""
    lock = load_flake_lock()
    root_inputs = lock.get("root", {}).get("inputs", {})
    result = []

    for input_name, node_name in sorted(root_inputs.items()):
        if isinstance(node_name, list):
            continue  # follows declaration
        node = lock.get(node_name or input_name, {})
        original = node.get("original", {})
        ref = original.get("ref")
        if not ref or not _is_version_ref(ref):
            continue
        owner = original.get("owner")
        repo = original.get("repo")
        input_type = original.get("type", "github")
        if owner and repo and input_type in ("github", "gitlab"):
            result.append(
                FlakeInputRef(
                    name=input_name,
                    owner=owner,
                    repo=repo,
                    ref=ref,
                    input_type=input_type,
                )
            )
    return result


def _extract_version_prefix(ref: str) -> str:
    """Extract the prefix before the version number in a ref.

    Examples:
        "v0.14.7" -> "v"
        "rust-v0.91.0" -> "rust-v"
        "1.0.0" -> ""
        "2.0.6" -> ""
    """
    match = re.match(r"^(.*?)\d", ref)
    if match:
        return match.group(1)
    return ""


async def fetch_github_latest_version_ref(
    session: aiohttp.ClientSession, owner: str, repo: str, prefix: str
) -> str | None:
    """Fetch the latest version ref from GitHub matching the given prefix.

    Strategy: try releases API first (non-draft, non-prerelease, sorted by
    date), then fall back to the tags API if no releases exist or none match.

    Returns the tag/ref name, or None if nothing matches.
    """
    # 1. Releases API (paginated, newest first)
    try:
        releases = await fetch_json(
            session,
            f"https://api.github.com/repos/{owner}/{repo}/releases?per_page=20",
            user_agent=DEFAULT_USER_AGENT,
            timeout=DEFAULT_TIMEOUT,
        )
        for release in releases:
            if release.get("draft") or release.get("prerelease"):
                continue
            tag = release.get("tag_name", "")
            if tag.startswith(prefix):
                return tag
    except RuntimeError:
        pass  # No releases endpoint or API error

    # 2. Tags API fallback (repos that use tags without GitHub Releases)
    try:
        tags = await fetch_json(
            session,
            f"https://api.github.com/repos/{owner}/{repo}/tags?per_page=30",
            user_agent=DEFAULT_USER_AGENT,
            timeout=DEFAULT_TIMEOUT,
        )
        for tag_info in tags:
            tag = tag_info.get("name", "")
            if tag.startswith(prefix):
                return tag
    except RuntimeError:
        pass

    return None


@dataclass(frozen=True)
class RefUpdateResult:
    """Result of checking/updating a flake input ref."""

    name: str
    current_ref: str
    latest_ref: str | None
    error: str | None = None


async def check_flake_ref_update(
    input_ref: FlakeInputRef,
    session: aiohttp.ClientSession,
) -> RefUpdateResult:
    """Check if a flake input ref has a newer version available."""
    prefix = _extract_version_prefix(input_ref.ref)

    if input_ref.input_type == "github":
        latest = await fetch_github_latest_version_ref(
            session, input_ref.owner, input_ref.repo, prefix
        )
    else:
        return RefUpdateResult(
            name=input_ref.name,
            current_ref=input_ref.ref,
            latest_ref=None,
            error=f"Unsupported input type: {input_ref.input_type}",
        )

    if latest is None:
        return RefUpdateResult(
            name=input_ref.name,
            current_ref=input_ref.ref,
            latest_ref=None,
            error="Could not determine latest version",
        )

    return RefUpdateResult(
        name=input_ref.name,
        current_ref=input_ref.ref,
        latest_ref=latest,
    )


async def update_flake_ref(
    input_ref: FlakeInputRef,
    new_ref: str,
    *,
    source: str,
) -> AsyncIterator[UpdateEvent]:
    """Update a flake input ref using flake-edit and lock the input.

    Raises RuntimeError if either flake-edit or nix flake lock fails.
    """
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.STATUS,
        message=f"Updating ref: {input_ref.ref} -> {new_ref}",
    )

    # Use flake-edit to change the ref in flake.nix
    new_url = f"github:{input_ref.owner}/{input_ref.repo}/{new_ref}"
    change_result: CommandResult | None = None
    async for event in stream_command(
        ["flake-edit", "change", input_ref.name, new_url],
        source=source,
    ):
        if event.kind == UpdateEventKind.COMMAND_END:
            change_result = event.payload
        yield event
    if change_result and change_result.returncode != 0:
        raise RuntimeError(
            f"flake-edit change failed (exit {change_result.returncode}): "
            f"{change_result.stderr.strip()}"
        )

    # Lock the updated input
    lock_result: CommandResult | None = None
    async for event in stream_command(
        ["nix", "flake", "lock", "--update-input", input_ref.name],
        source=source,
    ):
        if event.kind == UpdateEventKind.COMMAND_END:
            lock_result = event.payload
        yield event
    if lock_result and lock_result.returncode != 0:
        raise RuntimeError(
            f"nix flake lock failed (exit {lock_result.returncode}): "
            f"{lock_result.stderr.strip()}"
        )


async def _update_refs_task(
    input_ref: FlakeInputRef,
    session: aiohttp.ClientSession,
    queue: asyncio.Queue[UpdateEvent | None],
    *,
    dry_run: bool = False,
    flake_edit_lock: asyncio.Lock | None = None,
) -> None:
    """Task to check and optionally update a single flake input ref.

    The version check (API calls) runs concurrently across inputs, but the
    actual file mutations (flake-edit change + nix flake lock) are serialized
    via ``flake_edit_lock`` to avoid races on flake.nix / flake.lock.
    """
    source = input_ref.name
    try:
        await queue.put(
            UpdateEvent(
                source=source,
                kind=UpdateEventKind.STATUS,
                message=f"Checking {input_ref.owner}/{input_ref.repo} (current: {input_ref.ref})",
            )
        )
        result = await check_flake_ref_update(input_ref, session)

        if result.error:
            await queue.put(
                UpdateEvent(
                    source=source,
                    kind=UpdateEventKind.ERROR,
                    message=result.error,
                )
            )
            return

        if result.latest_ref == result.current_ref:
            await queue.put(
                UpdateEvent(
                    source=source,
                    kind=UpdateEventKind.STATUS,
                    message=f"Already at latest: {result.current_ref}",
                )
            )
            await queue.put(
                UpdateEvent(
                    source=source,
                    kind=UpdateEventKind.RESULT,
                    payload=None,
                )
            )
            return

        if dry_run:
            await queue.put(
                UpdateEvent(
                    source=source,
                    kind=UpdateEventKind.STATUS,
                    message=f"Update available: {result.current_ref} -> {result.latest_ref}",
                )
            )
            await queue.put(
                UpdateEvent(
                    source=source,
                    kind=UpdateEventKind.RESULT,
                    payload={
                        "current": result.current_ref,
                        "latest": result.latest_ref,
                    },
                )
            )
            return

        # Actually update the ref — serialize file mutations
        assert result.latest_ref is not None
        if flake_edit_lock:
            async with flake_edit_lock:
                async for event in update_flake_ref(
                    input_ref, result.latest_ref, source=source
                ):
                    await queue.put(event)
        else:
            async for event in update_flake_ref(
                input_ref, result.latest_ref, source=source
            ):
                await queue.put(event)

        await queue.put(
            UpdateEvent(
                source=source,
                kind=UpdateEventKind.RESULT,
                payload={"current": result.current_ref, "latest": result.latest_ref},
            )
        )
    except Exception as exc:
        await queue.put(
            UpdateEvent(source=source, kind=UpdateEventKind.ERROR, message=str(exc))
        )


async def _run_ref_updates(
    args: argparse.Namespace,
    *,
    dry_run: bool = False,
) -> int:
    """Run flake input ref updates."""
    out = OutputOptions(json_output=args.json, quiet=args.quiet)
    all_inputs = get_flake_inputs_with_refs()

    if args.source:
        # Filter to specific input
        matching = [i for i in all_inputs if i.name == args.source]
        if not matching:
            out.print_error(
                f"No flake input with version ref found for '{args.source}'"
            )
            available = [i.name for i in all_inputs]
            out.print_error(f"Available inputs with refs: {', '.join(available)}")
            return 1
        inputs = matching
    else:
        inputs = all_inputs

    if not inputs:
        out.print("No flake inputs with version refs found.")
        return 0

    names = [i.name for i in inputs]
    max_lines = _resolve_log_tail_lines(None)
    is_tty = _is_tty() and not args.quiet and not args.json

    # We re-use _consume_events but need a sources file for it.
    # Create a dummy one since we're not updating sources.json.
    dummy_sources = SourcesFile(entries={})

    async with aiohttp.ClientSession() as session:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        flake_edit_lock = asyncio.Lock()
        tasks = [
            asyncio.create_task(
                _update_refs_task(
                    inp,
                    session,
                    queue,
                    dry_run=dry_run,
                    flake_edit_lock=flake_edit_lock,
                )
            )
            for inp in inputs
        ]
        consumer = asyncio.create_task(
            _consume_events(
                queue,
                names,
                dummy_sources,
                max_lines=max_lines,
                is_tty=is_tty,
                quiet=args.quiet or args.json,
            )
        )
        await asyncio.gather(*tasks)
        await queue.put(None)
        _updated, errors, update_details = await consumer

    summary = UpdateSummary()
    for name, detail in update_details.items():
        if detail == "updated":
            summary.updated.append(name)
        elif detail == "error":
            summary.errors.append(name)
        else:
            summary.no_change.append(name)

    if args.json:
        print(json.dumps(summary.to_dict()))
        return 1 if summary.errors else 0

    verb = "Available updates" if dry_run else "Updated"
    if summary.updated:
        out.print_success(f"\n{verb}: {', '.join(summary.updated)}")
    else:
        out.print("\nNo ref updates needed.", style="dim")

    return 1 if errors else 0


# =============================================================================
# Main
# =============================================================================


@dataclass
class OutputOptions:
    """Control output format and verbosity."""

    json_output: bool = False
    quiet: bool = False
    _console: Any = field(default=None, repr=False)
    _err_console: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        from rich.console import Console

        self._console = Console()
        self._err_console = Console(stderr=True)

    def print(
        self, message: str, *, style: str | None = None, file: Any = None
    ) -> None:
        """Print message unless in quiet or json mode."""
        if not self.quiet and not self.json_output:
            if file is sys.stderr:
                self._err_console.print(message, style=style)
            else:
                self._console.print(message, style=style)

    def print_error(self, message: str) -> None:
        """Print error message (always shown unless json mode)."""
        if not self.json_output:
            self._err_console.print(message, style="red")

    def print_success(self, message: str) -> None:
        """Print success message unless in quiet or json mode."""
        if not self.quiet and not self.json_output:
            self._console.print(message, style="green")

    def print_warning(self, message: str) -> None:
        """Print warning message unless in quiet or json mode."""
        if not self.quiet and not self.json_output:
            self._console.print(message, style="yellow")


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

    Uses Rich's Live display with SIGWINCH handling for clean resize behavior.
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
                auto_refresh=False,  # We control refresh timing
                transient=True,  # Clear when stopped, render in-place
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
        # Calculate max_visible dynamically to handle terminal resize
        panel_height = self._initial_panel_height or max(1, height - 1)
        max_visible = min(panel_height, height - 1)

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
        self._live.update(self._build_display(), refresh=True)


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
                    if isinstance(result, dict):
                        # Ref update result: {"current": ..., "latest": ...}
                        updated = True
                        update_details[event.source] = "updated"
                        current_ref = result.get("current", "?")
                        latest_ref = result.get("latest", "?")
                        state.status = f"Updated :: {current_ref} => {latest_ref}"
                    elif isinstance(result, SourceEntry):
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
                        updated = True
                        update_details[event.source] = "updated"
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
    """Unified update orchestrator.

    Phase 1: Flake input ref updates (unless --no-refs)
    Phase 2: Source hash updates (unless --no-sources)

    Refs must complete before sources since source hash computation depends
    on the locked flake input state.
    """
    out = OutputOptions(json_output=args.json, quiet=args.quiet)

    if args.schema:
        print(json.dumps(SourcesFile.json_schema(), indent=2))
        return 0

    if args.list:
        if args.json:
            sources_list = sorted(UPDATERS.keys())
            ref_inputs = [i.name for i in get_flake_inputs_with_refs()]
            print(
                json.dumps({"sources": sources_list, "flakeInputsWithRefs": ref_inputs})
            )
        else:
            from rich.columns import Columns
            from rich.console import Console

            console = Console()
            console.print("[bold]Available sources (sources.json):[/bold]")
            console.print(Columns(sorted(UPDATERS.keys()), padding=(0, 2)))
            console.print()
            ref_inputs = get_flake_inputs_with_refs()
            if ref_inputs:
                console.print("[bold]Flake inputs with version refs:[/bold]")
                for inp in ref_inputs:
                    console.print(f"  {inp.name}: {inp.owner}/{inp.repo} @ {inp.ref}")
        return 0

    # Validate mode: just load and validate sources.json
    if args.validate:
        try:
            sources = load_sources()
            if args.json:
                print(json.dumps({"valid": True, "count": len(sources.entries)}))
            else:
                out.print_success(
                    f":heavy_check_mark: Validated {SOURCES_FILE}: "
                    f"{len(sources.entries)} sources OK"
                )
            return 0
        except Exception as exc:
            if args.json:
                print(json.dumps({"valid": False, "error": str(exc)}))
            else:
                out.print_error(f":x: Validation failed: {exc}")
            return 1

    # Resolve what's known to each system
    all_source_names = set(UPDATERS.keys())
    all_ref_inputs = get_flake_inputs_with_refs()
    all_ref_names = {i.name for i in all_ref_inputs}
    all_known_names = all_source_names | all_ref_names

    # Validate source name if specified
    if args.source and args.source not in all_known_names:
        out.print_error(f"Error: Unknown source or input '{args.source}'")
        out.print_error(f"Available: {', '.join(sorted(all_known_names))}")
        return 1

    do_refs = not args.no_refs
    do_sources = not args.no_sources
    do_input_refresh = not args.no_input
    dry_run = args.check

    # If a specific name is given, only run phases that know about it
    if args.source:
        if args.source not in all_ref_names:
            do_refs = False
        if args.source not in all_source_names:
            do_sources = False

    # Combined summary across both phases
    summary = UpdateSummary()
    had_errors = False

    # ── Phase 1: Ref updates ──────────────────────────────────────────
    if do_refs:
        if args.source:
            ref_inputs = [i for i in all_ref_inputs if i.name == args.source]
        else:
            ref_inputs = all_ref_inputs

        if ref_inputs:
            ref_names = [i.name for i in ref_inputs]
            max_lines = _resolve_log_tail_lines(None)
            is_tty = _is_tty() and not args.quiet and not args.json
            dummy_sources = SourcesFile(entries={})

            async with aiohttp.ClientSession() as session:
                queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
                flake_edit_lock = asyncio.Lock()
                tasks = [
                    asyncio.create_task(
                        _update_refs_task(
                            inp,
                            session,
                            queue,
                            dry_run=dry_run,
                            flake_edit_lock=flake_edit_lock,
                        )
                    )
                    for inp in ref_inputs
                ]
                consumer = asyncio.create_task(
                    _consume_events(
                        queue,
                        ref_names,
                        dummy_sources,
                        max_lines=max_lines,
                        is_tty=is_tty,
                        quiet=args.quiet or args.json,
                    )
                )
                await asyncio.gather(*tasks)
                await queue.put(None)
                _ref_updated, ref_errors, ref_details = await consumer

            for name, detail in ref_details.items():
                if detail == "updated":
                    summary.updated.append(name)
                elif detail == "error":
                    summary.errors.append(name)
                else:
                    summary.no_change.append(name)

            if ref_errors:
                had_errors = True

    # ── Phase 2: Source hash updates ──────────────────────────────────
    if do_sources:
        if args.source:
            source_names = [args.source] if args.source in all_source_names else []
        else:
            source_names = list(UPDATERS.keys())

        if source_names:
            sources = load_sources()
            max_lines = _resolve_log_tail_lines(None)
            is_tty = _is_tty() and not args.quiet and not args.json

            async with aiohttp.ClientSession() as session:
                queue2: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
                update_input_lock = asyncio.Lock()
                tasks = [
                    asyncio.create_task(
                        _update_source_task(
                            name,
                            sources,
                            update_input=do_input_refresh,
                            session=session,
                            update_input_lock=update_input_lock,
                            queue=queue2,
                        )
                    )
                    for name in source_names
                ]
                consumer = asyncio.create_task(
                    _consume_events(
                        queue2,
                        source_names,
                        sources,
                        max_lines=max_lines,
                        is_tty=is_tty,
                        quiet=args.quiet or args.json,
                    )
                )
                await asyncio.gather(*tasks)
                await queue2.put(None)
                src_updated, src_errors, src_details = await consumer

            for name, detail in src_details.items():
                if detail == "updated":
                    summary.updated.append(name)
                elif detail == "error":
                    summary.errors.append(name)
                else:
                    summary.no_change.append(name)

            if src_updated:
                save_sources(sources)
            if src_errors:
                had_errors = True

    # ── Combined output ───────────────────────────────────────────────
    if args.json:
        print(json.dumps(summary.to_dict()))
        return 1 if had_errors else 0

    if dry_run:
        if summary.updated:
            out.print_success(f"\nAvailable updates: {', '.join(summary.updated)}")
        else:
            out.print("\nNo updates available.", style="dim")
    else:
        if summary.updated:
            out.print_success(
                f"\n:heavy_check_mark: Updated: {', '.join(summary.updated)}"
            )
        else:
            out.print("\nNo updates needed.", style="dim")

    if summary.errors:
        out.print_error(f"\nFailed: {', '.join(summary.errors)}")

    if args.continue_on_error and summary.updated and had_errors:
        out.print_warning(
            f"\n:warning: {len(summary.errors)} item(s) failed but continuing."
        )
        return 0

    return 1 if had_errors else 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update source versions/hashes and flake input refs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available sources: {', '.join(UPDATERS.keys())}",
    )
    parser.add_argument(
        "source", nargs="?", help="Source or flake input to update (default: all)"
    )
    # Backwards-compat: --all is now a no-op (default behavior)
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "-l", "--list", action="store_true", help="List available sources and inputs"
    )
    parser.add_argument(
        "-R",
        "--no-refs",
        action="store_true",
        help="Skip flake input ref updates",
    )
    parser.add_argument(
        "-S",
        "--no-sources",
        action="store_true",
        help="Skip sources.json hash updates",
    )
    parser.add_argument(
        "-I",
        "--no-input",
        action="store_true",
        help="Skip flake input lock refresh before hashing",
    )
    parser.add_argument(
        "-c",
        "--check",
        action="store_true",
        help="Dry run: check for updates without applying",
    )
    parser.add_argument(
        "-k",
        "--continue-on-error",
        action="store_true",
        help="Continue updating other sources if one fails",
    )
    parser.add_argument(
        "-v",
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
        "-j",
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
    # Hidden backwards-compat aliases
    parser.add_argument("--update-input", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--update-refs", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    # Map old flags to new semantics
    if args.update_refs:
        # Old --update-refs means "only do refs" → skip sources
        args.no_sources = True
    if args.update_input:
        # Old --update-input explicitly requested → ensure not skipped
        args.no_input = False

    raise SystemExit(asyncio.run(_run_updates(args)))


if __name__ == "__main__":
    main()
