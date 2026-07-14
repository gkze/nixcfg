"""Updater for nix-manipulator checked-in uv.lock."""

from typing import ClassVar

from lib.update.updaters import UvLockUpdater, register_updater


@register_updater
class NixManipulatorUpdater(UvLockUpdater):
    """Uv lock updater for nix-manipulator."""

    name = "nix-manipulator"
    lock_env: ClassVar[dict[str, str]] = {
        "SETUPTOOLS_SCM_PRETEND_VERSION": "{version}",
    }
