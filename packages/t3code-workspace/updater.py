"""Updater for the internal T3 Code workspace Bun dependency cache."""

from lib.update.updaters.base import bun_node_modules_updater

# Helper derivation of the aarch64-darwin-only t3code package; match the
# registry constraint so compute-hashes skips Linux runners instead of
# trying to evaluate the missing flake attribute.
T3CodeWorkspaceUpdater = bun_node_modules_updater(
    "t3code-workspace",
    input_name="t3code",
    module=__name__,
    supported_platforms=("aarch64-darwin",),
)
