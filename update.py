#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#   "aiohttp>=3.13.3",
#   "filelock>=3.20.3",
#   "pydantic>=2.12.5",
#   "rich>=14.3.1",
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
    UPDATE_LOG_FULL           Show full, scrollable output (default: 0)

Sources are defined with custom update logic for fetching latest versions
and computing hashes from upstream release channels.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import json
import netrc
import os
import random
import re
import shlex
import shutil
import sys
import tempfile
import time
import urllib.parse
import xml.etree.ElementTree as ET
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Literal, Mapping

import aiohttp
from filelock import FileLock
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


@dataclass(frozen=True)
class UpdateConfig:
    default_timeout: int = 30
    default_subprocess_timeout: int = 600  # 10 minutes for nix builds
    default_log_tail_lines: int = 10
    default_render_interval: float = 0.05
    default_user_agent: str = "update.py"
    default_retries: int = 3
    default_retry_backoff: float = 1.0
    retry_jitter_ratio: float = 0.2
    fake_hash: str = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    deno_deps_platforms: tuple[str, ...] = (
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    )


DEFAULT_CONFIG = UpdateConfig()

# =============================================================================
# Core utilities
# =============================================================================


DEFAULT_TIMEOUT = DEFAULT_CONFIG.default_timeout
DEFAULT_SUBPROCESS_TIMEOUT = DEFAULT_CONFIG.default_subprocess_timeout
DEFAULT_LOG_TAIL_LINES = DEFAULT_CONFIG.default_log_tail_lines
DEFAULT_RENDER_INTERVAL = DEFAULT_CONFIG.default_render_interval
DEFAULT_USER_AGENT = DEFAULT_CONFIG.default_user_agent
DEFAULT_RETRIES = DEFAULT_CONFIG.default_retries
DEFAULT_RETRY_BACKOFF = DEFAULT_CONFIG.default_retry_backoff
# Placeholder hash used when a real hash cannot be computed (e.g., non-native platforms).
# In --native-only mode, updaters preserve FAKE_HASH for platforms that can't be built
# locally. The CI pipeline runs on multiple platforms and merges results, so each
# platform computes its native hashes while preserving placeholders for others.
# See merge_sources.py for the merge logic that combines platform-specific results.
FAKE_HASH = DEFAULT_CONFIG.fake_hash
# Platforms to compute Deno deps hashes for (matches flake's systems list)
DENO_DEPS_PLATFORMS = DEFAULT_CONFIG.deno_deps_platforms
SRI_PREFIX = "sha256-"
GO_LATEST_EXPR = "if pkgs ? go_1_26 then pkgs.go_1_26 else if pkgs ? go_1_25 then pkgs.go_1_25 else pkgs.go"
# Core tools needed for most operations
REQUIRED_TOOLS = ["nix", "nix-prefetch-url"]
# flake-edit only needed for ref updates
FIXED_OUTPUT_NOISE = (
    "error: hash mismatch in fixed-output derivation",
    "specified:",
    "got:",
    "error: Cannot build",
    "Reason:",
    "Output paths:",
    "error: Build failed due to failed dependency",
)


@functools.cache
def get_current_nix_platform() -> str:
    """Get current Nix platform (e.g., 'aarch64-darwin', 'x86_64-linux')."""
    import platform

    machine = platform.machine()
    system = platform.system().lower()

    arch_map = {"arm64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}
    arch = arch_map.get(machine, machine)

    return f"{arch}-{system}"


def _check_required_tools(*, include_flake_edit: bool = False) -> list[str]:
    """Check that required external tools are available. Returns list of missing tools."""
    tools = list(REQUIRED_TOOLS)
    if include_flake_edit:
        tools.append("flake-edit")
    missing = [tool for tool in tools if shutil.which(tool) is None]
    return missing


def _resolve_log_tail_lines(lines: int | None) -> int:
    if lines is not None:
        return max(1, lines)
    configured = os.environ.get("UPDATE_LOG_TAIL_LINES")
    if configured is None:
        return DEFAULT_LOG_TAIL_LINES
    try:
        return max(1, int(configured))
    except ValueError:
        return DEFAULT_LOG_TAIL_LINES


def _resolve_full_output() -> bool:
    configured = os.environ.get("UPDATE_LOG_FULL")
    if not configured:
        return False
    return configured.strip().lower() in {"1", "true", "yes", "on"}


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


def _is_terminal_status(message: str) -> bool:
    return message.startswith(
        (
            "Up to date",
            "Updated",
            "Update available",
            "Already at latest",
            "No updates needed",
        )
    )


# =============================================================================
# Event stream
# =============================================================================


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

    @classmethod
    def status(cls, source: str, message: str) -> "UpdateEvent":
        return cls(source=source, kind=UpdateEventKind.STATUS, message=message)

    @classmethod
    def error(cls, source: str, message: str) -> "UpdateEvent":
        return cls(source=source, kind=UpdateEventKind.ERROR, message=message)

    @classmethod
    def result(cls, source: str, payload: Any = None) -> "UpdateEvent":
        return cls(source=source, kind=UpdateEventKind.RESULT, payload=payload)

    @classmethod
    def value(cls, source: str, payload: Any) -> "UpdateEvent":
        return cls(source=source, kind=UpdateEventKind.VALUE, payload=payload)


# Type alias for async event streams
EventStream = AsyncIterator[UpdateEvent]


@dataclass
class ValueDrain:
    value: Any | None = None


async def drain_value_events(events: EventStream, drain: ValueDrain) -> EventStream:
    async for event in events:
        if event.kind == UpdateEventKind.VALUE:
            drain.value = event.payload
        else:
            yield event


def _require_value(drain: ValueDrain, error: str) -> Any:
    if drain.value is None:
        raise RuntimeError(error)
    return drain.value


# =============================================================================
# Data models
# =============================================================================


# SRI hash pattern for validation
_SRI_HASH_PATTERN = re.compile(r"^sha256-[A-Za-z0-9+/]+=*$")

# Common platform API mappings (nix platform -> API platform)
VSCODE_PLATFORMS = {
    "aarch64-darwin": "darwin-arm64",
    "aarch64-linux": "linux-arm64",
    "x86_64-linux": "linux-x64",
}

# Valid derivation types
DrvType = Literal[
    "buildGoModule",
    "bunBuild",  # For bun-based node_modules (e.g., opencode)
    "denoDeps",
    "fetchCargoVendor",
    "fetchFromGitHub",
    "fetchNpmDeps",
    "fetchurl",
    "importCargoLock",  # For Cargo.lock with git dependencies
]

# Valid hash types
HashType = Literal[
    "cargoHash",
    "denoDepsHash",
    "nodeModulesHash",  # For node_modules built via bun/custom builders
    "npmDepsHash",
    "sha256",
    "srcHash",
    "spectaOutputHash",  # For specta git dependency hash
    "tauriOutputHash",  # For tauri git dependency hash
    "tauriSpectaOutputHash",  # For tauri-specta git dependency hash
    "vendorHash",
]


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
    platform: str | None = None  # Optional platform for platform-specific hashes
    url: str | None = None
    urls: dict[str, str] | None = None
    git_dep: str | None = Field(
        default=None, alias="gitDep"
    )  # Git dependency name (for importCargoLock)

    @field_validator("hash")
    @classmethod
    def validate_hash(cls, v: str) -> str:
        return _validate_sri_hash(v)

    @classmethod
    def create(
        cls,
        drv_type: DrvType,
        hash_type: HashType,
        hash_value: str,
        *,
        git_dep: str | None = None,
        platform: str | None = None,
        url: str | None = None,
        urls: dict[str, str] | None = None,
    ) -> "HashEntry":
        """Convenience constructor with positional args."""
        return cls(
            drvType=drv_type,
            gitDep=git_dep,
            hashType=hash_type,
            hash=hash_value,
            platform=platform,
            url=url,
            urls=urls,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict with camelCase keys, sorted alphabetically."""
        return dict(
            sorted(
                {
                    k: v
                    for k, v in {
                        "drvType": self.drv_type,
                        "gitDep": self.git_dep,
                        "hash": self.hash,
                        "hashType": self.hash_type,
                        "platform": self.platform,
                        "url": self.url,
                        "urls": self.urls,
                    }.items()
                    if v is not None
                }.items()
            )
        )


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
        return dict(
            sorted(
                {
                    k: v
                    for k, v in {
                        "hashes": self.hashes.to_json(),
                        "commit": self.commit,
                        "input": self.input,
                        "urls": self.urls,
                        "version": self.version,
                    }.items()
                    if v is not None
                }.items()
            )
        )


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
        payload = json.dumps(data, indent=2, sort_keys=True) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        mode = path.stat().st_mode & 0o777 if path.exists() else None
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as tmp_file:
                tmp_file.write(payload)
                tmp_file.flush()
                os.fsync(tmp_file.fileno())
                tmp_path = Path(tmp_file.name)
                if mode is not None:
                    os.fchmod(tmp_file.fileno(), mode)
            tmp_path.replace(path)
        finally:
            if tmp_path is not None and tmp_path.exists():
                tmp_path.unlink()

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


@dataclass(frozen=True)
class CargoLockGitDep:
    git_dep: str
    hash_type: HashType
    match_name: str


def _verify_platform_versions(versions: dict[str, str], source_name: str) -> str:
    """Verify all platform versions match and return the common version."""
    unique = set(versions.values())
    if len(unique) != 1:
        raise RuntimeError(
            f"{source_name} version mismatch across platforms: {versions}"
        )
    return unique.pop()


# =============================================================================
# Network + subprocess utilities
# =============================================================================


@functools.cache
def _get_github_token() -> str | None:
    """Get GitHub token from GITHUB_TOKEN env var or ~/.netrc (cached)."""
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        return token
    netrc_path = Path.home() / ".netrc"
    if netrc_path.exists():
        try:
            netrc_data = netrc.netrc(str(netrc_path))
            for host in ("api.github.com", "github.com"):
                auth = netrc_data.authenticators(host)
                if auth:
                    return auth[2]  # password field contains token
        except (netrc.NetrcParseError, OSError):
            pass
    return None


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


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return max(0.0, float(value))
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    delay = (parsed - datetime.now(timezone.utc)).total_seconds()
    return max(0.0, delay)


def _apply_retry_jitter(delay: float, *, jitter_ratio: float) -> float:
    if jitter_ratio <= 0:
        return delay
    jitter_ratio = min(jitter_ratio, 1.0)
    low = max(0.0, 1.0 - jitter_ratio)
    high = 1.0 + jitter_ratio
    return max(0.0, delay * random.uniform(low, high))


def _build_request_headers(url: str, user_agent: str | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    github_token = _get_github_token()
    if url.startswith("https://api.github.com/") and github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return headers


def _format_http_error(response: aiohttp.ClientResponse, payload: bytes) -> str:
    error_body = payload.decode(errors="ignore").strip()
    detail = f"HTTP {response.status} {response.reason}"
    if error_body:
        detail = f"{detail}\n{error_body}"
    return detail


async def _request(
    session: aiohttp.ClientSession,
    url: str,
    *,
    user_agent: str | None = None,
    timeout: float | None = None,
    method: str = "GET",
    retries: int | None = None,
    backoff: float | None = None,
    config: UpdateConfig = DEFAULT_CONFIG,
) -> tuple[bytes, Mapping[str, str]]:
    if retries is None:
        retries = config.default_retries
    if backoff is None:
        backoff = config.default_retry_backoff
    headers = _build_request_headers(url, user_agent)
    timeout_config = aiohttp.ClientTimeout(total=timeout or config.default_timeout)

    last_error: Exception | None = None
    for attempt in range(retries):
        retry_after_delay: float | None = None
        try:
            async with session.request(
                method,
                url,
                headers=headers,
                timeout=timeout_config,
                allow_redirects=True,
            ) as response:
                payload = await response.read()
                if response.status < 400:
                    return payload, response.headers
                detail = _format_http_error(response, payload)
                error = RuntimeError(f"Request to {url} failed: {detail}")
                if response.status == 429:
                    last_error = error
                    retry_after_delay = _parse_retry_after(
                        response.headers.get("Retry-After")
                    )
                # Don't retry client errors (4xx), only server errors (5xx)
                elif response.status < 500:
                    raise error
                else:
                    last_error = error
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_error = e

        # Exponential backoff before retry
        if attempt < retries - 1:
            delay = retry_after_delay
            if delay is None:
                delay = backoff * (2**attempt)
                delay = _apply_retry_jitter(
                    delay, jitter_ratio=config.retry_jitter_ratio
                )
            await asyncio.sleep(delay)

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
    payload, _ = await _request(session, url, user_agent=user_agent, timeout=timeout)
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


# =============================================================================
# Subprocess execution
# =============================================================================


@dataclass(frozen=True)
class CommandResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str
    allow_failure: bool = False


async def stream_command(
    args: list[str],
    *,
    source: str,
    timeout: float = DEFAULT_SUBPROCESS_TIMEOUT,
    env: Mapping[str, str] | None = None,
    allow_failure: bool = False,
    suppress_patterns: tuple[str, ...] | None = None,
) -> EventStream:
    command_text = _truncate_command(shlex.join(args))
    yield UpdateEvent(
        source=source,
        kind=UpdateEventKind.COMMAND_START,
        message=command_text,
        payload=args,
    )

    # Use TERM=dumb to prevent subprocesses (especially nix) from writing
    # fancy progress output directly to /dev/tty, which corrupts cursor tracking.
    base_env = {**os.environ, "TERM": "dumb"}
    if env:
        base_env.update(env)
    process = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=base_env,
    )
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout

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
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise asyncio.TimeoutError(f"Command timed out after {timeout}s")
            label, text = await asyncio.wait_for(queue.get(), timeout=remaining)
            if text is None:
                done_streams += 1
                continue
            sanitized = _sanitize_log_line(text.rstrip("\n"))
            if sanitized:
                if suppress_patterns and any(
                    pattern in sanitized for pattern in suppress_patterns
                ):
                    continue
                yield UpdateEvent(
                    source=source,
                    kind=UpdateEventKind.LINE,
                    message=sanitized,
                    stream=label,
                )
        await asyncio.gather(*tasks)
        returncode = await process.wait()
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise RuntimeError(f"Command timed out after {timeout}s: {shlex.join(args)}")

    result = CommandResult(
        args=args,
        returncode=returncode,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
        allow_failure=allow_failure,
    )
    yield UpdateEvent(source=source, kind=UpdateEventKind.COMMAND_END, payload=result)


async def run_command(
    args: list[str],
    *,
    source: str,
    error: str,
    env: Mapping[str, str] | None = None,
    allow_failure: bool = False,
    suppress_patterns: tuple[str, ...] | None = None,
) -> EventStream:
    result_drain = ValueDrain()
    async for event in stream_command(
        args,
        source=source,
        env=env,
        allow_failure=allow_failure,
        suppress_patterns=suppress_patterns,
    ):
        if event.kind == UpdateEventKind.COMMAND_END:
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
    ):
        yield event


# =============================================================================
# Hash computation (downloaded artifacts)
# =============================================================================


async def convert_nix_hash_to_sri(source: str, hash_value: str) -> EventStream:
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
    yield UpdateEvent.value(source, result.stdout.strip())


async def compute_sri_hash(source: str, url: str) -> EventStream:
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


async def compute_url_hashes(source: str, urls: Iterable[str]) -> EventStream:
    hashes: dict[str, str] = {}
    for url in dict.fromkeys(urls):
        sri_drain = ValueDrain()
        async for event in drain_value_events(compute_sri_hash(source, url), sri_drain):
            yield event
        sri_value = _require_value(sri_drain, "Missing hash output")
        hashes[url] = sri_value
    yield UpdateEvent.value(source, hashes)


# =============================================================================
# Flake + nix helpers
# =============================================================================


def load_flake_lock() -> dict:
    """Load flake.lock nodes. Validates expected structure."""
    if not FLAKE_LOCK_FILE.exists():
        raise FileNotFoundError(f"flake.lock not found at {FLAKE_LOCK_FILE}")
    data = json.loads(FLAKE_LOCK_FILE.read_text())
    if "nodes" not in data:
        raise ValueError(
            f"Invalid flake.lock: missing 'nodes' key in {FLAKE_LOCK_FILE}"
        )
    nodes = data["nodes"]
    if "root" not in nodes:
        raise ValueError(
            f"Invalid flake.lock: missing 'root' node in {FLAKE_LOCK_FILE}"
        )
    return nodes


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

    return "\n".join(
        [
            "builtins.fetchTree { ",
            f'type = "{locked["type"]}"; ',
            f'owner = "{locked["owner"]}"; ',
            f'repo = "{locked["repo"]}"; ',
            f'rev = "{locked["rev"]}"; ',
            f'narHash = "{locked["narHash"]}"; ',
            " }",
        ]
    )


def nixpkgs_expr() -> str:
    node_name = get_root_input_name("nixpkgs")
    node = get_flake_input_node(node_name)
    return f"import ({flake_fetch_expr(node)}) {{ system = builtins.currentSystem; }}"


async def update_flake_input(input_name: str, *, source: str) -> EventStream:
    """Update a flake input in flake.lock."""
    async for event in stream_command(
        ["nix", "flake", "lock", "--update-input", input_name],
        source=source,
    ):
        yield event


# =============================================================================
# Hash computation (nix builds)
# =============================================================================


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
    raise RuntimeError(
        "Could not find hash in nix output. Output tail:\n"
        f"{_tail_output_excerpt(output, max_lines=DEFAULT_LOG_TAIL_LINES)}"
    )


def _tail_output_excerpt(output: str, *, max_lines: int) -> str:
    output = output.strip()
    if not output:
        return "<no output>"
    lines = output.splitlines()
    if len(lines) <= max_lines:
        return output
    tail = "\n".join(lines[-max_lines:])
    return f"... (last {max_lines} of {len(lines)} lines)\n{tail}"


def _compact_nix_expr(expr: str) -> str:
    return " ".join(line.strip() for line in expr.splitlines() if line.strip())


def _render_output_hashes(output_hashes: Mapping[str, str], *, fake_hash: str) -> str:
    lines = ["{"]
    for name in sorted(output_hashes):
        value = output_hashes[name]
        rendered = "pkgs.lib.fakeHash" if value == fake_hash else f'"{value}"'
        lines.append(f'  "{name}" = {rendered};')
    lines.append("}")
    return "\n".join(lines)


def _extract_git_dep_from_output(
    output: str, git_deps: Iterable[CargoLockGitDep]
) -> CargoLockGitDep:
    match = re.search(r"hash mismatch in fixed-output derivation '([^']+)'", output)
    search_text = output
    if match:
        search_text = match.group(1).rsplit("/", 1)[-1]
    matches = [dep for dep in git_deps if dep.match_name in search_text]
    if not matches:
        raise RuntimeError(
            "Could not identify git dependency from nix output. Output tail:\n"
            f"{_tail_output_excerpt(output, max_lines=DEFAULT_LOG_TAIL_LINES)}"
        )
    return max(matches, key=lambda dep: len(dep.match_name))


def _build_import_cargo_lock_expr(
    *,
    input_name: str,
    package_attr: str,
    lockfile_path: str,
    output_hashes_expr: str,
) -> str:
    repo_path = get_repo_file(".")
    return _compact_nix_expr(
        f"""
        let
          pkgs = {nixpkgs_expr()};
          flake = builtins.getFlake "git+file://{repo_path}?dirty=1";
          upstream = flake.inputs.{input_name}.packages.${{builtins.currentSystem}}.{package_attr};
        in pkgs.rustPlatform.importCargoLock {{
          lockFile = "${{upstream.src}}/{lockfile_path}";
          outputHashes = {output_hashes_expr};
        }}
        """
    )


async def _emit_sri_hash_from_build_result(
    source: str, result: CommandResult
) -> EventStream:
    hash_value = _extract_nix_hash(result.stderr + result.stdout)
    if hash_value.startswith(SRI_PREFIX):
        yield UpdateEvent.value(source, hash_value)
        return
    async for event in convert_nix_hash_to_sri(source, hash_value):
        yield event


async def compute_fixed_output_hash(source: str, expr: str) -> EventStream:
    """Compute hash by running a nix expression with lib.fakeHash."""
    expr = _compact_nix_expr(expr)
    result_drain = ValueDrain()
    async for event in drain_value_events(
        run_nix_build(
            source,
            expr,
            allow_failure=True,
            suppress_patterns=FIXED_OUTPUT_NOISE,
            verbose=True,
        ),
        result_drain,
    ):
        yield event
    result = _require_value(result_drain, "nix build did not return output")
    if result.returncode == 0:
        raise RuntimeError(
            "Expected nix build to fail with hash mismatch, but it succeeded"
        )
    async for event in _emit_sri_hash_from_build_result(source, result):
        yield event


def _build_nix_expr(body: str) -> str:
    """Build a nix expression with nixpkgs prelude."""
    return _compact_nix_expr(f"let pkgs = {nixpkgs_expr()}; in {body}")


async def _compute_nixpkgs_hash(source: str, expr_body: str) -> EventStream:
    """Compute hash for a nixpkgs expression that uses lib.fakeHash."""
    expr = _build_nix_expr(expr_body)
    async for event in compute_fixed_output_hash(source, expr):
        yield event


def _build_deno_deps_expr(source: str, platform: str) -> str:
    nix_attr = f'"{source}"'
    nixpkgs_node = get_flake_input_node(get_root_input_name("nixpkgs"))
    expr = (
        'let flake = builtins.getFlake "git+file://'
        f'{get_repo_file(".")}?dirty=1"; '
        "pkgs = import ("
        f"{flake_fetch_expr(nixpkgs_node)}"
        f') {{ system = "{platform}"; overlays = [ flake.overlays.default ]; }}; '
        f"in pkgs.{nix_attr}"
    )
    return _compact_nix_expr(expr)


def _build_deno_hash_entries(
    *,
    platforms: Iterable[str],
    active_platform: str,
    existing_hashes: Mapping[str, str],
    computed_hashes: Mapping[str, str],
    fake_hash: str,
) -> list[HashEntry]:
    entries: list[HashEntry] = []
    for platform in platforms:
        if platform == active_platform:
            hash_value = fake_hash
        else:
            hash_value = computed_hashes.get(platform) or existing_hashes.get(
                platform, fake_hash
            )
        entries.append(
            HashEntry.create(
                "denoDeps",
                "denoDepsHash",
                hash_value,
                platform=platform,
            )
        )
    return entries


def _build_deno_temp_sources(
    *,
    sources: SourcesFile,
    source: str,
    input_name: str,
    original_entry: SourceEntry | None,
    entries: list[HashEntry],
) -> SourcesFile:
    hash_collection = HashCollection.from_value(entries)
    if original_entry is not None:
        temp_entry = original_entry.model_copy(
            update={"hashes": hash_collection, "input": input_name}
        )
    else:
        temp_entry = SourceEntry(hashes=hash_collection, input=input_name)
    temp_entries = dict(sources.entries)
    temp_entries[source] = temp_entry
    return SourcesFile(entries=temp_entries)


async def compute_go_vendor_hash(
    source: str,
    input_name: str,
    *,
    pname: str,
    version: str,
    subpackages: list[str] | None = None,
    proxy_vendor: bool = False,
    go_package_expr: str | None = None,
) -> EventStream:
    subpackages_expr = ""
    if subpackages:
        quoted = " ".join(f'"{subpkg}"' for subpkg in subpackages)
        subpackages_expr = f"subPackages = [ {quoted} ];"
    proxy_expr = "proxyVendor = true;" if proxy_vendor else ""
    src_expr = flake_fetch_expr(get_flake_input_node(input_name))
    build_go_module = "pkgs.buildGoModule"
    if go_package_expr:
        build_go_module = f"(pkgs.buildGoModule.override {{ go = {go_package_expr}; }})"
    async for event in _compute_nixpkgs_hash(
        source,
        f"""{build_go_module} {{
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
) -> EventStream:
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


async def compute_npm_deps_hash(source: str, input_name: str) -> EventStream:
    src_expr = flake_fetch_expr(get_flake_input_node(input_name))
    async for event in _compute_nixpkgs_hash(
        source,
        f"""pkgs.fetchNpmDeps {{
        src = {src_expr};
        hash = pkgs.lib.fakeHash;
      }}""",
    ):
        yield event


async def compute_bun_node_modules_hash(source: str, input_name: str) -> EventStream:
    """Compute hash for bun-based node_modules from a flake input package.

    This is used for packages like opencode that use bun to build node_modules.
    The hash is computed by building the upstream package's node_modules derivation
    with a fake hash and extracting the correct hash from the error.
    """
    repo_path = get_repo_file(".")
    expr = _compact_nix_expr(f"""
        let
          pkgs = {nixpkgs_expr()};
          flake = builtins.getFlake "git+file://{repo_path}?dirty=1";
          upstream = flake.inputs.{input_name}.packages.${{builtins.currentSystem}}.{source};
        in upstream.node_modules.overrideAttrs {{ outputHash = pkgs.lib.fakeHash; }}
    """)
    async for event in compute_fixed_output_hash(source, expr):
        yield event


async def compute_import_cargo_lock_output_hashes(
    source: str,
    input_name: str,
    *,
    package_attr: str,
    lockfile_path: str,
    git_deps: list[CargoLockGitDep],
    config: UpdateConfig = DEFAULT_CONFIG,
) -> EventStream:
    hashes: dict[str, str] = {}
    remaining = {dep.git_dep for dep in git_deps}

    while remaining:
        output_hashes = {
            dep.git_dep: hashes.get(dep.git_dep, config.fake_hash) for dep in git_deps
        }
        output_hashes_expr = _render_output_hashes(
            output_hashes, fake_hash=config.fake_hash
        )
        expr = _build_import_cargo_lock_expr(
            input_name=input_name,
            package_attr=package_attr,
            lockfile_path=lockfile_path,
            output_hashes_expr=output_hashes_expr,
        )
        result_drain = ValueDrain()
        async for event in drain_value_events(
            run_nix_build(
                source,
                expr,
                allow_failure=True,
                suppress_patterns=FIXED_OUTPUT_NOISE,
                verbose=True,
            ),
            result_drain,
        ):
            yield event
        result = _require_value(result_drain, "nix build did not return output")
        if result.returncode == 0:
            raise RuntimeError(
                "Expected nix build to fail with hash mismatch, but it succeeded"
            )
        output = result.stderr + result.stdout
        hash_value = _extract_nix_hash(output)
        dep = _extract_git_dep_from_output(output, git_deps)
        if dep.git_dep in hashes:
            raise RuntimeError(
                f"Hash for {dep.git_dep} already computed; output:\n{_tail_output_excerpt(output, max_lines=DEFAULT_LOG_TAIL_LINES)}"
            )
        hashes[dep.git_dep] = hash_value
        remaining.discard(dep.git_dep)

    yield UpdateEvent.value(source, hashes)


async def _compute_deno_deps_hash_for_platform(
    source: str,
    input_name: str,
    platform: str,
    *,
    sources_path: Path | None = None,
) -> EventStream:
    """Compute Deno deps hash for a specific platform."""
    expr = _build_deno_deps_expr(source, platform)
    result_drain = ValueDrain()
    command_env = None
    if sources_path is not None:
        command_env = {"SOURCES_JSON": str(sources_path)}
    async for event in drain_value_events(
        run_nix_build(
            f"{source}:{platform}",
            expr,
            env=command_env,
        ),
        result_drain,
    ):
        yield event
    result = _require_value(result_drain, "nix build did not return output")

    if result.returncode == 0:
        raise RuntimeError(
            f"Expected nix build to fail with hash mismatch for {platform}, but it succeeded"
        )
    hash_drain = ValueDrain()
    async for event in drain_value_events(
        _emit_sri_hash_from_build_result(source, result), hash_drain
    ):
        yield event
    hash_value = _require_value(hash_drain, "Hash conversion failed")
    yield UpdateEvent.value(source, (platform, hash_value))


async def compute_deno_deps_hash(
    source: str,
    input_name: str,
    *,
    native_only: bool = False,
    sources_path: Path | None = None,
    config: UpdateConfig = DEFAULT_CONFIG,
) -> EventStream:
    """Compute Deno deps hash for supported platforms.

    Deno caches can include platform-specific dependencies (e.g., lefthook binaries),
    so we compute hashes for each supported platform separately. For non-native
    platforms, Nix will automatically use configured remote builders (e.g.,
    nix-rosetta-builder on macOS provides aarch64-linux and x86_64-linux builders).

    When native_only is True (--native-only flag), only computes hash for the
    current platform and preserves existing hashes for other platforms. This is
    useful in CI where remote builders aren't available.

    We compute hashes one platform at a time: set fakeHash for that platform,
    build the flake, extract the correct hash from the error, then move to next.
    Uses file locking to prevent concurrent modifications.
    """
    current_platform = get_current_nix_platform()
    platforms = config.deno_deps_platforms
    if current_platform not in platforms:
        raise RuntimeError(
            f"Current platform {current_platform} not in supported platforms: "
            f"{platforms}"
        )

    if sources_path is None:
        sources_path = SOURCES_FILE
    lock_path = sources_path.with_suffix(".json.lock")
    with FileLock(lock_path):
        sources = SourcesFile.load(sources_path)
        original_entry = sources.entries.get(source)

        # Preserve existing hashes for platforms we won't compute
        existing_hashes: dict[str, str] = {}
        if original_entry:
            if original_entry.hashes.entries:
                existing_hashes = {
                    entry.platform: entry.hash
                    for entry in original_entry.hashes.entries
                    if entry.platform
                }
            elif original_entry.hashes.mapping:
                existing_hashes = dict(original_entry.hashes.mapping)

        # Determine which platforms to compute
        platforms_to_compute = (current_platform,) if native_only else platforms

        platform_hashes: dict[str, str] = {}
        failed_platforms: list[str] = []
        with tempfile.TemporaryDirectory(prefix="sources-json-") as temp_dir:
            temp_sources_path = Path(temp_dir) / "sources.json"

            # Compute hash for each platform sequentially
            for platform in platforms_to_compute:
                yield UpdateEvent.status(source, f"Computing hash for {platform}...")

                temp_entries = _build_deno_hash_entries(
                    platforms=platforms,
                    active_platform=platform,
                    existing_hashes=existing_hashes,
                    computed_hashes=platform_hashes,
                    fake_hash=config.fake_hash,
                )
                temp_sources = _build_deno_temp_sources(
                    sources=sources,
                    source=source,
                    input_name=input_name,
                    original_entry=original_entry,
                    entries=temp_entries,
                )
                temp_sources.save(temp_sources_path)

                try:
                    async for event in _compute_deno_deps_hash_for_platform(
                        source,
                        input_name,
                        platform,
                        sources_path=temp_sources_path,
                    ):
                        if (
                            event.kind == UpdateEventKind.VALUE
                            and event.payload is not None
                        ):
                            plat, hash_val = event.payload
                            platform_hashes[plat] = hash_val
                        else:
                            yield event
                except RuntimeError:
                    # Non-native platform build failed - preserve existing hash if available
                    if platform != current_platform:
                        failed_platforms.append(platform)
                        if platform in existing_hashes:
                            yield UpdateEvent.status(
                                source,
                                f"Build failed for {platform}, preserving existing hash",
                            )
                            platform_hashes[platform] = existing_hashes[platform]
                        else:
                            yield UpdateEvent.status(
                                source,
                                f"Build failed for {platform}, no existing hash to preserve",
                            )
                    else:
                        # Native platform failed - this is a real error
                        raise

        # Warn if any platforms failed
        if failed_platforms:
            yield UpdateEvent.status(
                source,
                f"Warning: {len(failed_platforms)} platform(s) failed, "
                f"preserved existing hashes: {', '.join(failed_platforms)}",
            )

        # Merge computed hashes with preserved existing hashes
        final_hashes = {**existing_hashes, **platform_hashes}
        yield UpdateEvent.value(source, final_hashes)


# =============================================================================
# GitHub helpers
# =============================================================================


def github_raw_url(owner: str, repo: str, rev: str, path: str) -> str:
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{rev}/{path}"


def github_api_url(path: str) -> str:
    """Build GitHub API URL from path (e.g., 'repos/owner/repo')."""
    return f"https://api.github.com/{path}"


async def fetch_github_api(
    session: aiohttp.ClientSession, api_path: str, **params: str
) -> dict:
    """Fetch from GitHub API with standard options."""
    url = github_api_url(api_path)
    if params:
        url = f"{url}?{'&'.join(f'{k}={v}' for k, v in params.items())}"
    return await fetch_json(
        session, url, user_agent=DEFAULT_USER_AGENT, timeout=DEFAULT_TIMEOUT
    )


async def fetch_github_default_branch(
    session: aiohttp.ClientSession, owner: str, repo: str
) -> str:
    data = await fetch_github_api(session, f"repos/{owner}/{repo}")
    return data["default_branch"]


async def fetch_github_latest_commit(
    session: aiohttp.ClientSession, owner: str, repo: str, file_path: str, branch: str
) -> str:
    data = await fetch_github_api(
        session,
        f"repos/{owner}/{repo}/commits",
        path=urllib.parse.quote(file_path),
        sha=branch,
        per_page="1",
    )
    if not data:
        raise RuntimeError(f"No commits found for {owner}/{repo}:{file_path}")
    return data[0]["sha"]


# =============================================================================
# Updater framework
# =============================================================================

# Registry populated automatically via __init_subclass__
UPDATERS: dict[str, type["Updater"]] = {}


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
    ) -> EventStream:
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
    ) -> EventStream:
        """Check for updates. Yields UpdateEvent stream and final result."""
        yield UpdateEvent.status(self.name, f"Fetching latest {self.name} version...")
        info = await self.fetch_latest(session)

        yield UpdateEvent.status(self.name, f"Latest version: {info.version}")
        if self._is_latest(current, info):
            yield UpdateEvent.status(self.name, f"Up to date (version: {info.version})")
            yield UpdateEvent.result(self.name)
            return

        yield UpdateEvent.status(self.name, "Fetching hashes for all platforms...")
        hashes_drain = ValueDrain()
        async for event in drain_value_events(
            self.fetch_hashes(info, session), hashes_drain
        ):
            yield event
        hashes = _require_value(hashes_drain, "Missing hash output")
        result = self.build_result(info, hashes)
        if current is not None and result == current:
            yield UpdateEvent.status(self.name, "Up to date")
            yield UpdateEvent.result(self.name)
            return
        yield UpdateEvent.result(self.name, result)


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
    ) -> EventStream:
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
        yield UpdateEvent.value(self.name, hashes)


class DownloadHashUpdater(Updater):
    """Base for sources requiring download to compute hash, with URL deduplication."""

    PLATFORMS: dict[str, str]  # nix_platform -> download_url or template
    BASE_URL: str = ""  # Optional base URL for simple {BASE_URL}/{PLATFORMS[p]} pattern

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        """Return download URL for a platform. Override for custom URL building."""
        if self.BASE_URL:
            return f"{self.BASE_URL}/{self.PLATFORMS[platform]}"
        return self.PLATFORMS[platform]

    def _platform_urls(self, info: VersionInfo) -> dict[str, str]:
        return {
            platform: self.get_download_url(platform, info)
            for platform in self.PLATFORMS
        }

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Build result with platform URLs. Override for custom result building."""
        urls = self._platform_urls(info)
        return self._build_result_with_urls(info, hashes, urls)

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
        """Compute hashes by downloading, deduplicating identical URLs."""
        platform_urls = self._platform_urls(info)
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
        yield UpdateEvent.value(self.name, hashes)


class HashEntryUpdater(Updater):
    """Base for sources that emit hash entries in sources.json."""

    input_name: str | None = None

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        return SourceEntry(
            hashes=HashCollection.from_value(hashes), input=self.input_name
        )

    async def _emit_single_hash_entry(
        self,
        events: EventStream,
        *,
        error: str,
        drv_type: DrvType,
        hash_type: HashType,
    ) -> EventStream:
        hash_drain = ValueDrain()
        async for event in drain_value_events(events, hash_drain):
            yield event
        hash_value = _require_value(hash_drain, error)
        yield UpdateEvent.value(
            self.name, [HashEntry.create(drv_type, hash_type, hash_value)]
        )


class FlakeInputHashUpdater(HashEntryUpdater):
    """Base for hashes derived from flake inputs."""

    input_name: str | None = None
    drv_type: DrvType
    hash_type: HashType

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
    def _compute_hash(self, info: VersionInfo) -> EventStream:
        """Return async iterator that yields hash computation events."""

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
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
    go_package_expr: str | None = None

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_go_vendor_hash(
            self.name,
            self._input,
            pname=self.pname or self.name,
            version=info.version,
            subpackages=self.subpackages,
            proxy_vendor=self.proxy_vendor,
            go_package_expr=self.go_package_expr,
        )


class CargoVendorHashUpdater(FlakeInputHashUpdater):
    drv_type = "fetchCargoVendor"
    hash_type = "cargoHash"
    subdir: str | None = None

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_cargo_vendor_hash(self.name, self._input, subdir=self.subdir)


class NpmDepsHashUpdater(FlakeInputHashUpdater):
    drv_type = "fetchNpmDeps"
    hash_type = "npmDepsHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_npm_deps_hash(self.name, self._input)


class BunNodeModulesHashUpdater(FlakeInputHashUpdater):
    """Update bun-based node_modules hash for packages like opencode.

    Computes the hash by building the upstream flake input's package node_modules
    derivation with a fake hash and extracting the correct hash from the error.
    """

    drv_type = "bunBuild"
    hash_type = "nodeModulesHash"

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_bun_node_modules_hash(self.name, self._input)


class DenoDepsHashUpdater(FlakeInputHashUpdater):
    """Update Deno dependencies hash for FOD derivations like linear-cli.

    Computes platform-specific hashes since Deno deps can include platform-specific
    binaries (e.g., lefthook).
    """

    drv_type = "denoDeps"
    hash_type = "denoDepsHash"
    native_only: bool = False

    def _compute_hash(self, info: VersionInfo) -> EventStream:
        return compute_deno_deps_hash(
            self.name, self._input, native_only=self.native_only
        )

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
        """Override to handle multi-platform hash results."""
        hash_drain = ValueDrain()
        async for event in drain_value_events(self._compute_hash(info), hash_drain):
            yield event

        # compute_deno_deps_hash returns {platform: hash} dict
        platform_hashes = _require_value(hash_drain, f"Missing {self.hash_type} output")
        if not isinstance(platform_hashes, dict):
            raise TypeError(
                f"Expected dict of platform hashes, got {type(platform_hashes)}"
            )

        # Create HashEntry for each platform
        entries = [
            HashEntry.create(self.drv_type, self.hash_type, hash_val, platform=platform)
            for platform, hash_val in sorted(platform_hashes.items())
        ]
        yield UpdateEvent.value(self.name, entries)


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
    go_package_expr: str | None = None,
) -> type[GoVendorHashUpdater]:
    """Create and register a Go vendor hash updater."""
    attrs = {
        "name": name,
        "input_name": input_name,
        "pname": pname,
        "subpackages": subpackages,
        "proxy_vendor": proxy_vendor,
        "go_package_expr": go_package_expr,
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


def bun_node_modules_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[BunNodeModulesHashUpdater]:
    """Create and register a bun node_modules hash updater."""
    attrs = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (BunNodeModulesHashUpdater,), attrs)


def deno_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[DenoDepsHashUpdater]:
    """Create and register a Deno deps hash updater."""
    attrs = {"name": name, "input_name": input_name}
    return type(f"{name}Updater", (DenoDepsHashUpdater,), attrs)


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
# Handlers
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
    ) -> EventStream:
        url = github_raw_url(self.owner, self.repo, info.metadata["rev"], self.path)
        hashes_drain = ValueDrain()
        async for event in drain_value_events(
            compute_url_hashes(self.name, [url]), hashes_drain
        ):
            yield event
        hashes_by_url = _require_value(hashes_drain, "Missing hash output")
        hash_value = hashes_by_url[url]
        yield UpdateEvent.value(
            self.name, [HashEntry.create("fetchurl", "sha256", hash_value, url=url)]
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
    PLATFORMS = {
        "aarch64-darwin": "https://dl.google.com/chrome/mac/universal/stable/GGRO/googlechrome.dmg",
        "x86_64-linux": "https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        data = await fetch_json(
            session,
            "https://chromiumdash.appspot.com/fetch_releases?channel=Stable&platform=Mac&num=1",
        )
        return VersionInfo(version=data[0]["version"], metadata={})


class DataGripUpdater(ChecksumProvidedUpdater):
    """Update DataGrip to latest stable version."""

    name = "datagrip"

    API_URL = "https://data.services.jetbrains.com/products/releases?code=DG&latest=true&type=release"

    # nix platform -> JetBrains download key
    PLATFORMS = {
        "aarch64-darwin": "macM1",
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
        for nix_platform, jetbrains_key in self.PLATFORMS.items():
            checksum_url = release["downloads"][jetbrains_key]["checksumLink"]
            # Format: "hexhash *filename"
            payload = await fetch_url(session, checksum_url, timeout=DEFAULT_TIMEOUT)
            hex_hash = payload.decode().split()[0]
            checksums[nix_platform] = hex_hash
        return checksums

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        release = info.metadata["release"]
        urls = {
            nix_platform: release["downloads"][jetbrains_key]["link"]
            for nix_platform, jetbrains_key in self.PLATFORMS.items()
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

    # nix platform -> (os, arch)
    _PLATFORM_INFO: dict[str, tuple[str, str]] = {
        "aarch64-darwin": ("darwin", "arm64"),
        "x86_64-darwin": ("darwin", "x64"),
        "aarch64-linux": ("linux", "arm64"),
        "x86_64-linux": ("linux", "x64"),
    }
    PLATFORMS = {p: "" for p in _PLATFORM_INFO}  # Satisfy base class

    def _download_url(self, nix_platform: str, version: str) -> str:
        os_name, arch = self._PLATFORM_INFO[nix_platform]
        return f"{self.BASE_URL}/{version}/{os_name}/{arch}/droid"

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
        for nix_platform in self._PLATFORM_INFO:
            sha_url = f"{self._download_url(nix_platform, info.version)}.sha256"
            payload = await fetch_url(session, sha_url, timeout=DEFAULT_TIMEOUT)
            hex_hash = payload.decode().strip()
            checksums[nix_platform] = hex_hash
        return checksums

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {p: self._download_url(p, info.version) for p in self._PLATFORM_INFO}
        return self._build_result_with_urls(info, hashes, urls)


class ConductorUpdater(DownloadHashUpdater):
    """Update Conductor to latest version from CrabNebula CDN."""

    name = "conductor"
    BASE_URL = "https://cdn.crabnebula.app/download/melty/conductor/latest/platform"
    PLATFORMS = {"aarch64-darwin": "dmg-aarch64", "x86_64-darwin": "dmg-x86_64"}

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        url = f"{self.BASE_URL}/dmg-aarch64"
        _payload, headers = await _request(
            session, url, method="HEAD", timeout=DEFAULT_TIMEOUT
        )
        match = re.search(
            r"Conductor_([0-9.]+)_", headers.get("Content-Disposition", "")
        )
        if not match:
            raise RuntimeError("Could not parse version from Content-Disposition")
        return VersionInfo(version=match.group(1), metadata={})


class SculptorUpdater(DownloadHashUpdater):
    """Update Sculptor (uses Last-Modified header as version since no API exists)."""

    name = "sculptor"
    BASE_URL = "https://imbue-sculptor-releases.s3.us-west-2.amazonaws.com/sculptor"
    PLATFORMS = {
        "aarch64-darwin": "Sculptor.dmg",
        "x86_64-darwin": "Sculptor-x86_64.dmg",
        "x86_64-linux": "AppImage/x64/Sculptor.AppImage",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        url = f"{self.BASE_URL}/Sculptor.dmg"
        _payload, headers = await _request(
            session, url, method="HEAD", timeout=DEFAULT_TIMEOUT
        )
        last_modified = headers.get("Last-Modified", "")
        if not last_modified:
            raise RuntimeError("No Last-Modified header from Sculptor download")
        try:
            dt = datetime.strptime(last_modified, "%a, %d %b %Y %H:%M:%S %Z")
            version = dt.strftime("%Y-%m-%d")
        except ValueError:
            version = last_modified[:10]
        return VersionInfo(version=version, metadata={})


class PlatformAPIUpdater(ChecksumProvidedUpdater):
    """Base for updaters that fetch per-platform info from an API."""

    VERSION_KEY: str = "version"  # Key for version in API response
    CHECKSUM_KEY: str | None = None  # Key for checksum in API response (if provided)

    def _api_url(self, api_platform: str) -> str:
        """Return API URL for a platform. Override in subclass."""
        raise NotImplementedError

    def _download_url(self, api_platform: str, info: VersionInfo) -> str:
        """Return download URL for a platform. Override in subclass."""
        raise NotImplementedError

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        platform_info = {
            nix_plat: await fetch_json(session, self._api_url(api_plat))
            for nix_plat, api_plat in self.PLATFORMS.items()
        }
        versions = {p: info[self.VERSION_KEY] for p, info in platform_info.items()}
        version = _verify_platform_versions(versions, self.name)
        return VersionInfo(version=version, metadata={"platform_info": platform_info})

    async def fetch_checksums(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> dict[str, str]:
        if not self.CHECKSUM_KEY:
            raise NotImplementedError("No CHECKSUM_KEY defined")
        platform_info = info.metadata["platform_info"]
        return {p: platform_info[p][self.CHECKSUM_KEY] for p in self.PLATFORMS}

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {
            nix_plat: self._download_url(api_plat, info)
            for nix_plat, api_plat in self.PLATFORMS.items()
        }
        return self._build_result_with_urls(info, hashes, urls)


class VSCodeInsidersUpdater(PlatformAPIUpdater):
    """Update VS Code Insiders to latest version."""

    name = "vscode-insiders"
    PLATFORMS = VSCODE_PLATFORMS
    VERSION_KEY = "productVersion"
    CHECKSUM_KEY = "sha256hash"

    def _api_url(self, api_platform: str) -> str:
        return f"https://update.code.visualstudio.com/api/update/{api_platform}/insider/latest"

    def _download_url(self, api_platform: str, info: VersionInfo) -> str:
        return f"https://update.code.visualstudio.com/{info.version}/{api_platform}/insider"


class OpencodeDesktopCargoLockUpdater(HashEntryUpdater):
    """Update importCargoLock output hashes for opencode-desktop."""

    name = "opencode-desktop"
    input_name = "opencode"
    package_attr = "desktop"
    lockfile_path = "packages/desktop/src-tauri/Cargo.lock"
    git_deps = [
        CargoLockGitDep("specta-2.0.0-rc.22", "spectaOutputHash", "specta"),
        CargoLockGitDep("tauri-2.9.5", "tauriOutputHash", "tauri"),
        CargoLockGitDep(
            "tauri-specta-2.0.0-rc.21", "tauriSpectaOutputHash", "tauri-specta"
        ),
    ]

    @property
    def _input(self) -> str:
        assert self.input_name is not None
        return self.input_name

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        node = get_flake_input_node(self._input)
        version = get_flake_input_version(node)
        return VersionInfo(version=version, metadata={"node": node})

    async def fetch_hashes(
        self, info: VersionInfo, session: aiohttp.ClientSession
    ) -> EventStream:
        hash_drain = ValueDrain()
        async for event in drain_value_events(
            compute_import_cargo_lock_output_hashes(
                self.name,
                self._input,
                package_attr=self.package_attr,
                lockfile_path=self.lockfile_path,
                git_deps=self.git_deps,
            ),
            hash_drain,
        ):
            yield event
        hashes = _require_value(hash_drain, "Missing importCargoLock output hashes")
        entries = []
        for dep in self.git_deps:
            hash_value = hashes.get(dep.git_dep)
            if not hash_value:
                raise RuntimeError(f"Missing hash for {dep.git_dep}")
            entries.append(
                HashEntry.create(
                    "importCargoLock",
                    dep.hash_type,
                    hash_value,
                    git_dep=dep.git_dep,
                )
            )
        yield UpdateEvent.value(self.name, entries)


go_vendor_updater("axiom-cli", subpackages=["cmd/axiom"])
go_vendor_updater(
    "beads",
    subpackages=["cmd/bd"],
    proxy_vendor=True,
    go_package_expr=GO_LATEST_EXPR,
)
go_vendor_updater("crush")
go_vendor_updater("gogcli", subpackages=["cmd/gog"])
cargo_vendor_updater("codex", subdir="codex-rs")
npm_deps_updater("gemini-cli")
deno_deps_updater("linear-cli")
bun_node_modules_updater("opencode")


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
        data = await fetch_github_api(
            session, f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest"
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
    ) -> EventStream:
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

        yield UpdateEvent.value(
            self.name,
            [
                HashEntry.create("fetchFromGitHub", "srcHash", src_hash),
                HashEntry.create("fetchCargoVendor", "cargoHash", cargo_hash),
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
    PLATFORMS = {
        "aarch64-darwin": "darwin-arm64",
        "x86_64-darwin": "darwin-x64",
        "aarch64-linux": "linux-arm64",
        "x86_64-linux": "linux-x64",
    }

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        platform_info = {
            nix_plat: await fetch_json(
                session, f"{self.API_BASE}?platform={api_plat}&releaseTrack=stable"
            )
            for nix_plat, api_plat in self.PLATFORMS.items()
        }
        versions = {p: info["version"] for p, info in platform_info.items()}
        commits = {p: info["commitSha"] for p, info in platform_info.items()}
        version = _verify_platform_versions(versions, "Cursor")
        commit = _verify_platform_versions(commits, "Cursor commit")
        return VersionInfo(
            version=version,
            metadata={"commit": commit, "platform_info": platform_info},
        )

    def get_download_url(self, platform: str, info: VersionInfo) -> str:
        return info.metadata["platform_info"][platform]["downloadUrl"]

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        urls = {p: self.get_download_url(p, info) for p in self.PLATFORMS}
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
    """
    # 1. Releases API (paginated, newest first)
    try:
        releases = await fetch_github_api(
            session, f"repos/{owner}/{repo}/releases", per_page="20"
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
        tags = await fetch_github_api(
            session, f"repos/{owner}/{repo}/tags", per_page="30"
        )
        for tag_info in tags:
            if (tag := tag_info.get("name", "")).startswith(prefix):
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
) -> EventStream:
    """Update a flake input ref using flake-edit and lock the input.

    Raises RuntimeError if either flake-edit or nix flake lock fails.
    """
    yield UpdateEvent.status(source, f"Updating ref: {input_ref.ref} -> {new_ref}")

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
    put = queue.put  # Local reference for brevity
    try:
        await put(
            UpdateEvent.status(
                source,
                f"Checking {input_ref.owner}/{input_ref.repo} (current: {input_ref.ref})",
            )
        )
        result = await check_flake_ref_update(input_ref, session)

        if result.error:
            await put(UpdateEvent.error(source, result.error))
            return

        if result.latest_ref == result.current_ref:
            await put(
                UpdateEvent.status(source, f"Up to date (ref: {result.current_ref})")
            )
            await put(UpdateEvent.result(source))
            return

        update_payload = {"current": result.current_ref, "latest": result.latest_ref}
        if dry_run:
            await put(
                UpdateEvent.status(
                    source,
                    f"Update available: {result.current_ref} -> {result.latest_ref}",
                )
            )
            await put(UpdateEvent.result(source, update_payload))
            return

        # Actually update the ref — serialize file mutations
        latest_ref = result.latest_ref
        assert latest_ref is not None

        async def do_update() -> None:
            async for event in update_flake_ref(input_ref, latest_ref, source=source):
                await put(event)

        if flake_edit_lock:
            async with flake_edit_lock:
                await do_update()
        else:
            await do_update()

        await put(UpdateEvent.result(source, update_payload))
    except Exception as exc:
        await put(UpdateEvent.error(source, str(exc)))


# =============================================================================
# Rendering
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
        self, message: str, *, style: str | None = None, stderr: bool = False
    ) -> None:
        """Print message unless in quiet or json mode."""
        if not self.quiet and not self.json_output:
            console = self._err_console if stderr else self._console
            console.print(message, style=style)

    def print_error(self, message: str) -> None:
        """Print error message (always shown unless json mode)."""
        if not self.json_output:
            self._err_console.print(message, style="red")


class OperationKind(StrEnum):
    CHECK_VERSION = "check_version"
    UPDATE_REF = "update_ref"
    REFRESH_LOCK = "refresh_lock"
    COMPUTE_HASH = "compute_hash"


_ORIGIN_FLAKE_ONLY = "(flake.nix)"
_ORIGIN_SOURCES_ONLY = "(sources.json)"
_ORIGIN_BOTH = "(flake.nix + sources.json)"

_OPERATION_LABELS: dict[OperationKind, str] = {
    OperationKind.CHECK_VERSION: "Checking version",
    OperationKind.UPDATE_REF: "Updating ref",
    OperationKind.REFRESH_LOCK: "Refreshing lock",
    OperationKind.COMPUTE_HASH: "Computing hash",
}


@dataclass
class OperationState:
    kind: OperationKind
    label: str
    status: str = "pending"
    message: str | None = None
    tail: deque[str] = field(default_factory=deque)
    detail_lines: list[str] = field(default_factory=list)
    active_commands: int = 0
    spinner: Any | None = field(default=None, repr=False)

    def visible(self) -> bool:
        return (
            self.status != "pending"
            or self.message is not None
            or bool(self.detail_lines)
            or self.active_commands > 0
        )


@dataclass(frozen=True)
class ItemMeta:
    name: str
    origin: str
    op_order: tuple[OperationKind, ...]


@dataclass
class ItemState:
    name: str
    origin: str
    op_order: tuple[OperationKind, ...]
    operations: dict[OperationKind, OperationState]
    last_operation: OperationKind | None = None
    active_command_op: OperationKind | None = None

    @classmethod
    def from_meta(cls, meta: ItemMeta, *, max_lines: int) -> "ItemState":
        operations = {
            kind: OperationState(
                kind=kind,
                label=_OPERATION_LABELS[kind],
                tail=deque(maxlen=max_lines),
            )
            for kind in meta.op_order
        }
        return cls(
            name=meta.name,
            origin=meta.origin,
            op_order=meta.op_order,
            operations=operations,
        )


def _is_tty() -> bool:
    term = os.environ.get("TERM", "")
    return sys.stdout.isatty() and term.lower() not in {"", "dumb"}


_STATUS_UPDATE_AVAILABLE = re.compile(r"Update available: (.+) -> (.+)")
_STATUS_UP_TO_DATE_VERSION = re.compile(r"Up to date \(version: (.+)\)")
_STATUS_UP_TO_DATE_REF = re.compile(r"Up to date \(ref: (.+)\)")
_STATUS_LATEST_VERSION = re.compile(r"Latest version: (.+)")
_STATUS_CHECKING = re.compile(r"Checking .*\(current: (.+)\)")
_STATUS_UPDATE_REF = re.compile(r"Updating ref: (.+) -> (.+)")
_STATUS_UPDATE_INPUT = re.compile(r"Updating flake input '([^']+)'\.*")
_STATUS_COMPUTING_HASH = re.compile(r"Computing hash for (.+)\.")

_OP_STATUS_PRIORITY = {
    "pending": 0,
    "running": 1,
    "no_change": 2,
    "success": 3,
    "error": 4,
}


def _operation_for_status(message: str) -> OperationKind | None:
    lowered = message.lower()
    if lowered.startswith(
        (
            "checking ",
            "fetching latest",
            "latest version",
            "update available",
            "up to date (version",
            "up to date (ref",
        )
    ):
        return OperationKind.CHECK_VERSION
    if lowered.startswith("updating ref"):
        return OperationKind.UPDATE_REF
    if lowered.startswith("updating flake input"):
        return OperationKind.REFRESH_LOCK
    if (
        lowered.startswith(
            (
                "fetching hashes",
                "computing hash",
                "build failed",
                "warning:",
            )
        )
        or message == "Up to date"
    ):
        return OperationKind.COMPUTE_HASH
    return None


def _operation_for_command(args: list[str] | None) -> OperationKind:
    if not args:
        return OperationKind.COMPUTE_HASH
    if args[0] == "flake-edit":
        return OperationKind.UPDATE_REF
    if args[:3] == ["nix", "flake", "lock"] and "--update-input" in args:
        return OperationKind.REFRESH_LOCK
    return OperationKind.COMPUTE_HASH


def _set_operation_status(
    operation: OperationState,
    status: str,
    *,
    message: str | None = None,
    clear_message: bool = False,
) -> None:
    if _OP_STATUS_PRIORITY[status] < _OP_STATUS_PRIORITY[operation.status]:
        return
    operation.status = status
    if status != "running":
        operation.spinner = None
    if clear_message:
        operation.message = None
    elif message is not None:
        operation.message = message


def _apply_status(item: ItemState, message: str) -> None:
    kind = _operation_for_status(message)
    if kind is None:
        return
    operation = item.operations.get(kind)
    if operation is None:
        return
    item.last_operation = kind

    if kind == OperationKind.CHECK_VERSION:
        if match := _STATUS_UPDATE_AVAILABLE.match(message):
            _set_operation_status(
                operation,
                "success",
                message=f"{match.group(1)} → {match.group(2)}",
            )
            return
        if match := _STATUS_UP_TO_DATE_VERSION.match(message):
            _set_operation_status(
                operation,
                "no_change",
                message=f"{match.group(1)} (up to date)",
            )
            return
        if match := _STATUS_UP_TO_DATE_REF.match(message):
            _set_operation_status(
                operation,
                "no_change",
                message=f"{match.group(1)} (up to date)",
            )
            return
        if match := _STATUS_LATEST_VERSION.match(message):
            _set_operation_status(operation, "running", message=match.group(1))
            return
        if match := _STATUS_CHECKING.match(message):
            _set_operation_status(
                operation,
                "running",
                message=f"current {match.group(1)}",
            )
            return
        _set_operation_status(operation, "running")
        return

    if kind == OperationKind.UPDATE_REF:
        if match := _STATUS_UPDATE_REF.match(message):
            _set_operation_status(
                operation,
                "running",
                message=f"{match.group(1)} → {match.group(2)}",
            )
            return
        _set_operation_status(operation, "running")
        return

    if kind == OperationKind.REFRESH_LOCK:
        if match := _STATUS_UPDATE_INPUT.match(message):
            _set_operation_status(operation, "running", message=match.group(1))
            return
        _set_operation_status(operation, "running")
        return

    if kind == OperationKind.COMPUTE_HASH:
        if message == "Up to date":
            _set_operation_status(operation, "no_change", clear_message=True)
            return
        if message.startswith("Fetching hashes"):
            _set_operation_status(operation, "running", message="all platforms")
            return
        if match := _STATUS_COMPUTING_HASH.match(message):
            _set_operation_status(operation, "running", message=match.group(1))
            return
        _set_operation_status(operation, "running", message=message)


def _hash_label_map(entry: SourceEntry | None) -> dict[str, str]:
    if entry is None:
        return {}
    hashes = entry.hashes
    if hashes.mapping:
        return dict(hashes.mapping)
    mapping: dict[str, str] = {}
    if hashes.entries:
        for idx, item in enumerate(hashes.entries, start=1):
            if item.git_dep:
                key = f"{item.hash_type}:{item.git_dep}"
            elif item.platform:
                key = item.platform
            else:
                key = item.hash_type
            if key in mapping:
                key = f"{key}#{idx}"
            mapping[key] = item.hash
    return mapping


def _hash_diff_lines(
    old_entry: SourceEntry | None, new_entry: SourceEntry | None
) -> list[str]:
    old_map = _hash_label_map(old_entry)
    new_map = _hash_label_map(new_entry)
    labels = sorted(set(old_map) | set(new_map))
    lines: list[str] = []
    for label in labels:
        old_hash = old_map.get(label)
        new_hash = new_map.get(label)
        if old_hash == new_hash:
            continue
        if old_hash is None:
            lines.append(f"{label} :: <none> → {new_hash}")
        elif new_hash is None:
            lines.append(f"{label} :: {old_hash} → <removed>")
        else:
            lines.append(f"{label} :: {old_hash} → {new_hash}")
    return lines


class Renderer:
    """Rich-based live renderer for update progress.

    Uses Rich's Live display with SIGWINCH handling for clean resize behavior.
    """

    def __init__(
        self,
        items: dict[str, ItemState],
        order: list[str],
        *,
        is_tty: bool,
        full_output: bool = False,
        panel_height: int | None = None,
        quiet: bool = False,
    ) -> None:
        from rich.console import Console
        from rich.live import Live
        from rich.text import Text

        self.items = items
        self.order = order
        self.is_tty = is_tty
        self.full_output = full_output
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

    def _format_operation_text(self, operation: OperationState) -> str:
        text = f"{operation.label}..."
        message = operation.message
        if operation.status == "success" and not message:
            message = "done"
        elif operation.status == "no_change" and not message:
            message = "no change"
        elif operation.status == "error" and not message:
            message = "failed"
        if message:
            return f"{text} {message}"
        return text

    def _render_operation(self, operation: OperationState) -> Any:
        from rich.spinner import Spinner
        from rich.text import Text

        text = self._format_operation_text(operation)
        if operation.status == "running":
            if operation.spinner is None:
                operation.spinner = Spinner("dots", text, style="cyan")
            else:
                operation.spinner.text = text
            return operation.spinner

        operation.spinner = None

        symbol = "•"
        style = None
        if operation.status == "success":
            symbol = "✓"
            style = "green"
        elif operation.status == "no_change":
            symbol = "•"
            style = "yellow"
        elif operation.status == "error":
            symbol = "✗"
            style = "red"

        line = Text()
        line.append(symbol, style=style)
        line.append(" ")
        line.append(text, style=style)
        return line

    def _build_display(self, *, full_output: bool | None = None) -> Any:
        """Build the Rich renderable for current state."""
        from rich.console import Group
        from rich.text import Text
        from rich.tree import Tree

        if not self._console:
            return Text("")

        width = self._console.width
        height = self._console.height
        # Calculate max_visible dynamically to handle terminal resize
        panel_height = self._initial_panel_height or max(1, height - 1)
        max_visible = min(panel_height, height - 1)
        if full_output is None:
            full_output = self.full_output

        trees: list[Any] = []
        for name in self.order:
            item = self.items[name]
            header = Text()
            header.append(name, style="bold")
            header.append(" ")
            header.append(item.origin, style="dim")
            tree = Tree(header, guide_style="dim")

            operations = [
                item.operations[kind]
                for kind in item.op_order
                if item.operations[kind].visible()
            ]
            for operation in operations:
                op_node = tree.add(self._render_operation(operation))
                for detail in operation.detail_lines:
                    op_node.add(Text(detail))
                if operation.active_commands > 0:
                    for tail_line in operation.tail:
                        op_node.add(Text(f"> {tail_line}", style="dim"))

            trees.append(tree)

        renderable: Any = Group(*trees) if trees else Text("")
        if full_output:
            return renderable

        options = self._console.options.update(width=width, height=max_visible)
        rendered_lines = self._console.render_lines(renderable, options=options)
        lines: list[Text] = []
        for line in rendered_lines[:max_visible]:
            text = Text()
            for segment in line:
                if segment.text:
                    text.append(segment.text, style=segment.style)
            text.truncate(width - 1)
            lines.append(text)

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

        console = Console()
        console.print(self._build_display(full_output=True))

    def render(self) -> None:
        """Update the live display with current state."""
        if not self._live:
            return
        self._live.update(self._build_display(), refresh=True)


# =============================================================================
# Orchestration + CLI
# =============================================================================


async def _consume_events(
    queue: asyncio.Queue[UpdateEvent | None],
    order: list[str],
    sources: SourcesFile,
    *,
    item_meta: dict[str, ItemMeta],
    max_lines: int,
    is_tty: bool,
    quiet: bool = False,
) -> tuple[bool, int, dict[str, str]]:
    items = {
        name: ItemState.from_meta(item_meta[name], max_lines=max_lines)
        for name in order
        if name in item_meta
    }
    updated = False
    errors = 0
    update_details: dict[str, str] = {}  # name -> "updated" | "error" | "no_change"
    detail_priority = {"no_change": 0, "updated": 1, "error": 2}

    def set_detail(name: str, status: str) -> None:
        nonlocal updated
        current = update_details.get(name)
        if current is None or detail_priority[status] > detail_priority[current]:
            update_details[name] = status
        if status == "updated":
            updated = True

    renderer = Renderer(
        items,
        order,
        is_tty=is_tty,
        full_output=_resolve_full_output(),
        quiet=quiet,
    )

    while True:
        event = await queue.get()
        if event is None:
            break
        item = items.get(event.source)
        if item is None:
            continue

        match event.kind:
            case UpdateEventKind.STATUS:
                if event.message:
                    _apply_status(item, event.message)
                    if _is_terminal_status(event.message):
                        renderer.log(event.source, event.message)

            case UpdateEventKind.COMMAND_START:
                args = event.payload if isinstance(event.payload, list) else None
                op_kind = _operation_for_command(args)
                operation = item.operations.get(op_kind)
                if operation:
                    item.last_operation = op_kind
                    item.active_command_op = op_kind
                    operation.status = "running"
                    operation.active_commands += 1
                    if operation.active_commands == 1:
                        operation.tail.clear()

            case UpdateEventKind.LINE:
                label = event.stream or "stdout"
                message = event.message or ""
                line_text = f"[{label}] {message}" if label else message
                op_kind = item.active_command_op or item.last_operation
                if op_kind is None:
                    op_kind = OperationKind.COMPUTE_HASH
                operation = item.operations.get(op_kind)
                if operation and operation.active_commands > 0:
                    if not operation.tail or operation.tail[-1] != line_text:
                        operation.tail.append(line_text)

            case UpdateEventKind.COMMAND_END:
                result = event.payload
                if isinstance(result, CommandResult):
                    op_kind = _operation_for_command(result.args)
                    operation = item.operations.get(op_kind)
                    if operation:
                        operation.active_commands = max(
                            0, operation.active_commands - 1
                        )
                        if operation.active_commands == 0:
                            operation.tail.clear()
                            if item.active_command_op == op_kind:
                                item.active_command_op = None
                        if result.returncode != 0 and not result.allow_failure:
                            operation.status = "error"
                        elif (
                            op_kind
                            in (
                                OperationKind.UPDATE_REF,
                                OperationKind.REFRESH_LOCK,
                            )
                            and operation.status != "error"
                        ):
                            operation.status = "success"

            case UpdateEventKind.RESULT:
                result = event.payload
                if result is not None:
                    if isinstance(result, dict):
                        # Ref update result: {"current": ..., "latest": ...}
                        set_detail(event.source, "updated")
                        current_ref = result.get("current", "?")
                        latest_ref = result.get("latest", "?")
                        check_op = item.operations.get(OperationKind.CHECK_VERSION)
                        if check_op:
                            check_op.status = "success"
                            check_op.message = f"{current_ref} → {latest_ref}"
                            item.last_operation = OperationKind.CHECK_VERSION
                        for op_kind in (
                            OperationKind.UPDATE_REF,
                            OperationKind.REFRESH_LOCK,
                        ):
                            op = item.operations.get(op_kind)
                            if op and op.status == "running":
                                op.status = "success"
                        renderer.log(
                            event.source,
                            f"Updated: {current_ref} -> {latest_ref}",
                        )
                    elif isinstance(result, SourceEntry):
                        old_entry = sources.entries.get(event.source)
                        old_version = old_entry.version if old_entry else None
                        new_version = result.version
                        sources.entries[event.source] = result
                        set_detail(event.source, "updated")

                        check_op = item.operations.get(OperationKind.CHECK_VERSION)
                        if check_op:
                            if (
                                old_version
                                and new_version
                                and old_version != new_version
                            ):
                                check_op.status = "success"
                                check_op.message = f"{old_version} → {new_version}"
                            elif new_version:
                                check_op.status = "success"
                                if check_op.message is None:
                                    check_op.message = new_version

                        hash_op = item.operations.get(OperationKind.COMPUTE_HASH)
                        if hash_op:
                            hash_op.status = "success"
                            hash_op.detail_lines = _hash_diff_lines(old_entry, result)
                            hash_op.message = None
                        if old_version and new_version and old_version != new_version:
                            renderer.log(
                                event.source,
                                f"Updated: {old_version} -> {new_version}",
                            )
                        else:
                            old_hash = (
                                old_entry.hashes.primary_hash() if old_entry else None
                            )
                            new_hash = result.hashes.primary_hash()
                            if old_hash and new_hash and old_hash != new_hash:
                                renderer.log(
                                    event.source,
                                    f"Updated: hash {old_hash} -> {new_hash}",
                                )
                            else:
                                renderer.log(event.source, "Updated")
                    else:
                        set_detail(event.source, "updated")
                else:
                    set_detail(event.source, "no_change")
                    check_op = item.operations.get(OperationKind.CHECK_VERSION)
                    if check_op and check_op.status == "pending":
                        check_op.status = "no_change"

            case UpdateEventKind.ERROR:
                errors += 1
                set_detail(event.source, "error")
                message = event.message or "Unknown error"
                error_op: OperationState | None = None
                if item.active_command_op:
                    error_op = item.operations.get(item.active_command_op)
                if error_op is None and item.last_operation:
                    error_op = item.operations.get(item.last_operation)
                if error_op:
                    error_op.status = "error"
                    error_op.message = message
                    error_op.active_commands = 0
                    error_op.tail.clear()
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
    native_only: bool,
    session: aiohttp.ClientSession,
    update_input_lock: asyncio.Lock,
    queue: asyncio.Queue[UpdateEvent | None],
) -> None:
    current = sources.entries.get(name)
    updater = UPDATERS[name]()
    if isinstance(updater, DenoDepsHashUpdater):
        updater.native_only = native_only
    input_name = getattr(updater, "input_name", None)
    put = queue.put

    try:
        await put(UpdateEvent.status(name, "Starting update"))
        if update_input and input_name:
            await put(
                UpdateEvent.status(name, f"Updating flake input '{input_name}'...")
            )
            async with update_input_lock:
                async for event in update_flake_input(input_name, source=name):
                    await put(event)

        async for event in updater.update_stream(current, session):
            await put(event)
    except Exception as exc:
        await put(UpdateEvent.error(name, str(exc)))


_SUMMARY_STATUS_PRIORITY = {"no_change": 0, "updated": 1, "error": 2}


@dataclass
class UpdateSummary:
    """Summary of update run for JSON output."""

    updated: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    no_change: list[str] = field(default_factory=list)
    _status_by_name: dict[str, str] = field(default_factory=dict, repr=False)
    _order: list[str] = field(default_factory=list, repr=False)

    def _set_status(self, name: str, status: str) -> None:
        normalized = status if status in _SUMMARY_STATUS_PRIORITY else "no_change"
        if name not in self._status_by_name:
            self._order.append(name)
            self._status_by_name[name] = normalized
            return
        current = self._status_by_name[name]
        if _SUMMARY_STATUS_PRIORITY[normalized] > _SUMMARY_STATUS_PRIORITY[current]:
            self._status_by_name[name] = normalized

    def _rebuild_lists(self) -> None:
        self.updated = []
        self.errors = []
        self.no_change = []
        for name in self._order:
            status = self._status_by_name[name]
            if status == "updated":
                self.updated.append(name)
            elif status == "error":
                self.errors.append(name)
            else:
                self.no_change.append(name)

    def to_dict(self) -> dict[str, Any]:
        return {
            "updated": self.updated,
            "errors": self.errors,
            "noChange": self.no_change,
            "success": len(self.errors) == 0,
        }

    def accumulate(self, details: dict[str, str]) -> None:
        """Accumulate phase details into this summary."""
        for name, detail in details.items():
            self._set_status(name, detail)
        self._rebuild_lists()


async def _run_phase(
    task_coros: list[asyncio.Task[None]],
    names: list[str],
    sources: SourcesFile,
    queue: asyncio.Queue[UpdateEvent | None],
    *,
    quiet: bool,
    json_output: bool,
    item_meta: dict[str, ItemMeta] | None = None,
) -> tuple[bool, int, dict[str, str]]:
    """Run update tasks with shared event consumption.

    Returns (updated, error_count, details_dict).
    """
    max_lines = _resolve_log_tail_lines(None)
    is_tty = _is_tty() and not quiet and not json_output
    if item_meta is None:
        item_meta = {
            name: ItemMeta(
                name=name,
                origin=_ORIGIN_SOURCES_ONLY,
                op_order=(
                    OperationKind.CHECK_VERSION,
                    OperationKind.COMPUTE_HASH,
                ),
            )
            for name in names
        }
    consumer = asyncio.create_task(
        _consume_events(
            queue,
            names,
            sources,
            item_meta=item_meta,
            max_lines=max_lines,
            is_tty=is_tty,
            quiet=quiet or json_output,
        )
    )
    await asyncio.gather(*task_coros)
    await queue.put(None)
    return await consumer


@dataclass(frozen=True)
class ResolvedTargets:
    all_source_names: set[str]
    all_ref_inputs: list[FlakeInputRef]
    all_ref_names: set[str]
    all_known_names: set[str]
    do_refs: bool
    do_sources: bool
    do_input_refresh: bool
    dry_run: bool
    native_only: bool
    ref_inputs: list[FlakeInputRef]
    source_names: list[str]

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> "ResolvedTargets":
        all_source_names = set(UPDATERS.keys())
        all_ref_inputs = get_flake_inputs_with_refs()
        all_ref_names = {i.name for i in all_ref_inputs}
        all_known_names = all_source_names | all_ref_names

        do_refs = not args.no_refs
        do_sources = not args.no_sources
        if args.source:
            if args.source not in all_ref_names:
                do_refs = False
            if args.source not in all_source_names:
                do_sources = False

        ref_inputs = (
            [i for i in all_ref_inputs if i.name == args.source]
            if args.source
            else all_ref_inputs
        )
        source_names = (
            [args.source]
            if args.source in all_source_names
            else []
            if args.source
            else list(UPDATERS.keys())
        )
        if not do_refs:
            ref_inputs = []
        if not do_sources:
            source_names = []

        return cls(
            all_source_names=all_source_names,
            all_ref_inputs=all_ref_inputs,
            all_ref_names=all_ref_names,
            all_known_names=all_known_names,
            do_refs=do_refs,
            do_sources=do_sources,
            do_input_refresh=not args.no_input,
            dry_run=args.check,
            native_only=args.native_only,
            ref_inputs=ref_inputs,
            source_names=source_names,
        )


def _build_item_meta(
    resolved: ResolvedTargets, sources: SourcesFile | None
) -> tuple[dict[str, ItemMeta], list[str]]:
    flake_names = (
        {inp.name for inp in resolved.ref_inputs} if resolved.do_refs else set()
    )
    source_names = set(resolved.source_names) if resolved.do_sources else set()
    sources_with_input: set[str] = set()
    if sources is not None:
        sources_with_input = {
            name for name, entry in sources.entries.items() if entry.input
        }
    sources_with_input &= source_names

    both = flake_names & sources_with_input
    flake_only = flake_names - both
    sources_only = source_names - both

    item_meta: dict[str, ItemMeta] = {}
    for name in both:
        item_meta[name] = ItemMeta(
            name=name,
            origin=_ORIGIN_BOTH,
            op_order=(
                OperationKind.CHECK_VERSION,
                OperationKind.UPDATE_REF,
                OperationKind.REFRESH_LOCK,
                OperationKind.COMPUTE_HASH,
            ),
        )
    for name in flake_only:
        item_meta[name] = ItemMeta(
            name=name,
            origin=_ORIGIN_FLAKE_ONLY,
            op_order=(
                OperationKind.CHECK_VERSION,
                OperationKind.UPDATE_REF,
                OperationKind.REFRESH_LOCK,
            ),
        )
    for name in sources_only:
        if name in sources_with_input:
            op_order = (
                OperationKind.CHECK_VERSION,
                OperationKind.REFRESH_LOCK,
                OperationKind.COMPUTE_HASH,
            )
        else:
            op_order = (
                OperationKind.CHECK_VERSION,
                OperationKind.COMPUTE_HASH,
            )
        item_meta[name] = ItemMeta(
            name=name,
            origin=_ORIGIN_SOURCES_ONLY,
            op_order=op_order,
        )

    order = sorted(item_meta, key=lambda name: f"{item_meta[name].origin} {name}")
    return item_meta, order


async def _run_phase_tasks(
    names: list[str],
    sources: SourcesFile,
    *,
    quiet: bool,
    json_output: bool,
    build_tasks: Callable[
        [aiohttp.ClientSession, asyncio.Queue[UpdateEvent | None]],
        list[asyncio.Task[None]],
    ],
) -> tuple[bool, int, dict[str, str]]:
    async with aiohttp.ClientSession() as session:
        queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
        tasks = build_tasks(session, queue)
        return await _run_phase(
            tasks,
            names,
            sources,
            queue,
            quiet=quiet,
            json_output=json_output,
        )


def _emit_summary(
    args: argparse.Namespace,
    summary: UpdateSummary,
    *,
    had_errors: bool,
    out: OutputOptions,
    dry_run: bool,
) -> int:
    if args.json:
        print(json.dumps(summary.to_dict()))
        return 1 if had_errors else 0

    if dry_run:
        if summary.updated:
            out.print(
                f"\nAvailable updates: {', '.join(summary.updated)}", style="green"
            )
        else:
            out.print("\nNo updates available.", style="dim")
    else:
        if summary.updated:
            out.print(
                f"\n:heavy_check_mark: Updated: {', '.join(summary.updated)}",
                style="green",
            )
        else:
            out.print("\nNo updates needed.", style="dim")

    if summary.errors:
        out.print_error(f"\nFailed: {', '.join(summary.errors)}")

    if args.continue_on_error and summary.updated and had_errors:
        out.print(
            f"\n:warning: {len(summary.errors)} item(s) failed but continuing.",
            style="yellow",
        )
        return 0

    return 1 if had_errors else 0


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
            return 0

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
            sources = SourcesFile.load(SOURCES_FILE)
            if args.json:
                print(json.dumps({"valid": True, "count": len(sources.entries)}))
            else:
                out.print(
                    f":heavy_check_mark: Validated {SOURCES_FILE}: "
                    f"{len(sources.entries)} sources OK",
                    style="green",
                )
            return 0
        except Exception as exc:
            if args.json:
                print(json.dumps({"valid": False, "error": str(exc)}))
            else:
                out.print_error(f":x: Validation failed: {exc}")
            return 1

    resolved = ResolvedTargets.from_args(args)
    show_phase_headers = (
        not args.json
        and not args.quiet
        and not _is_tty()
        and resolved.do_refs
        and resolved.do_sources
        and resolved.ref_inputs
        and resolved.source_names
    )

    # Validate source name if specified
    if args.source and args.source not in resolved.all_known_names:
        out.print_error(f"Error: Unknown source or input '{args.source}'")
        out.print_error(f"Available: {', '.join(sorted(resolved.all_known_names))}")
        return 1

    # Combined summary across both phases
    summary = UpdateSummary()
    had_errors = False

    if not resolved.ref_inputs and not resolved.source_names:
        return _emit_summary(
            args, summary, had_errors=False, out=out, dry_run=resolved.dry_run
        )

    sources = (
        SourcesFile.load(SOURCES_FILE)
        if resolved.do_sources and resolved.source_names
        else SourcesFile(entries={})
    )
    item_meta, order = _build_item_meta(
        resolved, sources if resolved.do_sources else None
    )

    if not order:
        return _emit_summary(
            args, summary, had_errors=False, out=out, dry_run=resolved.dry_run
        )

    queue: asyncio.Queue[UpdateEvent | None] = asyncio.Queue()
    max_lines = _resolve_log_tail_lines(None)
    is_tty = _is_tty() and not args.quiet and not args.json
    consumer = asyncio.create_task(
        _consume_events(
            queue,
            order,
            sources,
            item_meta=item_meta,
            max_lines=max_lines,
            is_tty=is_tty,
            quiet=args.quiet or args.json,
        )
    )

    # ── Phase 1: Ref updates ──────────────────────────────────────────
    if resolved.do_refs and resolved.ref_inputs:
        if show_phase_headers:
            out.print("\nPhase 1: flake input refs", style="dim")
        async with aiohttp.ClientSession() as session:
            flake_edit_lock = asyncio.Lock()
            tasks = [
                asyncio.create_task(
                    _update_refs_task(
                        inp,
                        session,
                        queue,
                        dry_run=resolved.dry_run,
                        flake_edit_lock=flake_edit_lock,
                    )
                )
                for inp in resolved.ref_inputs
            ]
            await asyncio.gather(*tasks)

    # ── Phase 2: Source hash updates ──────────────────────────────────
    if resolved.do_sources and resolved.source_names:
        if show_phase_headers:
            out.print("\nPhase 2: sources.json updates", style="dim")
        async with aiohttp.ClientSession() as session:
            update_input_lock = asyncio.Lock()
            tasks = [
                asyncio.create_task(
                    _update_source_task(
                        name,
                        sources,
                        update_input=resolved.do_input_refresh,
                        native_only=resolved.native_only,
                        session=session,
                        update_input_lock=update_input_lock,
                        queue=queue,
                    )
                )
                for name in resolved.source_names
            ]
            await asyncio.gather(*tasks)

    await queue.put(None)
    _, error_count, details = await consumer
    summary.accumulate(details)
    had_errors = error_count > 0

    if resolved.do_sources and resolved.source_names:
        if any(details.get(name) == "updated" for name in resolved.source_names):
            sources.save(SOURCES_FILE)

    return _emit_summary(
        args, summary, had_errors=had_errors, out=out, dry_run=resolved.dry_run
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Update source versions/hashes and flake input refs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f"Available sources: {', '.join(UPDATERS.keys())}",
    )
    parser.add_argument(
        "source", nargs="?", help="Source or flake input to update (default: all)"
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
    parser.add_argument(
        "-n",
        "--native-only",
        action="store_true",
        help="Only compute platform-specific hashes for current platform (for CI)",
    )
    args = parser.parse_args()

    # Early exit modes that don't need tool checks
    if args.list or args.schema or args.validate:
        raise SystemExit(asyncio.run(_run_updates(args)))

    # Check for required tools before running
    # flake-edit only needed if we're updating refs (not --no-refs)
    # If a specific source is given, check if it's a ref input
    needs_flake_edit = not args.no_refs
    if needs_flake_edit and args.source:
        # Only need flake-edit if the source is a ref input
        ref_names = {i.name for i in get_flake_inputs_with_refs()}
        needs_flake_edit = args.source in ref_names

    missing = _check_required_tools(include_flake_edit=needs_flake_edit)
    if missing:
        sys.stderr.write(f"Error: Required tools not found: {', '.join(missing)}\n")
        sys.stderr.write("Please install them and ensure they are in your PATH.\n")
        raise SystemExit(1)

    raise SystemExit(asyncio.run(_run_updates(args)))


if __name__ == "__main__":
    main()
