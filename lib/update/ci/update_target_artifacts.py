"""Helpers for package-sliced update workflow artifacts.

This module intentionally uses only the Python standard library. The update
workflow calls it directly with python3 before and after package update steps,
including after failures that may leave the Nix app temporarily unevaluable.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any

STATUS_FILE_NAME = "nixcfg-update-target-status.json"
STATUS_COLLECTION_KIND = "nixcfg-update-target-status-collection"
STATUS_KIND = "nixcfg-update-target-status"

_RUNTIME_LOCK_TARGET = "t3code-desktop"
_RUNTIME_LOCK_PATHS = (
    "packages/t3code/bun.lock",
    "packages/t3code-desktop/bun.lock",
)
_REQUIRED_PLATFORMS = ("aarch64-darwin", "x86_64-linux", "aarch64-linux")
_SLUG_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        msg = f"Expected JSON object in {path}"
        raise TypeError(msg)
    return payload


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    path.write_text(serialized + "\n", encoding="utf-8")


def _repo_path(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or stripped.startswith("/") or ".." in Path(stripped).parts:
        return None
    return stripped


def _ordered_paths(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _artifact_slug(name: str) -> str:
    slug = _SLUG_RE.sub("-", name).strip("-._")
    return slug or "target"


def _artifact_paths_for_target(target: dict[str, Any], *, platform: str) -> list[str]:
    paths: list[str] = []
    source_target = target.get("sourceTarget")
    source_path = (
        _repo_path(source_target.get("path"))
        if isinstance(source_target, dict)
        else None
    )
    if source_path is not None:
        paths.append(source_path)

    if platform == "x86_64-linux":
        generated = target.get("generatedArtifacts")
        if isinstance(generated, list):
            paths.extend(path for item in generated if (path := _repo_path(item)))

    if platform == "aarch64-darwin" and target.get("name") == _RUNTIME_LOCK_TARGET:
        paths.extend(_RUNTIME_LOCK_PATHS)

    return _ordered_paths(paths)


def build_matrix(*, inventory: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Build the dynamic update-target matrix from inventory JSON."""
    targets = inventory.get("targets")
    if not isinstance(targets, list):
        msg = "Inventory JSON must contain a targets list"
        raise TypeError(msg)

    include: list[dict[str, Any]] = []
    for target in targets:
        if not isinstance(target, dict):
            continue
        name = target.get("name")
        handles = target.get("handles")
        if not isinstance(name, str) or not isinstance(handles, dict):
            continue
        if handles.get("sourceUpdate") is not True:
            continue
        artifact_slug = _artifact_slug(name)
        include.append({
            "target": name,
            "artifact_slug": artifact_slug,
            "artifact_paths_aarch64_darwin": _artifact_paths_for_target(
                target,
                platform="aarch64-darwin",
            ),
            "artifact_paths_x86_64_linux": _artifact_paths_for_target(
                target,
                platform="x86_64-linux",
            ),
            "artifact_paths_aarch64_linux": _artifact_paths_for_target(
                target,
                platform="aarch64-linux",
            ),
            "regenerate_runtime_locks": name == _RUNTIME_LOCK_TARGET,
        })
    return {"include": include}


def stage_artifact(
    *,
    paths: list[str],
    root: Path,
    output: Path,
    target: str,
    platform: str,
    conclusion: str,
    exit_code: int,
) -> dict[str, Any]:
    """Copy target-owned files into an artifact root and write status metadata."""
    copied: list[str] = []
    missing: list[str] = []
    for path in _ordered_paths(paths):
        source = root / path
        if not source.is_file():
            missing.append(path)
            continue
        destination = output / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(path)

    status = {
        "schemaVersion": 1,
        "kind": STATUS_KIND,
        "target": target,
        "platform": platform,
        "conclusion": conclusion,
        "exitCode": exit_code,
        "copiedPaths": copied,
        "missingPaths": missing,
    }
    _write_json(output / STATUS_FILE_NAME, status)
    return status


def _iter_status_files(artifacts_dir: Path) -> list[Path]:
    return sorted(artifacts_dir.glob(f"*/{STATUS_FILE_NAME}"))


def aggregate_artifacts(
    *,
    artifacts_dir: Path,
    output_root: Path,
    platform: str,
    status_output: Path,
    required_platforms: tuple[str, ...] = _REQUIRED_PLATFORMS,
) -> dict[str, Any]:
    """Apply successful target artifacts to output_root and collect status."""
    all_statuses = [
        (status_path, _load_json(status_path))
        for status_path in _iter_status_files(artifacts_dir)
    ]
    statuses = [
        status
        for _status_path, status in all_statuses
        if status.get("platform") == platform
    ]
    target_platforms: dict[str, set[str]] = {}
    for _status_path, status in all_statuses:
        if status.get("conclusion") != "success":
            continue
        target = status.get("target")
        status_platform = status.get("platform")
        if not isinstance(target, str) or not isinstance(status_platform, str):
            continue
        target_platforms.setdefault(target, set()).add(status_platform)
    eligible_targets = {
        target
        for target, platforms in target_platforms.items()
        if all(required in platforms for required in required_platforms)
    }

    for status_path, status in all_statuses:
        if status.get("platform") != platform:
            continue
        if status.get("conclusion") != "success":
            continue
        if status.get("target") not in eligible_targets:
            continue
        artifact_root = status_path.parent
        for path in status.get("copiedPaths", []):
            repo_path = _repo_path(path)
            if repo_path is None:
                continue
            source = artifact_root / repo_path
            if not source.is_file():
                continue
            destination = output_root / repo_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

    if not statuses:
        msg = f"No update target statuses found for {platform} under {artifacts_dir}"
        raise RuntimeError(msg)

    collection = {
        "schemaVersion": 1,
        "kind": STATUS_COLLECTION_KIND,
        "platform": platform,
        "eligibleTargets": sorted(eligible_targets),
        "requiredPlatforms": list(required_platforms),
        "targets": sorted(statuses, key=lambda item: str(item.get("target", ""))),
    }
    _write_json(status_output, collection)
    return collection


def _json_list(value: str) -> list[str]:
    payload = json.loads(value)
    if not isinstance(payload, list):
        msg = "Expected a JSON list of artifact paths"
        raise TypeError(msg)
    return [path for item in payload if (path := _repo_path(item))]


def _cmd_matrix(args: argparse.Namespace) -> int:
    matrix = build_matrix(inventory=_load_json(args.inventory))
    output = json.dumps(matrix, sort_keys=True, separators=(",", ":"))
    if args.output is None:
        sys.stdout.write(output + "\n")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(output + "\n", encoding="utf-8")
    return 0


def _cmd_stage(args: argparse.Namespace) -> int:
    conclusion = args.conclusion
    if conclusion is None:
        conclusion = "success" if args.exit_code == 0 else "failure"
    stage_artifact(
        paths=_json_list(args.paths_json),
        root=args.root,
        output=args.output,
        target=args.target,
        platform=args.platform,
        conclusion=conclusion,
        exit_code=args.exit_code,
    )
    return 0


def _cmd_aggregate(args: argparse.Namespace) -> int:
    status_output = args.status_output or args.output_root / STATUS_FILE_NAME
    aggregate_artifacts(
        artifacts_dir=args.artifacts_dir,
        output_root=args.output_root,
        platform=args.platform,
        status_output=status_output,
        required_platforms=tuple(args.required_platforms or _REQUIRED_PLATFORMS),
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for workflow artifact helpers."""
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    matrix = subparsers.add_parser("matrix")
    matrix.add_argument("--inventory", type=Path, required=True)
    matrix.add_argument("--output", type=Path)
    matrix.set_defaults(func=_cmd_matrix)

    stage = subparsers.add_parser("stage")
    stage.add_argument(
        "--paths-json",
        "--artifact-paths-json",
        dest="paths_json",
        required=True,
    )
    stage.add_argument("--root", type=Path, required=True)
    stage.add_argument("--output", type=Path, required=True)
    stage.add_argument("--target", required=True)
    stage.add_argument("--platform", required=True)
    stage.add_argument(
        "--conclusion",
        choices=("baseline", "success", "failure"),
    )
    stage.add_argument("--exit-code", type=int, default=0)
    stage.set_defaults(func=_cmd_stage)

    aggregate = subparsers.add_parser("aggregate")
    aggregate.add_argument("--artifacts-dir", type=Path, required=True)
    aggregate.add_argument(
        "--output-root", "--output", dest="output_root", type=Path, required=True
    )
    aggregate.add_argument("--platform", required=True)
    aggregate.add_argument("--status-output", type=Path)
    aggregate.add_argument(
        "--required-platform",
        action="append",
        dest="required_platforms",
    )
    aggregate.set_defaults(func=_cmd_aggregate)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the helper CLI and convert validation failures to exit code 1."""
    args = build_parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        sys.stderr.write(f"{exc}\n")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
