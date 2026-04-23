"""Factory helpers for common updater subclasses."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.update.updaters.base import (
    DenoDepsHashUpdater,
    DenoManifestUpdater,
    FlakeInputHashUpdater,
    UvLockUpdater,
)
from lib.update.updaters.registry import register_updater

if TYPE_CHECKING:
    from lib.nix.models.sources import HashType


def _resolve_module_name(module: str | None) -> str:
    return __name__ if module is None else module


def flake_input_hash_updater(
    name: str,
    hash_type: HashType,
    *,
    input_name: str | None = None,
    module: str | None = None,
    platform_specific: bool = False,
    supported_platforms: tuple[str, ...] | None = None,
) -> type[FlakeInputHashUpdater]:
    """Create and register a flake-input-backed hash updater.

    If ``supported_platforms`` is provided, the updater short-circuits on
    unsupported platforms and preserves existing hashes, so per-platform CI
    runners can skip packages whose system constraint excludes them without
    tripping over missing flake attributes.
    """
    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "input_name": input_name,
        "hash_type": hash_type,
        "platform_specific": platform_specific,
        "supported_platforms": supported_platforms,
    }
    return register_updater(type(f"{name}Updater", (FlakeInputHashUpdater,), attrs))


def go_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
    **_kw: object,
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "vendorHash", ...)``."""
    return flake_input_hash_updater(
        name,
        "vendorHash",
        input_name=input_name,
        module=module,
    )


def cargo_vendor_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
    **_kw: object,
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "cargoHash", ...)``."""
    return flake_input_hash_updater(
        name,
        "cargoHash",
        input_name=input_name,
        module=module,
    )


def npm_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "npmDepsHash", ...)``."""
    return flake_input_hash_updater(
        name,
        "npmDepsHash",
        input_name=input_name,
        module=module,
    )


def bun_node_modules_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
    supported_platforms: tuple[str, ...] | None = None,
) -> type[FlakeInputHashUpdater]:
    """Shorthand for platform-specific Bun ``nodeModulesHash`` updaters."""
    return flake_input_hash_updater(
        name,
        "nodeModulesHash",
        input_name=input_name,
        module=module,
        platform_specific=True,
        supported_platforms=supported_platforms,
    )


def uv_lock_hash_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "uvLockHash", ...)``."""
    return flake_input_hash_updater(
        name,
        "uvLockHash",
        input_name=input_name,
        module=module,
    )


def uv_lock_updater(
    name: str,
    *,
    input_name: str | None = None,
    lock_file: str = "uv.lock",
    lock_env: dict[str, str] | None = None,
    module: str | None = None,
) -> type[UvLockUpdater]:
    """Create and register a :class:`UvLockUpdater` subclass."""
    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "input_name": input_name,
        "lock_file": lock_file,
        "lock_env": dict(lock_env or {}),
    }
    return register_updater(type(f"{name}Updater", (UvLockUpdater,), attrs))


def deno_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
    module: str | None = None,
) -> type[DenoDepsHashUpdater]:
    """Create and register a :class:`DenoDepsHashUpdater` subclass."""
    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "input_name": input_name,
    }
    return register_updater(type(f"{name}Updater", (DenoDepsHashUpdater,), attrs))


def deno_manifest_updater(
    name: str,
    *,
    input_name: str | None = None,
    lock_file: str = "deno.lock",
    manifest_file: str = "deno-deps.json",
    module: str | None = None,
) -> type[DenoManifestUpdater]:
    """Create and register a :class:`DenoManifestUpdater` subclass."""
    attrs: dict[str, object] = {
        "__module__": _resolve_module_name(module),
        "name": name,
        "input_name": input_name,
        "lock_file": lock_file,
        "manifest_file": manifest_file,
    }
    return register_updater(type(f"{name}Updater", (DenoManifestUpdater,), attrs))


__all__ = [
    "bun_node_modules_updater",
    "cargo_vendor_updater",
    "deno_deps_updater",
    "deno_manifest_updater",
    "flake_input_hash_updater",
    "go_vendor_updater",
    "npm_deps_updater",
    "uv_lock_hash_updater",
    "uv_lock_updater",
]
