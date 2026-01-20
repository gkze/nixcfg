#!/usr/bin/env python3
"""
Remove duplicate git sources from Cargo.lock.

When a crate appears twice in Cargo.lock (once from crates.io, once from git),
this script removes the git-sourced entry and updates all references to point
to the crates.io version.

This solves the "File exists" error when Nix tries to vendor cargo dependencies
that have the same crate from multiple sources.

Usage: python3 dedup_cargo_lock.py <path-to-Cargo.lock>
       python3 dedup_cargo_lock.py --dry-run <path-to-Cargo.lock>
"""

from __future__ import annotations

import argparse
import logging
import sys
import tomllib
from collections import defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

# Type aliases for clarity
PackageKey = tuple[str, str]  # (name, version)
PackageGroup = dict[PackageKey, list["Package"]]
ReplacementMap = dict[str, str]  # git dep string -> crates.io dep string

logger = logging.getLogger(__name__)


class SourceType(Enum):
    """Classification of package sources in Cargo.lock."""

    REGISTRY = "registry"
    GIT = "git"
    OTHER = "other"


def classify_source(source: str) -> SourceType:
    """Classify a package source string."""
    if source.startswith("registry+"):
        return SourceType.REGISTRY
    if source.startswith("git+"):
        return SourceType.GIT
    return SourceType.OTHER


@dataclass
class Package:
    """Represents a package entry in Cargo.lock."""

    name: str
    version: str
    source: str = ""
    checksum: str = ""
    dependencies: list[str] = field(default_factory=list)
    build_dependencies: list[str] = field(default_factory=list)
    # Store any additional fields we don't explicitly handle
    extra_fields: dict[str, str | list[str]] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict) -> Package:
        """Create a Package from a parsed TOML dict."""
        return cls(
            name=data.get("name", ""),
            version=data.get("version", ""),
            source=data.get("source", ""),
            checksum=data.get("checksum", ""),
            dependencies=list(data.get("dependencies", [])),
            build_dependencies=list(data.get("build-dependencies", [])),
            extra_fields={
                k: v
                for k, v in data.items()
                if k
                not in {
                    "name",
                    "version",
                    "source",
                    "checksum",
                    "dependencies",
                    "build-dependencies",
                }
            },
        )

    def to_key(self) -> PackageKey:
        """Return the (name, version) key for this package."""
        return (self.name, self.version)

    def source_type(self) -> SourceType:
        """Classify this package's source."""
        return classify_source(self.source)

    def dep_key(self) -> str:
        """Create a dependency reference key (without source)."""
        return f"{self.name} {self.version}"

    def dep_key_with_source(self) -> str:
        """Create a dependency reference key with source."""
        return f"{self.name} {self.version} ({self.source})"


@dataclass
class DeduplicationResult:
    """Result of deduplicating packages."""

    packages: list[Package]
    removed_count: int
    replacements: ReplacementMap


def parse_cargo_lock(content: str) -> tuple[list[Package], int]:
    """
    Parse Cargo.lock into a list of Package objects.

    Returns (packages, lockfile_version).
    """
    data = tomllib.loads(content)
    version = data.get("version", 4)
    packages = [Package.from_dict(pkg) for pkg in data.get("package", [])]
    return packages, version


def format_value(key: str, value: str | list[str]) -> list[str]:
    """Format a single key-value pair for Cargo.lock output."""
    if isinstance(value, list):
        if not value:
            return []
        lines = [f"{key} = ["]
        lines.extend(f' "{item}",' for item in sorted(value))
        lines.append("]")
        return lines
    return [f'{key} = "{value}"']


def format_cargo_lock(packages: Sequence[Package], version: int = 4) -> str:
    """Format packages back into Cargo.lock format."""
    lines = [f"version = {version}", ""]

    # Specific key order for Cargo.lock
    key_order = [
        ("name", lambda p: p.name),
        ("version", lambda p: p.version),
        ("source", lambda p: p.source),
        ("checksum", lambda p: p.checksum),
        ("dependencies", lambda p: p.dependencies),
        ("build-dependencies", lambda p: p.build_dependencies),
    ]

    for pkg in packages:
        lines.append("[[package]]")

        # Write known keys in order
        for key, getter in key_order:
            value = getter(pkg)
            if value:  # Skip empty values
                lines.extend(format_value(key, value))

        # Write any extra fields
        for key, value in pkg.extra_fields.items():
            if value:
                lines.extend(format_value(key, value))

        lines.append("")

    return "\n".join(lines)


def classify_packages(
    pkgs: list[Package],
) -> tuple[Package | None, list[Package], list[Package]]:
    """Classify packages by source type. Returns (registry_pkg, git_pkgs, other_pkgs)."""
    registry_pkg: Package | None = None
    git_pkgs: list[Package] = []
    other_pkgs: list[Package] = []

    for pkg in pkgs:
        match pkg.source_type():
            case SourceType.REGISTRY:
                registry_pkg = pkg
            case SourceType.GIT:
                git_pkgs.append(pkg)
            case SourceType.OTHER:
                other_pkgs.append(pkg)

    return registry_pkg, git_pkgs, other_pkgs


def dedup_packages(packages: list[Package]) -> DeduplicationResult:
    """Remove duplicate packages, preferring crates.io over git sources."""
    # Group packages by (name, version)
    by_name_version: defaultdict[PackageKey, list[Package]] = defaultdict(list)
    for pkg in packages:
        by_name_version[pkg.to_key()].append(pkg)

    # Find duplicates and decide which to keep
    deduped: list[Package] = []
    removed_count = 0
    replacement_map: ReplacementMap = {}

    for (name, version), pkgs in by_name_version.items():
        if len(pkgs) == 1:
            deduped.append(pkgs[0])
            continue

        # Multiple packages with same name+version - classify by source
        registry_pkg, git_pkgs, other_pkgs = classify_packages(pkgs)

        # Keep packages with unknown sources
        deduped.extend(other_pkgs)

        if registry_pkg and git_pkgs:
            # Keep crates.io, remove git
            deduped.append(registry_pkg)
            removed_count += len(git_pkgs)

            crates_io_dep = registry_pkg.dep_key()
            for git_pkg in git_pkgs:
                git_dep = git_pkg.dep_key_with_source()
                replacement_map[git_dep] = crates_io_dep
                logger.info("  Removing git source for: %s %s", name, version)
        else:
            # No registry version or no git versions - keep all
            if registry_pkg:
                deduped.append(registry_pkg)
            deduped.extend(git_pkgs)

    return DeduplicationResult(deduped, removed_count, replacement_map)


def _remap_deps(deps: list[str], replacement_map: ReplacementMap) -> list[str]:
    """Remap dependencies using the replacement map, deduplicating."""
    seen: set[str] = set()
    result: list[str] = []
    for dep in deps:
        new_dep = replacement_map.get(dep, dep)
        if new_dep not in seen:
            seen.add(new_dep)
            result.append(new_dep)
    return result


def fix_dependency_references(
    packages: list[Package], replacement_map: ReplacementMap
) -> list[Package]:
    """Return packages with updated dependency references."""
    if not replacement_map:
        return packages

    return [
        replace(
            pkg,
            dependencies=_remap_deps(pkg.dependencies, replacement_map),
            build_dependencies=_remap_deps(pkg.build_dependencies, replacement_map),
        )
        for pkg in packages
    ]


def sort_packages(packages: list[Package]) -> list[Package]:
    """Sort packages by name, then version."""
    return sorted(packages, key=lambda p: p.to_key())


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "cargo_lock",
        type=Path,
        metavar="PATH",
        help="Path to Cargo.lock file",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Show what would be done without modifying the file",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Only show errors and final summary",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show detailed output",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        metavar="PATH",
        help="Write output to this file instead of modifying in-place",
    )
    return parser.parse_args(argv)


def configure_logging(quiet: bool, verbose: bool) -> None:
    """Configure logging based on command-line flags."""
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(
        level=level,
        format="%(message)s",
    )


class ExitCode(IntEnum):
    """Exit codes for the script."""

    SUCCESS = 0
    ERROR = 1
    NO_CHANGES = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Main entry point. Returns exit code."""
    args = parse_args(argv)
    configure_logging(args.quiet, args.verbose)

    cargo_lock: Path = args.cargo_lock

    if not cargo_lock.exists():
        logger.error("Error: %s not found", cargo_lock)
        return ExitCode.ERROR

    action = "Would deduplicate" if args.dry_run else "Deduplicating"
    logger.info("%s %s...", action, cargo_lock)

    content = cargo_lock.read_text()

    try:
        packages, lock_version = parse_cargo_lock(content)
    except tomllib.TOMLDecodeError as e:
        logger.error("Error parsing Cargo.lock: %s", e)
        return ExitCode.ERROR

    logger.info("  Found %d packages", len(packages))

    result = dedup_packages(packages)
    fixed = fix_dependency_references(result.packages, result.replacements)
    sorted_packages = sort_packages(fixed)

    if result.removed_count > 0:
        if not args.dry_run:
            new_content = format_cargo_lock(sorted_packages, lock_version)
            output_path = args.output if args.output else cargo_lock
            output_path.write_text(new_content)

        action = "Would remove" if args.dry_run else "Removed"
        logger.warning(
            "\n%s %d duplicate git-sourced packages", action, result.removed_count
        )
        logger.warning("Kept %d packages", len(sorted_packages))
        return ExitCode.SUCCESS

    logger.info("\nNo duplicate sources found to remove")
    return ExitCode.NO_CHANGES


if __name__ == "__main__":
    sys.exit(main())
