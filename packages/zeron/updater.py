"""Updater for the source-built Zeron macOS app."""

from typing import TYPE_CHECKING, ClassVar

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import EventSink, ignore_event
from lib.update.net import fetch_url, github_raw_url
from lib.update.nix import (
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
from packages.zeron.patch_nix_managed import UPDATE_SOURCE, validate_update_source

if TYPE_CHECKING:
    import aiohttp


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
            systems=("aarch64-darwin",),
        ),
    )

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Reject a release whose updater seam the Nix ownership patch cannot own.

        This runs in the Linux pins stage, so anchor drift fails only this
        target there instead of failing the Darwin build's patchPhase.
        """
        info = await super().fetch_latest(session, context=context)
        payload = await fetch_url(
            session,
            github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                self._require_commit(info),
                UPDATE_SOURCE.as_posix(),
            ),
            config=self.config,
        )
        try:
            validate_update_source(payload.decode("utf-8"))
        except RuntimeError as exc:
            msg = f"Zeron {info.version} updater seam drifted from the Nix ownership patch"
            raise RuntimeError(msg) from exc
        return info

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
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Hash the immutable tree, then its complete Cargo dependency closure."""
        _ = (session, context)
        commit = self._require_commit(info)
        return await stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    expr=lambda _resolved: self._src_expr(commit),
                ),
                FixedOutputHashStep(
                    hash_type="cargoHash",
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
            emit=emit,
        )

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist release version, exact source commit, and both closures."""
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value(hashes),
        )
