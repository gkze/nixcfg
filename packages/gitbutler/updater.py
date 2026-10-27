"""Updater for GitButler source metadata and crate2nix artifacts."""

import asyncio
import re
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from lib.nix.models.sources import HashEntry, SourceHashes
from lib.update import nix as update_nix
from lib.update.events import (
    EventSink,
    ignore_event,
)
from lib.update.locked_source import locked_github_source, resolve_locked_source
from lib.update.npm_semver import require_npm_version_matches_spec
from lib.update.updaters import (
    Crate2NixArtifactsMixin,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.flake_backed import FlakeInputHashUpdater
from lib.update.updaters.metadata import require_metadata_str
from lib.update.updaters.node_compatibility import (
    resolve_nixpkgs_nodejs_for_engine,
    resolve_nixpkgs_package_version,
)

if TYPE_CHECKING:
    import aiohttp


_MAX_MANIFEST_BYTES = 1024 * 1024
_PNPM_PACKAGE_MANAGER_PATTERN = re.compile(
    r"^pnpm@(?P<version>(?P<major>0|[1-9][0-9]*)"
    r"\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*))"
    r"(?:\+sha512\.[A-Za-z0-9_+/=-]+)?$"
)


@register_updater
class GitButlerUpdater(Crate2NixArtifactsMixin, FlakeInputHashUpdater):
    """Track the GitButler release input, pnpm cache, and crate2nix output."""

    name = "gitbutler"
    input_name = "gitbutler"
    hash_type: Literal["npmDepsHash"] = "npmDepsHash"
    hash_attr_path = ".frontend.pnpmDeps"
    # Fetcher v4 store paths differ by OS. Each native prepare certifies only
    # its own hash; candidate merge unions the complementary entries.
    platform_specific = True
    native_only = True
    supported_platforms = ("aarch64-darwin", "x86_64-linux")
    GITHUB_OWNER: ClassVar[str] = "gitbutlerapp"
    GITHUB_REPO: ClassVar[str] = "gitbutler"
    TOOLCHAIN_PIN_KEYS: ClassVar[tuple[str, ...]] = (
        "nodeEngine",
        "nodejsAttr",
        "nodejsVersion",
        "packageManager",
        "pnpmAttr",
        "pnpmVersion",
    )

    @classmethod
    async def _toolchain_pins(
        cls,
        manifest: object,
        *,
        command_timeout: float,
    ) -> dict[str, str]:
        """Derive and validate the Nix toolchain from upstream's package manifest."""
        if not isinstance(manifest, dict):
            msg = "GitButler package manifest is not a JSON object"
            raise TypeError(msg)
        package = cast("dict[str, object]", manifest)
        engines = package.get("engines")
        if not isinstance(engines, dict):
            msg = "GitButler package manifest Node engines are missing"
            raise TypeError(msg)
        node_engine = cast("dict[str, object]", engines).get("node")

        package_manager = package.get("packageManager")
        if not isinstance(package_manager, str) or not package_manager:
            msg = "GitButler package manifest packageManager is missing"
            raise TypeError(msg)
        pnpm_match = _PNPM_PACKAGE_MANAGER_PATTERN.fullmatch(package_manager)
        if pnpm_match is None:
            msg = (
                "GitButler package manifest must select an exact pnpm@<version>, "
                f"got {package_manager!r}"
            )
            raise RuntimeError(msg)
        pnpm_required_version = pnpm_match.group("version")
        pnpm_attr = f"pnpm_{pnpm_match.group('major')}"

        nodejs, pnpm_version = await asyncio.gather(
            resolve_nixpkgs_nodejs_for_engine(
                node_engine,
                command_timeout=command_timeout,
                source_name="GitButler",
            ),
            resolve_nixpkgs_package_version(
                pnpm_attr,
                command_timeout=command_timeout,
                source_name="GitButler",
            ),
        )
        require_npm_version_matches_spec(
            pnpm_version,
            f"^{pnpm_required_version}",
            context="GitButler nixpkgs pnpm",
        )
        return {
            "nodeEngine": nodejs.engine,
            "nodejsAttr": nodejs.attribute,
            "nodejsVersion": nodejs.version,
            "packageManager": package_manager,
            "pnpmAttr": pnpm_attr,
            "pnpmVersion": pnpm_version,
        }

    def source_pins_for(self, info: VersionInfo) -> dict[str, str]:
        """Persist the complete manifest-derived toolchain contract."""
        return {
            key: require_metadata_str(
                info.metadata,
                key,
                context="GitButler release metadata",
            )
            for key in self.TOOLCHAIN_PIN_KEYS
        }

    def _platform_targets(self, current_platform: str) -> tuple[str, ...]:
        """Return only this host.

        A foreign builder cannot materialize the other platform's pnpm store.
        """
        return (current_platform,)

    def _retained_hash(self, entry: HashEntry, platform: str) -> bool:
        """Keep hashes this run did not recompute."""
        if entry.hash_type != self.hash_type:
            return True
        return entry.platform not in {None, platform}

    async def _is_latest(
        self,
        context: UpdateContext,
        info: VersionInfo,
    ) -> bool:
        """Trust this platform's certificate instead of the merged drvHash.

        Parallel prepares last-win the scalar ``drvHash``. Comparing this host
        to that value rehashed one platform on every Update after the merge.
        """
        current = context.current
        if (
            current is None
            or current.version != info.version
            or current.pins != self.source_pins_for(info)
            or current.electron_version != self.build_result(info, []).electron_version
        ):
            return False
        platform = update_nix.get_current_nix_platform()
        if not self._hash_is_usable(self._existing_platform_hashes(context).get(platform)):
            return False
        try:
            await self._prepare_hash_probes(
                self.build_result(info, []),
                context=context,
            )
        except RuntimeError:
            return False
        certified = (current.platform_drv_hashes or {}).get(platform)
        return certified == context.drv_fingerprints.get(platform)

    async def fetch_latest(
        self, session: aiohttp.ClientSession, *, context: UpdateContext
    ) -> VersionInfo:
        """Resolve the release and its toolchain from one immutable input tree."""
        _ = context
        _ = session
        node = self._resolve_flake_node(VersionInfo(version="ignored"))
        ref = node.original.ref if node.original is not None else None
        if not isinstance(ref, str) or not ref.startswith("release/"):
            msg = "gitbutler flake input must be pinned to a release/<version> ref"
            raise RuntimeError(msg)
        locked_github = locked_github_source(
            node,
            context="gitbutler flake input",
            expected=(self.GITHUB_OWNER, self.GITHUB_REPO),
        )
        commit = locked_github.rev
        source = await resolve_locked_source(
            node,
            context="GitButler flake input",
            command_timeout=self.config.default_subprocess_timeout,
        )
        manifest = await source.read_json(
            "package.json",
            max_bytes=_MAX_MANIFEST_BYTES,
            description="package manifest",
        )
        toolchain_pins = await self._toolchain_pins(
            manifest,
            command_timeout=self.config.default_subprocess_timeout,
        )
        return VersionInfo(
            version=ref.removeprefix("release/"),
            metadata={
                "node": node.model_dump(mode="json"),
                "commit": commit,
                **toolchain_pins,
            },
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext,
        emit: EventSink = ignore_event,
    ) -> SourceHashes:
        """Refresh crate2nix artifacts, then record this host's pnpm hash."""
        _ = session
        await self.stream_materialized_artifacts(emit=emit)
        context.prepared_probes.clear()
        context.drv_fingerprints.clear()
        context.drv_fingerprint = None
        hash_value = await self._compute_hash(info, context=context, emit=emit)
        platform = update_nix.get_current_nix_platform()
        preserved: list[HashEntry] = []
        if context.current is not None:
            preserved.extend(
                entry
                for entry in context.current.hashes.entries or ()
                if self._retained_hash(entry, platform)
            )
            for key, value in (context.current.platform_drv_hashes or {}).items():
                if key != platform:
                    context.drv_fingerprints[key] = value
        return [
            *preserved,
            HashEntry.create(self.hash_type, hash_value, platform=platform),
        ]
