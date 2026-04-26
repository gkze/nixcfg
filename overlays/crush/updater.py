"""Updater for crush source and Go vendor hashes."""

from __future__ import annotations

from typing import TYPE_CHECKING

from lib.nix.commands.base import run_nix
from lib.update.net import fetch_github_api_paginated, fetch_url, github_raw_url
from lib.update.nix import (
    _build_fetch_from_github_expr,
    _build_flake_attr_expr,
    _build_overlay_expr,
    get_current_nix_platform,
)
from lib.update.paths import get_repo_file
from lib.update.updaters.base import (
    UpdateContext,
    VersionInfo,
    read_pinned_source_version,
    register_updater,
    stream_source_then_overlay_hashes,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater
from lib.update.updaters.metadata import GitHubReleaseMetadata

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry
    from lib.update.events import EventStream


_MIN_VERSION_PARTS = 2
_PATCHED_VERSION_PARTS = 3


def _parse_version_triplet(version: str) -> tuple[int, int, int]:
    """Parse ``major.minor[.patch]`` into a comparable tuple."""
    parts = version.strip().split(".")
    if len(parts) < _MIN_VERSION_PARTS:
        msg = f"Invalid version tuple: {version!r}"
        raise RuntimeError(msg)
    try:
        major = int(parts[0])
        minor = int(parts[1])
        patch = int(parts[2]) if len(parts) >= _PATCHED_VERSION_PARTS else 0
    except ValueError as exc:
        msg = f"Invalid numeric version tuple: {version!r}"
        raise RuntimeError(msg) from exc
    return major, minor, patch


def _extract_required_go_version(go_mod: str) -> tuple[int, int, int]:
    """Return the Go toolchain version required by a ``go.mod`` file."""
    for line in go_mod.splitlines():
        stripped = line.strip()
        if not stripped.startswith("go "):
            continue
        return _parse_version_triplet(stripped.removeprefix("go ").strip())
    msg = "Could not find Go toolchain requirement in crush go.mod"
    raise RuntimeError(msg)


@register_updater
class CrushUpdater(GitHubReleaseUpdater):
    """Resolve the newest crush release compatible with the current Go floor."""

    name = "crush"
    GITHUB_OWNER = "charmbracelet"
    GITHUB_REPO = "crush"

    @staticmethod
    def _go_version_expr(platform: str, go_attr: str) -> str:
        """Build a flake expression that returns the active Go toolchain version."""
        return _build_flake_attr_expr(
            f"path:{get_repo_file('.')}",
            "pkgs",
            platform,
            go_attr,
            "version",
            quoted_indices=(1,),
        )

    async def _resolve_supported_go_version(self) -> tuple[int, int, int]:
        """Resolve the Go version used by the repo's crush overlay toolchain."""
        platform = get_current_nix_platform()
        errors: list[str] = []
        for go_attr in ("go_latest", "go"):
            result = await run_nix(
                [
                    "nix",
                    "eval",
                    "--impure",
                    "--raw",
                    "--expr",
                    self._go_version_expr(platform, go_attr),
                ],
                check=False,
            )
            version = result.stdout.strip()
            if result.returncode == 0 and version:
                return _parse_version_triplet(version)
            details = (
                result.stderr.strip()
                or result.stdout.strip()
                or f"missing {go_attr}.version"
            )
            errors.append(f"{go_attr}: {details}")
        msg = (
            f"Failed to evaluate Go toolchain version for {self.name}: "
            f"{'; '.join(errors)}"
        )
        raise RuntimeError(msg)

    def _current_version(self) -> str:
        """Read the currently pinned crush version from ``sources.json``."""
        return read_pinned_source_version(self.name)

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Pick the newest release whose ``go.mod`` fits the update toolchain."""
        supported_go = await self._resolve_supported_go_version()
        releases = await fetch_github_api_paginated(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases",
            config=self.config,
            per_page=100,
        )

        for release in releases:
            if not isinstance(release, dict):
                msg = f"Unexpected release payload type: {type(release).__name__}"
                raise TypeError(msg)
            if release.get("draft") is True or release.get("prerelease") is True:
                continue

            tag_name = release.get("tag_name")
            if not isinstance(tag_name, str) or not tag_name:
                msg = f"Missing tag_name in release payload: {release!r}"
                raise RuntimeError(msg)

            go_mod = (
                await fetch_url(
                    session,
                    github_raw_url(
                        self.GITHUB_OWNER,
                        self.GITHUB_REPO,
                        tag_name,
                        "go.mod",
                    ),
                    config=self.config,
                )
            ).decode()
            required_go = _extract_required_go_version(go_mod)
            if required_go <= supported_go:
                return VersionInfo(
                    version=self._normalize_release_version(tag_name),
                    metadata=GitHubReleaseMetadata(tag=tag_name),
                )

        current_version = self._current_version()
        current_tag = f"v{current_version}"
        return VersionInfo(
            version=current_version,
            metadata=GitHubReleaseMetadata(tag=current_tag),
        )

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "charmbracelet",
            "crush",
            tag=f"v{version}",
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Compute source and vendor fixed-output hashes for the release."""
        _ = (session, context)

        async for event in stream_source_then_overlay_hashes(
            self.name,
            version=info.version,
            src_expr=self._src_expr(info.version),
            overlay_expr=_build_overlay_expr(self.name),
            dependency_hash_type="vendorHash",
            config=self.config,
        ):
            yield event
