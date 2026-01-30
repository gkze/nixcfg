"""Shared library for Nix configuration update tools."""

# Exceptions
from lib.exceptions import (
    UpdateError,
    NetworkError,
    RateLimitError,
    NixCommandError,
    HashExtractionError,
    ValidationError,
    FlakeLockError,
    CommandTimeoutError,
)

# Config
from lib.config import (
    RuntimeConfig,
    Paths,
    Timeouts,
    Limits,
    get_config,
    set_config,
    ConfigContext,
    get_current_nix_platform,
    ALL_PLATFORMS,
    DARWIN_PLATFORMS,
    FAKE_HASH,
    SRI_PREFIX,
    REQUIRED_TOOLS,
    OPTIONAL_TOOLS,
)

# Events
from lib.events import (
    EventKind,
    UpdateEvent,
    CommandResult,
    EventStream,
    EventCollector,
    collect_value,
)

# HTTP
from lib.http import (
    DEFAULT_USER_AGENT,
    get_github_token,
    check_github_rate_limit,
    request,
    fetch_url,
    fetch_json,
    github_raw_url,
    github_api_url,
    fetch_github_api,
    fetch_github_default_branch,
    fetch_github_latest_commit,
    fetch_github_latest_version_ref,
)

# Nix
from lib.nix import (
    sanitize_log_line,
    truncate_command,
    stream_command,
    run_command,
    extract_nix_hash,
    convert_hash_to_sri,
    compute_sri_hash,
    compute_url_hashes,
    compute_fixed_output_hash,
    FlakeLock,
    update_flake_input,
    check_required_tools,
    check_optional_tools,
)

# Models
from lib.models import (
    NixPlatform,
    DrvType,
    HashType,
    validate_sri_hash,
    VSCODE_PLATFORMS,
    PlatformMapping,
    HashEntry,
    SourceHashes,
    HashCollection,
    SourceEntry,
    SourcesFile,
    VersionInfo,
    verify_platform_versions,
)

# Renderer
from lib.renderer import (
    is_tty,
    read_cursor_row,
    TerminalInfo,
    fit_to_width,
    SourceState,
    Renderer,
    OutputOptions,
    process_event,
)

__all__ = [
    # Exceptions
    "UpdateError",
    "NetworkError",
    "RateLimitError",
    "NixCommandError",
    "HashExtractionError",
    "ValidationError",
    "FlakeLockError",
    "CommandTimeoutError",
    # Config
    "RuntimeConfig",
    "Paths",
    "Timeouts",
    "Limits",
    "get_config",
    "set_config",
    "ConfigContext",
    "get_current_nix_platform",
    "ALL_PLATFORMS",
    "DARWIN_PLATFORMS",
    "FAKE_HASH",
    "SRI_PREFIX",
    "REQUIRED_TOOLS",
    "OPTIONAL_TOOLS",
    # Events
    "EventKind",
    "UpdateEvent",
    "CommandResult",
    "EventStream",
    "EventCollector",
    "collect_value",
    # HTTP
    "DEFAULT_USER_AGENT",
    "get_github_token",
    "check_github_rate_limit",
    "request",
    "fetch_url",
    "fetch_json",
    "github_raw_url",
    "github_api_url",
    "fetch_github_api",
    "fetch_github_default_branch",
    "fetch_github_latest_commit",
    "fetch_github_latest_version_ref",
    # Nix
    "sanitize_log_line",
    "truncate_command",
    "stream_command",
    "run_command",
    "extract_nix_hash",
    "convert_hash_to_sri",
    "compute_sri_hash",
    "compute_url_hashes",
    "compute_fixed_output_hash",
    "FlakeLock",
    "update_flake_input",
    "check_required_tools",
    "check_optional_tools",
    # Models
    "NixPlatform",
    "DrvType",
    "HashType",
    "validate_sri_hash",
    "VSCODE_PLATFORMS",
    "PlatformMapping",
    "HashEntry",
    "SourceHashes",
    "HashCollection",
    "SourceEntry",
    "SourcesFile",
    "VersionInfo",
    "verify_platform_versions",
    # Renderer
    "is_tty",
    "read_cursor_row",
    "TerminalInfo",
    "fit_to_width",
    "SourceState",
    "Renderer",
    "OutputOptions",
    "process_event",
]
