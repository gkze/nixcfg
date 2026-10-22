"""Updater for Commander's signed Sparkle release channel."""

from typing import TYPE_CHECKING, ClassVar

from lib.update.updaters import SparkleAppcastUrlUpdater, VersionInfo, register_updater

if TYPE_CHECKING:
    from lib.update.updaters import UpdateContext


@register_updater
class CommanderUpdater(SparkleAppcastUrlUpdater):
    """Use the same release feed and archive as the installed Commander app."""

    name = "commander"
    APPCAST_URL = (
        "https://softwareupdate.thecommander.app/appcast/commanderai-appcast.xml"
    )
    VERSION_FIELD = "short_version"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "darwin",
        "x86_64-darwin": "darwin",
    }

    async def _is_latest(
        self,
        context: UpdateContext,
        info: VersionInfo,
    ) -> bool:
        """Recompute hashes because release artifacts may change in place."""
        _ = (context, info)
        return False
