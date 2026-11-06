"""Updater for the openai-cli Go vendor hash."""

from pathlib import Path
from typing import TYPE_CHECKING, override

from lib.codemods.errors import CodemodError
from lib.import_utils import load_module_from_path
from lib.update.locked_source import locked_github_source
from lib.update.net import fetch_url, github_raw_url
from lib.update.updaters import GoVendorHashUpdater, register_updater

if TYPE_CHECKING:
    import aiohttp

    from lib.update.updaters.core import UpdateContext
    from lib.update.updaters.metadata import VersionInfo

_PTY_WAIT = load_module_from_path(
    Path(__file__).with_name("patch_pty_wait.py"),
    "nixcfg_openai_cli_patch_pty_wait",
)


@register_updater
class OpenaiCliUpdater(GoVendorHashUpdater):
    """Go vendor hash updater for openai-cli."""

    name = "openai-cli"
    GITHUB_OWNER = "openai"
    GITHUB_REPO = "openai-cli"

    @override
    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Reject a commit whose PTY waiter the Darwin drain cannot anchor.

        This runs in the Linux pins stage, against the refreshed flake input.
        A third harness shape fails only this target there, before a Darwin
        builder reaches patch or check.
        """
        info = await super().fetch_latest(session, context=context)
        node = self._resolve_flake_node(info)
        source = locked_github_source(
            node,
            context=f"{self.name} flake input",
            expected=(self.GITHUB_OWNER, self.GITHUB_REPO),
        )
        payload = await fetch_url(
            session,
            github_raw_url(
                source.owner,
                source.repo,
                source.rev,
                _PTY_WAIT.HARNESS_PATH,
            ),
            config=self.config,
        )
        try:
            harness = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = f"{self.name} {info.version} PTY harness is not valid UTF-8"
            raise RuntimeError(msg) from exc
        try:
            _PTY_WAIT.harness_layout(harness)
        except CodemodError as exc:
            msg = (
                f"{self.name} {info.version} PTY harness drifted from the "
                "Darwin exit drain"
            )
            raise RuntimeError(msg) from exc
        return info
