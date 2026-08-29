"""Updater for the source-built Zeron macOS app."""

from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update.derivation_validation import DerivationValidation
from lib.update.nix import (
    _build_fetch_from_github_expr,
    _build_package_path_attr_expr,
)
from lib.update.updaters import (
    FixedOutputHashStep,
    GitHubReleaseUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
    stream_fixed_output_hashes,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.update.events import EventStream


@register_updater
class ZeronUpdater(GitHubReleaseUpdater):
    """Track Zeron releases and compute source plus Cargo vendor hashes."""

    name = "zeron"
    GITHUB_OWNER = "zeronsh"
    GITHUB_REPO = "comet"
    RELEASE_DISPLAY_NAME = "Zeron"
    RESOLVE_TAG_COMMIT = True
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            mode="build",
        ),
    )

    @classmethod
    def _src_expr(cls, commit: str) -> str:
        return _build_fetch_from_github_expr(
            cls.GITHUB_OWNER,
            cls.GITHUB_REPO,
            rev=commit,
            fetch_submodules=False,
        )

    def _source_override(self, info: VersionInfo, *, src_hash: str) -> SourceEntry:
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value([
                HashEntry.create("srcHash", src_hash),
                HashEntry.create("cargoHash", self.config.fake_hash),
            ]),
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash the immutable tree, then its complete Cargo dependency closure."""
        _ = (session, context)
        commit = self._require_commit(info)
        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    error="Missing srcHash output",
                    expr=lambda _resolved: self._src_expr(commit),
                ),
                FixedOutputHashStep(
                    hash_type="cargoHash",
                    error="Missing cargoHash output",
                    expr=lambda resolved: _build_package_path_attr_expr(
                        self.name,
                        ".cargoDeps",
                        system=self.DARWIN_PLATFORM,
                        source_overrides={
                            self.name: self._source_override(
                                info,
                                src_hash=resolved["srcHash"],
                            )
                        },
                    ),
                ),
            ),
            config=self.config,
        ):
            yield event

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist release version, exact source commit, and both closures."""
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value(hashes),
        )
