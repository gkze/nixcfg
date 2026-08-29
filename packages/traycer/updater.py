"""Pinned updater for the source-built Traycer macOS package."""

import asyncio
import base64
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from lib.bun_nix_normalizer import normalize_bun_nix_path
from lib.import_utils import load_module_from_path
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry
from lib.update import net as update_net
from lib.update import nix as update_nix
from lib.update.artifacts import GeneratedArtifact
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    CommandResult,
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_command_result,
    expect_str,
    raise_failed_command,
    require_value,
)
from lib.update.net import fetch_github_api, fetch_json, github_raw_url
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.paths import updater_dir_for
from lib.update.process import RunCommandOptions, run_command
from lib.update.updaters import GitHubReleaseUpdater, VersionInfo, register_updater
from lib.update.updaters.core import _coerce_context
from lib.update.updaters.materialization import MaterializesArtifactsMixin

if TYPE_CHECKING:
    from collections.abc import Callable

    import aiohttp

    from lib.nix.models.sources import SourceHashes
    from lib.update.updaters import UpdateContext

_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_DIGEST_PATTERN = re.compile(r"^sha256:(?P<hex>[0-9a-f]{64})$")


def _validate_generated_bun_graph(lock_path: Path, nix_path: Path) -> None:
    """Validate generated Bun inputs through Traycer's exact graph oracle."""
    validator_module = load_module_from_path(
        Path(__file__).with_name("validate_bun_graph.py"),
        "_updater_pkg.traycer_bun_graph",
    )
    validator = cast(
        "Callable[[Path, Path], object]",
        validator_module.validate_bun_graph,
    )
    validator(lock_path, nix_path)


@register_updater
class TraycerUpdater(MaterializesArtifactsMixin, GitHubReleaseUpdater):
    """Revalidate Traycer's documented 1.2.0 mixed-provenance exception."""

    name = "traycer"
    GITHUB_OWNER = "traycerai"
    GITHUB_REPO = "traycer"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    generated_artifact_files = ("bun.lock", "bun.nix")
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            mode="build",
        ),
    )

    PINNED_VERSION = "1.2.0"
    PINNED_PUBLIC_COMMIT = "85ee596fffab4c9aa72b6bddc73a0020839ed5ae"
    UNVERIFIED_PRIVATE_BUILD_COMMIT = "5198516d395fedc25c5f702263a3e4a72b05a655"
    PINNED_BUN_VERSION = "1.3.12"
    PINNED_ELECTRON_VERSION = "42.9.1"
    BUN_OWNER = "oven-sh"
    BUN_REPO = "bun"
    BUN_ASSET_NAME = "bun-darwin-aarch64.zip"
    BUN_ASSET_SIZE = 22_264_502
    BUN_ASSET_HASH = "sha256-bEu4fdAT7RqNahbjV6PQlJWf1VMLTXBh9/NoDDx86hw="
    HOST_ARCHIVE_NAME = "traycer-host-macos-arm64.tar.gz"
    HOST_ARCHIVE_SIZE = 76_162_681
    HOST_ARCHIVE_HASH = "sha256-Zs+B55nYJRRm407BO2FZAHy7EGncCR1tx14Qoo1UaTk="
    HOST_SIGNATURE_NAME = f"{HOST_ARCHIVE_NAME}.minisig"
    HOST_SIGNATURE_SIZE = 293
    HOST_SIGNATURE_HASH = "sha256-VW+v5cO8X2oqe85V9sssbGGxOalHpy5E9l29ncojQ50="
    HOST_MINISIGN_PUBLIC_KEY = (
        "RWSEfvU5EZoZYQTQUOVHeQFv3poThl1VM7FZLkNQr0Zu0FyL2x+u2O2l"
    )
    HOST_MINISIGN_KEY_ID = "847ef539119a1961"
    HOST_MINISIGN_TRUSTED_COMMENT = "traycer-host 1.2.0 darwin-arm64"

    @staticmethod
    def _require_object(payload: object, *, context: str) -> dict[str, object]:
        if not isinstance(payload, dict):
            msg = f"{context} is not a JSON object"
            raise TypeError(msg)
        return cast("dict[str, object]", payload)

    @staticmethod
    def _require_string(
        payload: dict[str, object],
        key: str,
        *,
        context: str,
    ) -> str:
        value = payload.get(key)
        if not isinstance(value, str):
            msg = f"{context} has invalid {key}"
            raise TypeError(msg)
        return value

    @classmethod
    def _asset(
        cls,
        release: dict[str, object],
        name: str,
        *,
        context: str,
    ) -> dict[str, object]:
        assets = release.get("assets")
        if not isinstance(assets, list):
            msg = f"{context} has invalid assets"
            raise TypeError(msg)
        matches: list[dict[str, object]] = []
        for asset in assets:
            if not isinstance(asset, dict):
                continue
            candidate = cast("dict[str, object]", asset)
            if candidate.get("name") == name:
                matches.append(candidate)
        if len(matches) != 1:
            msg = f"{context} must contain exactly one {name!r} asset"
            raise RuntimeError(msg)
        return matches[0]

    @staticmethod
    def _sri_from_github_digest(digest: object, *, context: str) -> str:
        if not isinstance(digest, str):
            msg = f"{context} has no immutable SHA-256 digest"
            raise TypeError(msg)
        match = _DIGEST_PATTERN.fullmatch(digest)
        if match is None:
            msg = f"{context} has an invalid SHA-256 digest"
            raise RuntimeError(msg)
        encoded = base64.b64encode(bytes.fromhex(match.group("hex"))).decode("ascii")
        return f"sha256-{encoded}"

    @classmethod
    def _validate_unverified_provenance(
        cls,
        payload: object,
        *,
        component: str,
    ) -> dict[str, object]:
        """Validate unsigned release notes without treating them as identity proof."""
        provenance = cls._require_object(
            payload,
            context=f"Traycer {component} unverified release provenance",
        )
        expected = {
            "component": component,
            "version": cls.PINNED_VERSION,
            "releaseChannel": "stable",
            "buildRepo": "traycerai/traycer-internal",
            "buildSha": cls.UNVERIFIED_PRIVATE_BUILD_COMMIT,
            "ossRepo": "traycerai/traycer",
            "ossRef": cls.PINNED_PUBLIC_COMMIT,
        }
        if component == "cli":
            expected["supportedHostVersion"] = cls.PINNED_VERSION
        for key, value in expected.items():
            if provenance.get(key) != value:
                msg = (
                    f"Traycer {component} unverified release provenance {key} "
                    "drifted: "
                    f"expected {value!r}, got {provenance.get(key)!r}"
                )
                raise RuntimeError(msg)
        return provenance

    @classmethod
    def _validate_root_manifest(cls, payload: object) -> None:
        manifest = cls._require_object(payload, context="Traycer root manifest")
        package_manager = manifest.get("packageManager")
        if package_manager != f"bun@{cls.PINNED_BUN_VERSION}":
            msg = (
                f"Traycer requires Bun {cls.PINNED_BUN_VERSION}, "
                f"got {package_manager!r}"
            )
            raise RuntimeError(msg)
        catalog = manifest.get("catalog")
        if not isinstance(catalog, dict):
            msg = "Traycer root manifest has no Electron catalog"
            raise TypeError(msg)
        electron = cast("dict[str, object]", catalog).get("electron")
        if electron != f"^{cls.PINNED_ELECTRON_VERSION}":
            msg = (
                f"Traycer requires Electron {cls.PINNED_ELECTRON_VERSION}, "
                f"got {electron!r}"
            )
            raise RuntimeError(msg)

    async def _release(
        self,
        session: aiohttp.ClientSession,
        component: str,
    ) -> dict[str, object]:
        tag = f"{component}-v{self.PINNED_VERSION}"
        payload = await fetch_github_api(
            session,
            f"repos/{self.GITHUB_OWNER}/{self.GITHUB_REPO}/releases/tags/{tag}",
            config=self.config,
        )
        release = self._require_object(payload, context=f"Traycer {component} release")
        if release.get("tag_name") != tag:
            msg = f"Traycer {component} release tag drifted from {tag}"
            raise RuntimeError(msg)
        return release

    async def _bun_release(
        self,
        session: aiohttp.ClientSession,
    ) -> dict[str, object]:
        tag = f"bun-v{self.PINNED_BUN_VERSION}"
        payload = await fetch_github_api(
            session,
            f"repos/{self.BUN_OWNER}/{self.BUN_REPO}/releases/tags/{tag}",
            config=self.config,
        )
        release = self._require_object(payload, context="Bun release")
        if release.get("tag_name") != tag:
            msg = f"Bun release tag drifted from {tag}"
            raise RuntimeError(msg)
        return release

    async def _unverified_provenance(
        self,
        session: aiohttp.ClientSession,
        release: dict[str, object],
        component: str,
    ) -> dict[str, object]:
        asset = self._asset(
            release,
            "release-provenance.json",
            context=f"Traycer {component} release",
        )
        url = self._require_string(
            asset,
            "browser_download_url",
            context=f"Traycer {component} release provenance asset",
        )
        payload = await fetch_json(session, url, config=self.config)
        return self._validate_unverified_provenance(payload, component=component)

    @classmethod
    def _validated_release_asset(
        cls,
        release: dict[str, object],
        *,
        owner: str,
        repo: str,
        tag: str,
        name: str,
        size: int,
        expected_hash: str,
        context: str,
    ) -> tuple[str, str]:
        asset = cls._asset(release, name, context=context)
        url = cls._require_string(
            asset,
            "browser_download_url",
            context=f"{context} {name} asset",
        )
        expected_url = (
            f"https://github.com/{owner}/{repo}/releases/download/{tag}/{name}"
        )
        if url != expected_url:
            msg = f"{context} {name} URL drifted from the pinned release"
            raise RuntimeError(msg)
        if asset.get("size") != size:
            msg = f"{context} {name} size drifted from {size}"
            raise RuntimeError(msg)
        sri_hash = cls._sri_from_github_digest(
            asset.get("digest"),
            context=f"{context} {name} asset",
        )
        if sri_hash != expected_hash:
            msg = f"{context} {name} digest drifted from the pinned identity"
            raise RuntimeError(msg)
        return url, sri_hash

    @classmethod
    def _validated_host_asset(
        cls,
        release: dict[str, object],
        *,
        name: str,
        size: int,
        expected_hash: str,
    ) -> tuple[str, str]:
        return cls._validated_release_asset(
            release,
            owner=cls.GITHUB_OWNER,
            repo=cls.GITHUB_REPO,
            tag=f"host-v{cls.PINNED_VERSION}",
            name=name,
            size=size,
            expected_hash=expected_hash,
            context="Traycer host release",
        )

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Revalidate the three coordinated pinned releases and exact inputs."""
        bun = await self._bun_release(session)
        desktop = await self._release(session, "desktop")
        cli = await self._release(session, "cli")
        host = await self._release(session, "host")
        await self._unverified_provenance(session, desktop, "desktop")
        await self._unverified_provenance(session, cli, "cli")
        await self._unverified_provenance(session, host, "host")

        root_manifest = await fetch_json(
            session,
            github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                self.PINNED_PUBLIC_COMMIT,
                "package.json",
            ),
            config=self.config,
        )
        self._validate_root_manifest(root_manifest)

        bun_url, bun_hash = self._validated_release_asset(
            bun,
            owner=self.BUN_OWNER,
            repo=self.BUN_REPO,
            tag=f"bun-v{self.PINNED_BUN_VERSION}",
            name=self.BUN_ASSET_NAME,
            size=self.BUN_ASSET_SIZE,
            expected_hash=self.BUN_ASSET_HASH,
            context="Bun release",
        )
        host_archive_url, host_archive_hash = self._validated_host_asset(
            host,
            name=self.HOST_ARCHIVE_NAME,
            size=self.HOST_ARCHIVE_SIZE,
            expected_hash=self.HOST_ARCHIVE_HASH,
        )
        host_signature_url, host_signature_hash = self._validated_host_asset(
            host,
            name=self.HOST_SIGNATURE_NAME,
            size=self.HOST_SIGNATURE_SIZE,
            expected_hash=self.HOST_SIGNATURE_HASH,
        )
        return VersionInfo(
            version=self.PINNED_VERSION,
            metadata={
                "bunHash": bun_hash,
                "bunSize": str(self.BUN_ASSET_SIZE),
                "bunUrl": bun_url,
                "bunVersion": self.PINNED_BUN_VERSION,
                "commit": self.PINNED_PUBLIC_COMMIT,
                "electronVersion": self.PINNED_ELECTRON_VERSION,
                "hostArchiveHash": host_archive_hash,
                "hostArchiveSize": str(self.HOST_ARCHIVE_SIZE),
                "hostArchiveUrl": host_archive_url,
                "hostMinisignKeyId": self.HOST_MINISIGN_KEY_ID,
                "hostMinisignPublicKey": self.HOST_MINISIGN_PUBLIC_KEY,
                "hostMinisignTrustedComment": self.HOST_MINISIGN_TRUSTED_COMMENT,
                "hostSignatureHash": host_signature_hash,
                "hostSignatureSize": str(self.HOST_SIGNATURE_SIZE),
                "hostSignatureUrl": host_signature_url,
                "unverifiedPrivateBuildCommit": (self.UNVERIFIED_PRIVATE_BUILD_COMMIT),
            },
        )

    async def _is_latest(
        self,
        context: UpdateContext | SourceEntry | None,
        info: VersionInfo,
    ) -> bool:
        """Revalidate this exception instead of tracking latest semver."""
        _ = (context, info)
        return False

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Hash the exact public source and preserve validated Host identities."""
        self._validate_info(info)
        resolved_context = _coerce_context(context)
        if not resolved_context.dry_run:
            package_dir = updater_dir_for(self.name)
            if package_dir is None:
                msg = f"Package directory not found for {self.name}"
                raise RuntimeError(msg)
            lock_bytes = await update_net.fetch_url(
                session,
                github_raw_url(
                    self.GITHUB_OWNER,
                    self.GITHUB_REPO,
                    self.PINNED_PUBLIC_COMMIT,
                    "bun.lock",
                ),
                request_timeout=self.config.default_timeout,
                config=self.config,
            )
            lock_text = lock_bytes.decode("utf-8")
            with tempfile.TemporaryDirectory(prefix="traycer-bun2nix-") as tmpdir:
                lock_path = Path(tmpdir) / "bun.lock"
                output_path = Path(tmpdir) / "bun.nix"
                await asyncio.to_thread(lock_path.write_bytes, lock_bytes)
                command = [
                    "nix",
                    "run",
                    "path:.#pkgs.aarch64-darwin.bun2nix",
                    "--",
                    "--lock-file",
                    str(lock_path),
                    "--copy-prefix",
                    "./",
                    "--output-file",
                    str(output_path),
                ]
                command_drain = ValueDrain[CommandResult]()
                async for event in drain_value_events(
                    run_command(
                        command,
                        options=RunCommandOptions(
                            source=self.name,
                            error="bun2nix did not return a command result",
                            config=self.config,
                        ),
                    ),
                    command_drain,
                    parse=expect_command_result,
                ):
                    yield event
                result = require_value(
                    command_drain,
                    "Missing Traycer bun2nix command result",
                )
                raise_failed_command("Refresh Traycer Bun closure", result)
                if not output_path.is_file():
                    msg = "bun2nix did not produce bun.nix"
                    raise RuntimeError(msg)
                await asyncio.to_thread(
                    _validate_generated_bun_graph,
                    lock_path,
                    output_path,
                )
                await asyncio.to_thread(normalize_bun_nix_path, output_path)
                bun_nix = await asyncio.to_thread(
                    output_path.read_text,
                    encoding="utf-8",
                )
            yield UpdateEvent.artifact(
                self.name,
                [
                    GeneratedArtifact.text(package_dir / "bun.lock", lock_text),
                    GeneratedArtifact.text(package_dir / "bun.nix", bun_nix),
                ],
            )

        hash_drain = ValueDrain[str]()
        expr = _build_fetch_from_github_expr(
            self.GITHUB_OWNER,
            self.GITHUB_REPO,
            rev=self.PINNED_PUBLIC_COMMIT,
            fetch_submodules=False,
        )
        async for event in drain_value_events(
            update_nix.compute_fixed_output_hash(
                self.name,
                expr,
                config=self.config,
            ),
            hash_drain,
            parse=expect_str,
        ):
            yield event
        src_hash = require_value(hash_drain, "Missing Traycer srcHash output")
        yield UpdateEvent.value(
            self.name,
            [
                HashEntry.create("srcHash", src_hash),
                HashEntry.create(
                    "sha256",
                    self._metadata_string(info, "bunHash"),
                    platform=self.DARWIN_PLATFORM,
                    url=self._metadata_string(info, "bunUrl"),
                ),
                HashEntry.create(
                    "sha256",
                    self._metadata_string(info, "hostArchiveHash"),
                    platform=self.DARWIN_PLATFORM,
                    url=self._metadata_string(info, "hostArchiveUrl"),
                ),
                HashEntry.create(
                    "sha256",
                    self._metadata_string(info, "hostSignatureHash"),
                    platform=self.DARWIN_PLATFORM,
                    url=self._metadata_string(info, "hostSignatureUrl"),
                ),
            ],
        )

    @classmethod
    def _metadata_string(cls, info: VersionInfo, key: str) -> str:
        metadata = cls._require_object(info.metadata, context="Traycer metadata")
        return cls._require_string(metadata, key, context="Traycer metadata")

    @classmethod
    def _validate_info(cls, info: VersionInfo) -> None:
        expected = {
            "bunHash": cls.BUN_ASSET_HASH,
            "bunSize": str(cls.BUN_ASSET_SIZE),
            "bunVersion": cls.PINNED_BUN_VERSION,
            "electronVersion": cls.PINNED_ELECTRON_VERSION,
            "hostArchiveHash": cls.HOST_ARCHIVE_HASH,
            "hostArchiveSize": str(cls.HOST_ARCHIVE_SIZE),
            "hostMinisignKeyId": cls.HOST_MINISIGN_KEY_ID,
            "hostMinisignPublicKey": cls.HOST_MINISIGN_PUBLIC_KEY,
            "hostMinisignTrustedComment": cls.HOST_MINISIGN_TRUSTED_COMMENT,
            "hostSignatureHash": cls.HOST_SIGNATURE_HASH,
            "hostSignatureSize": str(cls.HOST_SIGNATURE_SIZE),
            "unverifiedPrivateBuildCommit": cls.UNVERIFIED_PRIVATE_BUILD_COMMIT,
        }
        if info.version != cls.PINNED_VERSION:
            msg = f"Traycer metadata version drifted from {cls.PINNED_VERSION}"
            raise RuntimeError(msg)
        if info.commit != cls.PINNED_PUBLIC_COMMIT:
            msg = "Traycer metadata is missing the pinned public commit"
            raise RuntimeError(msg)
        for key, value in expected.items():
            actual = cls._metadata_string(info, key)
            if actual != value:
                msg = f"Traycer metadata {key} drifted from {value!r}"
                raise RuntimeError(msg)
        expected_urls = {
            "bunUrl": (
                f"https://github.com/{cls.BUN_OWNER}/{cls.BUN_REPO}/releases/"
                f"download/bun-v{cls.PINNED_BUN_VERSION}/{cls.BUN_ASSET_NAME}"
            ),
            "hostArchiveUrl": (
                f"https://github.com/{cls.GITHUB_OWNER}/{cls.GITHUB_REPO}/releases/"
                f"download/host-v{cls.PINNED_VERSION}/{cls.HOST_ARCHIVE_NAME}"
            ),
            "hostSignatureUrl": (
                f"https://github.com/{cls.GITHUB_OWNER}/{cls.GITHUB_REPO}/releases/"
                f"download/host-v{cls.PINNED_VERSION}/{cls.HOST_SIGNATURE_NAME}"
            ),
        }
        for key, value in expected_urls.items():
            if cls._metadata_string(info, key) != value:
                msg = f"Traycer metadata {key} drifted from the pinned release"
                raise RuntimeError(msg)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist exact source, Host URLs, hashes, and shared Electron version."""
        self._validate_info(info)
        return SourceEntry.model_validate({
            "version": self.PINNED_VERSION,
            "commit": self.PINNED_PUBLIC_COMMIT,
            "electronVersion": self.PINNED_ELECTRON_VERSION,
            "urls": {
                "bun": self._metadata_string(info, "bunUrl"),
                "hostArchive": self._metadata_string(info, "hostArchiveUrl"),
                "hostSignature": self._metadata_string(info, "hostSignatureUrl"),
            },
            "hashes": HashCollection.from_value(hashes),
        })
