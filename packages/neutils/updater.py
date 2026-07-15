"""Updater for neutils source hash and generated Zig dependency materialization."""

from __future__ import annotations

import asyncio
import io
import os
import tarfile
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    import aiohttp

    from lib.nix.models.sources import SourceEntry

from lib.nix.models.sources import HashEntry, SourceHashes
from lib.update import net as update_net
from lib.update import nix as update_nix
from lib.update import paths as update_paths
from lib.update.artifacts import GeneratedArtifact
from lib.update.events import (
    CommandResult,
    EventStream,
    StatusInfo,
    StatusKind,
    UpdateEvent,
    ValueDrain,
    drain_value_events,
    expect_command_result,
    expect_str,
    require_value,
)
from lib.update.nix import _build_fetch_from_github_expr
from lib.update.paths import get_repo_file, local_flake_url
from lib.update.process import RunCommandOptions, run_command
from lib.update.updaters import (
    UpdateContext,
    VersionInfo,
    register_updater,
)
from lib.update.updaters.github_release import GitHubReleaseUpdater


def _local_flake_url(root: Path) -> str:
    return local_flake_url(root)


@register_updater
class NeutilsUpdater(GitHubReleaseUpdater):
    """Refresh neutils source metadata and checked-in Zig dependency cache Nix."""

    name = "neutils"
    GITHUB_OWNER = "deevus"
    GITHUB_REPO = "neutils"
    materialize_when_current = True
    generated_artifact_files: ClassVar[tuple[str, ...]] = ("build.zig.zon.nix",)

    _ZON2NIX_COMMIT = "0ece5ed15107ecafbb121ad46aa85413dd40ff03"
    _ZON2NIX_FLAKE = f"github:jcollie/zon2nix/{_ZON2NIX_COMMIT}#zon2nix"
    _ZON2NIX_MAX_ATTEMPTS = 3
    _ZON2NIX_TIMEOUT_SECONDS = 180
    _ZON2NIX_TRANSIENT_MARKERS: ClassVar[tuple[str, ...]] = (
        "502 Bad Gateway",
        "Could not resolve host",
        "HttpConnectionClosing",
        "NameServerFailure",
        "ReadFailed",
        "Temporary failure in name resolution",
        "connection reset",
        "timed out",
    )

    @staticmethod
    def _src_expr(version: str) -> str:
        return _build_fetch_from_github_expr(
            "deevus",
            "neutils",
            tag=f"v{version}",
        )

    @classmethod
    def _archive_url(cls, version: str) -> str:
        return (
            f"https://github.com/{cls.GITHUB_OWNER}/{cls.GITHUB_REPO}/archive/"
            f"refs/tags/v{version}.tar.gz"
        )

    @staticmethod
    def _extract_archive(archive_bytes: bytes, destination: Path) -> Path:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            archive.extractall(destination, filter="data")
        matches = sorted(destination.glob("*/build.zig.zon"))
        if not matches:
            msg = "Could not locate build.zig.zon in neutils archive"
            raise RuntimeError(msg)
        return matches[0]

    async def _resolve_installable_path(self, installable: str) -> EventStream:
        result_drain = ValueDrain()
        async for event in drain_value_events(
            run_command(
                ["nix", "build", "--no-link", "--print-out-paths", installable],
                options=RunCommandOptions(
                    source=self.name,
                    error=f"nix build did not return output for {installable}",
                    config=self.config,
                ),
            ),
            result_drain,
            parse=expect_command_result,
        ):
            yield event
        result = require_value(
            result_drain,
            f"Missing nix build result for {installable}",
        )
        if result.returncode != 0:
            msg = (
                result.stderr.strip()
                or result.stdout.strip()
                or f"nix build failed for {installable}"
            )
            raise RuntimeError(msg)
        out_paths = [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]
        if not out_paths:
            msg = f"nix build returned no out path for {installable}"
            raise RuntimeError(msg)
        yield UpdateEvent.value(self.name, out_paths[-1])

    @classmethod
    def _is_transient_zon2nix_text(cls, output: str) -> bool:
        output = output.casefold()
        return any(
            marker.casefold() in output for marker in cls._ZON2NIX_TRANSIENT_MARKERS
        )

    @classmethod
    def _is_transient_zon2nix_failure(cls, result: CommandResult) -> bool:
        return cls._is_transient_zon2nix_text(f"{result.stderr}\n{result.stdout}")

    @staticmethod
    def _current_context_source(
        context: UpdateContext | SourceEntry | None,
    ) -> SourceEntry | None:
        return context.current if isinstance(context, UpdateContext) else context

    async def _run_zon2nix(
        self,
        *,
        zon2nix_path: str,
        build_zig_zon: Path,
        output_path: Path,
        env: dict[str, str],
    ) -> EventStream:
        command = [
            str(Path(zon2nix_path) / "bin" / "zon2nix"),
            f"--nix={output_path}",
            str(build_zig_zon),
        ]
        attempt = 1
        while True:
            await asyncio.to_thread(output_path.unlink, missing_ok=True)
            zon2nix_result_drain = ValueDrain()
            try:
                async for event in drain_value_events(
                    run_command(
                        command,
                        options=RunCommandOptions(
                            source=self.name,
                            error="zon2nix did not return output",
                            command_timeout=self._ZON2NIX_TIMEOUT_SECONDS,
                            env=env,
                            config=self.config,
                        ),
                    ),
                    zon2nix_result_drain,
                    parse=expect_command_result,
                ):
                    yield event
            except RuntimeError as exc:
                if (
                    attempt < self._ZON2NIX_MAX_ATTEMPTS
                    and self._is_transient_zon2nix_text(str(exc))
                ):
                    attempt += 1
                    yield UpdateEvent.status(
                        self.name,
                        "zon2nix hit a transient fetch failure; retrying...",
                        operation="compute_hash",
                        status=StatusInfo(
                            kind=StatusKind.RETRY,
                            value=f"attempt {attempt}/{self._ZON2NIX_MAX_ATTEMPTS}",
                        ),
                    )
                    await asyncio.sleep(max(0.0, self.config.default_retry_backoff))
                    continue
                raise
            zon2nix_result = require_value(
                zon2nix_result_drain,
                "Missing zon2nix command result",
            )
            if zon2nix_result.returncode == 0:
                return

            message = (
                zon2nix_result.stderr.strip()
                or zon2nix_result.stdout.strip()
                or "zon2nix failed"
            )
            if (
                attempt < self._ZON2NIX_MAX_ATTEMPTS
                and self._is_transient_zon2nix_failure(zon2nix_result)
            ):
                attempt += 1
                yield UpdateEvent.status(
                    self.name,
                    "zon2nix hit a transient fetch failure; retrying...",
                    operation="compute_hash",
                    status=StatusInfo(
                        kind=StatusKind.RETRY,
                        value=f"attempt {attempt}/{self._ZON2NIX_MAX_ATTEMPTS}",
                    ),
                )
                await asyncio.sleep(max(0.0, self.config.default_retry_backoff))
                continue
            raise RuntimeError(message)

    async def _render_build_zig_zon_nix(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
    ) -> EventStream:
        archive_bytes = await update_net.fetch_url(
            session,
            self._archive_url(info.version),
            request_timeout=self.config.default_timeout,
            config=self.config,
        )

        current_system = update_nix.get_current_nix_platform()
        repo_root = get_repo_file(".")
        zig_installable = (
            f"{_local_flake_url(repo_root)}#pkgs.{current_system}.zig_0_15"
        )

        with tempfile.TemporaryDirectory(prefix=f"{self.name}-zon2nix-") as tmpdir_str:
            tmpdir = Path(tmpdir_str)
            build_zig_zon = await asyncio.to_thread(
                self._extract_archive,
                archive_bytes,
                tmpdir,
            )
            output_path = tmpdir / "build.zig.zon.nix"

            zig_path_drain = ValueDrain[str]()
            async for event in drain_value_events(
                self._resolve_installable_path(zig_installable),
                zig_path_drain,
                parse=expect_str,
            ):
                yield event
            zig_path = require_value(zig_path_drain, "Missing Zig toolchain path")

            zon2nix_path_drain = ValueDrain[str]()
            async for event in drain_value_events(
                self._resolve_installable_path(self._ZON2NIX_FLAKE),
                zon2nix_path_drain,
                parse=expect_str,
            ):
                yield event
            zon2nix_path = require_value(
                zon2nix_path_drain,
                "Missing zon2nix tool path",
            )

            home_dir = tmpdir / ".home"
            cache_dir = tmpdir / ".cache"
            env = {
                "HOME": str(home_dir),
                "PATH": f"{Path(zig_path) / 'bin'}:{os.environ.get('PATH', '')}",
                "XDG_CACHE_HOME": str(cache_dir),
            }
            await asyncio.to_thread(home_dir.mkdir, parents=True, exist_ok=True)
            await asyncio.to_thread(cache_dir.mkdir, parents=True, exist_ok=True)

            async for event in self._run_zon2nix(
                zon2nix_path=zon2nix_path,
                build_zig_zon=build_zig_zon,
                output_path=output_path,
                env=env,
            ):
                yield event

            rendered = await asyncio.to_thread(output_path.read_text, encoding="utf-8")
        yield UpdateEvent.value(self.name, rendered)

    async def fetch_hashes(
        self,
        info: VersionInfo,
        session: aiohttp.ClientSession,
        *,
        context: UpdateContext | SourceEntry | None = None,
    ) -> EventStream:
        """Generate ``build.zig.zon.nix`` and compute the pinned source hash."""
        pkg_dir = update_paths.package_dir_for(self.name)
        if pkg_dir is None:
            msg = f"Package directory not found for {self.name}"
            raise RuntimeError(msg)
        artifact_path = pkg_dir / self.generated_artifact_files[0]

        yield UpdateEvent.status(
            self.name,
            f"Refreshing {artifact_path.name}...",
            operation="compute_hash",
        )

        artifact_drain = ValueDrain[str]()
        try:
            async for event in drain_value_events(
                self._render_build_zig_zon_nix(info, session),
                artifact_drain,
                parse=expect_str,
            ):
                yield event
            artifact_content = require_value(
                artifact_drain,
                f"Missing generated {artifact_path.name} content",
            )
        except RuntimeError as exc:
            current = self._current_context_source(context)
            if (
                current is not None
                and current.version == info.version
                and self._is_transient_zon2nix_text(str(exc))
                and artifact_path.exists()
            ):
                yield UpdateEvent.status(
                    self.name,
                    f"Preserving existing {artifact_path.name} after transient zon2nix failure.",
                    operation="compute_hash",
                    status=StatusInfo(
                        kind=StatusKind.PRESERVED_ARTIFACT,
                        value=str(artifact_path),
                    ),
                )
                artifact_content = await asyncio.to_thread(
                    artifact_path.read_text,
                    encoding="utf-8",
                )
            else:
                raise
        yield UpdateEvent.artifact(
            self.name,
            GeneratedArtifact.text(artifact_path, artifact_content),
        )

        src_hash_drain = ValueDrain[str]()
        async for event in drain_value_events(
            update_nix.compute_fixed_output_hash(
                self.name,
                self._src_expr(info.version),
                config=self.config,
            ),
            src_hash_drain,
            parse=expect_str,
        ):
            yield event
        src_hash = require_value(src_hash_drain, "Missing srcHash output")

        hashes: SourceHashes = [HashEntry.create("srcHash", src_hash)]
        yield UpdateEvent.value(self.name, hashes)
