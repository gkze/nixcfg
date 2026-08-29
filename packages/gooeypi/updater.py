"""Updater for the source-built GooeyPi macOS app."""

from typing import TYPE_CHECKING, ClassVar, cast

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update.net import fetch_json, github_raw_url
from lib.update.nix import (
    _build_fetch_from_github_call,
    _build_fetch_from_github_expr,
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


@register_updater
class GooeyPiUpdater(GitHubReleaseUpdater):
    """Track immutable GooeyPi releases and their npm dependency closure."""

    name = "gooeypi"
    GITHUB_OWNER = "am-will"
    GITHUB_REPO = "gooey-pi"
    RELEASE_DISPLAY_NAME = "GooeyPi"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    APP_ID: ClassVar[str] = "app.gooeypi.desktop"
    NODE_ENGINE: ClassVar[str] = ">=24.15.0"
    NPM_ENGINE: ClassVar[str] = ">=12.0.2"
    PACKAGE_MANAGER: ClassVar[str] = "npm@12.0.2"
    supported_platforms = (DARWIN_PLATFORM,)

    @staticmethod
    def _manifest_version(payload: object, *, label: str) -> str:
        if not isinstance(payload, dict):
            msg = f"GooeyPi {label} manifest is not a JSON object"
            raise TypeError(msg)
        version = cast("dict[str, object]", payload).get("version")
        if not isinstance(version, str) or not version:
            msg = f"GooeyPi {label} manifest version is missing"
            raise TypeError(msg)
        return version

    @classmethod
    def _validate_app_id(cls, package_manifest: object) -> None:
        package = cast("dict[str, object]", package_manifest)
        build = package.get("build")
        if not isinstance(build, dict):
            msg = "GooeyPi package manifest build configuration is missing"
            raise TypeError(msg)
        app_id = cast("dict[str, object]", build).get("appId")
        if app_id != cls.APP_ID:
            msg = f"GooeyPi package appId {app_id!r} does not match {cls.APP_ID!r}"
            raise RuntimeError(msg)

    @staticmethod
    def _electron_spec(package_manifest: object) -> str:
        package = cast("dict[str, object]", package_manifest)
        dependencies = package.get("devDependencies")
        if not isinstance(dependencies, dict):
            msg = "GooeyPi package Electron dependency is missing"
            raise TypeError(msg)
        electron = cast("dict[str, object]", dependencies).get("electron")
        if not isinstance(electron, str) or not electron:
            msg = "GooeyPi package Electron dependency is missing"
            raise TypeError(msg)
        return electron

    @classmethod
    def _validate_build_toolchain(cls, package_manifest: object) -> str:
        package = cast("dict[str, object]", package_manifest)
        engines = package.get("engines")
        if not isinstance(engines, dict):
            msg = "GooeyPi package build toolchain is missing"
            raise TypeError(msg)
        node_engine = cast("dict[str, object]", engines).get("node")
        if node_engine != cls.NODE_ENGINE:
            msg = (
                f"GooeyPi package Node engine {node_engine!r} does not match "
                f"the source-build contract {cls.NODE_ENGINE!r}"
            )
            raise RuntimeError(msg)
        npm_engine = cast("dict[str, object]", engines).get("npm")
        if npm_engine != cls.NPM_ENGINE:
            msg = (
                f"GooeyPi package npm engine {npm_engine!r} does not match "
                f"the source-build contract {cls.NPM_ENGINE!r}"
            )
            raise RuntimeError(msg)
        package_manager = package.get("packageManager")
        if package_manager != cls.PACKAGE_MANAGER:
            msg = (
                f"GooeyPi package manager {package_manager!r} does not match "
                f"the source-build contract {cls.PACKAGE_MANAGER!r}"
            )
            raise RuntimeError(msg)
        return cls.PACKAGE_MANAGER.removeprefix("npm@")

    @staticmethod
    def _locked_electron_version(lock_manifest: object) -> str:
        lock = cast("dict[str, object]", lock_manifest)
        packages = lock.get("packages")
        if not isinstance(packages, dict):
            msg = "GooeyPi package lock has no package mapping"
            raise TypeError(msg)
        electron = cast("dict[str, object]", packages).get("node_modules/electron")
        if not isinstance(electron, dict):
            msg = "GooeyPi package lock has no exact Electron package"
            raise TypeError(msg)
        version = cast("dict[str, object]", electron).get("version")
        if not isinstance(version, str) or not version:
            msg = "GooeyPi package lock has no exact Electron version"
            raise TypeError(msg)
        return version

    @classmethod
    def _validate_release_manifests(
        cls,
        *,
        version: str,
        package_manifest: object,
        lock_manifest: object,
    ) -> tuple[str, str]:
        package_version = cls._manifest_version(package_manifest, label="package")
        if package_version != version:
            msg = (
                f"GooeyPi package manifest version {package_version!r} does not "
                f"match release version {version!r}"
            )
            raise RuntimeError(msg)
        lock_version = cls._manifest_version(lock_manifest, label="lock")
        if lock_version != version:
            msg = (
                f"GooeyPi lock manifest version {lock_version!r} does not match "
                f"release version {version!r}"
            )
            raise RuntimeError(msg)

        cls._validate_app_id(package_manifest)
        electron_spec = cls._electron_spec(package_manifest)
        npm_version = cls._validate_build_toolchain(package_manifest)
        electron_version = cls._locked_electron_version(lock_manifest)
        if electron_spec not in {electron_version, f"^{electron_version}"}:
            msg = (
                f"GooeyPi package Electron spec {electron_spec!r} does not resolve "
                f"exactly to locked version {electron_version!r}"
            )
            raise RuntimeError(msg)
        return electron_version, npm_version

    @staticmethod
    def _require_electron_version(info: VersionInfo) -> str:
        return require_metadata_str(
            info.metadata,
            "electronVersion",
            context="GooeyPi release metadata",
        )

    @staticmethod
    def _require_npm_version(info: VersionInfo) -> str:
        return require_metadata_str(
            info.metadata,
            "npmVersion",
            context="GooeyPi release metadata",
        )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the latest release to one immutable, internally coherent tree."""
        version, tag_name, commit = await self._fetch_release_version_tag_commit(
            session
        )

        package_manifest = await fetch_json(
            session,
            github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
                "package.json",
            ),
            config=self.config,
        )
        lock_manifest = await fetch_json(
            session,
            github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
                "package-lock.json",
            ),
            config=self.config,
        )
        electron_version, npm_version = self._validate_release_manifests(
            version=version,
            package_manifest=package_manifest,
            lock_manifest=lock_manifest,
        )
        return VersionInfo(
            version=version,
            metadata={
                "commit": commit,
                "electronVersion": electron_version,
                "npmVersion": npm_version,
                "tag": tag_name,
            },
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute both fixed-output closures even when metadata is unchanged."""
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

    @classmethod
    def _npm_deps_expr(
        cls,
        *,
        commit: str,
        version: str,
        src_hash: str,
    ) -> str:
        """Hash npm dependencies without requiring the package derivation to exist."""
        expression = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchNpmDeps"),
            argument=AttributeSet(
                values=[
                    Binding(
                        name="name",
                        value=StringPrimitive(value=f"{cls.name}-{version}-npm-deps"),
                    ),
                    Binding(
                        name="src",
                        value=_build_fetch_from_github_call(
                            cls.GITHUB_OWNER,
                            cls.GITHUB_REPO,
                            rev=commit,
                            hash_value=src_hash,
                            fetch_submodules=False,
                        ),
                    ),
                    Binding(
                        name="hash",
                        value=identifier_attr_path("pkgs", "lib", "fakeHash"),
                    ),
                ]
            ),
        )
        return compact_nix_expr(expression.rebuild())

    @staticmethod
    def _npm_cli_url(npm_version: str) -> str:
        return f"https://registry.npmjs.org/npm/-/npm-{npm_version}.tgz"

    @classmethod
    def _npm_cli_expr(cls, npm_version: str) -> str:
        expression = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchurl"),
            argument=AttributeSet(
                values=[
                    Binding(
                        name="url",
                        value=StringPrimitive(value=cls._npm_cli_url(npm_version)),
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
        """Hash the immutable source tree, then its package-lock closure."""
        _ = (session, context)
        commit = self._require_commit(info)
        self._require_electron_version(info)
        npm_version = self._require_npm_version(info)
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
                    expr=lambda resolved: self._npm_deps_expr(
                        commit=commit,
                        version=info.version,
                        src_hash=resolved["srcHash"],
                    ),
                ),
                FixedOutputHashStep(
                    hash_type="sha256",
                    error="Missing npm CLI source hash output",
                    expr=lambda _resolved: self._npm_cli_expr(npm_version),
                ),
            ),
            config=self.config,
        ):
            yield event

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the release, immutable commit, Electron runtime, and closures."""
        commit = self._require_commit(info)
        electron_version = self._require_electron_version(info)
        npm_version = self._require_npm_version(info)
        collection = HashCollection.from_value(hashes)
        if collection.entries is None:
            msg = "GooeyPi updater expected structured source hash entries"
            raise TypeError(msg)
        npm_hashes = [
            entry for entry in collection.entries if entry.hash_type == "sha256"
        ]
        if len(npm_hashes) != 1:
            msg = f"GooeyPi updater expected one npm CLI hash, found {len(npm_hashes)}"
            raise RuntimeError(msg)
        npm_url = self._npm_cli_url(npm_version)
        annotated_hashes = [
            HashEntry.create(
                entry.hash_type,
                entry.hash,
                git_dep=entry.git_dep,
                platform=entry.platform,
                url=npm_url if entry.hash_type == "sha256" else entry.url,
                urls=entry.urls,
            )
            for entry in collection.entries
        ]
        return SourceEntry.model_validate({
            "version": info.version,
            "commit": commit,
            "electronVersion": electron_version,
            "hashes": annotated_hashes,
        })
