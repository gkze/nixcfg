#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "aiohttp",
# ]
# ///
"""Update source versions and hashes in sources.json.

Usage:
    ./update.py <source>                 Update a specific source
    ./update.py <source> --update-input  Update and refresh flake input
    ./update.py --all                    Update all sources
    ./update.py --list                   List available sources

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
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Mapping

import aiohttp


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


def _resolve_timeout(timeout: float | None) -> float:
    return DEFAULT_TIMEOUT if timeout is None else timeout


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
) -> tuple[bytes, Mapping[str, str]]:
    headers = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    timeout_config = aiohttp.ClientTimeout(total=_resolve_timeout(timeout))
    async with session.request(
        method, url, headers=headers, timeout=timeout_config, allow_redirects=True
    ) as response:
        payload = await response.read()
        if response.status >= 400:
            error_body = payload.decode(errors="ignore").strip()
            detail = f"HTTP {response.status} {response.reason}"
            if error_body:
                detail = f"{detail}\n{error_body}"
            raise RuntimeError(f"Request to {url} failed: {detail}")
        return payload, response.headers


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

    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
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
    expr = _build_nix_expr(f"""pkgs.buildGoModule {{
        pname = "{pname}";
        version = "{version}";
        src = {src_expr};
        {subpackages_expr}
        {proxy_expr}
        vendorHash = pkgs.lib.fakeHash;
      }}""")
    async for event in compute_fixed_output_hash(source, expr):
        yield event


async def compute_cargo_vendor_hash(
    source: str, input_name: str, *, subdir: str | None = None
) -> AsyncIterator[UpdateEvent]:
    src_expr = flake_fetch_expr(get_flake_input_node(input_name))
    if subdir:
        src_expr = f'"${{{src_expr}}}/{subdir}"'
    expr = _build_nix_expr(f"""pkgs.rustPlatform.fetchCargoVendor {{
        src = {src_expr};
        hash = pkgs.lib.fakeHash;
      }}""")
    async for event in compute_fixed_output_hash(source, expr):
        yield event


async def compute_npm_deps_hash(
    source: str, input_name: str
) -> AsyncIterator[UpdateEvent]:
    src_expr = flake_fetch_expr(get_flake_input_node(input_name))
    expr = _build_nix_expr(f"""pkgs.fetchNpmDeps {{
        src = {src_expr};
        hash = pkgs.lib.fakeHash;
      }}""")
    async for event in compute_fixed_output_hash(source, expr):
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


@dataclass(frozen=True)
class HashEntry:
    """Single hash entry for sources.json."""

    drv_type: str
    hash_type: str
    hash: str
    url: str | None = None
    urls: dict[str, str] | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "HashEntry":
        return cls(
            drv_type=str(data["drvType"]),
            hash_type=str(data["hashType"]),
            hash=str(data["hash"]),
            url=data.get("url"),
            urls=data.get("urls"),
        )

    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "drvType": self.drv_type,
            "hashType": self.hash_type,
            "hash": self.hash,
        }
        if self.url is not None:
            entry["url"] = self.url
        if self.urls is not None:
            entry["urls"] = self.urls
        return entry


SourceHashes = dict[str, str] | list[HashEntry]


@dataclass(frozen=True)
class HashCollection:
    entries: list[HashEntry] | None = None
    mapping: dict[str, str] | None = None

    @classmethod
    def from_value(cls, value: SourceHashes | "HashCollection") -> "HashCollection":
        if isinstance(value, HashCollection):
            return value
        if isinstance(value, list):
            entries = [
                item if isinstance(item, HashEntry) else HashEntry.from_dict(item)
                for item in value
            ]
            return cls(entries=entries)
        if isinstance(value, dict):
            return cls(mapping=dict(value))
        raise TypeError("Source entry 'hashes' must be a list or dict")

    def to_json(self) -> dict[str, Any] | list[dict[str, Any]]:
        if self.entries is not None:
            return [hash_entry.to_dict() for hash_entry in self.entries]
        if self.mapping is not None:
            return dict(self.mapping)
        return {}


@dataclass(frozen=True)
class SourceEntry:
    """Normalized schema for sources.json entries."""

    hashes: HashCollection
    version: str | None = None
    input: str | None = None
    urls: dict[str, str] | None = None
    commit: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "hashes", HashCollection.from_value(self.hashes))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourceEntry":
        if "hashes" not in data:
            raise ValueError("Source entry is missing 'hashes'")
        hashes = HashCollection.from_value(data["hashes"])
        return cls(
            hashes=hashes,
            version=data.get("version"),
            input=data.get("input"),
            urls=data.get("urls"),
            commit=data.get("commit"),
        )

    def to_dict(self) -> dict[str, Any]:
        entry: dict[str, Any] = {"hashes": self.hashes.to_json()}
        if self.version is not None:
            entry["version"] = self.version
        if self.input is not None:
            entry["input"] = self.input
        if self.urls is not None:
            entry["urls"] = self.urls
        if self.commit is not None:
            entry["commit"] = self.commit
        return entry


@dataclass
class SourcesFile:
    """Container for sources.json entries."""

    entries: dict[str, SourceEntry]

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SourcesFile":
        return cls(
            entries={
                str(name): SourceEntry.from_dict(entry) for name, entry in data.items()
            }
        )

    @classmethod
    def load(cls, path: Path) -> "SourcesFile":
        if not path.exists():
            return cls(entries={})
        return cls.from_dict(json.loads(path.read_text()))

    def to_dict(self) -> dict[str, Any]:
        return {name: entry.to_dict() for name, entry in self.entries.items()}

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n")


@dataclass
class VersionInfo:
    """Version and metadata fetched from upstream."""

    version: str
    metadata: dict[
        str, Any
    ]  # Updater-specific data (URLs, checksums, release info, etc.)


@dataclass
class UpdateResult:
    """Result of an update check."""

    entry: SourceEntry


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

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> UpdateResult:
        """Build UpdateResult from version info and hashes. Override to customize."""
        entry = SourceEntry(
            version=info.version, hashes=HashCollection.from_value(hashes)
        )
        return UpdateResult(entry=entry)

    def _is_latest(self, current: SourceEntry | None, info: VersionInfo) -> bool:
        return current is not None and current.version == info.version

    def _build_result_or_none(
        self,
        current: SourceEntry | None,
        info: VersionInfo,
        hashes: SourceHashes,
    ) -> UpdateResult | None:
        result = self.build_result(info, hashes)
        if current is not None and result.entry == current:
            return None
        return result

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
        result = self._build_result_or_none(current, info, hashes)
        if result is None:
            yield UpdateEvent(
                source=self.name,
                kind=UpdateEventKind.STATUS,
                message="No updates needed.",
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

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> UpdateResult:
        entry = SourceEntry(
            hashes=HashCollection.from_value(hashes), input=self.input_name
        )
        return UpdateResult(entry=entry)

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


class HomebrewZshCompletionUpdater(GitHubRawFileUpdater):
    name = "homebrew-zsh-completion"
    owner = "Homebrew"
    repo = "brew"
    path = "completions/zsh/_brew"


class GituiKeyConfigUpdater(GitHubRawFileUpdater):
    name = "gitui-key-config"
    owner = "extrawurst"
    repo = "gitui"
    path = "vim_style_key_config.ron"


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
        import xml.etree.ElementTree as ET

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

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> UpdateResult:
        """Include the versioned URL in the result."""
        entry = SourceEntry(
            version=info.version,
            hashes=HashCollection.from_value(hashes),
            urls={"darwin": info.metadata["url"]},
        )
        return UpdateResult(entry=entry)


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
        import re

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


class AxiomCliUpdater(GoVendorHashUpdater):
    name = "axiom-cli"
    subpackages = ["cmd/axiom"]


class BeadsUpdater(GoVendorHashUpdater):
    name = "beads"
    subpackages = ["cmd/bd"]
    proxy_vendor = True


class CodexUpdater(CargoVendorHashUpdater):
    name = "codex"
    subdir = "codex-rs"


class CrushUpdater(GoVendorHashUpdater):
    name = "crush"


class GeminiCliUpdater(NpmDepsHashUpdater):
    name = "gemini-cli"


class SentryCliUpdater(CargoVendorHashUpdater):
    name = "sentry-cli"


# =============================================================================
# Main
# =============================================================================


def load_sources() -> SourcesFile:
    return SourcesFile.load(SOURCES_FILE)


def save_sources(sources: SourcesFile) -> None:
    sources.save(SOURCES_FILE)


@dataclass
class SourceState:
    status: str = "pending"
    tail: deque[str] = field(default_factory=deque)
    active_commands: int = 0


@dataclass
class RenderState:
    lines_rendered: int = 0
    panel_height: int = 0


def _is_tty() -> bool:
    term = os.environ.get("TERM", "")
    return sys.stdout.isatty() and term.lower() not in {"", "dumb"}


def _fit_to_width(text: str, width: int) -> str:
    if width <= 0:
        return text
    # Reserve the last column to avoid line wrapping on some terminals.
    return text[: max(0, width - 1)]


def _format_header_line(
    name: str,
    status: str,
    *,
    width: int,
    is_tty: bool,
    bold_prefix: str,
    bold_suffix: str,
) -> str:
    header_plain = _fit_to_width(f"{name}: {status}", width)
    if is_tty and header_plain:
        name_len = min(len(name), len(header_plain))
        return (
            f"{bold_prefix}{header_plain[:name_len]}{bold_suffix}"
            f"{header_plain[name_len:]}"
        )
    return header_plain


def _format_detail_line(
    line: str,
    *,
    width: int,
    is_tty: bool,
    dim_prefix: str,
    dim_suffix: str,
) -> str:
    entry = _fit_to_width(f"  {line}", width)
    if is_tty:
        return f"{dim_prefix}{entry}{dim_suffix}"
    return entry


@dataclass(frozen=True)
class RenderStyle:
    bold_prefix: str
    bold_suffix: str
    dim_prefix: str
    dim_suffix: str


class Renderer:
    def __init__(
        self,
        states: dict[str, SourceState],
        order: list[str],
        *,
        is_tty: bool,
        panel_height: int | None = None,
    ) -> None:
        self.states = states
        self.order = order
        self.is_tty = is_tty
        self.style = RenderStyle(
            bold_prefix="\x1b[1m" if is_tty else "",
            bold_suffix="\x1b[0m" if is_tty else "",
            dim_prefix="\x1b[2m" if is_tty else "",
            dim_suffix="\x1b[0m" if is_tty else "",
        )
        self.render_state = RenderState(
            panel_height=TerminalInfo.panel_height()
            if panel_height is None
            else panel_height
        )
        self.last_render = 0.0
        self.needs_render = False

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
        if self.is_tty and self.needs_render:
            self.render()

    def render(self) -> None:
        if not self.is_tty:
            return
        width = TerminalInfo.width()
        style = self.style
        max_visible = self.render_state.panel_height
        lines: list[str] = []
        for name in self.order:
            if len(lines) >= max_visible:
                break
            state = self.states[name]
            status = state.status or "pending"
            lines.append(
                _format_header_line(
                    name,
                    status,
                    width=width,
                    is_tty=self.is_tty,
                    bold_prefix=style.bold_prefix,
                    bold_suffix=style.bold_suffix,
                )
            )
            if len(lines) >= max_visible:
                break
            if state.active_commands > 0:
                for line in state.tail:
                    if len(lines) >= max_visible:
                        break
                    lines.append(
                        _format_detail_line(
                            line,
                            width=width,
                            is_tty=self.is_tty,
                            dim_prefix=style.dim_prefix,
                            dim_suffix=style.dim_suffix,
                        )
                    )
        if self.render_state.lines_rendered > 1:
            sys.stdout.write(f"\x1b[{self.render_state.lines_rendered - 1}A")
        sys.stdout.write("\r\x1b[J")
        sys.stdout.write("\n".join(lines))
        self.render_state.lines_rendered = len(lines)
        sys.stdout.flush()


async def _consume_events(
    queue: asyncio.Queue[UpdateEvent | None],
    order: list[str],
    sources: SourcesFile,
    *,
    max_lines: int,
    is_tty: bool,
) -> tuple[bool, int]:
    states = {
        name: SourceState(status="pending", tail=deque(maxlen=max_lines))
        for name in order
    }
    updated = False
    errors = 0
    renderer = Renderer(states, order, is_tty=is_tty)

    while True:
        event = await queue.get()
        if event is None:
            break
        state = states.get(event.source)
        if state is None:
            continue

        if event.kind == UpdateEventKind.STATUS:
            state.status = event.message or state.status
            if not is_tty and event.message:
                print(f"[{event.source}] {event.message}")
        elif event.kind == UpdateEventKind.COMMAND_START:
            state.active_commands += 1
            if event.message:
                state.status = event.message
            if state.active_commands == 1:
                state.tail.clear()
            if not is_tty and event.message:
                print(f"[{event.source}] {event.message}")
        elif event.kind == UpdateEventKind.LINE:
            label = event.stream or "stdout"
            message = event.message or ""
            line_text = f"[{label}] {message}" if label else message
            if state.active_commands > 0:
                if not state.tail or state.tail[-1] != line_text:
                    state.tail.append(line_text)
            if not is_tty:
                print(f"[{event.source}][{label}] {message}")
        elif event.kind == UpdateEventKind.COMMAND_END:
            state.active_commands = max(0, state.active_commands - 1)
            if state.active_commands == 0:
                state.tail.clear()
            if not is_tty:
                result = event.payload
                if isinstance(result, CommandResult):
                    print(
                        f"[{event.source}] command finished (exit {result.returncode})"
                    )
        elif event.kind == UpdateEventKind.RESULT:
            result = event.payload
            if result is not None:
                sources.entries[event.source] = result.entry
                updated = True
                state.status = "Updated."
            elif not state.status:
                state.status = "No updates needed."
        elif event.kind == UpdateEventKind.ERROR:
            errors += 1
            message = event.message or "Unknown error"
            state.status = f"Error: {message}"
            state.active_commands = 0
            state.tail.clear()
            if not is_tty:
                print(f"[{event.source}] Error: {message}", file=sys.stderr)

        renderer.request_render()
        renderer.render_if_due(time.monotonic())

    renderer.finalize()

    return updated, errors


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


async def _run_updates(args: argparse.Namespace) -> int:
    if args.list:
        print("Available sources:")
        for name in UPDATERS:
            print(f"  {name}")
        return 0

    if not args.source and not args.all:
        print("No source specified. Use --all or provide a source name.")
        return 1

    if not args.all and args.source not in UPDATERS:
        print(f"Error: Unknown source '{args.source}'")
        print(f"Available sources: {', '.join(UPDATERS.keys())}")
        return 1

    sources = load_sources()
    names = list(UPDATERS.keys()) if args.all else [args.source]
    max_lines = _resolve_log_tail_lines(None)
    is_tty = _is_tty()

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
            _consume_events(queue, names, sources, max_lines=max_lines, is_tty=is_tty)
        )
        await asyncio.gather(*tasks)
        await queue.put(None)
        updated, errors = await consumer

    if updated:
        save_sources(sources)
        print(f"\nUpdated {SOURCES_FILE}")
        print("Run: nh darwin switch --no-nom .")
    else:
        print("\nNo updates needed.")

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
    args = parser.parse_args()

    raise SystemExit(asyncio.run(_run_updates(args)))


if __name__ == "__main__":
    main()
