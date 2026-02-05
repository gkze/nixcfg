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
import select
import subprocess
import time
from datetime import datetime
from pathlib import Path


# Timeout for nix commands (10 minutes - large configs can take 5+ minutes to evaluate)
NIX_COMMAND_TIMEOUT = 600

# Timeout for nix-store builds (2 hours - Zed can take up to 1 hour to build from source)
NIX_BUILD_TIMEOUT = 7200

# Maximum derivations per batch to avoid ARG_MAX limits
MAX_DERIVATIONS_PER_BATCH = 500


class Logger:
    """Simple logger with optional verbose output."""

    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose

    def log(self, message: str, verbose_only: bool = False) -> None:
        """Print a timestamped log message."""
        if verbose_only and not self.verbose:
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}", flush=True)

    def progress(self, current: int, total: int, message: str) -> None:
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


def get_derivations_to_build(flake_ref: str, logger: Logger) -> set[str]:
    """Get the set of derivations that need to be built for a flake reference."""
    logger.log(f"Evaluating {flake_ref}...", verbose_only=True)
    start_time = time.time()

    try:
        result = subprocess.run(
            ["nix", "build", flake_ref, "--dry-run"],
            capture_output=True,
            text=True,
            timeout=NIX_COMMAND_TIMEOUT,
            check=False,  # dry-run "fails" with build info, which is expected
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Timed out evaluating {flake_ref} after {NIX_COMMAND_TIMEOUT}s"
        ) from exc

    elapsed = time.time() - start_time

    output = "\n".join([result.stdout, result.stderr]).strip()

    # Check for actual errors (not just dry-run output)
    if result.returncode != 0 and "will be built:" not in output:
        detail = output or "no output"
        raise RuntimeError(f"Failed to evaluate {flake_ref}: {detail}")

    logger.log(f"Evaluation completed in {format_duration(elapsed)}", verbose_only=True)

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


def build_derivations(
    derivations: set[str], logger: Logger, dry_run: bool = False
) -> bool:
    """Build the specified derivations.

    Builds derivations directly using their .drv paths with nix-store --realise.
    This ensures Nix can build from source if paths aren't in any cache,
    unlike `nix build <output-path>` which only checks substituters.
    """
    if not derivations:
        logger.log("No derivations to build.")
        return True

    derivation_list = list(derivations)
    logger.log(f"Building {len(derivation_list)} derivations...")

    if dry_run:
        logger.log(
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
            logger.progress(
                batch_num, total_batches, f"Building batch ({len(batch)} derivations)"
            )
        else:
            logger.log(f"Building {len(batch)} derivations...")

        batch_start = time.time()
        last_progress_time = batch_start
        progress_interval = 60  # Log progress every 60 seconds during long builds

        # Use nix-store --realise to build derivations directly
        # This allows building from source when paths aren't cached
        cmd = ["nix-store", "--realise", *batch]

        try:
            # Stream output in real-time for better CI visibility
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,  # Line buffered
            )

            while True:
                # Check if process has output ready (with 1 second timeout)
                if process.stdout:
                    # Use select for non-blocking read on Unix
                    ready, _, _ = select.select([process.stdout], [], [], 1.0)
                    if ready:
                        line = process.stdout.readline()
                        if line:
                            print(f"  {line.rstrip()}", flush=True)
                            last_progress_time = time.time()

                # Check if process finished
                if process.poll() is not None:
                    # Read any remaining output
                    if process.stdout:
                        for line in process.stdout:
                            print(f"  {line.rstrip()}", flush=True)
                    break

                # Log periodic progress for long-running builds
                current_time = time.time()
                elapsed = current_time - batch_start
                if current_time - last_progress_time >= progress_interval:
                    logger.log(
                        f"Still building... ({format_duration(elapsed)} elapsed, "
                        f"timeout at {format_duration(NIX_BUILD_TIMEOUT)})"
                    )
                    last_progress_time = current_time

                # Check for timeout
                if elapsed > NIX_BUILD_TIMEOUT:
                    process.kill()
                    process.wait()
                    raise subprocess.TimeoutExpired(cmd, NIX_BUILD_TIMEOUT)

            returncode = process.returncode

        except subprocess.TimeoutExpired:
            logger.log(
                f"ERROR: Build timed out for batch {batch_num} after {format_duration(NIX_BUILD_TIMEOUT)}"
            )
            success = False
            continue

        batch_elapsed = time.time() - batch_start

        if returncode != 0:
            logger.log(
                f"ERROR: Build failed for batch {batch_num} (exit code {returncode})"
            )
            success = False
            # Continue with other batches to build as much as possible
        else:
            logger.log(
                f"Batch {batch_num} completed in {format_duration(batch_elapsed)}"
            )

    overall_elapsed = time.time() - overall_start
    logger.log(f"Total build time: {format_duration(overall_elapsed)}")

    return success


def main() -> int:
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

    logger = Logger(args.verbose)

    logger.log(
        f"Build configuration: eval timeout={format_duration(NIX_COMMAND_TIMEOUT)}, "
        f"build timeout={format_duration(NIX_BUILD_TIMEOUT)}"
    )

    if len(args.targets) < 2:
        logger.log("ERROR: Need at least 2 targets to find shared derivations.")
        return 1

    logger.log(f"Analyzing {len(args.targets)} targets for shared derivations...")

    # Get derivations to build for each target
    target_derivations: dict[str, set[str]] = {}
    try:
        for idx, target in enumerate(args.targets, 1):
            logger.progress(idx, len(args.targets), f"Evaluating {target}")
            derivations = get_derivations_to_build(target, logger)
            target_derivations[target] = derivations
            logger.log(f"  → {len(derivations)} derivations need building")
    except RuntimeError as exc:
        logger.log(f"ERROR: {exc}")
        return 1

    # Find derivations shared by at least min_shared targets
    logger.log("Analyzing derivation overlap...")
    all_derivations: set[str] = set()
    for drvs in target_derivations.values():
        all_derivations.update(drvs)

    shared_derivations: set[str] = set()
    for drv in all_derivations:
        count = sum(1 for drvs in target_derivations.values() if drv in drvs)
        if count >= args.min_shared:
            shared_derivations.add(drv)

    logger.log(
        f"Found {len(shared_derivations)} shared derivations (out of {len(all_derivations)} total)"
    )

    if args.verbose and shared_derivations:
        logger.log("Shared derivations:")
        for drv in sorted(shared_derivations):
            # Extract package name from derivation path
            name = Path(drv).name.replace(".drv", "")
            # Remove hash prefix
            if "-" in name:
                name = name.split("-", 1)[1]
            print(f"    - {name}", flush=True)

    # Calculate unique derivations per target
    logger.log("Per-target breakdown:")
    for target, drvs in target_derivations.items():
        unique = drvs - shared_derivations
        print(f"    {target}: {len(unique)} unique derivations", flush=True)

    if not shared_derivations:
        logger.log(
            "No shared derivations to build - all targets are independent or already cached."
        )
        return 0

    logger.log(f"Starting build of {len(shared_derivations)} shared derivations...")
    success = build_derivations(shared_derivations, logger, dry_run=args.dry_run)

    if success:
        logger.log("✓ Shared closure built successfully!")
        logger.log("  Subsequent builds of individual targets will hit the cache.")
        return 0

    logger.log("✗ Failed to build shared closure")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
