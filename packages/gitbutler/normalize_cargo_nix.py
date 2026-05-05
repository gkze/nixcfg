#!/usr/bin/env python3
"""Normalize generated crate2nix output for the checked-in GitButler Cargo.nix."""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path


def _bootstrap_repo_import_path() -> None:
    """Add the repository root to ``sys.path`` for direct script execution."""
    if env_root := os.environ.get("REPO_ROOT"):
        sys.path.insert(0, str(Path(env_root).expanduser().resolve()))
        return

    candidates: list[Path] = []
    cwd = Path.cwd().resolve()
    candidates.extend((cwd, *cwd.parents))

    script_path = Path(__file__).resolve()
    for candidate in (script_path.parent, *script_path.parents):
        if candidate not in candidates:
            candidates.append(candidate)

    for candidate in candidates:
        if (candidate / ".root").is_file():
            sys.path.insert(0, str(candidate))
            return

    msg = f"Could not find repo root for {script_path}"
    raise RuntimeError(msg)


_bootstrap_repo_import_path()

from lib.cargo_nix_normalizer import normalize as normalize_cargo_nix  # noqa: E402
from lib.update.paths import get_repo_root  # noqa: E402

_GIX_TRACE_REGISTRY_PACKAGE = re.compile(
    r"(?P<package>(?P<indent>[ \t]*)"
    r'"registry\+https://github\.com/rust-lang/crates\.io-index#'
    r'gix-trace@0\.1\.18" = rec \{.*?'
    r"[ \t]*resolvedDefaultFeatures = \[ \"default\" \];\n"
    r"(?P=indent)\};)",
    re.DOTALL | re.MULTILINE,
)
_GIX_TRACE_DISAMBIGUATOR = "crate2nix-source-registry"
_GIX_TRACE_FEATURES_LINE = re.compile(r"(?m)^(?P<indent>[ \t]*)features = \{\n")
_GIX_TRACE_REGISTRY_DEPENDENCY = re.compile(
    r"(?P<dependency>(?P<indent>[ \t]*)\{\n"
    r"(?P=indent)  name = \"gix-trace\";\n"
    r"(?P=indent)  packageId = \"registry\+https://github\.com/rust-lang/"
    r"crates\.io-index#gix-trace@0\.1\.18\";\n"
    r"(?P=indent)\})",
    re.MULTILINE,
)
_GITBUTLER_TAURI_PACKAGE_PREFIX = re.compile(
    r"(?P<package>(?P<indent>[ \t]*)\"gitbutler-tauri\" = rec \{\n"
    r"(?P=indent)  crateName = \"gitbutler-tauri\";.*?"
    r"[ \t]*buildDependencies = \[)",
    re.DOTALL | re.MULTILINE,
)
_DEPENDENCIES_LINE = re.compile(r"(?m)^(?P<indent>[ \t]*)dependencies = \[\n")


def _disambiguate_registry_gix_trace(text: str) -> str:
    """Give the crates.io gix-trace package a distinct rustc metadata hash."""

    def replace_package(match: re.Match[str]) -> str:
        package = match.group("package")
        if _GIX_TRACE_DISAMBIGUATOR in package:
            return package

        features_match = _GIX_TRACE_FEATURES_LINE.search(package)
        if features_match is None:
            return package

        feature_item_indent = f"{features_match.group('indent')}  "
        package = package.replace(
            features_match.group(0),
            f'{features_match.group(0)}{feature_item_indent}"{_GIX_TRACE_DISAMBIGUATOR}" = [ ];\n',
            1,
        )
        return package.replace(
            '        resolvedDefaultFeatures = [ "default" ];',
            f'        resolvedDefaultFeatures = [ "{_GIX_TRACE_DISAMBIGUATOR}" "default" ];',
            1,
        ).replace(
            '      resolvedDefaultFeatures = [ "default" ];',
            f'      resolvedDefaultFeatures = [ "{_GIX_TRACE_DISAMBIGUATOR}" "default" ];',
            1,
        )

    text = _GIX_TRACE_REGISTRY_PACKAGE.sub(replace_package, text, count=1)

    def replace_dependency(match: re.Match[str]) -> str:
        dependency = match.group("dependency")
        indent = match.group("indent")
        package_id_line = f'{indent}  packageId = "registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18";\n'
        return dependency.replace(
            package_id_line,
            f'{package_id_line}{indent}  features = [ "{_GIX_TRACE_DISAMBIGUATOR}" ];\n',
            1,
        )

    return _GIX_TRACE_REGISTRY_DEPENDENCY.sub(replace_dependency, text, count=1)


def _ensure_gitbutler_tauri_but_dependency(text: str) -> str:
    """Restore the optional ``but`` edge needed by ``builtin-but``."""

    def replace_package(match: re.Match[str]) -> str:
        package = match.group("package")
        if 'name = "but";' in package and 'packageId = "but";' in package:
            return package

        dependencies_match = _DEPENDENCIES_LINE.search(package)
        if dependencies_match is None:
            return package

        indent = dependencies_match.group("indent")
        dependency = (
            f"{indent}  {{\n"
            f'{indent}    name = "but";\n'
            f'{indent}    packageId = "but";\n'
            f"{indent}    optional = true;\n"
            f"{indent}  }}\n"
        )
        return package.replace(
            dependencies_match.group(0),
            dependencies_match.group(0) + dependency,
            1,
        )

    return _GITBUTLER_TAURI_PACKAGE_PREFIX.sub(replace_package, text, count=1)


def _resolve_path(path_text: str) -> Path:
    """Resolve one CLI path against the repository root."""
    path = Path(path_text).expanduser()
    if path.is_absolute():
        return path
    return get_repo_root() / path


def normalize(text: str) -> tuple[str, int, bool]:
    """Return normalized GitButler Cargo.nix text plus replacement counts."""
    normalized, path_rewrites, added_root_src = normalize_cargo_nix(
        text,
        local_path_prefixes=("crates",),
    )
    return (
        _ensure_gitbutler_tauri_but_dependency(
            _disambiguate_registry_gix_trace(normalized)
        ),
        path_rewrites,
        added_root_src,
    )


def main() -> int:
    """Normalize a GitButler Cargo.nix file in place and report what changed."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path",
        nargs="?",
        default=str(get_repo_root() / "packages/gitbutler/Cargo.nix"),
    )
    args = parser.parse_args()

    path = _resolve_path(args.path)
    original = path.read_text()
    normalized, path_rewrites, added_root_src = normalize(original)

    if normalized != original:
        path.write_text(normalized)

    status = []
    status.append("added rootSrc" if added_root_src else "rootSrc already present")
    status.append(f"rewrote {path_rewrites} source path(s)")
    status.append("updated file" if normalized != original else "no content change")
    sys.stdout.write(f"{path}: " + ", ".join(status) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
