"""Factory helpers for common updater subclasses."""

from __future__ import annotations

import inspect
from typing import TYPE_CHECKING

from lib.update.updaters.base import (
    DenoDepsHashUpdater,
    DenoManifestUpdater,
    FlakeInputHashUpdater,
)
from lib.update.updaters.registry import register_updater

if TYPE_CHECKING:
    from lib.nix.models.sources import HashType


def _caller_module_name() -> str:
    caller_frames = inspect.stack(context=0)
    return str(caller_frames[1].frame.f_globals.get("__name__", __name__))


def flake_input_hash_updater(
    name: str,
    hash_type: HashType,
    *,
    input_name: str | None = None,
    platform_specific: bool = False,
) -> type[FlakeInputHashUpdater]:
    """Create and register a flake-input-backed hash updater."""
    attrs: dict[str, object] = {
        "__module__": _caller_module_name(),
        "name": name,
        "input_name": input_name,
        "hash_type": hash_type,
        "platform_specific": platform_specific,
    }
    return register_updater(type(f"{name}Updater", (FlakeInputHashUpdater,), attrs))


def go_vendor_updater(
    name: str, *, input_name: str | None = None, **_kw: object
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "vendorHash", ...)``."""
    return flake_input_hash_updater(name, "vendorHash", input_name=input_name)


def cargo_vendor_updater(
    name: str, *, input_name: str | None = None, **_kw: object
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "cargoHash", ...)``."""
    return flake_input_hash_updater(name, "cargoHash", input_name=input_name)


def npm_deps_updater(
    name: str, *, input_name: str | None = None
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "npmDepsHash", ...)``."""
    return flake_input_hash_updater(name, "npmDepsHash", input_name=input_name)


def bun_node_modules_updater(
    name: str, *, input_name: str | None = None
) -> type[FlakeInputHashUpdater]:
    """Shorthand for platform-specific Bun ``nodeModulesHash`` updaters."""
    return flake_input_hash_updater(
        name,
        "nodeModulesHash",
        input_name=input_name,
        platform_specific=True,
    )


def uv_lock_hash_updater(
    name: str, *, input_name: str | None = None
) -> type[FlakeInputHashUpdater]:
    r"""Shorthand: ``flake_input_hash_updater(name, "uvLockHash", ...)``."""
    return flake_input_hash_updater(name, "uvLockHash", input_name=input_name)


def deno_deps_updater(
    name: str,
    *,
    input_name: str | None = None,
) -> type[DenoDepsHashUpdater]:
    """Create and register a :class:`DenoDepsHashUpdater` subclass."""
    attrs: dict[str, object] = {
        "__module__": _caller_module_name(),
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
) -> type[DenoManifestUpdater]:
    """Create and register a :class:`DenoManifestUpdater` subclass."""
    attrs: dict[str, object] = {
        "__module__": _caller_module_name(),
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
]
