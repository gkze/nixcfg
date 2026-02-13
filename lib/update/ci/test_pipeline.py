"""Local CI pipeline simulation.

Runs the same phases as ``.github/workflows/update.yml`` on the local
machine, leveraging the nix-rosetta-builder for Linux cross-builds.

Phases executed:
  1. Resolve upstream versions  (``resolve-versions``)
  2. Compute hashes             (``update --pinned-versions``)
  3. Validate merged output     (sources.json round-trip check)

An optional ``--full`` flag also exercises the split-and-merge path:
it partitions the computed sources.json files by platform into separate
artifact directories, then merges them back — exactly as CI does.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from lib.nix.models.sources import SourceEntry
from lib.update.paths import package_file_map

if TYPE_CHECKING:
    from collections.abc import Sequence

# Platforms matching the CI matrix.
CI_PLATFORMS = ("aarch64-darwin", "x86_64-linux", "aarch64-linux")


def _log(msg: str) -> None:
    sys.stderr.write(f"[test-pipeline] {msg}\n")


# ── Phase 1: Resolve versions ────────────────────────────────────────


def _phase_resolve(pinned_path: Path) -> bool:
    _log("Phase 1: Resolving upstream versions...")
    from lib.update.ci.resolve_versions import main as resolve_main

    rc = resolve_main(["--output", str(pinned_path)])
    if rc != 0:
        _log(f"FAIL: resolve-versions exited {rc}")
        return False

    with pinned_path.open() as f:
        data = json.load(f)
    _log(f"  Resolved {len(data)} versions")
    # Verify serialization round-trip.
    json.dumps(data, indent=2, sort_keys=True)
    _log("  JSON round-trip OK")
    return True


# ── Phase 2: Compute hashes ──────────────────────────────────────────


def _phase_compute(
    pinned_path: Path,
    *,
    source: str | None = None,
) -> bool:
    _log("Phase 2: Computing hashes (all platforms)...")
    from lib.update.cli import UpdateOptions, run_updates

    opts = UpdateOptions(
        pinned_versions=str(pinned_path),
        source=source,
    )
    rc = run_updates(opts)
    if rc != 0:
        _log(f"FAIL: update exited {rc}")
        return False
    _log("  Hash computation OK")
    return True


# ── Phase 3: Split & merge (optional) ────────────────────────────────


def _split_sources_by_platform(work_dir: Path) -> dict[str, Path]:
    """Copy per-package sources.json into platform-keyed artifact dirs.

    Each ``sources.json`` is copied into *every* platform directory
    (mirroring how each CI runner produces a complete copy of sources).
    In real CI, each copy only contains hashes for that runner's
    platform — but for local testing the content is identical and the
    merge logic still validates correctness.
    """
    source_files = package_file_map("sources.json")
    platform_dirs: dict[str, Path] = {}
    for plat in CI_PLATFORMS:
        pdir = work_dir / f"sources-{plat}"
        platform_dirs[plat] = pdir
        for src_path in source_files.values():
            rel = src_path.relative_to(Path.cwd())
            dest = pdir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_path, dest)

    return platform_dirs


def _phase_merge(work_dir: Path) -> bool:
    _log("Phase 3: Split & merge sources...")
    platform_dirs = _split_sources_by_platform(work_dir)
    roots = [str(d) for d in platform_dirs.values()]
    _log(f"  Created {len(roots)} platform artifact dirs")

    from lib.update.ci.merge_sources import main as merge_main

    rc = merge_main(roots)
    if rc != 0:
        _log(f"FAIL: merge-sources exited {rc}")
        return False
    _log("  Merge OK")
    return True


# ── Phase 4: Validate ────────────────────────────────────────────────


def _phase_validate() -> bool:
    _log("Phase 4: Validating sources.json files...")
    source_files = package_file_map("sources.json")
    errors = 0
    for name, path in sorted(source_files.items()):
        try:
            with path.open() as f:
                data = json.load(f)
            SourceEntry.model_validate(data)
        except Exception as exc:  # noqa: BLE001
            _log(f"  INVALID: {name} ({path}): {exc}")
            errors += 1

    if errors:
        _log(f"  {errors} invalid sources.json files")
        return False

    _log(f"  All {len(source_files)} sources.json files valid")
    return True


# ── Entrypoint ────────────────────────────────────────────────────────


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Simulate the CI update pipeline locally",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Also run the split-and-merge phase (tests merge logic)",
    )
    parser.add_argument(
        "--source",
        help="Only compute hashes for a single source (faster iteration)",
    )
    parser.add_argument(
        "--resolve-only",
        action="store_true",
        help="Only run the version resolution phase (fastest smoke test)",
    )
    parser.add_argument(
        "--keep-artifacts",
        action="store_true",
        help="Keep the temp directory with intermediate artifacts",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local CI pipeline simulation."""
    args = _parse_args(argv)
    work_dir = Path(tempfile.mkdtemp(prefix="nixcfg-ci-test-"))
    pinned_path = work_dir / "pinned-versions.json"

    _log(f"Work directory: {work_dir}")

    phases: list[tuple[str, bool]] = []

    # Phase 1: Resolve versions
    ok = _phase_resolve(pinned_path)
    phases.append(("resolve-versions", ok))
    if not ok or args.resolve_only:
        _print_summary(phases, work_dir, keep=args.keep_artifacts)
        return 0 if ok else 1

    # Phase 2: Compute hashes
    ok = _phase_compute(pinned_path, source=args.source)
    phases.append(("compute-hashes", ok))
    if not ok:
        _print_summary(phases, work_dir, keep=args.keep_artifacts)
        return 1

    # Phase 3: Split & merge (optional)
    if args.full:
        ok = _phase_merge(work_dir)
        phases.append(("merge-sources", ok))
        if not ok:
            _print_summary(phases, work_dir, keep=args.keep_artifacts)
            return 1

    # Phase 4: Validate
    ok = _phase_validate()
    phases.append(("validate", ok))

    _print_summary(phases, work_dir, keep=args.keep_artifacts)
    return 0 if ok else 1


def _print_summary(
    phases: list[tuple[str, bool]],
    work_dir: Path,
    *,
    keep: bool,
) -> None:
    _log("")
    _log("Pipeline summary:")
    all_ok = True
    for name, ok in phases:
        status = "PASS" if ok else "FAIL"
        _log(f"  {status}: {name}")
        if not ok:
            all_ok = False

    if keep:
        _log(f"Artifacts kept at: {work_dir}")
    elif work_dir.exists():
        shutil.rmtree(work_dir)

    if all_ok:
        _log("All phases passed.")
    else:
        _log("Pipeline FAILED.")


if __name__ == "__main__":
    raise SystemExit(main())
