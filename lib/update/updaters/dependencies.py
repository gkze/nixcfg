"""Explicit dependency adapter for updater implementations.

This adapter intentionally delegates through ``lib.update.updaters.base`` on
each access so existing tests and package updaters that monkeypatch the public
base facade keep affecting the deeper updater implementations.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from lib.update.updaters._base_proxy import base_module

if TYPE_CHECKING:
    from collections.abc import Awaitable, Iterable, Mapping
    from pathlib import Path

    import aiohttp

    from lib.nix.models.flake_lock import FlakeLockNode
    from lib.nix.models.sources import HashMapping
    from lib.update.config import UpdateConfig
    from lib.update.events import EventStream
    from lib.update.updaters._base_proxy import UpdateProcessModule


@dataclass(frozen=True, slots=True)
class _UpdaterDependencies:
    """Monkeypatch-compatible dependency access for updater internals."""

    def compute_deno_deps_hash(
        self,
        source: str,
        input_name: str,
        *,
        native_only: bool = False,
        config: UpdateConfig | None = None,
    ) -> EventStream:
        return base_module().compute_deno_deps_hash(
            source,
            input_name,
            native_only=native_only,
            config=config,
        )

    def compute_fixed_output_hash(
        self,
        source: str,
        expr: str,
        *,
        env: Mapping[str, str] | None = None,
        config: UpdateConfig | None = None,
    ) -> EventStream:
        return base_module().compute_fixed_output_hash(
            source,
            expr,
            env=env,
            config=config,
        )

    def compute_drv_fingerprint(
        self,
        source: str,
        *,
        system: str | None = None,
        config: UpdateConfig | None = None,
    ) -> Awaitable[str]:
        return base_module().compute_drv_fingerprint(
            source,
            system=system,
            config=config,
        )

    def compute_overlay_hash(
        self,
        source: str,
        *,
        system: str | None = None,
        config: UpdateConfig | None = None,
    ) -> EventStream:
        return base_module().compute_overlay_hash(source, system=system, config=config)

    def compute_url_hashes(self, source_name: str, urls: Iterable[str]) -> EventStream:
        return base_module().compute_url_hashes(source_name, urls)

    def convert_nix_hash_to_sri(self, source_name: str, nix_hash: str) -> EventStream:
        return base_module().convert_nix_hash_to_sri(source_name, nix_hash)

    def expect_hash_mapping(self, payload: object) -> HashMapping:
        return base_module().expect_hash_mapping(payload)

    def expect_str(self, payload: object) -> str:
        return base_module().expect_str(payload)

    def fetch_url(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        user_agent: str | None = None,
        request_timeout: float | None = None,
        config: UpdateConfig | None = None,
        **kwargs: object,
    ) -> Awaitable[bytes]:
        return base_module().fetch_url(
            session,
            url,
            user_agent=user_agent,
            request_timeout=request_timeout,
            config=config,
            **kwargs,
        )

    def get_current_nix_platform(self) -> str:
        return base_module().get_current_nix_platform()

    def get_flake_input_node(self, input_name: str) -> FlakeLockNode:
        return base_module().get_flake_input_node(input_name)

    def get_flake_input_version(self, node: FlakeLockNode) -> str:
        return base_module().get_flake_input_version(node)

    def package_dir_for(self, name: str) -> Path | None:
        return base_module().package_dir_for(name)

    @property
    def update_process(self) -> UpdateProcessModule:
        return base_module().update_process


_DEPENDENCIES = _UpdaterDependencies()


def updater_dependencies() -> _UpdaterDependencies:
    """Return updater dependencies while keeping public-facade monkeypatching."""
    return _DEPENDENCIES


__all__ = ["updater_dependencies"]
