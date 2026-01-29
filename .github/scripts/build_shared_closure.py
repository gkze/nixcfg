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
from pathlib import Path


# Timeout for nix commands (5 minutes should be plenty for evaluation)
NIX_COMMAND_TIMEOUT = 300

# Maximum derivations per batch to avoid ARG_MAX limits
MAX_DERIVATIONS_PER_BATCH = 500


def get_derivations_to_build(flake_ref: str) -> set[str]:
    """Get the set of derivations that need to be built for a flake reference."""
    try:
        result = subprocess.run(
            ["nix", "build", flake_ref, "--dry-run"],
            capture_output=True,
            text=True,
            timeout=NIX_COMMAND_TIMEOUT,
            check=False,  # dry-run "fails" with build info, which is expected
        )
    except subprocess.TimeoutExpired:
        print(f"Error: Timed out evaluating {flake_ref}", file=sys.stderr)
        sys.exit(1)

    # Check for actual errors (not just dry-run output)
    if result.returncode != 0 and "will be built:" not in result.stderr:
        print(f"Error evaluating {flake_ref}: {result.stderr}", file=sys.stderr)
        sys.exit(1)

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

    # Process in batches to avoid ARG_MAX limits
    for i in range(0, len(derivation_list), MAX_DERIVATIONS_PER_BATCH):
        batch = derivation_list[i : i + MAX_DERIVATIONS_PER_BATCH]

        try:
            result = subprocess.run(
                ["nix", "derivation", "show", *batch],
                capture_output=True,
                text=True,
                timeout=NIX_COMMAND_TIMEOUT,
                check=False,
            )
        except subprocess.TimeoutExpired:
            print(
                f"Warning: Timed out getting derivation info for batch {i // MAX_DERIVATIONS_PER_BATCH + 1}",
                file=sys.stderr,
            )
            continue

        if result.returncode != 0:
            print(
                f"Warning: Failed to get derivation info: {result.stderr}",
                file=sys.stderr,
            )
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
            print(f"Warning: Failed to parse derivation info: {e}", file=sys.stderr)
            continue

    return outputs


def build_derivations(derivations: set[str], dry_run: bool = False) -> bool:
    """Build the specified derivations."""
    if not derivations:
        print("No derivations to build.")
        return True

    # Get the output paths for the derivations
    outputs = get_output_paths(derivations)

    if not outputs:
        print("No output paths found for derivations.", file=sys.stderr)
        return False

    print(f"Building {len(outputs)} store paths from {len(derivations)} derivations...")

    if dry_run:
        print(f"Would run: nix build --no-link ... ({len(outputs)} paths)")
        return True

    # Build in batches to avoid ARG_MAX limits
    success = True
    for i in range(0, len(outputs), MAX_DERIVATIONS_PER_BATCH):
        batch = outputs[i : i + MAX_DERIVATIONS_PER_BATCH]
        batch_num = i // MAX_DERIVATIONS_PER_BATCH + 1
        total_batches = (
            len(outputs) + MAX_DERIVATIONS_PER_BATCH - 1
        ) // MAX_DERIVATIONS_PER_BATCH

        if total_batches > 1:
            print(
                f"  Building batch {batch_num}/{total_batches} ({len(batch)} paths)..."
            )

        cmd = ["nix", "build", "--no-link", *batch]
        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            print(f"Error building batch {batch_num}: {result.stderr}", file=sys.stderr)
            success = False
            # Continue with other batches to build as much as possible

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

    if len(args.targets) < 2:
        print("Need at least 2 targets to find shared derivations.", file=sys.stderr)
        sys.exit(1)

    print(f"Analyzing {len(args.targets)} targets...")

    # Get derivations to build for each target
    target_derivations: dict[str, set[str]] = {}
    for target in args.targets:
        print(f"  Checking {target}...")
        derivations = get_derivations_to_build(target)
        target_derivations[target] = derivations
        print(f"    {len(derivations)} derivations need building")

    # Find derivations shared by at least min_shared targets
    all_derivations: set[str] = set()
    for drvs in target_derivations.values():
        all_derivations.update(drvs)

    shared_derivations: set[str] = set()
    for drv in all_derivations:
        count = sum(1 for drvs in target_derivations.values() if drv in drvs)
        if count >= args.min_shared:
            shared_derivations.add(drv)

    print(
        f"\nFound {len(shared_derivations)} shared derivations (out of {len(all_derivations)} total)"
    )

    if args.verbose and shared_derivations:
        print("\nShared derivations:")
        for drv in sorted(shared_derivations):
            # Extract package name from derivation path
            name = Path(drv).name.replace(".drv", "")
            # Remove hash prefix
            if "-" in name:
                name = name.split("-", 1)[1]
            print(f"  - {name}")

    # Calculate unique derivations per target
    for target, drvs in target_derivations.items():
        unique = drvs - shared_derivations
        print(f"  {target}: {len(unique)} unique derivations")

    if not shared_derivations:
        print("\nNo shared derivations to build.")
        return

    print(f"\nBuilding {len(shared_derivations)} shared derivations...")
    success = build_derivations(shared_derivations, dry_run=args.dry_run)

    if success:
        print("\n✓ Shared closure built successfully!")
        print("  Subsequent builds of individual targets will hit the cache.")
    else:
        print("\n✗ Failed to build shared closure", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
