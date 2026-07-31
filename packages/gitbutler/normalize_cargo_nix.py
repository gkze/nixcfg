"""Normalize generated crate2nix output for the checked-in GitButler Cargo.nix."""

from __future__ import annotations

import re

from lib.cargo_nix_normalizer import normalize as normalize_cargo_nix

_GIX_TRACE_REGISTRY_PACKAGE = re.compile(
    r"(?P<package>(?P<indent>[ \t]*)"
    r'"registry\+https://github\.com/rust-lang/crates\.io-index#'
    r'gix-trace@0\.1\.18" = rec \{.*?'
    r"[ \t]*resolvedDefaultFeatures = \[ \"default\" \];\n"
    r"(?P=indent)\};)",
    re.DOTALL | re.MULTILINE,
)
_REGISTRY_SOURCE_DISAMBIGUATOR = "crate2nix-source-registry"
_GIX_TRACE_FEATURES_LINE = re.compile(r"(?m)^(?P<indent>[ \t]*)features = \{\n")
_GIX_TRACE_REGISTRY_DEPENDENCY = re.compile(
    r"(?P<dependency>(?P<indent>[ \t]*)\{\n"
    r"(?P=indent)  name = \"gix-trace\";\n"
    r"(?P=indent)  packageId = \"registry\+https://github\.com/rust-lang/"
    r"crates\.io-index#gix-trace@0\.1\.18\";\n"
    r"(?P=indent)\})",
    re.MULTILINE,
)
_GIX_VALIDATE_REGISTRY_PACKAGE = re.compile(
    r"(?P<package>(?P<indent>[ \t]*)\"registry\+https://github\.com/"
    r"rust-lang/crates\.io-index#gix-validate@0\.11\.2\" = rec \{.*?\n"
    r"(?P=indent)\};)",
    re.DOTALL | re.MULTILINE,
)
_GIX_VALIDATE_REGISTRY_DEPENDENCY = re.compile(
    r"(?P<dependency>(?P<indent>[ \t]*)\{\n"
    r"(?P=indent)  name = \"gix-validate\";\n"
    r"(?P=indent)  packageId = \"registry\+https://github\.com/rust-lang/"
    r"crates\.io-index#gix-validate@0\.11\.2\";\n"
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
        if _REGISTRY_SOURCE_DISAMBIGUATOR in package:
            return package

        features_match = _GIX_TRACE_FEATURES_LINE.search(package)
        if features_match is None:
            return package

        feature_item_indent = f"{features_match.group('indent')}  "
        source_line = (
            f'{feature_item_indent}"{_REGISTRY_SOURCE_DISAMBIGUATOR}" = [ ];\n'
        )
        package = package.replace(
            features_match.group(0),
            f"{features_match.group(0)}{source_line}",
            1,
        )
        return package.replace(
            '        resolvedDefaultFeatures = [ "default" ];',
            f'        resolvedDefaultFeatures = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" "default" ];',
            1,
        ).replace(
            '      resolvedDefaultFeatures = [ "default" ];',
            f'      resolvedDefaultFeatures = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" "default" ];',
            1,
        )

    text = _GIX_TRACE_REGISTRY_PACKAGE.sub(replace_package, text, count=1)

    def replace_dependency(match: re.Match[str]) -> str:
        dependency = match.group("dependency")
        indent = match.group("indent")
        package_id_line = f'{indent}  packageId = "registry+https://github.com/rust-lang/crates.io-index#gix-trace@0.1.18";\n'
        return dependency.replace(
            package_id_line,
            f'{package_id_line}{indent}  features = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" ];\n',
            1,
        )

    return _GIX_TRACE_REGISTRY_DEPENDENCY.sub(replace_dependency, text, count=1)


def _disambiguate_registry_gix_validate(text: str) -> str:
    """Give the crates.io gix-validate package a distinct rustc metadata hash."""

    def replace_package(match: re.Match[str]) -> str:
        package = match.group("package")
        if _REGISTRY_SOURCE_DISAMBIGUATOR in package:
            return package

        indent = match.group("indent")
        closing = f"{indent}}};"
        insertion = (
            f"{indent}  features = {{\n"
            f'{indent}    "{_REGISTRY_SOURCE_DISAMBIGUATOR}" = [ ];\n'
            f"{indent}  }};\n"
            f'{indent}  resolvedDefaultFeatures = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" ];\n'
        )
        return package.replace(closing, insertion + closing, 1)

    text = _GIX_VALIDATE_REGISTRY_PACKAGE.sub(replace_package, text, count=1)

    def replace_dependency(match: re.Match[str]) -> str:
        dependency = match.group("dependency")
        indent = match.group("indent")
        package_id_line = (
            f'{indent}  packageId = "registry+https://github.com/rust-lang/'
            'crates.io-index#gix-validate@0.11.2";\n'
        )
        return dependency.replace(
            package_id_line,
            f'{package_id_line}{indent}  features = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" ];\n',
            1,
        )

    return _GIX_VALIDATE_REGISTRY_DEPENDENCY.sub(replace_dependency, text, count=1)


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


def normalize(text: str) -> tuple[str, int, bool]:
    """Return normalized GitButler Cargo.nix text plus replacement counts."""
    normalized, path_rewrites, added_root_src = normalize_cargo_nix(
        text,
        local_path_prefixes=("crates",),
    )
    return (
        _ensure_gitbutler_tauri_but_dependency(
            _disambiguate_registry_gix_validate(
                _disambiguate_registry_gix_trace(normalized)
            )
        ),
        path_rewrites,
        added_root_src,
    )
