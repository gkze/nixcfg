"""Updater for goose-v8 source hash."""

from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashEntry, HashType, SourceEntry, SourceHashes
from lib.update.events import (
    CapturedValue,
    EventStream,
    UpdateEvent,
    ValueDrain,
    capture_stream_value,
    drain_value_events,
    expect_hash_mapping,
    require_value,
)
from lib.update.nix import _build_fetchgit_expr, compute_fixed_output_hash
from lib.update.updaters.base import (
    FlakeInputUpdater,
    HashEntryUpdater,
    UpdateContext,
    VersionInfo,
    compute_url_hashes,
    fetch_url,
    register_updater,
)

if TYPE_CHECKING:
    import aiohttp


@register_updater
class GooseV8Updater(FlakeInputUpdater, HashEntryUpdater):
    """Track the pinned goose-v8 source without rehashing unchanged revisions.

    The goose V8 fork is pinned to an exact commit in ``flake.nix`` and fetched
    recursively with Chromium submodules. Recomputing the same ``srcHash`` on
    every unrelated flake refresh needlessly re-hits upstream submodule hosts,
    so treat an unchanged pinned revision with an existing ``srcHash`` as
    current.
    """

    name = "goose-v8"
    input_name = "goose-v8"
    PLATFORMS: ClassVar[dict[str, str]] = {
        "x86_64-linux": "x86_64-unknown-linux-gnu",
    }

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetchgit_expr(
            "https://github.com/jh-block/rusty_v8.git",
            version,
            fetch_submodules=True,
        )

    @classmethod
    def _archive_url(cls, release_version: str, platform: str) -> str:
        version = release_version.removeprefix("v")
        return (
            "https://github.com/denoland/rusty_v8/releases/download/"
            f"v{version}/librusty_v8_release_{cls.PLATFORMS[platform]}.a.gz"
        )

    @classmethod
    def _binding_url(cls, release_version: str, platform: str) -> str:
        version = release_version.removeprefix("v")
        return (
            "https://github.com/denoland/rusty_v8/releases/download/"
            f"v{version}/src_binding_release_{cls.PLATFORMS[platform]}.rs"
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

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute the recursive source hash and matching upstream Linux assets."""
        _ = context

        src_hash_drain = ValueDrain[object]()
        async for event in drain_value_events(
            compute_fixed_output_hash(
                self.name,
                self._src_expr(info.version),
                config=self.config,
            ),
            src_hash_drain,
            parse=lambda payload: payload,
        ):
            yield event

        src_hash = require_value(src_hash_drain, "Missing srcHash output")
        if not isinstance(src_hash, str):
            msg = f"Expected src hash string, got {type(src_hash).__name__}"
            raise TypeError(msg)

        cargo_toml_url = (
            "https://raw.githubusercontent.com/jh-block/rusty_v8/"
            f"{info.version}/Cargo.toml"
        )
        cargo_toml = await fetch_url(
            session,
            cargo_toml_url,
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        release_version = tomllib.loads(cargo_toml.decode())["package"]["version"]

        platform_urls: dict[tuple[HashType, str], str] = {}
        for platform in self.PLATFORMS:
            platform_urls[("rustyV8ArchiveHash", platform)] = self._archive_url(
                release_version, platform
            )
            platform_urls[("rustyV8BindingHash", platform)] = self._binding_url(
                release_version, platform
            )

        async for asset_item in capture_stream_value(
            compute_url_hashes(self.name, platform_urls.values()),
            error="Missing prebuilt rusty_v8 hash output",
        ):
            if isinstance(asset_item, CapturedValue):
                hashes_by_url = expect_hash_mapping(asset_item.captured)
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
                yield asset_item
