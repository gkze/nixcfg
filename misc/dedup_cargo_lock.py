#!/usr/bin/env python3
"""
Remove duplicate git sources from Cargo.lock.

When a crate appears twice in Cargo.lock (once from crates.io, once from git),
this script removes the git-sourced entry and updates all references to point
to the crates.io version.

This solves the "File exists" error when Nix tries to vendor cargo dependencies
that have the same crate from multiple sources.

Usage: python3 dedup_cargo_lock.py <path-to-Cargo.lock>
"""

import re
import sys
from pathlib import Path


def parse_cargo_lock(content: str) -> list[dict]:
    """Parse Cargo.lock into a list of package entries."""
    packages = []
    current_pkg = {}
    current_key = None
    in_array = False
    array_content = []

    for line in content.splitlines():
        if line.strip() == "[[package]]":
            if current_pkg:
                packages.append(current_pkg)
            current_pkg = {}
            current_key = None
            in_array = False
            continue

        if not current_pkg and line.startswith("version ="):
            # This is the lockfile version line at the top
            continue

        if in_array:
            if line.strip() == "]":
                current_pkg[current_key] = array_content
                in_array = False
                array_content = []
                current_key = None
            else:
                # Strip quotes and comma
                val = line.strip().rstrip(",").strip('"')
                if val:
                    array_content.append(val)
            continue

        match = re.match(r"^(\w+)\s*=\s*(.+)$", line)
        if match:
            key, value = match.groups()
            value = value.strip()
            if value == "[":
                current_key = key
                in_array = True
                array_content = []
            elif value.startswith("[") and value.endswith("]"):
                # Inline array
                items = value[1:-1].split(",")
                current_pkg[key] = [
                    i.strip().strip('"') for i in items if i.strip().strip('"')
                ]
            elif value.startswith('"') and value.endswith('"'):
                current_pkg[key] = value[1:-1]
            else:
                current_pkg[key] = value

    if current_pkg:
        packages.append(current_pkg)

    return packages


def format_cargo_lock(packages: list[dict], version: int = 4) -> str:
    """Format packages back into Cargo.lock format."""
    lines = [f"version = {version}", ""]

    for pkg in packages:
        lines.append("[[package]]")
        # Specific key order for Cargo.lock
        key_order = [
            "name",
            "version",
            "source",
            "checksum",
            "dependencies",
            "build-dependencies",
        ]

        # Write known keys in order
        for key in key_order:
            if key in pkg:
                value = pkg[key]
                if isinstance(value, list):
                    if not value:
                        continue
                    lines.append(f"{key} = [")
                    for item in sorted(value):
                        lines.append(f' "{item}",')
                    lines.append("]")
                else:
                    lines.append(f'{key} = "{value}"')

        # Write any remaining keys
        for key, value in pkg.items():
            if key in key_order:
                continue
            if isinstance(value, list):
                if not value:
                    continue
                lines.append(f"{key} = [")
                for item in sorted(value):
                    lines.append(f' "{item}",')
                lines.append("]")
            else:
                lines.append(f'{key} = "{value}"')

        lines.append("")

    return "\n".join(lines)


def make_dep_key(name: str, version: str) -> str:
    """Create a dependency reference key."""
    return f"{name} {version}"


def dedup_packages(packages: list[dict]) -> tuple[list[dict], int]:
    """
    Remove duplicate packages, preferring crates.io over git sources.
    Returns (deduped_packages, count_removed).
    """
    # Group packages by (name, version)
    by_name_version: dict[tuple[str, str], list[dict]] = {}
    for pkg in packages:
        key = (pkg.get("name", ""), pkg.get("version", ""))
        if key not in by_name_version:
            by_name_version[key] = []
        by_name_version[key].append(pkg)

    # Find duplicates and decide which to keep
    deduped = []
    removed_count = 0
    # Map from git source dep string to crates.io dep string for fixing references
    replacement_map: dict[str, str] = {}

    for (name, version), pkgs in by_name_version.items():
        if len(pkgs) == 1:
            deduped.append(pkgs[0])
            continue

        # Multiple packages with same name+version - find crates.io and git versions
        crates_io_pkg = None
        git_pkgs = []

        for pkg in pkgs:
            source = pkg.get("source", "")
            if source.startswith("registry+"):
                crates_io_pkg = pkg
            elif source.startswith("git+"):
                git_pkgs.append(pkg)
            else:
                # Unknown source, keep it
                deduped.append(pkg)

        if crates_io_pkg and git_pkgs:
            # Keep crates.io, remove git
            deduped.append(crates_io_pkg)
            removed_count += len(git_pkgs)

            crates_io_dep = make_dep_key(name, version)
            for git_pkg in git_pkgs:
                git_source = git_pkg.get("source", "")
                git_dep = f"{name} {version} ({git_source})"
                replacement_map[git_dep] = crates_io_dep
                print(f"  Removing git source for: {name} {version}")
        else:
            # No crates.io version, keep all
            if crates_io_pkg:
                deduped.append(crates_io_pkg)
            deduped.extend(git_pkgs)

    # Fix dependency references
    if replacement_map:
        for pkg in deduped:
            for dep_key in ["dependencies", "build-dependencies"]:
                if dep_key not in pkg:
                    continue
                new_deps = []
                for dep in pkg[dep_key]:
                    if dep in replacement_map:
                        new_dep = replacement_map[dep]
                        if new_dep not in new_deps:
                            new_deps.append(new_dep)
                    else:
                        new_deps.append(dep)
                pkg[dep_key] = new_deps

    # Sort by name, then version
    deduped.sort(key=lambda p: (p.get("name", ""), p.get("version", "")))

    return deduped, removed_count


def main():
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to-Cargo.lock>", file=sys.stderr)
        sys.exit(1)

    cargo_lock = Path(sys.argv[1])
    if not cargo_lock.exists():
        print(f"Error: {cargo_lock} not found", file=sys.stderr)
        sys.exit(1)

    print(f"Deduplicating {cargo_lock}...")

    content = cargo_lock.read_text()

    # Extract lockfile version
    version_match = re.search(r"^version\s*=\s*(\d+)", content, re.MULTILINE)
    lock_version = int(version_match.group(1)) if version_match else 4

    packages = parse_cargo_lock(content)
    print(f"  Found {len(packages)} packages")

    deduped, removed = dedup_packages(packages)

    if removed > 0:
        new_content = format_cargo_lock(deduped, lock_version)
        cargo_lock.write_text(new_content)
        print(f"\nRemoved {removed} duplicate git-sourced packages")
        print(f"Kept {len(deduped)} packages")
    else:
        print("\nNo duplicate sources found to remove")


if __name__ == "__main__":
    main()
