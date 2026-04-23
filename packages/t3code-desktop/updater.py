"""Updater for T3 Code Desktop's staged runtime Bun cache."""

from lib.update.updaters.base import bun_node_modules_updater

# Mirror the aarch64-darwin-only constraint from packages/registry.nix so the
# per-platform compute-hashes job skips Linux runners cleanly.
T3CodeDesktopUpdater = bun_node_modules_updater(
    "t3code-desktop",
    input_name="t3code",
    module=__name__,
    supported_platforms=("aarch64-darwin",),
)
