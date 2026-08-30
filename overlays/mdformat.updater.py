"""Updater for the flat mdformat overlay source metadata."""

# ruff: noqa: N999 -- updater discovery intentionally uses a flat dotted sidecar.

from typing import TYPE_CHECKING

from lib import json_utils
from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_json
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.updaters import (
    FixedOutputHashStep,
    UpdateContext,
    Updater,
    VersionInfo,
    register_updater,
    stream_fixed_output_hashes,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream

_PYPI_URL = "https://pypi.org/pypi/mdformat/json"


@register_updater
class MdformatUpdater(Updater):
    """Track the PyPI release and hash the matching GitHub source tag."""

    name = "mdformat"
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.mdformat",
            mode="build",
        ),
    )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the latest published mdformat version from PyPI."""
        payload = await fetch_json(
            session,
            _PYPI_URL,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        metadata = json_utils.as_object_dict(payload, context=_PYPI_URL)
        info = json_utils.as_object_dict(
            metadata.get("info"),
            context=f"{_PYPI_URL} info",
        )
        version = json_utils.get_required_str(
            info,
            "version",
            context=f"{_PYPI_URL} info",
        )
        if not version:
            msg = f"Empty PyPI version in {_PYPI_URL}"
            raise RuntimeError(msg)
        return VersionInfo(version=version)

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "hukkin",
            "mdformat",
            tag=version,
            fetch_submodules=False,
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash the unpacked source tree for the matching GitHub tag."""
        _ = (session, context)
        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    error="Missing mdformat srcHash output",
                    expr=lambda _resolved: self._src_expr(info.version),
                ),
            ),
            config=self.config,
        ):
            yield event
