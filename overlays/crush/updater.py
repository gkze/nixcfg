"""Updater for crush source and Go vendor hashes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from lib.nix.models.sources import HashEntry, SourceHashes
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.net import fetch_github_api
from lib.update.nix import compute_fixed_output_hash
from lib.update.paths import get_repo_file
from lib.update.updaters.base import Updater, VersionInfo

if TYPE_CHECKING:
    import aiohttp


class CrushUpdater(Updater):
    """Resolve latest crush tag and compute src/vendor fixed-output hashes."""

    name = "crush"
    GITHUB_OWNER = "charmbracelet"
    GITHUB_REPO = "crush"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Return the newest upstream release version metadata."""
        payload = await fetch_github_api(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/latest",
            config=self.config,
        )
        if not isinstance(payload, dict):
            msg = f"Unexpected release payload type: {type(payload).__name__}"
            raise TypeError(msg)
        tag_name = payload.get("tag_name")
        if not isinstance(tag_name, str) or not tag_name:
            msg = f"Missing tag_name in release payload: {payload!r}"
            raise RuntimeError(msg)
        version = tag_name.removeprefix("v")
        return VersionInfo(version=version, metadata={"tag": tag_name})

    @staticmethod
    def _src_expr(version: str) -> str:
        return (
            "pkgs.fetchFromGitHub { "
            'owner = "charmbracelet"; '
            'repo = "crush"; '
            f'tag = "v{version}"; '
            "hash = pkgs.lib.fakeHash; "
            "}"
        )

    @staticmethod
    def _overlay_expr(source: str) -> str:
        flake_url = f"git+file://{get_repo_file('.')}?dirty=1"
        return (
            "let"
            f'  flake = builtins.getFlake "{flake_url}";'
            "  system = builtins.currentSystem;"
            "  pkgs = import flake.inputs.nixpkgs {"
            "    inherit system;"
            "    config = { allowUnfree = true; allowInsecurePredicate = _: true; };"
            "  };"
            "  applied = pkgs.lib.fix (self: pkgs // flake.overlays.default self pkgs);"
            f'in applied."{source}"'
        )

    @staticmethod
    def _override_env(version: str, src_hash: str, fake_hash: str) -> dict[str, str]:
        payload = {
            "crush": {
                "version": version,
                "hashes": [
                    {"hashType": "srcHash", "hash": src_hash},
                    {"hashType": "vendorHash", "hash": fake_hash},
                ],
            },
        }
        return {"UPDATE_SOURCE_OVERRIDES_JSON": json.dumps(payload)}

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        """Compute source and vendor fixed-output hashes for the release."""
        _ = session

        src_hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                self._src_expr(info.version),
                config=self.config,
            ),
            src_hash_drain,
            parse=expect_str,
        ):
            yield event
        src_hash = require_value(src_hash_drain, "Missing srcHash output")

        vendor_hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                self._overlay_expr(self.name),
                env=self._override_env(info.version, src_hash, self.config.fake_hash),
                config=self.config,
            ),
            vendor_hash_drain,
            parse=expect_str,
        ):
            yield event
        vendor_hash = require_value(vendor_hash_drain, "Missing vendorHash output")

        hashes: SourceHashes = [
            HashEntry.create("srcHash", src_hash),
            HashEntry.create("vendorHash", vendor_hash),
        ]
        yield UpdateEvent.value(self.name, hashes)
