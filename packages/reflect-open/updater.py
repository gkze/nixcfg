"""Updater for the source-built Reflect Open macOS application."""

import re
from typing import TYPE_CHECKING, cast

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_json, github_raw_url
from lib.update.nix import (
    _build_fetch_from_github_expr,
    _build_repo_package_attr_expr,
)
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
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

_PACKAGE_MANAGER_PATTERN = re.compile(
    r"^pnpm@(?P<version>[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)$"
)


@register_updater
class ReflectOpenUpdater(GitHubReleaseUpdater):
    """Resolve Reflect releases to immutable public source revisions."""

    name = "reflect-open"
    GITHUB_OWNER = "team-reflect"
    GITHUB_REPO = "reflect-open"
    RELEASE_DISPLAY_NAME = "Reflect"
    RESOLVE_TAG_COMMIT = True
    supported_platforms = ("aarch64-darwin",)
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            systems=("aarch64-darwin",),
            mode="build",
        ),
    )

    @staticmethod
    def _manifest_pnpm_version(payload: object) -> str:
        if not isinstance(payload, dict):
            msg = "Reflect package manifest is not a JSON object"
            raise TypeError(msg)
        package_manager = cast("dict[str, object]", payload).get("packageManager")
        if not isinstance(package_manager, str) or not package_manager:
            msg = "Reflect package manifest packageManager is missing"
            raise TypeError(msg)
        match = _PACKAGE_MANAGER_PATTERN.fullmatch(package_manager)
        if match is None:
            msg = f"Reflect requires an exact pnpm packageManager, got {package_manager!r}"
            raise RuntimeError(msg)
        return match.group("version")

    @staticmethod
    def _require_pnpm_version(info: VersionInfo) -> str:
        try:
            return require_metadata_str(
                info.metadata,
                "pnpmVersion",
                context="Reflect release metadata",
            )
        except TypeError as exc:
            msg = "Reflect release metadata is missing a pnpm version"
            raise RuntimeError(msg) from exc

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the release commit and exact package-manager toolchain."""
        info = await super().fetch_latest(session)
        commit = self._require_commit(info)
        manifest = await fetch_json(
            session,
            github_raw_url(self.GITHUB_OWNER, self.GITHUB_REPO, commit, "package.json"),
            config=self.config,
        )
        pnpm_version = self._manifest_pnpm_version(manifest)
        tag = require_metadata_str(
            info.metadata,
            "tag",
            context="Reflect release metadata",
        )
        return VersionInfo(
            version=info.version,
            metadata={
                "commit": commit,
                "pnpmVersion": pnpm_version,
                "tag": tag,
            },
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute both dependency closures before accepting release metadata."""
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
        pnpm_hash: str,
        npm_deps_hash: str,
        cargo_hash: str,
    ) -> SourceEntry:
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value([
                HashEntry.create("srcHash", src_hash),
                HashEntry.create(
                    "sha256",
                    pnpm_hash,
                    url=self._pnpm_url(self._require_pnpm_version(info)),
                ),
                HashEntry.create("npmDepsHash", npm_deps_hash),
                HashEntry.create("cargoHash", cargo_hash),
            ]),
        )

    @staticmethod
    def _pnpm_url(pnpm_version: str) -> str:
        return f"https://registry.npmjs.org/pnpm/-/pnpm-{pnpm_version}.tgz"

    @classmethod
    def _pnpm_expr(cls, pnpm_version: str) -> str:
        expression = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchurl"),
            argument=AttributeSet(
                values=[
                    Binding(
                        name="url",
                        value=StringPrimitive(value=cls._pnpm_url(pnpm_version)),
                    ),
                    Binding(
                        name="hash",
                        value=identifier_attr_path("pkgs", "lib", "fakeHash"),
                    ),
                ]
            ),
        )
        return compact_nix_expr(expression.rebuild())

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
        pnpm_version = self._require_pnpm_version(info)
        fake_hash = self.config.fake_hash
        package_file = "packages/reflect-open/package.nix"
        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    error="Missing srcHash output",
                    expr=lambda _resolved: self._src_expr(commit),
                ),
                FixedOutputHashStep(
                    hash_type="sha256",
                    error="Missing pnpm source hash output",
                    expr=lambda _resolved: self._pnpm_expr(pnpm_version),
                ),
                FixedOutputHashStep(
                    hash_type="npmDepsHash",
                    error="Missing npmDepsHash output",
                    expr=lambda resolved: _build_repo_package_attr_expr(
                        package_file,
                        ".pnpmDeps",
                        system="aarch64-darwin",
                        source_overrides={
                            self.name: self._source_override(
                                info,
                                src_hash=resolved["srcHash"],
                                pnpm_hash=resolved["sha256"],
                                npm_deps_hash=fake_hash,
                                cargo_hash=fake_hash,
                            )
                        },
                    ),
                ),
                FixedOutputHashStep(
                    hash_type="cargoHash",
                    error="Missing cargoHash output",
                    expr=lambda resolved: _build_repo_package_attr_expr(
                        package_file,
                        ".cargoDeps",
                        system="aarch64-darwin",
                        source_overrides={
                            self.name: self._source_override(
                                info,
                                src_hash=resolved["srcHash"],
                                pnpm_hash=resolved["sha256"],
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
        """Persist the release commit and complete source dependency closure."""
        pnpm_version = self._require_pnpm_version(info)
        collection = HashCollection.from_value(hashes)
        if collection.entries is None:
            msg = "Reflect updater expected structured source hash entries"
            raise TypeError(msg)
        pnpm_hashes = [
            entry for entry in collection.entries if entry.hash_type == "sha256"
        ]
        if len(pnpm_hashes) != 1:
            msg = f"Reflect updater expected one pnpm source hash, found {len(pnpm_hashes)}"
            raise RuntimeError(msg)
        pnpm_url = self._pnpm_url(pnpm_version)
        annotated_hashes = [
            HashEntry.create(
                entry.hash_type,
                entry.hash,
                git_dep=entry.git_dep,
                platform=entry.platform,
                url=pnpm_url if entry.hash_type == "sha256" else entry.url,
                urls=entry.urls,
            )
            for entry in collection.entries
        ]
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            hashes=HashCollection.from_value(annotated_hashes),
        )
