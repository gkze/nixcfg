from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    import argparse
    from collections.abc import Callable, Iterable


@dataclass(frozen=True)
class UpdateConfig:
    default_timeout: int = 30
    default_subprocess_timeout: int = 1200  # 20 minutes for nix builds
    default_log_tail_lines: int = 10
    default_render_interval: float = 0.05
    default_user_agent: str = "update.py"
    default_retries: int = 3
    default_retry_backoff: float = 1.0
    retry_jitter_ratio: float = 0.2
    fake_hash: str = "sha256-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    max_nix_builds: int = 4  # concurrent nix build processes
    deno_deps_platforms: tuple[str, ...] = (
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    )


DEFAULT_CONFIG = UpdateConfig()

_T = TypeVar("_T")


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


def _env_get(
    name: str,
    *,
    default: _T,
    parser: Callable[[str], _T],
    allow_empty: bool = False,
) -> _T:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip()
    if not value and not allow_empty:
        return default
    try:
        return parser(value)
    except ValueError:
        return default


def _env_int(name: str, *, default: int) -> int:
    return _env_get(name, default=default, parser=int)


def _env_float(name: str, *, default: float) -> float:
    return _env_get(name, default=default, parser=float)


def _env_str(name: str, *, default: str) -> str:
    return _env_get(name, default=default, parser=lambda value: value)


def _env_csv(name: str, *, default: Iterable[str]) -> list[str]:
    def _parse(value: str) -> list[str]:
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError("empty")
        return parts

    return _env_get(name, default=list(default), parser=_parse)


def _resolve_config(args: argparse.Namespace | None = None) -> UpdateConfig:
    defaults = DEFAULT_CONFIG

    def pick(
        value: _T | None, env: str, default: _T, parser: Callable[[str], _T]
    ) -> _T:
        if value is not None:
            return value
        return _env_get(env, default=default, parser=parser)

    def pick_csv(
        value: str | None, env: str, default: Iterable[str]
    ) -> tuple[str, ...]:
        if value is not None:
            parts = [part.strip() for part in value.split(",") if part.strip()]
            return tuple(parts) if parts else tuple(default)
        return tuple(_env_csv(env, default=default))

    args_timeout = getattr(args, "http_timeout", None) if args else None
    args_subprocess_timeout = (
        getattr(args, "subprocess_timeout", None) if args else None
    )
    args_log_tail_lines = getattr(args, "log_tail_lines", None) if args else None
    args_render_interval = getattr(args, "render_interval", None) if args else None
    args_user_agent = getattr(args, "user_agent", None) if args else None
    args_retries = getattr(args, "retries", None) if args else None
    args_retry_backoff = getattr(args, "retry_backoff", None) if args else None
    args_retry_jitter = getattr(args, "retry_jitter_ratio", None) if args else None
    args_fake_hash = getattr(args, "fake_hash", None) if args else None
    args_max_nix_builds = getattr(args, "max_nix_builds", None) if args else None
    args_deno_platforms = getattr(args, "deno_platforms", None) if args else None

    return UpdateConfig(
        default_timeout=pick(
            args_timeout, "UPDATE_HTTP_TIMEOUT", defaults.default_timeout, int
        ),
        default_subprocess_timeout=pick(
            args_subprocess_timeout,
            "UPDATE_SUBPROCESS_TIMEOUT",
            defaults.default_subprocess_timeout,
            int,
        ),
        default_log_tail_lines=max(
            1,
            pick(
                args_log_tail_lines,
                "UPDATE_LOG_TAIL_LINES",
                defaults.default_log_tail_lines,
                int,
            ),
        ),
        default_render_interval=pick(
            args_render_interval,
            "UPDATE_RENDER_INTERVAL",
            defaults.default_render_interval,
            float,
        ),
        default_user_agent=pick(
            args_user_agent, "UPDATE_USER_AGENT", defaults.default_user_agent, str
        ),
        default_retries=max(
            0,
            pick(args_retries, "UPDATE_RETRIES", defaults.default_retries, int),
        ),
        default_retry_backoff=pick(
            args_retry_backoff,
            "UPDATE_RETRY_BACKOFF",
            defaults.default_retry_backoff,
            float,
        ),
        retry_jitter_ratio=pick(
            args_retry_jitter,
            "UPDATE_RETRY_JITTER_RATIO",
            defaults.retry_jitter_ratio,
            float,
        ),
        fake_hash=pick(args_fake_hash, "UPDATE_FAKE_HASH", defaults.fake_hash, str),
        max_nix_builds=max(
            1,
            pick(
                args_max_nix_builds,
                "UPDATE_MAX_NIX_BUILDS",
                defaults.max_nix_builds,
                int,
            ),
        ),
        deno_deps_platforms=pick_csv(
            args_deno_platforms,
            "UPDATE_DENO_DEPS_PLATFORMS",
            defaults.deno_deps_platforms,
        ),
    )


__all__ = [
    "DEFAULT_CONFIG",
    "UpdateConfig",
    "_env_bool",
    "_resolve_active_config",
    "_resolve_config",
]
