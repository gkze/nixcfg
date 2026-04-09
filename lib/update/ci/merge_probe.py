"""Probe whether a shared flake-backed hash diverges across CI platforms."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import inspect
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Annotated

import typer

from lib.nix.models.sources import HashCollection, HashEntry, HashType, SourceEntry
from lib.update import io as update_io
from lib.update.ci import merge_sources
from lib.update.ci._cli import make_main, make_typer_app
from lib.update.config import resolve_active_config
from lib.update.events import ValueDrain, drain_value_events, expect_str, require_value
from lib.update.nix import compute_overlay_hash
from lib.update.paths import SOURCES_FILE_NAME, get_repo_root, package_file_map_in
from lib.update.updaters import UPDATERS, FlakeInputHashUpdater, ensure_updaters_loaded

CI_PLATFORMS = ("aarch64-darwin", "x86_64-linux", "aarch64-linux")


@dataclasses.dataclass(frozen=True)
class ProbeTarget:
    """One shared-hash source that can be audited across platforms."""

    source: str
    hash_type: HashType


@dataclasses.dataclass(frozen=True)
class ProbePlan:
    """Planned merge-probe work for one or more sources."""

    targets: tuple[ProbeTarget, ...]
    platforms: tuple[str, ...]

    @property
    def hash_builds(self) -> int:
        """Return the total number of per-platform hash computations."""
        return len(self.targets) * len(self.platforms)

    @property
    def merge_runs(self) -> int:
        """Return the total number of merge runs required."""
        return len(self.targets)


def _log(message: str) -> None:
    sys.stderr.write(f"[merge-probe] {message}\n")


def _run_checked(
    args: list[str],
    *,
    cwd: Path | None = None,
) -> None:
    result = subprocess.run(  # noqa: S603
        args,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode == 0:
        return
    details = [f"Command failed ({result.returncode}): {' '.join(args)}"]
    if result.stdout.strip():
        details.append(f"stdout:\n{result.stdout.strip()}")
    if result.stderr.strip():
        details.append(f"stderr:\n{result.stderr.strip()}")
    raise RuntimeError("\n\n".join(details))


def _overlay_seed_root(seed_root: Path, workspace: Path) -> None:
    if seed_root.is_dir():
        shutil.copytree(seed_root, workspace, dirs_exist_ok=True)
        return
    if seed_root.is_file():
        destination = workspace / seed_root.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(seed_root, destination)
        return
    msg = f"Seed root does not exist: {seed_root}"
    raise RuntimeError(msg)


def _instantiate_updater(source: str) -> object:
    ensure_updaters_loaded()
    updater_cls = UPDATERS.get(source)
    if updater_cls is None:
        msg = f"Unknown source: {source}"
        raise RuntimeError(msg)
    init_params = inspect.signature(updater_cls.__init__).parameters
    if "config" in init_params:
        return updater_cls(config=resolve_active_config(None))
    return updater_cls()


def _get_updater(source: str) -> FlakeInputHashUpdater:
    updater = _instantiate_updater(source)
    if not isinstance(updater, FlakeInputHashUpdater):
        msg = f"Source {source!r} is not a flake-backed hash updater ({type(updater).__name__})"
        raise TypeError(msg)
    if updater.platform_specific:
        msg = (
            f"Source {source!r} is already platform-specific; "
            "merge-probe only targets shared-hash sources"
        )
        raise RuntimeError(msg)
    return updater


def _shared_probe_targets() -> tuple[ProbeTarget, ...]:
    """Return all shared flake-backed hash sources that this probe can audit."""
    ensure_updaters_loaded()
    targets: list[ProbeTarget] = []
    for source in sorted(UPDATERS):
        updater = _instantiate_updater(source)
        if not isinstance(updater, FlakeInputHashUpdater):
            continue
        if updater.platform_specific:
            continue
        targets.append(ProbeTarget(source=source, hash_type=updater.hash_type))
    return tuple(targets)


def _resolve_plan(
    *,
    source: str | None,
    all_sources: bool,
    platforms: tuple[str, ...],
) -> ProbePlan:
    """Resolve requested CLI options into a probe plan."""
    if all_sources == (source is not None):
        msg = "Specify exactly one of --source or --all"
        raise RuntimeError(msg)
    if all_sources:
        return ProbePlan(targets=_shared_probe_targets(), platforms=platforms)
    updater = _get_updater(source or "")
    return ProbePlan(
        targets=(ProbeTarget(source=source or "", hash_type=updater.hash_type),),
        platforms=platforms,
    )


def _plan_text(plan: ProbePlan) -> str:
    """Render a dry-run summary for a probe plan."""
    lines = [
        "Merge probe dry run",
        f"- sources: {len(plan.targets)}",
        f"- platforms: {len(plan.platforms)} ({', '.join(plan.platforms)})",
        f"- hash computations: {plan.hash_builds}",
        f"- merge runs: {plan.merge_runs}",
    ]
    if plan.targets:
        lines.append("- targets:")
        lines.extend(
            f"  - {target.source} ({target.hash_type})" for target in plan.targets
        )
    return "\n".join(lines)


def _load_source_entry(source: str, workspace: Path) -> tuple[Path, SourceEntry]:
    source_path = package_file_map_in(workspace, SOURCES_FILE_NAME).get(source)
    if source_path is None:
        msg = f"No {SOURCES_FILE_NAME} found for source {source!r} under {workspace}"
        raise RuntimeError(msg)
    with source_path.open(encoding="utf-8") as handle:
        entry = SourceEntry.model_validate(json.load(handle))
    return source_path, entry


def _entry_with_hash(
    entry: SourceEntry,
    *,
    hash_type: HashType,
    hash_value: str,
) -> SourceEntry:
    return entry.model_copy(
        update={
            "hashes": HashCollection(entries=[HashEntry.create(hash_type, hash_value)])
        }
    )


async def _compute_hash(
    source: str,
    *,
    platform: str,
    workspace: Path,
) -> str:
    drain = ValueDrain[str]()
    async for _event in drain_value_events(
        compute_overlay_hash(source, system=platform, repo_root=str(workspace)),
        drain,
        parse=expect_str,
    ):
        pass
    return require_value(drain, f"Missing hash output for {source} on {platform}")


async def _compute_hashes(
    source: str,
    *,
    platforms: tuple[str, ...],
    workspace: Path,
) -> dict[str, str]:
    async def _run_one(platform: str) -> tuple[str, str]:
        return platform, await _compute_hash(
            source, platform=platform, workspace=workspace
        )

    results = await asyncio.gather(*(_run_one(platform) for platform in platforms))
    return dict(results)


def _write_artifact_entry(
    *,
    artifact_root: Path,
    workspace: Path,
    source_path: Path,
    entry: SourceEntry,
) -> None:
    destination = artifact_root / source_path.relative_to(workspace)
    update_io.atomic_write_json(destination, entry.to_dict(), mkdir=True)


app = make_typer_app(
    help_text=(
        "Compute a shared flake-backed hash independently on each CI platform "
        "and run the normal sources merge step."
    ),
    no_args_is_help=False,
)


@app.callback(invoke_without_command=True)
def cli(
    *,
    source: Annotated[
        str | None,
        typer.Option(
            "-S",
            "--source",
            help="Source name to probe.",
        ),
    ] = None,
    all_sources: Annotated[
        bool,
        typer.Option(
            "-a",
            "--all",
            help="Probe all shared flake-backed hash sources.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "-n",
            "--dry-run",
            help="Print the planned work and exit without building.",
        ),
    ] = False,
    revision: Annotated[
        str,
        typer.Option(
            "-r",
            "--revision",
            help="Git revision to materialize in the temp worktree.",
        ),
    ] = "HEAD",
    seed_root: Annotated[
        Path | None,
        typer.Option(
            "-s",
            "--seed-root",
            help=(
                "Optional file or directory to overlay onto the temp worktree "
                "before computing hashes (for example a downloaded "
                "flake-lock artifact)."
            ),
        ),
    ] = None,
    keep_artifacts: Annotated[
        bool,
        typer.Option(
            "-k",
            "--keep-artifacts",
            help="Keep the temp worktree and per-platform artifact roots.",
        ),
    ] = False,
    platform: Annotated[
        list[str] | None,
        typer.Option(
            "-p",
            "--platform",
            help="Platform to probe. Repeat to override the default CI platform set.",
        ),
    ] = None,
) -> None:
    """Compute per-platform hashes for one or more shared-hash sources."""
    platforms = tuple(platform) if platform else CI_PLATFORMS
    try:
        plan = _resolve_plan(
            source=source,
            all_sources=all_sources,
            platforms=platforms,
        )
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc

    if dry_run:
        typer.echo(_plan_text(plan))
        raise typer.Exit(code=0)

    if len(plan.targets) > 1:
        msg = "--all currently supports --dry-run only"
        raise typer.BadParameter(msg)

    raise typer.Exit(
        code=run(
            source=plan.targets[0].source,
            revision=revision,
            seed_root=seed_root,
            keep_artifacts=keep_artifacts,
            platforms=plan.platforms,
        )
    )


def run(
    *,
    source: str,
    revision: str,
    seed_root: Path | None,
    keep_artifacts: bool,
    platforms: tuple[str, ...],
) -> int:
    """Run the merge probe in a detached temp worktree."""
    temp_root = Path(tempfile.mkdtemp(prefix="nixcfg-merge-probe-"))
    repo_root = get_repo_root()
    workspace_path = temp_root / "repo"
    keep = keep_artifacts
    status = 0

    _log(f"Temp root: {temp_root}")
    try:
        _run_checked(
            ["git", "worktree", "add", "--detach", str(workspace_path), revision],
            cwd=repo_root,
        )
        workspace = workspace_path.resolve()
        if seed_root is not None:
            _log(f"Overlaying seed root: {seed_root}")
            _overlay_seed_root(seed_root, workspace)

        updater = _get_updater(source)
        source_path, current_entry = _load_source_entry(source, workspace)
        roots: list[str] = []

        _log("Computing platform hashes concurrently for: " + ", ".join(platforms))
        hashes = asyncio.run(
            _compute_hashes(source, platforms=platforms, workspace=workspace)
        )

        for current_platform in platforms:
            hash_value = hashes[current_platform]
            _log(f"  {current_platform}: {hash_value}")
            artifact_root = temp_root / f"sources-{current_platform}"
            artifact_entry = _entry_with_hash(
                current_entry,
                hash_type=updater.hash_type,
                hash_value=hash_value,
            )
            _write_artifact_entry(
                artifact_root=artifact_root,
                workspace=workspace,
                source_path=source_path,
                entry=artifact_entry,
            )
            roots.append(str(artifact_root))

        _log("Running sources merge...")
        status = merge_sources.run(roots=roots, output_root=workspace)
        if status == 0:
            _log("Merge OK")
        else:
            _log(f"Merge returned {status}")
            keep = True
    except Exception as exc:  # noqa: BLE001
        _log(str(exc))
        keep = True
        status = 1
    finally:
        if keep:
            _log(f"Artifacts kept at: {temp_root}")
        else:
            if workspace_path.exists():
                with contextlib.suppress(RuntimeError):
                    _run_checked(
                        ["git", "worktree", "remove", "--force", str(workspace_path)],
                        cwd=repo_root,
                    )
            if temp_root.exists():
                shutil.rmtree(temp_root)

    return status


main = make_main(app, prog_name="pipeline merge-probe")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
