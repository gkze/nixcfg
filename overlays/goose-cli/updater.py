"""Updater for goose-cli source and cargo hashes."""

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


class GooseCliUpdater(Updater):
    """Resolve latest goose release and compute source/cargo hashes."""

    name = "goose-cli"
    GITHUB_OWNER = "block"
    GITHUB_REPO = "goose"

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Return latest upstream release version metadata."""
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
            'owner = "block"; '
            'repo = "goose"; '
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
            "goose-cli": {
                "version": version,
                "hashes": [
                    {"hashType": "srcHash", "hash": src_hash},
                    {"hashType": "cargoHash", "hash": fake_hash},
                ],
            },
        }
        return {"UPDATE_SOURCE_OVERRIDES_JSON": json.dumps(payload)}

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        """Compute source and cargo vendor fixed-output hashes."""
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

        cargo_hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                self._overlay_expr(self.name),
                env=self._override_env(info.version, src_hash, self.config.fake_hash),
                config=self.config,
            ),
            cargo_hash_drain,
            parse=expect_str,
        ):
            yield event
        cargo_hash = require_value(cargo_hash_drain, "Missing cargoHash output")

        hashes: SourceHashes = [
            HashEntry.create("srcHash", src_hash),
            HashEntry.create("cargoHash", cargo_hash),
        ]
        yield UpdateEvent.value(self.name, hashes)
