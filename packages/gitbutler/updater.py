"""Updater for GitButler source metadata and crate2nix artifacts."""

import asyncio
import re
from typing import TYPE_CHECKING, ClassVar, Literal, cast

from lib.nix.models.sources import HashEntry
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_str,
    require_value,
)
from lib.update.locked_source import resolve_locked_source
from lib.update.npm_semver import require_npm_version_matches_spec
from lib.update.updaters import (
    Crate2NixArtifactsMixin,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.flake_backed import FlakeInputHashUpdater
from lib.update.updaters.metadata import FlakeInputMetadata, require_metadata_str
from lib.update.updaters.node_compatibility import (
    resolve_nixpkgs_nodejs_for_engine,
    resolve_nixpkgs_package_version,
)

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry


_IMMUTABLE_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
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

    async def fetch_latest(
        self,
        session: aiohttp.ClientSession,
    ) -> VersionInfo:
        """Resolve the release and its toolchain from one immutable input tree."""
        _ = session
        node = self._resolve_flake_node(VersionInfo(version="ignored"))
        ref = node.original.ref if node.original is not None else None
        if not isinstance(ref, str) or not ref.startswith("release/"):
            msg = "gitbutler flake input must be pinned to a release/<version> ref"
            raise RuntimeError(msg)
        locked = node.locked
        commit = locked.rev if locked is not None else None
        if (
            locked is None
            or locked.type != "github"
            or locked.owner != self.GITHUB_OWNER
            or locked.repo != self.GITHUB_REPO
            or not isinstance(commit, str)
            or _IMMUTABLE_COMMIT_PATTERN.fullmatch(commit) is None
        ):
            msg = (
                "gitbutler flake input must resolve to an immutable "
                f"{self.GITHUB_OWNER}/{self.GITHUB_REPO} commit"
            )
            raise RuntimeError(msg)
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
                **FlakeInputMetadata(node=node, commit=commit).to_dict(),
                **toolchain_pins,
            },
        )

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Refresh crate2nix artifacts before computing the pnpm hash."""
        _ = (session, context)
        async for event in self.stream_materialized_artifacts():
            yield event

        hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            self._compute_hash(info),
            hash_drain,
            parse=expect_str,
        ):
            yield event
        hash_value = require_value(hash_drain, "Missing npmDepsHash output")
        yield UpdateEvent.value(
            self.name,
            [HashEntry.create(self.hash_type, hash_value)],
        )
