#!/usr/bin/env python3
"""Merge multiple sources.json files from different platforms.

Each input file contains hashes computed on a specific platform (via --native-only).
This script merges them by taking the union of platform-specific hashes.

Usage:
    python merge_sources.py base.json platform1.json platform2.json ... -o merged.json
"""

import argparse
import json
import sys
from pathlib import Path


def merge_hash_entries(entries_list: list[list[dict]]) -> list[dict]:
    """Merge hash entries from multiple sources, keyed by (hashType, platform, drvType).

    Each entry in the hashes array can have different identifying fields:
    - hashType: e.g., "srcHash", "cargoHash", "vendorHash"
    - platform: e.g., "aarch64-darwin", "x86_64-linux" (optional)
    - drvType: e.g., "fetchFromGitHub", "fetchCargoVendor" (optional)

    We key by (hashType, platform, drvType) to preserve all unique hash entries.
    """
    # Key by (hashType, platform, drvType) tuple to preserve all unique entries
    by_key: dict[tuple[str | None, str | None, str | None], dict] = {}

    for entries in entries_list:
        for entry in entries:
            hash_type = entry.get("hashType")
            platform = entry.get("platform")
            drv_type = entry.get("drvType")
            key = (hash_type, platform, drv_type)
            # Skip FAKE_HASH entries
            hash_val = entry.get("hash")
            if isinstance(hash_val, str) and hash_val.startswith("sha256-AAAAAAA"):
                continue
            # Take first valid hash for each (hashType, platform, drvType) combination
            if key not in by_key:
                by_key[key] = entry

    return list(by_key.values())


def merge_hash_dicts(dicts_list: list[dict]) -> dict:
    """Merge hash dicts from multiple sources."""
    merged = {}
    for d in dicts_list:
        for platform, hash_val in d.items():
            # Skip FAKE_HASH
            if isinstance(hash_val, str) and hash_val.startswith("sha256-AAAAAAA"):
                continue
            if platform not in merged:
                merged[platform] = hash_val
    return merged


def merge_sources(sources_list: list[dict]) -> dict:
    """Merge multiple sources.json contents."""
    if not sources_list:
        return {}

    # Start with first source as base
    merged = {}

    # Get all keys across all sources
    all_keys = set()
    for sources in sources_list:
        all_keys.update(sources.keys())

    for key in sorted(all_keys):
        # Collect this key's data from all sources
        entries_data = [s.get(key, {}) for s in sources_list if key in s]
        if not entries_data:
            continue

        # Use first entry as base
        base = dict(entries_data[0])

        # Merge hashes field
        if "hashes" in base:
            hashes_list = [e.get("hashes", {}) for e in entries_data if "hashes" in e]
            if hashes_list:
                first_hashes = hashes_list[0]
                if isinstance(first_hashes, list):
                    # Array of hash entries (e.g., denoDeps with platform)
                    base["hashes"] = merge_hash_entries(hashes_list)
                elif isinstance(first_hashes, dict):
                    # Dict of platform -> hash
                    base["hashes"] = merge_hash_dicts(hashes_list)

        # Merge urls field if present
        if any("urls" in e for e in entries_data):
            urls_list = [e.get("urls", {}) for e in entries_data if "urls" in e]
            base["urls"] = merge_hash_dicts(urls_list)

        merged[key] = base

    return merged


def main():
    parser = argparse.ArgumentParser(
        description="Merge sources.json files from different platforms"
    )
    parser.add_argument("files", nargs="+", help="Input sources.json files to merge")
    parser.add_argument(
        "-o",
        "--output",
        default="sources.json",
        help="Output file (default: sources.json)",
    )
    args = parser.parse_args()

    sources_list = []
    for filepath in args.files:
        path = Path(filepath)
        if not path.exists():
            print(f"Warning: {filepath} not found, skipping", file=sys.stderr)
            continue
        with path.open(encoding="utf-8") as f:
            sources_list.append(json.load(f))

    if not sources_list:
        print("Error: No valid input files", file=sys.stderr)
        sys.exit(1)

    merged = merge_sources(sources_list)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2, sort_keys=True)
        f.write("\n")

    print(f"Merged {len(args.files)} files into {args.output}")


if __name__ == "__main__":
    main()
