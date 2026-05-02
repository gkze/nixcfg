"""Updater for T3 Code Desktop's staged runtime Bun cache."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from lib.update.nix import _build_overlay_attr_expr
from lib.update.updaters._base_proxy import base_module as _base_module
from lib.update.updaters.base import VersionInfo, register_updater
from lib.update.updaters.flake_backed import FlakeInputHashUpdater

if TYPE_CHECKING:
    from lib.update.events import EventStream


@register_updater
class T3CodeDesktopUpdater(FlakeInputHashUpdater):
    """Compute only the desktop runtime ``node_modules`` hash."""

    # Mirror the aarch64-darwin-only constraint from packages/registry.nix so the
    # per-platform compute-hashes job skips Linux runners cleanly.
    name = "t3code-desktop"
    input_name = "t3code"
    hash_type: Literal["nodeModulesHash"] = "nodeModulesHash"
    platform_specific = True
    supported_platforms = ("aarch64-darwin",)

    @classmethod
    def _node_modules_expr(cls, *, system: str | None = None) -> str:
        """Return the package-path expression for this package's FOD."""
        return _build_overlay_attr_expr(cls.name, ".node_modules", system=system)

    def _compute_hash_for_system(
        self,
        info: VersionInfo,
        *,
        system: str | None,
    ) -> EventStream:
        """Hash the runtime cache directly so Electron dist FODs cannot pollute it."""
        _ = info
        return _base_module().compute_fixed_output_hash(
            self.name,
            self._node_modules_expr(system=system),
            env={"FAKE_HASHES": "1"},
            config=self.config,
        )
