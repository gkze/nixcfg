"""Configuration models and environment/CLI resolution helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

if TYPE_CHECKING:
    import argparse


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
    default_subprocess_timeout: int  # 20 minutes for nix builds
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
    subprocess_timeout: int = 1200
    log_tail_lines: int = 10
    render_interval: float = 0.05
    user_agent: str = "update.py"
    retries: int = 3
    retry_backoff: float = 1.0
    fake_hash: str = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
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


def _resolve_active_config(config: UpdateConfig | None) -> UpdateConfig:
    return config or DEFAULT_CONFIG


def _env_bool(name: str, *, default: bool = False) -> bool:
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


def _resolve_config(args: argparse.Namespace | None = None) -> UpdateConfig:
    settings_data = DEFAULT_SETTINGS.model_dump()
    if args is not None:
        arg_to_setting = {
            "http_timeout": "http_timeout",
            "subprocess_timeout": "subprocess_timeout",
            "log_tail_lines": "log_tail_lines",
            "render_interval": "render_interval",
            "user_agent": "user_agent",
            "retries": "retries",
            "retry_backoff": "retry_backoff",
            "fake_hash": "fake_hash",
            "max_nix_builds": "max_nix_builds",
            "deno_platforms": "deno_deps_platforms",
        }
        for arg_name, setting_name in arg_to_setting.items():
            value = getattr(args, arg_name, None)
            if value is not None:
                settings_data[setting_name] = value

    merged_settings = UpdateSettings.model_validate(settings_data)
    return _settings_to_config(merged_settings)


__all__ = [
    "DEFAULT_CONFIG",
    "UpdateConfig",
    "_env_bool",
    "_resolve_active_config",
    "_resolve_config",
    "default_max_nix_builds",
]
