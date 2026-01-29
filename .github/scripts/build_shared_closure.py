#!/usr/bin/env python3
"""
Build shared derivations between multiple Nix flake outputs.

This script identifies derivations that need to be built for multiple targets
and builds them first, allowing subsequent parallel builds to hit the cache.

Usage:
    ./build_shared_closure.py .#darwinConfigurations.argus.system .#darwinConfigurations.rocinante.system

In CI, this is used as a preliminary step:
1. Run this script to build shared derivations
2. Cachix automatically pushes built paths
3. Subsequent parallel builds hit the cache
"""

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


# Timeout for nix commands (5 minutes should be plenty for evaluation)
NIX_COMMAND_TIMEOUT = 300

# Maximum derivations per batch to avoid ARG_MAX limits
MAX_DERIVATIONS_PER_BATCH = 500

# Global verbose flag (set by main)
VERBOSE = False


def log(message: str, verbose_only: bool = False) -> None:
    """Print a timestamped log message."""
    if verbose_only and not VERBOSE:
        return
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {message}", flush=True)


def log_progress(current: int, total: int, message: str) -> None:
    """Print a progress message."""
    percent = (current / total * 100) if total > 0 else 0
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] [{current}/{total} {percent:.0f}%] {message}", flush=True)


def format_duration(seconds: float) -> str:
    """Format a duration in seconds to a human-readable string."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    return f"{minutes}m {secs}s"


def get_derivations_to_build(flake_ref: str) -> set[str]:
    """Get the set of derivations that need to be built for a flake reference."""
    log(f"Evaluating {flake_ref}...", verbose_only=True)
    start_time = time.time()

    try:
        result = subprocess.run(
            ["nix", "build", flake_ref, "--dry-run"],
            capture_output=True,
            text=True,
            timeout=NIX_COMMAND_TIMEOUT,
            check=False,  # dry-run "fails" with build info, which is expected
        )
    except subprocess.TimeoutExpired:
        log(f"ERROR: Timed out evaluating {flake_ref} after {NIX_COMMAND_TIMEOUT}s")
        sys.exit(1)

    elapsed = time.time() - start_time

    # Check for actual errors (not just dry-run output)
    if result.returncode != 0 and "will be built:" not in result.stderr:
        log(f"ERROR: Failed to evaluate {flake_ref}: {result.stderr}")
        sys.exit(1)

    log(f"Evaluation completed in {format_duration(elapsed)}", verbose_only=True)

    # Derivations are listed in stderr
    output = result.stderr

    derivations = set()
    in_build_list = False

    for line in output.splitlines():
        if "will be built:" in line:
            in_build_list = True
            continue
        if in_build_list:
            line = line.strip()
            if line.endswith(".drv"):
                derivations.add(line)
            elif line and not line.startswith("/nix/store"):
                # End of derivation list
                break

    return derivations


def get_output_paths(derivations: set[str]) -> list[str]:
    """Convert derivation paths to their output store paths."""
    if not derivations:
        return []

    outputs = []
    derivation_list = list(derivations)
    total_batches = (
        len(derivation_list) + MAX_DERIVATIONS_PER_BATCH - 1
    ) // MAX_DERIVATIONS_PER_BATCH

    log(f"Resolving output paths for {len(derivations)} derivations...")

    # Process in batches to avoid ARG_MAX limits
    for i in range(0, len(derivation_list), MAX_DERIVATIONS_PER_BATCH):
        batch = derivation_list[i : i + MAX_DERIVATIONS_PER_BATCH]
        batch_num = i // MAX_DERIVATIONS_PER_BATCH + 1

        if total_batches > 1:
            log_progress(
                batch_num, total_batches, f"Resolving batch ({len(batch)} derivations)"
            )

        try:
            result = subprocess.run(
                ["nix", "derivation", "show", *batch],
                capture_output=True,
                text=True,
                timeout=NIX_COMMAND_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            log(f"WARNING: Timed out getting derivation info for batch {batch_num}")
            continue

        if result.returncode != 0:
            log(f"WARNING: Failed to get derivation info: {result.stderr[:200]}")
            continue

        try:
            drv_info = json.loads(result.stdout)
            # Handle new nix derivation show format with "derivations" key
            derivations_data = drv_info.get("derivations", drv_info)
            for drv_name, info in derivations_data.items():
                if not isinstance(info, dict):
                    continue
                for output_name, output_info in info.get("outputs", {}).items():
                    if "path" in output_info:
                        path = output_info["path"]
                        # Handle both full paths and hash-name format
                        # Nix store paths follow: /nix/store/<hash>-<name>
                        if not path.startswith("/nix/store/"):
                            path = f"/nix/store/{path}"
                        outputs.append(path)
        except json.JSONDecodeError as e:
            log(f"WARNING: Failed to parse derivation info: {e}")
            continue

    log(f"Resolved {len(outputs)} output paths")
    return outputs


def build_derivations(derivations: set[str], dry_run: bool = False) -> bool:
    """Build the specified derivations.

    Builds derivations directly using their .drv paths with nix-store --realise.
    This ensures Nix can build from source if paths aren't in any cache,
    unlike `nix build <output-path>` which only checks substituters.
    """
    if not derivations:
        log("No derivations to build.")
        return True

    derivation_list = list(derivations)
    log(f"Building {len(derivation_list)} derivations...")

    if dry_run:
        log(
            f"DRY RUN: Would run: nix-store --realise ... ({len(derivation_list)} derivations)"
        )
        return True

    # Build in batches to avoid ARG_MAX limits
    success = True
    total_batches = (
        len(derivation_list) + MAX_DERIVATIONS_PER_BATCH - 1
    ) // MAX_DERIVATIONS_PER_BATCH
    overall_start = time.time()

    for i in range(0, len(derivation_list), MAX_DERIVATIONS_PER_BATCH):
        batch = derivation_list[i : i + MAX_DERIVATIONS_PER_BATCH]
        batch_num = i // MAX_DERIVATIONS_PER_BATCH + 1

        if total_batches > 1:
            log_progress(
                batch_num, total_batches, f"Building batch ({len(batch)} derivations)"
            )
        else:
            log(f"Building {len(batch)} derivations...")

        batch_start = time.time()
        # Use nix-store --realise to build derivations directly
        # This allows building from source when paths aren't cached
        cmd = ["nix-store", "--realise", *batch]

        # Stream output in real-time instead of capturing
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,  # Line buffered
        )

        # Stream output lines as they come
        if process.stdout:
            for line in process.stdout:
                line = line.rstrip()
                if line:
                    print(f"  {line}", flush=True)

        process.wait()
        batch_elapsed = time.time() - batch_start

        if process.returncode != 0:
            log(
                f"ERROR: Build failed for batch {batch_num} (exit code {process.returncode})"
            )
            success = False
            # Continue with other batches to build as much as possible
        else:
            log(f"Batch {batch_num} completed in {format_duration(batch_elapsed)}")

    overall_elapsed = time.time() - overall_start
    log(f"Total build time: {format_duration(overall_elapsed)}")

    return success


def main():
    parser = argparse.ArgumentParser(
        description="Build shared derivations between multiple Nix flake outputs"
    )
    parser.add_argument(
        "targets",
        nargs="+",
        help="Flake references to analyze (e.g., .#darwinConfigurations.argus.system)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be built without building",
    )
    parser.add_argument(
        "--min-shared",
        type=int,
        default=2,
        help="Minimum number of targets that must share a derivation (default: 2)",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed output",
    )

    args = parser.parse_args()

    # Set verbose mode
    global VERBOSE
    VERBOSE = args.verbose

    if len(args.targets) < 2:
        log("ERROR: Need at least 2 targets to find shared derivations.")
        sys.exit(1)

    log(f"Analyzing {len(args.targets)} targets for shared derivations...")

    # Get derivations to build for each target
    target_derivations: dict[str, set[str]] = {}
    for idx, target in enumerate(args.targets, 1):
        log_progress(idx, len(args.targets), f"Evaluating {target}")
        derivations = get_derivations_to_build(target)
        target_derivations[target] = derivations
        log(f"  → {len(derivations)} derivations need building")

    # Find derivations shared by at least min_shared targets
    log("Analyzing derivation overlap...")
    all_derivations: set[str] = set()
    for drvs in target_derivations.values():
        all_derivations.update(drvs)

    shared_derivations: set[str] = set()
    for drv in all_derivations:
        count = sum(1 for drvs in target_derivations.values() if drv in drvs)
        if count >= args.min_shared:
            shared_derivations.add(drv)

    log(
        f"Found {len(shared_derivations)} shared derivations (out of {len(all_derivations)} total)"
    )

    if args.verbose and shared_derivations:
        log("Shared derivations:")
        for drv in sorted(shared_derivations):
            # Extract package name from derivation path
            name = Path(drv).name.replace(".drv", "")
            # Remove hash prefix
            if "-" in name:
                name = name.split("-", 1)[1]
            print(f"    - {name}", flush=True)

    # Calculate unique derivations per target
    log("Per-target breakdown:")
    for target, drvs in target_derivations.items():
        unique = drvs - shared_derivations
        print(f"    {target}: {len(unique)} unique derivations", flush=True)

    if not shared_derivations:
        log(
            "No shared derivations to build - all targets are independent or already cached."
        )
        return

    log(f"Starting build of {len(shared_derivations)} shared derivations...")
    success = build_derivations(shared_derivations, dry_run=args.dry_run)

    if success:
        log("✓ Shared closure built successfully!")
        log("  Subsequent builds of individual targets will hit the cache.")
    else:
        log("✗ Failed to build shared closure")
        sys.exit(1)


if __name__ == "__main__":
    main()
