"""Updater for codex-v8 source hash."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashEntry, HashType, SourceHashes
from lib.update.events import (
    CapturedValue,
    EventStream,
    UpdateEvent,
    ValueDrain,
    capture_stream_value,
    drain_value_events,
    expect_hash_mapping,
    expect_str,
    require_value,
)
from lib.update.nix import _build_fetchgit_expr, compute_fixed_output_hash
from lib.update.updaters.base import (
    HashEntryUpdater,
    UpdateContext,
    VersionInfo,
    compute_url_hashes,
    register_updater,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry


@register_updater
class CodexV8Updater(GitHubReleaseUpdater, HashEntryUpdater):
    """Resolve the pinned rusty_v8 tag and compute its recursive source hash."""

    name = "codex-v8"
    input_name = "codex-v8"
    GITHUB_OWNER = "denoland"
    GITHUB_REPO = "rusty_v8"
    TAG_PREFIX = ""
    PLATFORMS: ClassVar[dict[str, str]] = {
        "x86_64-linux": "x86_64-unknown-linux-gnu",
    }

    @staticmethod
    def _release_version(version: str) -> str:
        return version.removeprefix("v")

    @classmethod
    def _archive_url(cls, version: str, platform: str) -> str:
        return (
            "https://github.com/denoland/rusty_v8/releases/download/"
            f"v{cls._release_version(version)}/"
            f"librusty_v8_release_{cls.PLATFORMS[platform]}.a.gz"
        )

    @classmethod
    def _binding_url(cls, version: str, platform: str) -> str:
        return (
            "https://github.com/denoland/rusty_v8/releases/download/"
            f"v{cls._release_version(version)}/"
            f"src_binding_release_{cls.PLATFORMS[platform]}.rs"
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        current = getattr(context, "current", context)
        if current is None or getattr(current, "version", None) != info.version:
            return False

        hashes = getattr(current, "hashes", None)
        entries = getattr(hashes, "entries", None)
        if entries is None:
            return False

        required = {
            ("srcHash", None),
            *(("rustyV8ArchiveHash", platform) for platform in self.PLATFORMS),
            *(("rustyV8BindingHash", platform) for platform in self.PLATFORMS),
        }
        present = {
            (getattr(entry, "hash_type", None), getattr(entry, "platform", None))
            for entry in entries
        }
        return required <= present

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetchgit_expr(
            "https://github.com/denoland/rusty_v8.git",
            version,
            fetch_submodules=True,
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute the recursive fetchgit source hash and Linux release assets."""
        _ = (context, session)

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

        platform_urls: dict[tuple[HashType, str], str] = {}
        for platform in self.PLATFORMS:
            platform_urls[("rustyV8ArchiveHash", platform)] = self._archive_url(
                info.version, platform
            )
            platform_urls[("rustyV8BindingHash", platform)] = self._binding_url(
                info.version, platform
            )

        async for item in capture_stream_value(
            compute_url_hashes(self.name, platform_urls.values()),
            error="Missing prebuilt rusty_v8 hash output",
        ):
            if isinstance(item, CapturedValue):
                hashes_by_url = expect_hash_mapping(item.captured)
                hashes: SourceHashes = [HashEntry.create("srcHash", src_hash)] + [
                    HashEntry.create(
                        hash_type,
                        hashes_by_url[url],
                        platform=platform,
                        url=url,
                    )
                    for (hash_type, platform), url in sorted(platform_urls.items())
                ]
                yield UpdateEvent.value(self.name, hashes)
            else:
                yield item
