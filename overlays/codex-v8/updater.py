"""Updater for codex-v8 source hash."""

import re
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashEntry, HashType, SourceHashes
from lib.update import nix as update_nix
from lib.update import process as update_process
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
from lib.update.nix import _build_fetchgit_expr
from lib.update.paths import REPO_ROOT
from lib.update.updaters import (
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.core import _coerce_context
from lib.update.updaters.github_release import GitHubReleaseUpdater

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry


@register_updater
class CodexV8Updater(GitHubReleaseUpdater):
    """Track the Codex-pinned rusty_v8 release and compute its source hash."""

    name = "codex-v8"
    companion_of = "codex"
    GITHUB_OWNER = "denoland"
    GITHUB_REPO = "rusty_v8"
    RESOLVE_TAG_COMMIT = True
    _CODEX_CARGO_NIX_PATH = Path("packages/codex/Cargo.nix")
    _CODEX_V8_VERSION_RE = re.compile(
        r'"v8"\s*=\s*rec\s*\{.*?^\s*version\s*=\s*"(?P<version>[^"]+)";',
        re.MULTILINE | re.DOTALL,
    )
    PLATFORMS: ClassVar[dict[str, str]] = {
        "x86_64-linux": "x86_64-unknown-linux-gnu",
    }

    @classmethod
    def _cargo_nix_text(cls, context: UpdateContext | SourceEntry | None) -> str:
        resolved_context = _coerce_context(context)
        overridden = resolved_context.generated_artifacts.get(cls._CODEX_CARGO_NIX_PATH)
        if overridden is not None:
            return overridden
        return (REPO_ROOT / cls._CODEX_CARGO_NIX_PATH).read_text(encoding="utf-8")

    @classmethod
    def _codex_v8_version(cls, cargo_nix_text: str) -> str:
        match = cls._CODEX_V8_VERSION_RE.search(cargo_nix_text)
        if match is None:
            msg = f"Could not resolve Codex v8 version from {cls._CODEX_CARGO_NIX_PATH}"
            raise RuntimeError(msg)
        return f"v{match.group('version').removeprefix('v')}"

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> VersionInfo:
        """Resolve Codex's rusty_v8 tag and persist its immutable commit."""
        version = self._codex_v8_version(self._cargo_nix_text(context))
        commit = await self._resolve_release_tag_commit(session, version)
        return VersionInfo(
            version=version,
            metadata={"commit": commit, "tag": version},
        )

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
        if (
            current is None
            or getattr(current, "version", None) != info.version
            or info.commit is None
            or getattr(current, "commit", None) != info.commit
        ):
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

        commit = self._require_commit(info)
        src_hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            update_nix.compute_fixed_output_hash(
                self.name,
                self._src_expr(commit),
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
            update_process.compute_url_hashes(
                self.name,
                platform_urls.values(),
                config=self.config,
            ),
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
