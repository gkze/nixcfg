"""Configuration models and environment/CLI resolution helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TypedDict, Unpack

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from lib.update.constants import FAKE_HASH


def default_max_nix_builds() -> int:
    """Return a conservative default for concurrent nix build jobs."""
    cores = os.cpu_count()
    if cores is None:
        return 4
    return max(1, (cores * 7 + 9) // 10)


@dataclass(frozen=True)
class UpdateConfig:
    """Resolved runtime configuration for update operations."""

    default_timeout: int
    default_subprocess_timeout: int  # 40 minutes for nix builds
    default_log_tail_lines: int
    default_render_interval: float
    default_user_agent: str
    default_retries: int
    default_retry_backoff: float
    fake_hash: str
    max_nix_builds: int  # concurrent nix build processes
    deno_deps_platforms: tuple[str, ...]


class UpdateSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="UPDATE_", extra="ignore")

    http_timeout: int = 30
    subprocess_timeout: int = 2400
    log_tail_lines: int = 10
    render_interval: float = 0.05
    user_agent: str = "nixcfg"
    retries: int = 3
    retry_backoff: float = 1.0
    fake_hash: str = FAKE_HASH
    max_nix_builds: int = default_max_nix_builds()
    deno_deps_platforms: tuple[str, ...] = (
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    )

    @field_validator("deno_deps_platforms", mode="before")
    @classmethod
    def _parse_deno_deps_platforms(cls, value: object) -> object:
        if isinstance(value, str):
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return (
                tuple(parts)
                if parts
                else cls.model_fields["deno_deps_platforms"].default
            )
        return value


def _settings_to_config(settings: UpdateSettings) -> UpdateConfig:
    return UpdateConfig(
        default_timeout=settings.http_timeout,
        default_subprocess_timeout=settings.subprocess_timeout,
        default_log_tail_lines=max(1, settings.log_tail_lines),
        default_render_interval=settings.render_interval,
        default_user_agent=settings.user_agent,
        default_retries=max(0, settings.retries),
        default_retry_backoff=settings.retry_backoff,
        fake_hash=settings.fake_hash,
        max_nix_builds=max(1, settings.max_nix_builds),
        deno_deps_platforms=settings.deno_deps_platforms,
    )


DEFAULT_SETTINGS = UpdateSettings()
DEFAULT_CONFIG = _settings_to_config(DEFAULT_SETTINGS)


class _ResolveConfigOverrides(TypedDict, total=False):
    http_timeout: int | None
    subprocess_timeout: int | None
    log_tail_lines: int | None
    render_interval: float | None
    user_agent: str | None
    retries: int | None
    retry_backoff: float | None
    fake_hash: str | None
    max_nix_builds: int | None
    deno_platforms: str | None
    deno_deps_platforms: str | tuple[str, ...] | None


def resolve_active_config(config: UpdateConfig | None) -> UpdateConfig:
    """Return *config* if provided, otherwise the default configuration."""
    return config or DEFAULT_CONFIG


def env_bool(name: str, *, default: bool = False) -> bool:
    """Read an environment variable as a boolean flag."""
    truthy = {"1", "true", "yes", "on"}
    falsy = {"0", "false", "no", "off"}
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in truthy:
        return True
    if normalized in falsy:
        return False
    return default


def resolve_config(**overrides: Unpack[_ResolveConfigOverrides]) -> UpdateConfig:
    """Build an UpdateConfig by merging explicit overrides onto env defaults."""
    settings_data = DEFAULT_SETTINGS.model_dump()

    normalized_overrides: dict[str, object] = dict(overrides)
    deno_platforms = normalized_overrides.pop("deno_platforms", None)
    if deno_platforms is not None:
        normalized_overrides["deno_deps_platforms"] = deno_platforms

    for setting_name, value in normalized_overrides.items():
        if value is not None:
            settings_data[setting_name] = value

    merged_settings = UpdateSettings.model_validate(settings_data)
    return _settings_to_config(merged_settings)


__all__ = [
    "DEFAULT_CONFIG",
    "UpdateConfig",
    "default_max_nix_builds",
    "env_bool",
    "resolve_active_config",
    "resolve_config",
]
