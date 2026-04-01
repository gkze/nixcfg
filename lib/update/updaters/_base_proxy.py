"""Shared typed access to ``lib.update.updaters.base``.

``core.py`` and ``flake_backed.py`` intentionally resolve the public
``lib.update.updaters.base`` module at runtime so monkeypatches against that
module keep affecting deep updater flows. Centralize the importlib/Protocol glue
here so that behavior stays the same without duplicating the indirection.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterable
    from pathlib import Path

    import aiohttp

    from lib.nix.models.flake_lock import FlakeLockNode
    from lib.nix.models.sources import HashMapping
    from lib.update.config import UpdateConfig
    from lib.update.events import EventStream
    from lib.update.process import RunCommandOptions


class UpdateProcessModule(Protocol):
    RunCommandOptions: type[RunCommandOptions]

    def run_command(
        self, args: list[str], *, options: RunCommandOptions
    ) -> EventStream: ...


class BaseModule(Protocol):
    package_dir_for: Callable[[str], Path | None]
    update_process: UpdateProcessModule

    def compute_deno_deps_hash(
        self,
        source: str,
        input_name: str,
        *,
        native_only: bool = False,
        config: UpdateConfig | None = None,
    ) -> EventStream: ...

    def compute_drv_fingerprint(
        self,
        source: str,
        *,
        system: str | None = None,
        config: UpdateConfig | None = None,
    ) -> Awaitable[str]: ...

    def compute_overlay_hash(
        self,
        source: str,
        *,
        system: str | None = None,
        config: UpdateConfig | None = None,
    ) -> EventStream: ...

    def compute_url_hashes(
        self, source_name: str, urls: Iterable[str]
    ) -> EventStream: ...

    def convert_nix_hash_to_sri(
        self, source_name: str, nix_hash: str
    ) -> EventStream: ...

    def expect_hash_mapping(self, payload: object) -> HashMapping: ...

    def expect_str(self, payload: object) -> str: ...

    def fetch_url(
        self,
        session: aiohttp.ClientSession,
        url: str,
        *,
        user_agent: str | None = None,
        request_timeout: float | None = None,
        config: UpdateConfig | None = None,
        **kwargs: object,
    ) -> Awaitable[bytes]: ...

    def get_current_nix_platform(self) -> str: ...

    def get_flake_input_node(self, input_name: str) -> FlakeLockNode: ...

    def get_flake_input_version(self, node: FlakeLockNode) -> str: ...


def base_module() -> BaseModule:
    """Return the monkeypatch-friendly updater base facade module."""
    return cast("BaseModule", importlib.import_module("lib.update.updaters.base"))


__all__ = ["BaseModule", "UpdateProcessModule", "base_module"]
