"""Updater for the source-built Writer macOS app and CLI."""

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
class WriterComputerUpdater(GitHubReleaseUpdater):
    """Pin Writer releases and both of their source dependency closures."""

    name = "writer-computer"
    GITHUB_OWNER = "joelbqz"
    GITHUB_REPO = "writer-computer"
    RELEASE_DISPLAY_NAME = "Writer"
    RESOLVE_TAG_COMMIT = True
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute source closures before accepting release metadata."""
        _ = (context, info)
        return False

    @classmethod
    def _src_expr(cls, commit: str) -> str:
        return _build_fetch_from_github_expr(
            cls.GITHUB_OWNER,
            cls.GITHUB_REPO,
            rev=commit,
            fetch_submodules=False,
        )

    def _source_override(
        self,
        info: VersionInfo,
        *,
        src_hash: str,
        npm_deps_hash: str,
        cargo_hash: str,
    ) -> SourceEntry:
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value([
                HashEntry.create("srcHash", src_hash),
                HashEntry.create("npmDepsHash", npm_deps_hash),
                HashEntry.create("cargoHash", cargo_hash),
            ]),
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash source, pnpm store, and Cargo vendor tree in dependency order."""
        _ = (session, context)
        commit = self._require_commit(info)
        fake_hash = self.config.fake_hash
        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    error="Missing srcHash output",
                    expr=lambda _resolved: self._src_expr(commit),
                ),
                FixedOutputHashStep(
                    hash_type="npmDepsHash",
                    error="Missing npmDepsHash output",
                    expr=lambda resolved: _build_package_path_attr_expr(
                        self.name,
                        ".pnpmDeps",
                        system=self.DARWIN_PLATFORM,
                        source_overrides={
                            self.name: self._source_override(
                                info,
                                src_hash=resolved["srcHash"],
                                npm_deps_hash=fake_hash,
                                cargo_hash=fake_hash,
                            )
                        },
                    ),
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
                                npm_deps_hash=resolved["npmDepsHash"],
                                cargo_hash=fake_hash,
                            )
                        },
                    ),
                ),
            ),
            config=self.config,
        ):
            yield event

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist release version, source commit, and both dependency hashes."""
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value(hashes),
        )
