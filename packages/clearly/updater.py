"""Updater for the source-built Clearly macOS app."""

import urllib.parse
from typing import TYPE_CHECKING, ClassVar, cast

import yaml
from packaging.version import InvalidVersion, Version

from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update.derivation_validation import DerivationValidation
from lib.update.net import fetch_github_api_paginated, fetch_url, github_raw_url
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
from lib.update.updaters.metadata import metadata_get

if TYPE_CHECKING:
    import aiohttp

    from lib.update.events import EventStream

_GITHUB_REPO_PATH_COMPONENTS = 2


@register_updater
class ClearlyUpdater(GitHubReleaseUpdater):
    """Pin Clearly releases and their SwiftPM closure to immutable commits."""

    name = "clearly"
    GITHUB_OWNER = "Shpigford"
    GITHUB_REPO = "clearly"
    RELEASE_DISPLAY_NAME = "Clearly"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}",
            mode="build",
        ),
    )

    _DEPENDENCY_NAMES: ClassVar[tuple[str, ...]] = (
        "cmark-gfm",
        "KeyboardShortcuts",
    )

    @staticmethod
    def _github_repo_from_url(url: str) -> tuple[str, str]:
        parsed = urllib.parse.urlparse(url)
        path_parts = parsed.path.removesuffix(".git").strip("/").split("/")
        if (
            parsed.scheme != "https"
            or parsed.hostname != "github.com"
            or len(path_parts) != _GITHUB_REPO_PATH_COMPONENTS
        ):
            msg = f"Clearly has an unsupported Swift dependency URL: {url}"
            raise TypeError(msg)
        return path_parts[0], path_parts[1]

    @classmethod
    def _dependency_requirements(
        cls, project_yaml: bytes
    ) -> dict[str, tuple[str, str]]:
        try:
            project = yaml.safe_load(project_yaml)
        except yaml.YAMLError as exc:
            msg = "Could not parse Clearly project.yml"
            raise RuntimeError(msg) from exc
        if not isinstance(project, dict):
            msg = "Clearly project.yml has no package mapping"
            raise TypeError(msg)
        packages = project.get("packages")
        if not isinstance(packages, dict):
            msg = "Clearly project.yml has no package mapping"
            raise TypeError(msg)

        requirements: dict[str, tuple[str, str]] = {}
        for name in cls._DEPENDENCY_NAMES:
            package = packages.get(name)
            if not isinstance(package, dict):
                msg = f"Clearly project.yml is missing the {name} Swift dependency"
                raise TypeError(msg)
            url = package.get("url")
            minimum = package.get("from")
            if not isinstance(url, str) or not isinstance(minimum, str):
                msg = f"Clearly {name} must use a SwiftPM 'url' plus 'from' requirement"
                raise TypeError(msg)
            requirements[name] = (url, minimum)
        return requirements

    async def _resolve_dependency_url(
        self,
        session: aiohttp.ClientSession,
        *,
        dependency: str,
        repo_url: str,
        minimum: str,
    ) -> str:
        owner, repo = self._github_repo_from_url(repo_url)
        try:
            lower_bound = Version(minimum)
        except InvalidVersion as exc:
            msg = f"Clearly {dependency} has invalid SwiftPM version {minimum!r}"
            raise RuntimeError(msg) from exc
        upper_bound = Version(f"{lower_bound.major + 1}.0.0")

        tags = await fetch_github_api_paginated(
            session,
            f"repos/{owner}/{repo}/tags",
            config=self.config,
            item_limit=500,
        )
        selected: tuple[Version, str] | None = None
        for tag in tags:
            if not isinstance(tag, dict):
                continue
            tag_name = tag.get("name")
            commit = tag.get("commit")
            if not isinstance(tag_name, str) or not isinstance(commit, dict):
                continue
            commit_sha = commit.get("sha")
            if (
                not isinstance(commit_sha, str)
                or self._COMMIT_PATTERN.fullmatch(commit_sha) is None
            ):
                continue
            try:
                candidate = Version(tag_name.removeprefix("v"))
            except InvalidVersion:
                continue
            if (
                candidate.is_prerelease
                or candidate < lower_bound
                or candidate >= upper_bound
            ):
                continue
            if selected is None or candidate > selected[0]:
                selected = (candidate, commit_sha)

        if selected is None:
            msg = (
                f"Could not resolve Clearly {dependency} in SwiftPM range "
                f">={lower_bound}, <{upper_bound}"
            )
            raise RuntimeError(msg)
        return f"https://github.com/{owner}/{repo}/archive/{selected[1]}.tar.gz"

    async def _resolve_dependency_urls(
        self,
        session: aiohttp.ClientSession,
        project_yaml: bytes,
    ) -> dict[str, str]:
        requirements = self._dependency_requirements(project_yaml)
        urls: dict[str, str] = {}
        for dependency in self._DEPENDENCY_NAMES:
            repo_url, minimum = requirements[dependency]
            urls[dependency] = await self._resolve_dependency_url(
                session,
                dependency=dependency,
                repo_url=repo_url,
                minimum=minimum,
            )
        return urls

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the release tag, source commit, and exact Swift dependencies."""
        version, tag_name, commit = await self._fetch_release_version_tag_commit(
            session
        )

        project_yaml = await fetch_url(
            session,
            github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
                "project.yml",
            ),
            request_timeout=self.config.default_timeout,
            config=self.config,
        )
        dependency_urls = await self._resolve_dependency_urls(session, project_yaml)
        return VersionInfo(
            version=version,
            metadata={
                "commit": commit,
                "dependency_urls": dependency_urls,
                "tag": tag_name,
            },
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Recompute the source closure before accepting matching metadata."""
        _ = (context, info)
        return False

    @classmethod
    def _dependency_urls_from_info(cls, info: VersionInfo) -> dict[str, str]:
        raw_urls = metadata_get(
            info.metadata,
            "dependency_urls",
            context="Clearly release metadata",
        )
        if not isinstance(raw_urls, dict):
            msg = "Clearly release metadata is missing Swift dependency URLs"
            raise TypeError(msg)
        raw_urls_map = cast("dict[str, object]", raw_urls)
        urls: dict[str, str] = {}
        for dependency in cls._DEPENDENCY_NAMES:
            url = raw_urls_map.get(dependency)
            if not isinstance(url, str) or not url:
                msg = f"Clearly release metadata is missing the {dependency} URL"
                raise RuntimeError(msg)
            urls[dependency] = url
        return urls

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
        dependency_urls: dict[str, str],
        src_hash: str,
    ) -> SourceEntry:
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            urls=dependency_urls,
            hashes=HashCollection.from_value([
                HashEntry.create("srcHash", src_hash),
                HashEntry.create("vendorHash", self.config.fake_hash),
            ]),
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash the release source, then its normalized SwiftPM closure."""
        _ = (session, context)
        commit = self._require_commit(info)
        dependency_urls = self._dependency_urls_from_info(info)
        async for event in stream_fixed_output_hashes(
            self.name,
            steps=(
                FixedOutputHashStep(
                    hash_type="srcHash",
                    error="Missing srcHash output",
                    expr=lambda _resolved: self._src_expr(commit),
                ),
                FixedOutputHashStep(
                    hash_type="vendorHash",
                    error="Missing vendorHash output",
                    expr=lambda resolved: _build_package_path_attr_expr(
                        self.name,
                        ".swiftDeps",
                        system=self.DARWIN_PLATFORM,
                        source_overrides={
                            self.name: self._source_override(
                                info,
                                dependency_urls=dependency_urls,
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
        """Persist release, source commit, dependency URLs, and both hashes."""
        return SourceEntry(
            version=info.version,
            commit=self._require_commit(info),
            urls=self._dependency_urls_from_info(info),
            hashes=HashCollection.from_value(hashes),
        )
