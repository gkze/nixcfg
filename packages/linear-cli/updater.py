"""Updater for linear-cli Deno dependency manifest and denort runtime hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.nix.commands.base import run_nix
from lib.nix.models.sources import HashEntry, SourceEntry
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    expect_source_hashes,
    require_value,
)
from lib.update.nix import _build_flake_attr_expr, get_current_nix_platform
from lib.update.paths import get_repo_file
from lib.update.updaters.base import (
    DenoManifestUpdater,
    UpdateContext,
    VersionInfo,
    compute_url_hashes,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp


@register_updater
class LinearCliUpdater(DenoManifestUpdater):
    """Update deno-deps manifest plus denort runtime hashes per platform."""

    name = "linear-cli"
    required_tools: ClassVar[tuple[str, ...]] = (
        *DenoManifestUpdater.required_tools,
        "nix",
        "nix-prefetch-url",
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "aarch64-darwin": "aarch64-apple-darwin",
        "aarch64-linux": "aarch64-unknown-linux-gnu",
        "x86_64-darwin": "x86_64-apple-darwin",
        "x86_64-linux": "x86_64-unknown-linux-gnu",
    }

    @staticmethod
    def _deno_version_expr(platform: str) -> str:
        return _build_flake_attr_expr(
            f"path:{get_repo_file('.')}",
            "pkgs",
            platform,
            "deno",
            "version",
            quoted_indices=(1,),
        )

    async def _resolve_deno_version(self) -> str:
        platform = get_current_nix_platform()
        result = await run_nix(
            [
                "nix",
                "eval",
                "--impure",
                "--raw",
                "--expr",
                self._deno_version_expr(platform),
            ],
            check=False,
        )
        version = result.stdout.strip()
        if result.returncode != 0 or not version:
            details = result.stderr.strip() or result.stdout.strip()
            msg = details or "nix eval failed"
            msg = f"Failed to evaluate deno.version for {self.name}: {msg}"
            raise RuntimeError(msg)
        return version

    @staticmethod
    def _denort_url(target: str, deno_version: str) -> str:
        return f"https://dl.deno.land/release/v{deno_version}/denort-{target}.zip"

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        if not await super()._is_latest(context, info):
            return False
        current = context.current if isinstance(context, UpdateContext) else context
        if current is None or current.hashes.entries is None:
            return False
        deno_version = await self._resolve_deno_version()
        expected_urls = {
            platform: self._denort_url(target, deno_version)
            for platform, target in self.PLATFORMS.items()
        }
        current_urls = {
            entry.platform: entry.url
            for entry in current.hashes.entries
            if entry.hash_type == "sha256" and entry.platform and entry.url
        }
        return current_urls == expected_urls

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Resolve manifest plus per-platform denort fixed-output hashes."""
        manifest_drain = ValueDrain()
        async for event in drain_value_events(
            super().fetch_hashes(info, session, context=context),
            manifest_drain,
            parse=expect_source_hashes,
        ):
            yield event
        require_value(manifest_drain, "Missing deno manifest output")

        deno_version = await self._resolve_deno_version()
        urls = {
            platform: self._denort_url(target, deno_version)
            for platform, target in self.PLATFORMS.items()
        }
        yield UpdateEvent.status(
            self.name,
            f"Fetching denort runtime hashes for Deno v{deno_version}...",
        )

        hash_drain = ValueDrain()
        async for event in drain_value_events(
            compute_url_hashes(self.name, urls.values()),
            hash_drain,
            parse=expect_hash_mapping,
        ):
            yield event
        hashes_by_url = require_value(hash_drain, "Missing denort hash output")
        entries = [
            HashEntry.create(
                "sha256",
                hashes_by_url[urls[platform]],
                platform=platform,
                url=urls[platform],
            )
            for platform in sorted(urls)
        ]
        yield UpdateEvent.value(self.name, entries)
