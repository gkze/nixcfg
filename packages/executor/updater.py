"""Updater for the source-built Executor macOS app."""

import asyncio
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from nix_manipulator.expressions.binding import Binding
from nix_manipulator.expressions.function.call import FunctionCall
from nix_manipulator.expressions.primitive import StringPrimitive
from nix_manipulator.expressions.set import AttributeSet

from lib.bun_nix_normalizer import normalize_bun_nix_path
from lib.nix.models.sources import HashCollection, HashEntry, SourceEntry, SourceHashes
from lib.update import net as update_net
from lib.update import nix as update_nix
from lib.update.artifacts import GeneratedArtifact
from lib.update.derivation_validation import DerivationValidation
from lib.update.events import (
    EventStream,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_command_result,
    expect_str,
    raise_failed_command,
    require_value,
)
from lib.update.net import fetch_json, github_raw_url
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.nix_expr import compact_nix_expr, identifier_attr_path
from lib.update.paths import updater_dir_for
from lib.update.process import RunCommandOptions, run_command
from lib.update.updaters import (
    GitHubReleaseUpdater,
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.core import _coerce_context
from lib.update.updaters.materialization import MaterializesArtifactsMixin
from lib.update.updaters.metadata import require_metadata_str

if TYPE_CHECKING:
    import aiohttp

_PACKAGE_MANAGER_PATTERN = re.compile(r"^bun@(?P<version>[^\s]+)$")
_REQUIRED_BUN_VERSION = "1.3.11"


@register_updater
class ExecutorUpdater(MaterializesArtifactsMixin, GitHubReleaseUpdater):
    """Track immutable Executor releases and their exact source toolchain."""

    name = "executor"
    GITHUB_OWNER = "UsefulSoftwareCo"
    GITHUB_REPO = "executor"
    RELEASE_DISPLAY_NAME = "Executor"
    DARWIN_PLATFORM: ClassVar[str] = "aarch64-darwin"
    supported_platforms = (DARWIN_PLATFORM,)
    source_pins: ClassVar[dict[str, str]] = {
        "@1password/sdk-core@0.4.1-beta.1": (
            "source:patches/@1password%2Fsdk-core@0.4.1-beta.1.patch"
        ),
        "@electric-sql/pglite-socket@0.1.4": (
            "source:patches/@electric-sql%2Fpglite-socket@0.1.4.patch"
        ),
        "agents@0.17.3": "source:patches/agents@0.17.3.patch",
        "bunLockPatch": ("local:bun-lock-libsql-0.3.19-remove-self-dependency.patch"),
        "effectLspPatchVersion": "0.85.1",
        "libsql@0.3.19": "local:libsql-0.3.19-remove-self-dependency.patch",
        "libsql@0.5.29": "source:patches/libsql@0.5.29.patch",
        "postgres@3.4.9": "source:patches/postgres@3.4.9.patch",
    }
    generated_artifact_files = ("bun.lock", "bun.nix")
    derivation_validations = (
        DerivationValidation(
            installable="path:.#pkgs.{system}.{name}",
            mode="build",
        ),
    )

    @staticmethod
    def _require_electron_version(info: VersionInfo) -> str:
        try:
            return require_metadata_str(
                info.metadata,
                "electronVersion",
                context="Executor release metadata",
            )
        except TypeError as exc:
            msg = "Executor release metadata is missing an Electron version"
            raise RuntimeError(msg) from exc

    @staticmethod
    def _require_bun_version(info: VersionInfo) -> str:
        try:
            version = require_metadata_str(
                info.metadata,
                "bunVersion",
                context="Executor release metadata",
            )
        except TypeError as exc:
            msg = "Executor release metadata is missing a Bun version"
            raise RuntimeError(msg) from exc
        if version != _REQUIRED_BUN_VERSION:
            msg = f"Executor requires Bun {_REQUIRED_BUN_VERSION}, got {version!r}"
            raise RuntimeError(msg)
        return version

    @staticmethod
    def _root_bun_version(payload: object) -> str:
        if not isinstance(payload, dict):
            msg = "Executor root manifest is not a JSON object"
            raise TypeError(msg)
        package_manager = cast("dict[str, object]", payload).get("packageManager")
        if not isinstance(package_manager, str) or not package_manager:
            msg = "Executor root manifest packageManager is missing"
            raise TypeError(msg)
        match = _PACKAGE_MANAGER_PATTERN.fullmatch(package_manager)
        if match is None or match.group("version") != _REQUIRED_BUN_VERSION:
            msg = f"Executor requires Bun {_REQUIRED_BUN_VERSION}, got {package_manager!r}"
            raise RuntimeError(msg)
        return match.group("version")

    @staticmethod
    def _desktop_metadata(payload: object) -> tuple[str, str]:
        if not isinstance(payload, dict):
            msg = "Executor desktop manifest is not a JSON object"
            raise TypeError(msg)
        manifest = cast("dict[str, object]", payload)
        version = manifest.get("version")
        if not isinstance(version, str) or not version:
            msg = "Executor desktop manifest version is missing"
            raise TypeError(msg)
        dev_dependencies = manifest.get("devDependencies")
        if not isinstance(dev_dependencies, dict):
            msg = "Executor desktop manifest Electron version is missing"
            raise TypeError(msg)
        electron_version = cast("dict[str, object]", dev_dependencies).get("electron")
        if not isinstance(electron_version, str) or not electron_version:
            msg = "Executor desktop manifest Electron version is missing"
            raise TypeError(msg)
        return version, electron_version

    async def fetch_latest(self, session: aiohttp.ClientSession) -> VersionInfo:
        """Resolve the latest release tag to exact public source metadata."""
        version, tag_name, commit = await self._fetch_release_version_tag_commit(
            session
        )

        root_manifest = await fetch_json(
            session,
            github_raw_url(
                self.GITHUB_OWNER,
                self.GITHUB_REPO,
                commit,
                "package.json",
            ),
            config=self.config,
        )
        bun_version = self._root_bun_version(root_manifest)
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
        desktop_version, electron_version = self._desktop_metadata(desktop_manifest)
        if desktop_version != version:
            msg = (
                f"Executor desktop manifest version {desktop_version!r} does not "
                f"match release version {version!r}"
            )
            raise RuntimeError(msg)
        return VersionInfo(
            version=version,
            metadata={
                "bunVersion": bun_version,
                "commit": commit,
                "electronVersion": electron_version,
                "tag": tag_name,
            },
        )

    @classmethod
    def _src_expr(cls, commit: str) -> str:
        return _build_fetch_from_github_expr(
            cls.GITHUB_OWNER,
            cls.GITHUB_REPO,
            rev=commit,
            fetch_submodules=False,
        )

    @classmethod
    def _bun_lock_url(cls, commit: str) -> str:
        return github_raw_url(
            cls.GITHUB_OWNER,
            cls.GITHUB_REPO,
            commit,
            "bun.lock",
        )

    @staticmethod
    def _bun_source_url(bun_version: str) -> str:
        return (
            "https://github.com/oven-sh/bun/releases/download/"
            f"bun-v{bun_version}/bun-darwin-aarch64.zip"
        )

    @classmethod
    def _bun_source_expr(cls, bun_version: str) -> str:
        expression = FunctionCall(
            name=identifier_attr_path("pkgs", "fetchurl"),
            argument=AttributeSet(
                values=[
                    Binding(
                        name="url",
                        value=StringPrimitive(value=cls._bun_source_url(bun_version)),
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
        """Materialize the exact Bun graph, then hash the immutable source."""
        commit = self._require_commit(info)
        self._require_electron_version(info)
        bun_version = self._require_bun_version(info)
        resolved_context = _coerce_context(context)

        if not resolved_context.dry_run:
            package_dir = updater_dir_for(self.name)
            if package_dir is None:
                msg = f"Package directory not found for {self.name}"
                raise RuntimeError(msg)
            lock_bytes = await update_net.fetch_url(
                session,
                self._bun_lock_url(commit),
                request_timeout=self.config.default_timeout,
                config=self.config,
            )
            lock_text = lock_bytes.decode("utf-8")
            with tempfile.TemporaryDirectory(prefix="executor-bun2nix-") as tmpdir:
                lock_path = Path(tmpdir) / "bun.lock"
                output_path = Path(tmpdir) / "bun.nix"
                await asyncio.to_thread(lock_path.write_bytes, lock_bytes)
                command = [
                    "nix",
                    "run",
                    "path:.#pkgs.aarch64-darwin.executor.passthru.bun2nix",
                    "--",
                    "--lock-file",
                    str(lock_path),
                    "--copy-prefix",
                    "./",
                    "--output-file",
                    str(output_path),
                ]
                command_drain = ValueDrain()
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
                    "Missing bun2nix command result",
                )
                raise_failed_command("Refresh Executor Bun closure", result)
                if not output_path.is_file():
                    msg = "bun2nix did not produce bun.nix"
                    raise RuntimeError(msg)
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

        entries: list[HashEntry] = []
        for hash_type, expr, error in (
            ("srcHash", self._src_expr(commit), "Missing srcHash output"),
            (
                "sha256",
                self._bun_source_expr(bun_version),
                "Missing Bun source hash output",
            ),
        ):
            hash_drain = ValueDrain[str]()
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
            hash_value = require_value(hash_drain, error)
            entries.append(HashEntry.create(hash_type, hash_value))
        yield UpdateEvent.value(self.name, entries)

    def build_result(self, info: VersionInfo, hashes: SourceHashes) -> SourceEntry:
        """Persist the immutable source and shared Electron runtime version."""
        commit = self._require_commit(info)
        electron_version = self._require_electron_version(info)
        bun_version = self._require_bun_version(info)
        collection = HashCollection.from_value(hashes)
        if collection.entries is None:
            msg = "Executor updater expected structured source hash entries"
            raise TypeError(msg)
        bun_hashes = [
            entry for entry in collection.entries if entry.hash_type == "sha256"
        ]
        if len(bun_hashes) != 1:
            msg = f"Executor updater expected one Bun source hash, found {len(bun_hashes)}"
            raise RuntimeError(msg)
        bun_url = self._bun_source_url(bun_version)
        annotated_hashes = [
            HashEntry.create(
                entry.hash_type,
                entry.hash,
                git_dep=entry.git_dep,
                platform=entry.platform,
                url=bun_url if entry.hash_type == "sha256" else entry.url,
                urls=entry.urls,
            )
            for entry in collection.entries
        ]
        return SourceEntry.model_validate({
            "version": info.version,
            "commit": commit,
            "electronVersion": electron_version,
            "hashes": HashCollection.from_value(annotated_hashes),
            "pins": self.source_pins,
        })
