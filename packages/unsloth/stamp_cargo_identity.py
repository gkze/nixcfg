"""Stamp Unsloth Desktop's release and Git identities into its Cargo documents.

``desktop.nix`` runs this against copies of the immutable upstream
``Cargo.toml``/``Cargo.lock`` and turns the difference into ``cargoPatches``, so
cargo vendoring and the offline build consume the same identities. Both
documents are edited through ``tomlkit``, which preserves every byte outside
the three stamped values.
"""

import re
from pathlib import Path
from typing import TYPE_CHECKING

import tomlkit
from tomlkit.items import AoT, InlineTable, Table

from lib.codemods.source_patch import source_tree_parser

if TYPE_CHECKING:
    from collections.abc import Sequence

    from tomlkit.toml_document import TOMLDocument

CARGO_MANIFEST = Path("studio/src-tauri/Cargo.toml")
CARGO_LOCK = Path("studio/src-tauri/Cargo.lock")
_DESKTOP_PACKAGE = "unsloth-studio"
_FIX_PATH_ENV_PACKAGE = "fix-path-env"
_FIX_PATH_ENV_URL = "https://github.com/tauri-apps/fix-path-env-rs"
_DESKTOP_VERSION = re.compile(
    r"[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z]+(?:[.-][0-9A-Za-z]+)*)?",
    re.ASCII,
)
_FIX_PATH_ENV_SOURCE = re.compile(
    rf"git\+{re.escape(_FIX_PATH_ENV_URL)}"
    r"(?:\?rev=(?P<revision>[0-9a-f]{40}))?#(?P<commit>[0-9a-f]{40})",
    re.ASCII,
)


def _manifest_tables(manifest: TOMLDocument) -> tuple[Table, Table]:
    package = manifest.get("package")
    if not isinstance(package, Table) or package.get("name") != _DESKTOP_PACKAGE:
        msg = f"Cargo.toml must define package {_DESKTOP_PACKAGE}"
        raise RuntimeError(msg)
    if not isinstance(package.get("version"), str):
        msg = "Cargo.toml package version must be a string"
        raise TypeError(msg)
    dependencies = manifest.get("dependencies")
    if not isinstance(dependencies, Table):
        msg = "Cargo.toml must contain object dependencies"
        raise TypeError(msg)
    return package, dependencies


def _lock_package(lock: TOMLDocument, name: str) -> Table:
    packages = lock.get("package")
    if not isinstance(packages, AoT):
        msg = "Cargo.lock must contain object package entries"
        raise TypeError(msg)
    matches = [entry for entry in packages if entry.get("name") == name]
    if len(matches) != 1:
        msg = f"Cargo.lock must contain one {name} package, found {len(matches)}"
        raise RuntimeError(msg)
    return matches[0]


def _fix_path_env_revision(dependency: object, lock_package: Table) -> str:
    """Derive and cross-check the locked fix-path-env commit."""
    if not isinstance(dependency, InlineTable):
        msg = "Cargo.toml fix-path-env dependency must be an inline table"
        raise TypeError(msg)
    if dependency.get("git") != _FIX_PATH_ENV_URL:
        msg = "Cargo.toml fix-path-env dependency uses an unexpected Git source"
        raise RuntimeError(msg)
    if set(dependency) - {"git", "rev"}:
        msg = "Cargo.toml fix-path-env dependency has unsupported source selectors"
        raise RuntimeError(msg)

    lock_source_id = lock_package.get("source")
    if not isinstance(lock_source_id, str):
        msg = "Cargo.lock fix-path-env source must be a string"
        raise TypeError(msg)
    source_match = _FIX_PATH_ENV_SOURCE.fullmatch(lock_source_id)
    if source_match is None:
        msg = (
            "Cargo.lock fix-path-env source is not an immutable supported Git identity"
        )
        raise RuntimeError(msg)
    revision = source_match.group("commit")
    pinned_revision = source_match.group("revision")
    if pinned_revision is not None and pinned_revision != revision:
        msg = "Cargo.lock fix-path-env rev selector disagrees with its locked commit"
        raise RuntimeError(msg)
    manifest_revision = dependency.get("rev")
    if manifest_revision is not None and manifest_revision != revision:
        msg = "Cargo.toml fix-path-env rev selector disagrees with Cargo.lock"
        raise RuntimeError(msg)
    return str(revision)


def _pinned_git_dependency(revision: str) -> InlineTable:
    """Render ``{ git = ..., rev = ... }`` with Cargo's conventional padding."""
    dependency = tomlkit.inline_table()
    dependency.add(tomlkit.ws(" "))
    dependency.append("git", _FIX_PATH_ENV_URL)
    dependency.append("rev", revision)
    dependency.add(tomlkit.ws(" "))
    return dependency


def stamp_documents(
    manifest_source: str,
    lock_source: str,
    desktop_version: str,
) -> tuple[str, str]:
    """Return both Cargo documents with release and Git identities stamped."""
    if _DESKTOP_VERSION.fullmatch(desktop_version) is None:
        msg = f"invalid Unsloth desktop version: {desktop_version!r}"
        raise RuntimeError(msg)
    manifest = tomlkit.parse(manifest_source)
    lock = tomlkit.parse(lock_source)

    package, dependencies = _manifest_tables(manifest)
    desktop_lock = _lock_package(lock, _DESKTOP_PACKAGE)
    if desktop_lock.get("version") != package["version"]:
        msg = "Cargo.toml and Cargo.lock desktop versions disagree before release stamping"
        raise RuntimeError(msg)
    fix_path_env_lock = _lock_package(lock, _FIX_PATH_ENV_PACKAGE)
    revision = _fix_path_env_revision(
        dependencies.get(_FIX_PATH_ENV_PACKAGE),
        fix_path_env_lock,
    )

    package["version"] = desktop_version
    dependencies[_FIX_PATH_ENV_PACKAGE] = _pinned_git_dependency(revision)
    desktop_lock["version"] = desktop_version
    fix_path_env_lock["source"] = f"git+{_FIX_PATH_ENV_URL}?rev={revision}#{revision}"
    return tomlkit.dumps(manifest), tomlkit.dumps(lock)


def stamp_tree(source_root: Path, desktop_version: str) -> None:
    """Stamp both Cargo documents only after both validate."""
    manifest_path = source_root / CARGO_MANIFEST
    lock_path = source_root / CARGO_LOCK
    manifest, lock = stamp_documents(
        manifest_path.read_text(encoding="utf-8"),
        lock_path.read_text(encoding="utf-8"),
        desktop_version,
    )
    manifest_path.write_text(manifest, encoding="utf-8")
    lock_path.write_text(lock, encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Stamp one copied Unsloth Cargo tree for the candidate desktop version."""
    parser = source_tree_parser(__doc__)
    parser.add_argument("--desktop-version", required=True)
    args = parser.parse_args(argv)
    stamp_tree(args.source_root, args.desktop_version)
    return 0


if __name__ == "__main__":  # pragma: no cover -- packaged CLI guard
    raise SystemExit(main())
