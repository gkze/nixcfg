"""Runtime configuration for update tools.

Replaces global variables with explicit configuration objects,
enabling easier testing and avoiding hidden dependencies.
"""

from __future__ import annotations

import functools
import os
import platform
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def get_repo_root() -> Path:
    """Get the repository root directory.

    Handles both normal execution and nix store paths.
    """
    # Check if we're running from nix store
    current_file = Path(__file__)
    if "/nix/store" in str(current_file):
        return Path.cwd()
    # Normal case: lib/ is in repo root
    return current_file.parent.parent


@dataclass(frozen=True)
class Paths:
    """File paths used by update tools."""

    root: Path = field(default_factory=get_repo_root)

    @property
    def sources_file(self) -> Path:
        return self.root / "sources.json"

    @property
    def flake_lock(self) -> Path:
        return self.root / "flake.lock"

    @property
    def flake_nix(self) -> Path:
        return self.root / "flake.nix"


@dataclass(frozen=True)
class Timeouts:
    """Timeout configuration."""

    http_request: float = 30.0
    subprocess: float = 600.0  # 10 minutes for nix builds
    nix_eval: float = 600.0  # Large configs can take 5+ minutes


@dataclass(frozen=True)
class Limits:
    """Resource limits."""

    max_derivations_per_batch: int = 500  # Avoid ARG_MAX
    log_tail_lines: int = 10
    max_retries: int = 3
    retry_backoff: float = 1.0


@dataclass
class RuntimeConfig:
    """Runtime configuration that may change during execution.

    This replaces module-level globals like VERBOSE and _NATIVE_ONLY.
    Use the context variable to access the current config.
    """

    verbose: bool = False
    native_only: bool = False
    dry_run: bool = False
    quiet: bool = False
    json_output: bool = False

    # Paths and limits (usually constant but overridable for testing)
    paths: Paths = field(default_factory=Paths)
    timeouts: Timeouts = field(default_factory=Timeouts)
    limits: Limits = field(default_factory=Limits)

    def log_tail_lines(self) -> int:
        """Get log tail lines from env or default."""
        env_val = os.environ.get("UPDATE_LOG_TAIL_LINES")
        if env_val:
            try:
                return max(1, int(env_val))
            except ValueError:
                pass
        return self.limits.log_tail_lines


# Context variable for current runtime config
_config_var: ContextVar[RuntimeConfig] = ContextVar("config", default=RuntimeConfig())


def get_config() -> RuntimeConfig:
    """Get the current runtime configuration."""
    return _config_var.get()


def set_config(config: RuntimeConfig) -> None:
    """Set the current runtime configuration."""
    _config_var.set(config)


class ConfigContext:
    """Context manager for temporarily changing config."""

    def __init__(self, **kwargs: Any):
        self._kwargs = kwargs
        self._token: Any = None
        self._old_config: RuntimeConfig | None = None

    def __enter__(self) -> RuntimeConfig:
        from dataclasses import replace

        self._old_config = get_config()
        new_config = replace(self._old_config, **self._kwargs)
        self._token = _config_var.set(new_config)
        return new_config

    def __exit__(self, *args: Any) -> None:
        if self._token is not None:
            _config_var.reset(self._token)


@functools.cache
def get_current_nix_platform() -> str:
    """Get current Nix platform (e.g., 'aarch64-darwin', 'x86_64-linux')."""
    machine = platform.machine()
    system = platform.system().lower()

    arch_map = {"arm64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}
    arch = arch_map.get(machine, machine)

    return f"{arch}-{system}"


# Common platform definitions
ALL_PLATFORMS = ("aarch64-darwin", "aarch64-linux", "x86_64-linux")
DARWIN_PLATFORMS = ("aarch64-darwin",)

# Placeholder hash for platforms that can't be built locally
FAKE_HASH = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
SRI_PREFIX = "sha256-"

# Required and optional external tools
REQUIRED_TOOLS = ["nix", "nix-prefetch-url"]
OPTIONAL_TOOLS = ["flake-edit"]  # Only needed for ref updates
