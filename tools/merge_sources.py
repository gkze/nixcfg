"""Merge multiple sources.json files from different platforms.

Each input file contains hashes computed on a specific platform (via --native-only).
This script merges them by taking the union of platform-specific hashes.
"""

import argparse
import sys
from pathlib import Path

from libnix.models.sources import SourcesFile


def main() -> int:
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

    merged: SourcesFile | None = None
    loaded = 0

    for filepath in args.files:
        path = Path(filepath)
        if not path.exists():
            print(f"Warning: {filepath} not found, skipping", file=sys.stderr)
            continue
        sf = SourcesFile.load(path)
        merged = sf if merged is None else merged.merge(sf)
        loaded += 1

    if merged is None:
        print("Error: No valid input files", file=sys.stderr)
        return 1

    merged.save(Path(args.output))
    print(f"Merged {loaded} files into {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
