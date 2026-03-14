"""Resolve the source snapshot that fed a realised generation build."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final

import typer

from lib.nix.commands import nix_store_query_deriver, nix_store_query_requisites

DEFAULT_GENERATION: Final[str] = "/run/current-system"
DEFAULT_MARKER_FILES: Final[tuple[str, ...]] = (
    "flake.nix",
    "flake.lock",
    "nixcfg.py",
    "modules/common.nix",
)


@dataclass(frozen=True)
class SnapshotPlan:
    """Resolved source snapshot for one realised generation."""

    generation: str
    resolved_target: str
    deriver: str
    snapshot: str

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serialisable representation."""
        return asdict(self)


def _normalise_generation_path(generation: str) -> tuple[Path, Path]:
    """Return the user-supplied generation path and its resolved target."""
    generation_path = Path(generation).expanduser()
    try:
        resolved_target = generation_path.resolve(strict=True)
    except FileNotFoundError as exc:
        msg = f"Generation path not found: {generation_path}"
        raise RuntimeError(msg) from exc
    return generation_path, resolved_target


def _digest_bytes(payload: bytes) -> str:
    """Return a stable digest for *payload*."""
    return hashlib.sha256(payload).hexdigest()


def _snapshot_fingerprint(root: Path) -> tuple[tuple[str, str, str], ...]:
    """Fingerprint all files and symlinks under *root*.

    This is only used to collapse duplicate matching source snapshots emitted
    in the derivation closure.  Distinct trees must not compare equal here.
    """
    entries: list[tuple[str, str, str]] = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries.append(("symlink", relative, path.readlink().as_posix()))
            continue
        if path.is_file():
            entries.append(("file", relative, _digest_bytes(path.read_bytes())))
    return tuple(entries)


def _select_source_snapshot(
    requisites: list[str],
    *,
    marker_files: tuple[str, ...] = DEFAULT_MARKER_FILES,
) -> Path:
    """Return the unique source snapshot matching *marker_files*."""
    candidates = sorted(
        {
            Path(path)
            for path in requisites
            if Path(path).is_dir()
            and all((Path(path) / marker).is_file() for marker in marker_files)
        },
        key=lambda path: path.as_posix(),
    )
    if not candidates:
        msg = (
            "Could not locate a source snapshot in the derivation closure "
            f"matching markers: {', '.join(marker_files)}"
        )
        raise RuntimeError(msg)
    if len(candidates) == 1:
        return candidates[0]

    fingerprints = {
        candidate.as_posix(): _snapshot_fingerprint(candidate)
        for candidate in candidates
    }
    unique = set(fingerprints.values())
    if len(unique) != 1:
        rendered = "\n".join(f"- {path}" for path in fingerprints)
        msg = "Multiple distinct source snapshots matched the recovery markers:\n"
        raise RuntimeError(msg + rendered)
    return candidates[0]


async def plan_snapshot_recovery(generation: str = DEFAULT_GENERATION) -> SnapshotPlan:
    """Resolve the source snapshot that fed a realised generation."""
    generation_path, resolved_target = _normalise_generation_path(generation)

    deriver = await nix_store_query_deriver(str(resolved_target))
    if deriver is None:
        msg = f"Could not resolve deriver for generation: {resolved_target}"
        raise RuntimeError(msg)

    requisites = await nix_store_query_requisites(deriver)
    snapshot = _select_source_snapshot(requisites)

    return SnapshotPlan(
        generation=str(generation_path),
        resolved_target=str(resolved_target),
        deriver=deriver,
        snapshot=str(snapshot),
    )


def run_snapshot_recovery(
    generation: str = DEFAULT_GENERATION,
    *,
    json_output: bool = False,
) -> int:
    """Resolve and print the source snapshot for a realised generation."""
    try:
        plan = asyncio.run(plan_snapshot_recovery(generation))
    except Exception as exc:  # noqa: BLE001
        if json_output:
            typer.echo(json.dumps({"success": False, "error": str(exc)}))
        else:
            typer.echo(f"Error: {exc}", err=True)
        return 1

    if json_output:
        typer.echo(json.dumps({"success": True, "plan": plan.to_dict()}))
    else:
        typer.echo(plan.snapshot)
    return 0
