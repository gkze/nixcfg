"""Convert git-sourced gitoxide crates to crates.io sources in Cargo.lock.

GitButler uses gitoxide crates from git (branch=main), but these same versions
exist on crates.io. This causes issues with Nix's cargo vendor because having
the same crate from multiple sources creates conflicts.

This script:
1. Converts git-sourced gitoxide packages to crates.io sources (fetching checksums)
2. Removes duplicate packages (same name+version from both sources)
3. Updates dependency references to remove source qualifiers

Usage: python3 dedup_cargo_lock.py <path-to-Cargo.lock>
       python3 dedup_cargo_lock.py --dry-run <path-to-Cargo.lock>
"""

import argparse
import json
import logging
import sys
import tomllib
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from dataclasses import dataclass, field, replace
from enum import Enum, IntEnum
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

if TYPE_CHECKING:
    from collections.abc import Sequence

# Type aliases for clarity
type PackageKey = tuple[str, str]  # (name, version)

logger = logging.getLogger(__name__)

# Pre-fetched checksums for gitoxide packages (GitButler 0.18.6)
# These versions exist on both git and crates.io with identical content
_GITOXIDE_CHECKSUMS: dict[str, str] = {
    "gix@0.77.0": "3d8284d86a2f5c0987fbf7219a128815cc04af5a18f5fd7eec6a76d83c2b78cc",
    "gix-actor@0.37.1": "c345528d405eab51d20f505f5fe1a4680973953694e0292c6bbe97827daa55c4",
    "gix-attributes@0.29.0": "f47dabf8a50f1558c3a55d978440c7c4f22f87ac897bef03b4edbc96f6115966",
    "gix-bitmap@0.2.15": "5e150161b8a75b5860521cb876b506879a3376d3adc857ec7a9d35e7c6a5e531",
    "gix-chunk@0.4.12": "5c356b3825677cb6ff579551bb8311a81821e184453cbd105e2fc5311b288eeb",
    "gix-command@0.6.5": "46f9c425730a654835351e6da8c3c69ba1804f8b8d4e96d027254151138d5c64",
    "gix-commitgraph@0.31.0": "efdcba8048045baf15225daf949d597c3e6183d130245e22a7fbd27084abe63a",
    "gix-config@0.50.0": "b58e2ff8eef96b71f2c5e260f02ca0475caff374027c5cc5a29bda69fac67404",
    "gix-config-value@0.16.0": "2409cffa4fe8b303847d5b6ba8df9da9ba65d302fc5ee474ea0cac5afde79840",
    "gix-credentials@0.34.1": "316a12842fb761a7a6e9ae963d7bae9f0f4c433f242282df91192ef084b1891c",
    "gix-date@0.12.1": "fe4a31bab8159e233094fa70d2e5fd3ec6f19e593f67e6ae01281daa48f8d8e7",
    "gix-diff@0.57.1": "3506936e63ce14cd54b5f28ed06c8e43b92ef9f41c2238cc0bc271a9259b4e90",
    "gix-dir@0.19.0": "709d9fad32d2eb8b0129850874246569e801b6d5877e0c41356c23e9e2501e06",
    "gix-discover@0.45.0": "42ce096dc132533802a09d6fd5d4008858f2038341dfe2e69e0d0239edb359de",
    "gix-error@0.0.0": "7dffc9ca4dfa4f519a3d2cf1c038919160544923577ac60f45bcb602a24d82c6",
    "gix-features@0.45.2": "d56aad357ae016449434705033df644ac6253dfcf1281aad3af3af9e907560d1",
    "gix-filter@0.24.1": "10c02464962482570c1f94ad451a608c4391514f803e8074662d02c5629a25dc",
    "gix-fs@0.18.2": "785b9c499e46bc78d7b81c148c21b3fca18655379ee729a856ed19ce50d359ec",
    "gix-glob@0.23.0": "e8546300aee4c65c5862c22a3e321124a69b654a61a8b60de546a9284812b7e2",
    "gix-hash@0.21.2": "e153930f42ccdab8a3306b1027cd524879f6a8996cd0c474d18b0e56cae7714d",
    "gix-hashtable@0.11.0": "222f7428636020bef272a87ed833ea48bf5fb3193f99852ae16fbb5a602bd2f0",
    "gix-ignore@0.18.0": "dfa727fdf54fd9fb53fa3fbb1a5c17172d3073e8e336bf155f3cac3e25b81b21",
    "gix-index@0.45.1": "9ea6d3e9e11647ba49f441dea0782494cc6d2875ff43fa4ad9094e6957f42051",
    "gix-lock@20.0.1": "115268ae5e3b3b7bc7fc77260eecee05acca458e45318ca45d35467fa81a3ac5",
    "gix-mailmap@0.29.0": "c785c575f26335ba96ab514610ee9d6aa5839a75a98290141c63218e950f8d5e",
    "gix-merge@0.10.0": "c1fc77875f74129523060283b79325957503277b98a2e8521cf0680f1cfd6c69",
    "gix-negotiate@0.25.0": "f3cee7e32e6198e356caef0e4b8321bc2f9b2afeb76f870c0bf9aa05fe53edb6",
    "gix-object@0.54.1": "363d6a879c52e4890180e0ffa7d8c9a364fd0b7e807caa368e860b80e8d0bc81",
    "gix-odb@0.74.0": "165a907df369a12ed4330faf8baf7ae597aadb08cfacb4ed8649f93d90bcc0c5",
    "gix-pack@0.64.1": "b04a73d5ab07ea0faae55e2c0ae6f24e36e365ac8ce140394dee3a2c89cd4366",
    "gix-packetline@0.20.0": "fad0ffb982a289888087a165d3e849cbac724f2aa5431236b050dd2cb9c7de31",
    "gix-path@0.10.22": "7cb06c3e4f8eed6e24fd915fa93145e28a511f4ea0e768bae16673e05ed3f366",
    "gix-pathspec@0.14.0": "ed9e0c881933c37a7ef45288d6c5779c4a7b3ad240b4c37657e1d9829eb90085",
    "gix-prompt@0.12.0": "974b142ea650fb0050a301958f49c8cc68929c36f686e9606a381ce39da34fd9",
    "gix-protocol@0.55.0": "02c5dfd068789442c5709e702ef42d851765f2c09a11bf0a13749d24363f4d07",
    "gix-quote@0.6.1": "e912ec04b7b1566a85ad486db0cab6b9955e3e32bcd3c3a734542ab3af084c5b",
    "gix-ref@0.57.0": "ccb33aa97006e37e9e83fde233569a66b02ed16fd4b0406cdf35834b06cf8a63",
    "gix-refspec@0.35.0": "dcbba6ae5389f4021f73a2d62a4195aace7db1e8bb684b25521d3d685f57da02",
    "gix-revision@0.39.0": "91898c83b18c635696f7355d171cfa74a52f38022ff89581f567768935ebc4c8",
    "gix-revwalk@0.25.0": "0d063699278485016863d0d2bb0db7609fd2e8ba9a89379717bf06fd96949eb2",
    "gix-sec@0.12.2": "ea9962ed6d9114f7f100efe038752f41283c225bb507a2888903ac593dffa6be",
    "gix-shallow@0.7.0": "9c1c467fb9f7ec1d33613c2ea5482de514bcb84b8222a793cdc4c71955832356",
    "gix-status@0.24.0": "ed0d94c685a831c679ca5454c22f350e8c233f50dcf377ca00d858bcba9696d2",
    "gix-submodule@0.24.0": "efee2a61198413d80de10028aa507344537827d776ade781760130721bec2419",
    "gix-tempfile@20.0.1": "ad89218e74850f42d364ed3877c7291f0474c8533502df91bb877ecc5cb0dd40",
    "gix-trace@0.1.17": "6e42a4c2583357721ba2d887916e78df504980f22f1182df06997ce197b89504",
    "gix-transport@0.52.1": "a4d4ed02a2ebe771a26111896ecda0b98b58ed35e1d9c0ccf07251c1abb4918d",
    "gix-traverse@0.51.1": "d052b83d1d1744be95ac6448ac02f95f370a8f6720e466be9ce57146e39f5280",
    "gix-url@0.34.0": "cff1996dfb9430b3699d89224c674169c1ae355eacc52bf30a03c0b8bffe73d9",
    "gix-utils@0.3.1": "befcdbdfb1238d2854591f760a48711bed85e72d80a10e8f2f93f656746ef7c5",
    "gix-validate@0.10.1": "5b1e63a5b516e970a594f870ed4571a8fdcb8a344e7bd407a20db8bd61dbfde4",
    "gix-worktree@0.46.0": "1cfb7ce8cdbfe06117d335d1ad329351468d20331e0aafd108ceb647c1326aca",
    "gix-worktree-state@0.24.0": "7f34c19e29e0a359b97faaf92fdd053d4cc33aa0e69cabb30f0e120effe4ff3b",
}

# Cache for crates.io checksums (runtime fetches)
_checksum_cache: dict[PackageKey, str] = {}


class SourceType(Enum):
    """Classification of package sources in Cargo.lock."""

    REGISTRY = "registry"
    GIT_GITOXIDE = "git_gitoxide"  # Specifically gitoxide git sources
    GIT_OTHER = "git_other"
    OTHER = "other"


def classify_source(source: str) -> SourceType:
    """Classify a package source string."""
    if source.startswith("registry+"):
        return SourceType.REGISTRY
    if source.startswith("git+") and "GitoxideLabs/gitoxide" in source:
        return SourceType.GIT_GITOXIDE
    if source.startswith("git+"):
        return SourceType.GIT_OTHER
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

    def dep_key_with_source(self) -> str:
        """Create a dependency reference key with source."""
        return f"{self.name} {self.version} ({self.source})"


def fetch_checksum(name: str, version: str) -> str | None:
    """Fetch checksum from embedded data or crates.io API."""
    key = (name, version)

    # Check runtime cache first
    if key in _checksum_cache:
        return _checksum_cache[key]

    # Check embedded checksums (for offline/sandbox use)
    embedded_key = f"{name}@{version}"
    if embedded_key in _GITOXIDE_CHECKSUMS:
        checksum = _GITOXIDE_CHECKSUMS[embedded_key]
        _checksum_cache[key] = checksum
        return checksum

    # Fall back to crates.io API
    url = (
        "https://crates.io/api/v1/crates/"
        f"{quote(name, safe='')}/{quote(version, safe='')}"
    )
    try:
        req = urllib.request.Request(  # noqa: S310
            url,
            headers={"User-Agent": "nix-cargo-dedup/1.0"},
        )
        with urllib.request.urlopen(req, timeout=10) as response:  # noqa: S310
            data = json.loads(response.read().decode())
            checksum = data.get("version", {}).get("checksum")
            if checksum:
                _checksum_cache[key] = checksum
                return checksum
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        logger.warning("Failed to fetch checksum for %s %s: %s", name, version, e)
    return None


def parse_cargo_lock(content: str) -> tuple[list[Package], int]:
    """Parse Cargo.lock into a list of Package objects."""
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
        for key, getter in key_order:
            value = getter(pkg)
            if value:
                lines.extend(format_value(key, value))
        for key, value in pkg.extra_fields.items():
            if value:
                lines.extend(format_value(key, value))
        lines.append("")

    return "\n".join(lines)


REGISTRY_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"


def convert_gitoxide_to_registry(packages: list[Package]) -> tuple[list[Package], int]:
    """Convert git-sourced gitoxide packages to crates.io sources.

    Returns (converted_packages, conversion_count).
    """
    converted = []
    conversion_count = 0

    for pkg in packages:
        if pkg.source_type() == SourceType.GIT_GITOXIDE:
            checksum = fetch_checksum(pkg.name, pkg.version)
            if checksum:
                logger.info("  Converting to crates.io: %s %s", pkg.name, pkg.version)
                converted.append(
                    replace(pkg, source=REGISTRY_SOURCE, checksum=checksum),
                )
                conversion_count += 1
            else:
                logger.warning(
                    "  Could not fetch checksum for %s %s, keeping git source",
                    pkg.name,
                    pkg.version,
                )
                converted.append(pkg)
        else:
            converted.append(pkg)

    return converted, conversion_count


def remove_duplicates(packages: list[Package]) -> tuple[list[Package], int]:
    """Remove duplicate packages (same name+version from multiple sources).

    Prefers registry sources over git sources.
    """
    # Group by (name, version)
    by_key: defaultdict[PackageKey, list[Package]] = defaultdict(list)
    for pkg in packages:
        by_key[pkg.to_key()].append(pkg)

    deduped = []
    removed_count = 0

    for key, pkgs in by_key.items():
        if len(pkgs) == 1:
            deduped.append(pkgs[0])
        else:
            # Multiple entries for same name+version - keep registry, remove others
            registry_pkgs = [p for p in pkgs if p.source_type() == SourceType.REGISTRY]
            if registry_pkgs:
                deduped.append(registry_pkgs[0])
                removed_count += len(pkgs) - 1
                logger.info(
                    "  Removing %d duplicate(s) for: %s %s",
                    len(pkgs) - 1,
                    key[0],
                    key[1],
                )
            else:
                # No registry version, keep first
                deduped.append(pkgs[0])
                removed_count += len(pkgs) - 1

    return deduped, removed_count


def strip_git_commit_hash(source: str) -> str:
    """Strip the commit hash from a git source URL.

    Cargo.lock dependency strings use the shortened form:
      git+https://github.com/GitoxideLabs/gitoxide?branch=main
    But package sources include the commit hash:
      git+https://github.com/GitoxideLabs/gitoxide?branch=main#12924d75...

    This returns the shortened form for building replacement maps.
    """
    if "#" in source:
        return source.split("#", maxsplit=1)[0]
    return source


def build_dep_replacement_map(
    original: list[Package],
    final: list[Package],
) -> dict[str, str]:
    """Build a map of old dependency strings to new ones.

    Maps qualified dependency strings to simple "pkg version" format for:
    - Packages converted from git to registry
    - Packages removed as duplicates (git version removed, registry kept)
    - Registry packages that had duplicates (to unify all references)

    Note: Dependency strings in Cargo.lock use shortened git URLs without
    the commit hash, so we strip the hash when building the map keys.
    """
    replacement_map = {}

    # Create lookup of final packages by key
    final_by_key = {p.to_key(): p for p in final}

    # Find all packages that were deduplicated (had multiple sources)
    key_counts = Counter(p.to_key() for p in original)
    deduped_keys = {k for k, count in key_counts.items() if count > 1}

    for pkg in original:
        key = pkg.to_key()
        new_dep = f"{pkg.name} {pkg.version}"
        source_type = pkg.source_type()

        if source_type == SourceType.GIT_GITOXIDE:
            # Check if this key exists in final packages as registry
            if key in final_by_key:
                final_pkg = final_by_key[key]
                if final_pkg.source_type() == SourceType.REGISTRY:
                    # Map shortened git dep string to simple version
                    short_source = strip_git_commit_hash(pkg.source)
                    old_dep = f"{pkg.name} {pkg.version} ({short_source})"
                    replacement_map[old_dep] = new_dep

        elif source_type == SourceType.GIT_OTHER and key in deduped_keys:
            # Non-gitoxide git package that had a duplicate (e.g., file-id from notify)
            # If final version is registry, map the git reference to simple version
            if key in final_by_key:
                final_pkg = final_by_key[key]
                if final_pkg.source_type() == SourceType.REGISTRY:
                    short_source = strip_git_commit_hash(pkg.source)
                    old_dep = f"{pkg.name} {pkg.version} ({short_source})"
                    replacement_map[old_dep] = new_dep

        elif source_type == SourceType.REGISTRY and key in deduped_keys:
            # This registry package had a duplicate (git version was removed)
            # Simplify its qualified references too
            old_dep = f"{pkg.name} {pkg.version} ({pkg.source})"
            replacement_map[old_dep] = new_dep

    return replacement_map


def fix_dependency_references(
    packages: list[Package],
    replacement_map: dict[str, str],
) -> list[Package]:
    """Update dependency references using the replacement map."""
    if not replacement_map:
        return packages

    def remap_deps(deps: list[str]) -> list[str]:
        seen: set[str] = set()
        result: list[str] = []
        for dep in deps:
            new_dep = replacement_map.get(dep, dep)
            if new_dep not in seen:
                seen.add(new_dep)
                result.append(new_dep)
        return result

    return [
        replace(
            pkg,
            dependencies=remap_deps(pkg.dependencies),
            build_dependencies=remap_deps(pkg.build_dependencies),
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


def configure_logging(*, quiet: bool, verbose: bool) -> None:
    """Configure logging based on command-line flags."""
    if quiet:
        level = logging.WARNING
    elif verbose:
        level = logging.DEBUG
    else:
        level = logging.INFO

    logging.basicConfig(level=level, format="%(message)s")


class ExitCode(IntEnum):
    """Exit codes for the script."""

    SUCCESS = 0
    ERROR = 1
    NO_CHANGES = 2


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI workflow and return an exit code."""
    args = parse_args(argv)
    configure_logging(quiet=args.quiet, verbose=args.verbose)

    cargo_lock: Path = args.cargo_lock

    if not cargo_lock.exists():
        logger.error("Error: %s not found", cargo_lock)
        return ExitCode.ERROR

    action = "Would process" if args.dry_run else "Processing"
    logger.info("%s %s...", action, cargo_lock)

    content = cargo_lock.read_text()

    try:
        packages, lock_version = parse_cargo_lock(content)
    except tomllib.TOMLDecodeError:
        logger.exception("Error parsing Cargo.lock")
        return ExitCode.ERROR

    logger.info("  Found %d packages", len(packages))

    # Step 1: Convert gitoxide git sources to crates.io
    logger.info("\nConverting gitoxide git sources to crates.io...")
    converted, convert_count = convert_gitoxide_to_registry(packages)

    # Step 2: Remove duplicates
    logger.info("\nRemoving duplicates...")
    deduped, dup_count = remove_duplicates(converted)

    # Step 3: Build replacement map and fix dependencies
    replacement_map = build_dep_replacement_map(packages, deduped)
    fixed = fix_dependency_references(deduped, replacement_map)

    # Step 4: Sort
    sorted_packages = sort_packages(fixed)

    total_changes = convert_count + dup_count

    if total_changes > 0:
        if not args.dry_run:
            new_content = format_cargo_lock(sorted_packages, lock_version)
            output_path = args.output or cargo_lock
            output_path.write_text(new_content)

        action = "Would make" if args.dry_run else "Made"
        logger.warning(
            "\n%s %d changes: %d conversions, %d duplicates removed",
            action,
            total_changes,
            convert_count,
            dup_count,
        )
        logger.warning("Result: %d packages", len(sorted_packages))
        return ExitCode.SUCCESS

    logger.info("\nNo changes needed")
    return ExitCode.NO_CHANGES


if __name__ == "__main__":
    sys.exit(main())
