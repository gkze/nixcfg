#!/usr/bin/env python3
"""Generate a human-readable diff of flake.lock changes."""

import argparse
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass
class InputInfo:
    """Relevant info from a flake input node."""

    name: str
    type: str
    owner: str
    repo: str
    rev: str
    date: str


def get_input_info(nodes: dict, name: str) -> InputInfo | None:
    """Extract relevant info from a flake input node."""
    if name not in nodes:
        return None

    node = nodes[name]
    locked = node.get("locked", {})
    last_modified = locked.get("lastModified", 0)

    return InputInfo(
        name=name,
        type=locked.get("type", "github"),
        owner=locked.get("owner", ""),
        repo=locked.get("repo", ""),
        rev=locked.get("rev", "")[:7] if locked.get("rev") else "",
        date=datetime.fromtimestamp(last_modified, tz=timezone.utc).strftime("%Y-%m-%d")
        if last_modified
        else "",
    )


def main(old_lock_path: Path, new_lock_path: Path) -> None:
    """Compare two flake.lock files and print the differences."""
    old_lock = json.loads(old_lock_path.read_text())
    new_lock = json.loads(new_lock_path.read_text())

    old_nodes = old_lock.get("nodes", {})
    new_nodes = new_lock.get("nodes", {})

    all_inputs = (set(old_nodes.keys()) | set(new_nodes.keys())) - {"root"}

    added, removed, updated = [], [], []
    for name in sorted(all_inputs):
        old_info = get_input_info(old_nodes, name)
        new_info = get_input_info(new_nodes, name)

        if old_info and new_info and old_info.rev != new_info.rev:
            updated.append((old_info, new_info))
        elif new_info and not old_info:
            added.append(new_info)
        elif old_info and not new_info:
            removed.append(old_info)

    if not (added or removed or updated):
        print("No input changes detected.")
        return

    print("Flake lock file updates:\n")
    for old, new in updated:
        print(f"* Updated '{old.name}':")
        print(f"    {old.type}:{old.owner}/{old.repo}/{old.rev} ({old.date})")
        print(f"  -> {new.type}:{new.owner}/{new.repo}/{new.rev} ({new.date})")
    for info in added:
        print(f"+ Added '{info.name}': {info.type}:{info.owner}/{info.repo}/{info.rev}")
    for info in removed:
        print(f"- Removed '{info.name}'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_lock", type=Path, help="Path to old flake.lock")
    parser.add_argument("new_lock", type=Path, help="Path to new flake.lock")
    args = parser.parse_args()
    main(args.old_lock, args.new_lock)
