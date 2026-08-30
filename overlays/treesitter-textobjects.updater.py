"""Updater for the flat treesitter-textobjects source metadata."""

# ruff: noqa: N999 -- updater discovery intentionally uses a flat dotted sidecar.

import re
from typing import TYPE_CHECKING, cast

from lib.nix.models.sources import HashCollection, SourceEntry, SourceHashes
from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_github_api
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.updaters import (
    FixedOutputHashStep,
    UpdateContext,
    Updater,
    VersionInfo,
    register_updater,
    stream_fixed_output_hashes,
)
from lib.update.updaters.metadata import require_metadata_str

if TYPE_CHECKING:
    import aiohttp

    from lib.update.events import EventStream

_BRANCH = "main"
_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_OWNER = "gkze"
_REPO = "nvim-treesitter-textobjects"


@register_updater
class TreesitterTextobjectsUpdater(Updater):
    """Track the immutable head commit of the upstream main branch."""

    name = "treesitter-textobjects"
    derivation_validations = (
        DerivationValidation(
            installable=("path:.#pkgs.{system}.vimPlugins.nvim-treesitter-textobjects"),
            mode="build",
        ),
    )

    @staticmethod
    def _commit_from_payload(payload: object) -> str:
        if not isinstance(payload, dict):
            msg = "Treesitter textobjects branch response must be a JSON object"
            raise TypeError(msg)
        commit = cast("dict[str, object]", payload).get("sha")
        if not isinstance(commit, str) or _COMMIT_PATTERN.fullmatch(commit) is None:
            msg = "Treesitter textobjects branch response has no immutable commit"
            raise RuntimeError(msg)
        return commit

    @staticmethod
    def _require_commit(info: VersionInfo) -> str:
        commit = require_metadata_str(
            info.metadata,
            "commit",
            context="Treesitter textobjects metadata",
        )
        if _COMMIT_PATTERN.fullmatch(commit) is None:
            msg = "Treesitter textobjects metadata has no immutable commit"
            raise RuntimeError(msg)
        return commit

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve main through the GitHub commits API."""
        payload = await fetch_github_api(
            session,
            f"repos/{_OWNER}/{_REPO}/commits/{_BRANCH}",
            config=self.config,
        )
        commit = self._commit_from_payload(payload)
        return VersionInfo(
            version=_BRANCH,
            metadata={"commit": commit},
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Require the persisted branch label and immutable commit to match."""
        current = context.current if isinstance(context, UpdateContext) else context
        return (
            current is not None
            and current.version == info.version
            and current.commit == self._require_commit(info)
        )

    @staticmethod
    def _src_expr(commit: str) -> str:
        return _build_fetch_from_github_expr(
            _OWNER,
            _REPO,
            rev=commit,
            fetch_submodules=False,
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash the unpacked source tree at the resolved commit."""
        _ = (session, context)
        commit = self._require_commit(info)
        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    error="Missing treesitter-textobjects srcHash output",
                    expr=lambda _resolved: self._src_expr(commit),
                ),
            ),
            config=self.config,
        ):
            yield event

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the branch label, immutable commit, and source hash."""
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value(hashes),
        )
