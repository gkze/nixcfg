"""Updater for the source-built bb desktop app."""

from typing import TYPE_CHECKING, ClassVar, cast

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_json, github_raw_url
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
from lib.update.updaters.metadata import require_metadata_str

if TYPE_CHECKING:
    import aiohttp

    from lib.update.events import EventStream


@register_updater
class BbUpdater(GitHubReleaseUpdater):
    """Track immutable desktop tags and rebuild bb from their source commits."""

    name = "bb"
    GITHUB_OWNER = "get-bb"
    GITHUB_REPO = "bb"
    TAG_PREFIX = "desktop-v"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )

    @staticmethod
    def _manifest_version(payload: object, *, label: str) -> str:
        if not isinstance(payload, dict):
            msg = f"bb {label} manifest is not a JSON object"
            raise TypeError(msg)
        version = cast("dict[str, object]", payload).get("version")
        if not isinstance(version, str) or not version:
            msg = f"bb {label} manifest version is missing"
            raise TypeError(msg)
        return version

    @staticmethod
    def _electron_version(payload: object) -> str:
        if not isinstance(payload, dict):
            msg = "bb desktop manifest is not a JSON object"
            raise TypeError(msg)
        dev_dependencies = cast("dict[str, object]", payload).get("devDependencies")
        if not isinstance(dev_dependencies, dict):
            msg = "bb desktop manifest Electron version is missing"
            raise TypeError(msg)
        electron_version = cast("dict[str, object]", dev_dependencies).get("electron")
        if not isinstance(electron_version, str) or not electron_version:
            msg = "bb desktop manifest Electron version is missing"
            raise TypeError(msg)
        return electron_version

    @staticmethod
    def _require_electron_version(info: VersionInfo) -> str:
        return require_metadata_str(
            info.metadata,
            "electronVersion",
            context="bb release metadata",
        )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the latest immutable desktop release and its source commit."""
        payload = await self._fetch_latest_release_payload(session)
        tag_name = self._release_tag_from_payload(payload)
        version = self._normalize_release_version(tag_name)
        commit = payload.get("target_commitish")
        if (
            not isinstance(commit, str)
            or self._COMMIT_PATTERN.fullmatch(commit) is None
        ):
            msg = f"bb release {tag_name} has no immutable target commit"
            raise RuntimeError(msg)
        desktop_manifest = await fetch_json(
            session,
            github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
                "apps/desktop/package.json",
            ),
            config=self.config,
        )
        bb_app_manifest = await fetch_json(
            session,
            github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
                "packages/bb-app/package.json",
            ),
            config=self.config,
        )
        desktop_version = self._manifest_version(
            desktop_manifest,
            label="desktop",
        )
        if desktop_version != version:
            msg = (
                f"bb desktop manifest version {desktop_version!r} does not match "
                f"release version {version!r}"
            )
            raise RuntimeError(msg)
        bb_app_version = self._manifest_version(
            bb_app_manifest,
            label="bb-app",
        )
        if bb_app_version != version:
            msg = (
                f"bb bb-app manifest version {bb_app_version!r} does not match "
                f"release version {version!r}"
            )
            raise RuntimeError(msg)
        electron_version = self._electron_version(desktop_manifest)
        return VersionInfo(
            version=version,
            metadata={
                "commit": commit,
                "electronVersion": electron_version,
                "tag": tag_name,
            },
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute the pnpm closure before deciding metadata is unchanged."""
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
    ) -> SourceEntry:
        return SourceEntry.model_validate({
            "version": info.version,
            "commit": self._require_commit(info),
            "electronVersion": self._require_electron_version(info),
            "hashes": HashCollection.from_value([
                HashEntry.create("srcHash", src_hash),
                HashEntry.create("npmDepsHash", self.config.fake_hash),
            ]),
        })

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash the immutable source, then the pnpm dependency closure."""
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
                            )
                        },
                    ),
                ),
            ),
            config=self.config,
        ):
            yield event

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist both release version and exact source commit."""
        return SourceEntry.model_validate({
            "version": info.version,
            "commit": self._require_commit(info),
            "electronVersion": self._require_electron_version(info),
            "hashes": HashCollection.from_value(hashes),
        })
