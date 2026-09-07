"""Updater for linear-cli Deno dependency manifest and denort runtime hashes."""

import asyncio
from typing import TYPE_CHECKING, ClassVar

from lib.nix.commands.base import run_nix
from lib.nix.models.sources import HashEntry, SourceHashes
from lib.update import nix as update_nix
from lib.update import process as update_process
from lib.update.events import (
    EventSink,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ignore_event,
)
from lib.update.nix import _build_flake_attr_expr
from lib.update.paths import local_flake_url
from lib.update.updaters import (
    DenoManifestUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp


DENO_MANIFEST_ATTEMPTS = 3


def _is_transient_deno_manifest_error(exc: BaseException) -> bool:
    if isinstance(exc, TimeoutError):
        return True
    exc_type = type(exc)
    if exc_type.__module__.startswith("aiohttp"):
        return True
    detail = f"{exc_type.__name__}: {exc}"
    return any(
        marker in detail
        for marker in (
            "Connection reset",
            "Server disconnected",
            "TimeoutError",
            "timed out",
        )
    )


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
            local_flake_url(),
            "pkgs",
            platform,
            "deno",
            "version",
            quoted_indices=(1,),
        )

    async def _resolve_deno_version(self) -> str:
        platform = update_nix.get_current_nix_platform()
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
        context: UpdateContext,
        info: VersionInfo,
    ) -> bool:
        if not await super()._is_latest(context, info):
            return False
        current = context.current
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
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Resolve manifest plus per-platform denort fixed-output hashes."""
        for attempt in range(1, DENO_MANIFEST_ATTEMPTS + 1):
            try:
                await super().fetch_hashes(info, session, context=context, emit=emit)
                break
            except BaseException as exc:
                if (
                    attempt >= DENO_MANIFEST_ATTEMPTS
                    or not _is_transient_deno_manifest_error(exc)
                ):
                    raise
                await emit(
                    UpdateEvent.status(
                        self.name,
                        "Retrying Deno manifest resolution after transient "
                        f"{type(exc).__name__} ({attempt}/{DENO_MANIFEST_ATTEMPTS})",
                        operation="compute_hash",
                        status=StatusInfo(
                            kind=StatusKind.RETRY,
                            value=type(exc).__name__,
                        ),
                    )
                )
                await asyncio.sleep(0.5 * attempt)

        deno_version = await self._resolve_deno_version()
        urls = {
            platform: self._denort_url(target, deno_version)
            for platform, target in self.PLATFORMS.items()
        }
        await emit(
            UpdateEvent.status(
                self.name,
                f"Fetching denort runtime hashes for Deno v{deno_version}...",
                operation="compute_hash",
                status=StatusInfo(
                    kind=StatusKind.COMPUTING_HASH,
                    value=f"denort Deno v{deno_version}",
                ),
            )
        )
        hashes_by_url = await update_process.compute_url_hashes(
            self.name, urls.values(), config=self.config, emit=emit
        )
        return [
            HashEntry.create(
                "sha256",
                hashes_by_url[urls[platform]],
                platform=platform,
                url=urls[platform],
            )
            for platform in sorted(urls)
        ]
