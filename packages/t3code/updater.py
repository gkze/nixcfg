"""Updater for T3 Code's platform-specific Bun workspace cache."""

from lib.update.updaters.base import bun_node_modules_updater

# T3 Code's _shared.nix bunTarget map only covers aarch64-darwin; keep the
# updater's platform set in sync with packages/registry.nix so the
# compute-hashes Linux runners skip rather than try to build the missing
# flake attribute.
T3CodeUpdater = bun_node_modules_updater(
    "t3code",
    input_name="t3code",
    module=__name__,
    supported_platforms=("aarch64-darwin",),
)
