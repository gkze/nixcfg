"""Updater for nix-manipulator checked-in uv.lock."""

from lib.update.updaters.base import uv_lock_updater

NixManipulatorUpdater = uv_lock_updater(
    "nix-manipulator",
    lock_env={"SETUPTOOLS_SCM_PRETEND_VERSION": "{version}"},
    module=__name__,
)
