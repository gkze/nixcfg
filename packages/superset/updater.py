"""Updater for Superset Desktop release assets and source-build metadata."""

import re
from typing import TYPE_CHECKING, ClassVar

from lib import json_utils
from lib.bun_nix_normalizer import normalize_bun_nix
from lib.nix.models.sources import HashEntry
from lib.update import flake as update_flake
from lib.update import process as update_process
from lib.update.bun_lock import parse_bun_lock_text
from lib.update.bun_toolchain import (
    bun_release_urls,
    bun_runtime_hash_entries,
    require_bun_package_manager,
)
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_hash_mapping,
    require_value,
)
from lib.update.generated_artifact_commands import stream_command_materialized_artifacts
from lib.update.net import fetch_json, fetch_url, github_raw_url
from lib.update.nix import _build_package_path_attr_expr
from lib.update.npm_semver import require_npm_version_matches_spec
from lib.update.updaters import register_updater
from lib.update.updaters.core import _coerce_context
from lib.update.updaters.github_release import GitHubReleaseAssetURLsUpdater
from lib.update.updaters.materialization import MaterializesArtifactsMixin
from lib.update.updaters.metadata import VersionInfo, require_metadata_str

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry, SourceHashes
    from lib.update.events import EventStream
    from lib.update.updaters import UpdateContext

_BUN_ARTIFACTS = (
    "packages/superset/bun.lock",
    "packages/superset/bun.nix",
)
_BUN_ARTIFACT_DETAIL = "Superset Bun lock artifacts"
_ELECTRON_VERSION = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?$")
_SOURCE_BUILD_SYSTEMS = (
    "aarch64-darwin",
    "x86_64-linux",
)


@register_updater
class SupersetUpdater(MaterializesArtifactsMixin, GitHubReleaseAssetURLsUpdater):
    """Track Superset Desktop AppImage URL and hash for Linux."""

    name = "superset"
    aggregate_into = ("electron-runtimes",)
    input_name = "superset"
    generated_artifact_files = ("bun.lock", "bun.nix")
    GITHUB_OWNER = "superset-sh"
    GITHUB_REPO = "superset"
    TAG_PREFIX = "desktop-v"
    supported_platforms = _SOURCE_BUILD_SYSTEMS
    derivation_validations = (
        DerivationValidation(
            installable=".#pkgs.{system}.{name}.drvPath",
            systems=_SOURCE_BUILD_SYSTEMS,
        ),
    )

    PLATFORMS: ClassVar[dict[str, str]] = {
        "x86_64-linux": "x86_64",
    }

    def _asset_name(self, version: str, platform_value: str) -> str:
        return f"superset-{version}-{platform_value}.AppImage"

    def _fallback_url(self, version: str, platform_value: str) -> str:
        return (
            "https://github.com/superset-sh/superset/releases/download/"
            f"{self.TAG_PREFIX}{version}/{self._asset_name(version, platform_value)}"
        )

    def _normalize_release_version(self, tag_name: str) -> str:
        if not tag_name.startswith(self.TAG_PREFIX):
            msg = f"Unexpected Superset release tag format: {tag_name}"
            raise RuntimeError(msg)

        version = tag_name.removeprefix(self.TAG_PREFIX)
        if not version:
            msg = f"Missing version segment in Superset release tag: {tag_name}"
            raise RuntimeError(msg)
        return version

    def _missing_asset_message(self, expected_name: str, tag_name: str) -> str:
        return (
            "Could not find Superset desktop release asset "
            f"{expected_name!r} in tag {tag_name}"
        )

    @classmethod
    def _locked_source_commit(cls, tag_name: str) -> str:
        """Return the immutable Superset input commit for ``tag_name``."""
        node = update_flake.get_flake_input_node(cls.input_name)
        original = node.original
        locked = node.locked
        if (
            original is None
            or original.type != "github"
            or original.owner != cls.GITHUB_OWNER
            or original.repo != cls.GITHUB_REPO
            or original.ref != tag_name
        ):
            msg = (
                "Superset flake input must track the same immutable desktop "
                f"release tag as the binary asset: expected {tag_name!r}"
            )
            raise RuntimeError(msg)
        if (
            locked is None
            or locked.type != "github"
            or locked.owner != cls.GITHUB_OWNER
            or locked.repo != cls.GITHUB_REPO
            or locked.rev is None
            or cls._COMMIT_PATTERN.fullmatch(locked.rev) is None
        ):
            msg = "Superset flake input has no immutable GitHub source commit"
            raise RuntimeError(msg)
        return locked.rev

    @staticmethod
    def _electron_spec(manifest: object) -> tuple[str, str]:
        desktop = json_utils.as_object_dict(
            manifest,
            context="Superset desktop package manifest",
        )
        version = json_utils.get_required_str(
            desktop,
            "version",
            context="Superset desktop package manifest",
        )
        dev_dependencies = json_utils.as_object_dict(
            desktop.get("devDependencies"),
            context="Superset desktop devDependencies",
        )
        electron_spec = json_utils.get_required_str(
            dev_dependencies,
            "electron",
            context="Superset desktop devDependencies",
        )
        return version, electron_spec

    @staticmethod
    def _locked_electron_version(lock: object) -> tuple[str, str]:
        lock_data = json_utils.as_object_dict(lock, context="Superset bun.lock")
        workspaces = json_utils.as_object_dict(
            lock_data.get("workspaces"),
            context="Superset bun.lock workspaces",
        )
        desktop = json_utils.as_object_dict(
            workspaces.get("apps/desktop"),
            context="Superset bun.lock desktop workspace",
        )
        dev_dependencies = json_utils.as_object_dict(
            desktop.get("devDependencies"),
            context="Superset bun.lock desktop devDependencies",
        )
        workspace_spec = json_utils.get_required_str(
            dev_dependencies,
            "electron",
            context="Superset bun.lock desktop devDependencies",
        )

        packages = json_utils.as_object_dict(
            lock_data.get("packages"),
            context="Superset bun.lock packages",
        )
        electron = json_utils.as_object_list(
            packages.get("electron"),
            context="Superset bun.lock Electron package",
        )
        resolution = electron[0] if electron else None
        if not isinstance(resolution, str) or not resolution.startswith("electron@"):
            msg = "Superset bun.lock has no exact Electron package resolution"
            raise TypeError(msg)
        version = resolution.removeprefix("electron@")
        if _ELECTRON_VERSION.fullmatch(version) is None:
            msg = f"Superset bun.lock has invalid Electron version {version!r}"
            raise RuntimeError(msg)
        return workspace_spec, version

    @classmethod
    def _validate_release_metadata(
        cls,
        *,
        version: str,
        desktop_manifest: object,
        bun_lock: object,
    ) -> str:
        manifest_version, manifest_spec = cls._electron_spec(desktop_manifest)
        if manifest_version != version:
            msg = (
                f"Superset desktop manifest version {manifest_version!r} does not "
                f"match release version {version!r}"
            )
            raise RuntimeError(msg)
        workspace_spec, electron_version = cls._locked_electron_version(bun_lock)
        if workspace_spec != manifest_spec:
            msg = (
                f"Superset Electron manifest spec {manifest_spec!r} does not match "
                f"bun.lock workspace spec {workspace_spec!r}"
            )
            raise RuntimeError(msg)
        return require_npm_version_matches_spec(
            electron_version,
            manifest_spec,
            context="Superset Electron",
        )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve one internally coherent immutable desktop release."""
        payload = await self._fetch_latest_release_payload(session)
        tag_name = self._release_tag_from_payload(payload)
        version = self._normalize_release_version(tag_name)
        asset_urls = self._asset_urls_from_payload(
            payload,
            version=version,
            tag_name=tag_name,
        )
        release_commit = await self._resolve_release_tag_commit(session, tag_name)
        locked_commit = self._locked_source_commit(tag_name)
        if locked_commit != release_commit:
            msg = (
                f"Superset source input commit {locked_commit!r} does not match "
                f"release tag commit {release_commit!r}"
            )
            raise RuntimeError(msg)

        def raw(path: str) -> str:
            return github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                locked_commit,
                path,
            )

        desktop_manifest = await fetch_json(
            session,
            raw("apps/desktop/package.json"),
            config=self.config,
        )
        root_manifest = await fetch_json(
            session,
            raw("package.json"),
            config=self.config,
        )
        bun_lock_payload = await fetch_url(
            session,
            raw("bun.lock"),
            config=self.config,
        )
        try:
            bun_lock_text = bun_lock_payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            msg = "Superset bun.lock is not UTF-8 text"
            raise ValueError(msg) from exc
        electron_version = self._validate_release_metadata(
            version=version,
            desktop_manifest=desktop_manifest,
            bun_lock=parse_bun_lock_text(
                bun_lock_text,
                context=f"Superset release {tag_name} bun.lock",
            ),
        )
        bun_version = require_bun_package_manager(
            root_manifest,
            context="Superset root package manifest",
        )
        return VersionInfo(
            version=version,
            metadata={
                "asset_urls": asset_urls,
                "commit": release_commit,
                "bunVersion": bun_version,
                "electronVersion": electron_version,
                "tag": tag_name,
            },
        )

    def source_pins_for(self, info: VersionInfo) -> dict[str, str]:
        """Persist the root manifest's exact Bun runtime identity."""
        return {
            "bunVersion": require_metadata_str(
                info.metadata,
                "bunVersion",
                context="Superset release metadata",
            )
        }

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist release asset, source commit, and source-build runtime."""
        electron_version = require_metadata_str(
            info.metadata,
            "electronVersion",
            context="Superset release metadata",
        )
        return self._build_result_with_urls(
            info,
            hashes,
            self._platform_urls(info),
            commit=self._require_commit(info),
        ).model_copy(
            update={"electron_version": electron_version},
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash exact Bun and binary inputs before regenerating Bun artifacts."""
        context = _coerce_context(context)
        asset_hash_drain = ValueDrain[dict[str, str]]()
        async for event in drain_value_events(
            super().fetch_hashes(info, session, context=context),
            asset_hash_drain,
            parse=expect_hash_mapping,
        ):
            yield event
        asset_hashes = require_value(
            asset_hash_drain,
            "Missing Superset AppImage hashes",
        )
        asset_entries = [
            HashEntry.create("sha256", hash_value, platform=platform)
            for platform, hash_value in sorted(asset_hashes.items())
        ]

        bun_version = self.source_pins_for(info)["bunVersion"]
        bun_urls = bun_release_urls(bun_version, _SOURCE_BUILD_SYSTEMS)
        bun_hash_drain = ValueDrain[dict[str, str]]()
        async for event in drain_value_events(
            update_process.compute_url_hashes(
                self.name,
                bun_urls.values(),
                config=self.config,
            ),
            bun_hash_drain,
            parse=expect_hash_mapping,
        ):
            yield event
        bun_entries = bun_runtime_hash_entries(
            bun_version,
            _SOURCE_BUILD_SYSTEMS,
            require_value(bun_hash_drain, "Missing Superset Bun runtime hashes"),
        )
        hashes = [*bun_entries, *asset_entries]
        candidate = self.build_result(info, hashes)
        update_script_expr = _build_package_path_attr_expr(
            self.name,
            ".passthru.updateScript",
            source_overrides={self.name: candidate},
            fake_hashes=False,
        )

        async def emit_hashes() -> EventStream:
            yield UpdateEvent.value(self.name, hashes)

        async for event in stream_command_materialized_artifacts(
            self.name,
            args=["nix", "run", "--impure", "--expr", update_script_expr],
            artifact_paths=_BUN_ARTIFACTS,
            inner=emit_hashes(),
            dry_run=context.dry_run,
            config=self.config,
            detail=_BUN_ARTIFACT_DETAIL,
            artifact_normalizers={_BUN_ARTIFACTS[1]: normalize_bun_nix},
        ):
            yield event
