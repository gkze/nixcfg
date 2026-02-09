"""Generate a human-readable diff of sources.json changes."""

import argparse
import json
from pathlib import Path
from typing import Any


def _get_version_info(entry: dict[str, Any]) -> str:
    """Extract version info from a sources.json entry."""
    parts = []
    if "version" in entry:
        parts.append(f"v{entry['version']}")
    if "commit" in entry:
        parts.append(entry["commit"][:7])
    return " ".join(parts) if parts else ""


def _get_hash_summary(entry: dict[str, Any]) -> str:
    """Summarize hash info from a sources.json entry."""
    hashes = entry.get("hashes", {})
    if isinstance(hashes, list):
        return f"{len(hashes)} hash(es)"
    if isinstance(hashes, dict):
        platforms = sorted(hashes.keys())
        return ", ".join(platforms) if platforms else "no hashes"
    return ""


def _compare_entries(
    name: str, old_entry: dict[str, Any] | None, new_entry: dict[str, Any] | None
) -> list[str]:
    """Compare two entries and return a list of change descriptions."""
    changes = []

    if old_entry is None and new_entry is not None:
        ver = _get_version_info(new_entry)
        changes.append(f"+ Added '{name}'" + (f" ({ver})" if ver else ""))
        return changes

    if old_entry is not None and new_entry is None:
        changes.append(f"- Removed '{name}'")
        return changes

    if old_entry is None or new_entry is None:
        return changes

    old_ver = old_entry.get("version", "")
    new_ver = new_entry.get("version", "")
    if old_ver != new_ver:
        changes.append(f"* '{name}': version {old_ver} -> {new_ver}")

    old_commit = old_entry.get("commit", "")
    new_commit = new_entry.get("commit", "")
    if old_commit != new_commit:
        changes.append(
            f"* '{name}': commit {old_commit[:7] if old_commit else '(none)'} "
            f"-> {new_commit[:7] if new_commit else '(none)'}"
        )

    old_hashes = old_entry.get("hashes", {})
    new_hashes = new_entry.get("hashes", {})
    if old_hashes != new_hashes:
        if isinstance(old_hashes, dict) and isinstance(new_hashes, dict):
            old_platforms = set(old_hashes.keys())
            new_platforms = set(new_hashes.keys())
            added_platforms = new_platforms - old_platforms
            removed_platforms = old_platforms - new_platforms
            changed_platforms = [
                p
                for p in old_platforms & new_platforms
                if old_hashes.get(p) != new_hashes.get(p)
            ]
            if added_platforms:
                changes.append(
                    f"* '{name}': added hashes for {', '.join(sorted(added_platforms))}"
                )
            if removed_platforms:
                changes.append(
                    f"* '{name}': removed hashes for "
                    f"{', '.join(sorted(removed_platforms))}"
                )
            if changed_platforms:
                changes.append(
                    f"* '{name}': updated hashes for "
                    f"{', '.join(sorted(changed_platforms))}"
                )
        else:
            changes.append(f"* '{name}': hashes changed")

    old_urls = old_entry.get("urls", {})
    new_urls = new_entry.get("urls", {})
    if old_urls != new_urls:
        changes.append(f"* '{name}': URLs updated")

    return changes


def run_diff(old_path: Path, new_path: Path) -> None:
    """Compare two sources.json files and print the differences."""
    with old_path.open() as f:
        old_data: dict[str, Any] = json.load(f)
    with new_path.open() as f:
        new_data: dict[str, Any] = json.load(f)

    all_keys = set(old_data.keys()) | set(new_data.keys())
    all_changes: list[str] = []

    for key in sorted(all_keys):
        old_entry = old_data.get(key)
        new_entry = new_data.get(key)
        all_changes.extend(_compare_entries(key, old_entry, new_entry))

    if not all_changes:
        print("No sources.json changes detected.")
        return

    print("Sources.json updates:\n")
    for change in all_changes:
        print(change)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old_sources", type=Path, help="Path to old sources.json")
    parser.add_argument("new_sources", type=Path, help="Path to new sources.json")
    args = parser.parse_args(argv)
    run_diff(args.old_sources, args.new_sources)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
