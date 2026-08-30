"""Updater for emdash's platform-specific npm dependency hashes."""

from typing import ClassVar

from lib.update.updaters import NpmDepsHashUpdater, register_updater


@register_updater
class EmdashUpdater(NpmDepsHashUpdater):
    """Npm deps hash updater for emdash."""

    name = "emdash"
    source_pins: ClassVar[dict[str, str]] = {"electronVersion": "40.7.0"}
    platform_specific = True
    supported_platforms = (
        "aarch64-darwin",
        "aarch64-linux",
        "x86_64-linux",
    )
