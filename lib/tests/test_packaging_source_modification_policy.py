"""Policy checks for package and overlay source modifications."""

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Final

from lib.codemods import packaging_source_policy
from lib.codemods.packaging_source_policy import (
    NixSubstituteAudit,
    NixSubstituteSite,
    PythonRewriteAudit,
    PythonRewriteSite,
)

if TYPE_CHECKING:
    import pytest


def _nix_sites(path: str, *commands: str) -> tuple[NixSubstituteSite, ...]:
    return tuple((path, command) for command in commands)


def _python_sites(path: str, *calls: str) -> tuple[PythonRewriteSite, ...]:
    return tuple((path, call) for call in calls)


# Baselines are not approval of these mechanisms. They make existing ad hoc source
# edits visible so new ones fail until they move to .patch files, lib.codemods, or
# are intentionally recorded as migration debt. Semantic identities avoid churn
# when unrelated edits move a rewrite to another line.
_ALLOWED_NIX_SUBSTITUTE_SITES: Final = (
    *_nix_sites(
        "overlays/github-desktop/default.nix",
        r"""substituteInPlace "$node_addon_api_header" --replace-fail 'static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>(-1);' 'static const napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>(0);'""",
    ),
    *_nix_sites(
        "overlays/rio/default.nix",
        r"""substituteInPlace "$out/Applications/Rio.app/Contents/Info.plist" --replace-fail '{{.Version}}.{{.Now.Format "20060102150405"}}' '${version}' --replace-fail '{{.Version}}' '${version}'""",
    ),
    # Buzz retains nixpkgs' inherited ONNX Runtime postPatch, then narrowly
    # reverses its Darwin tool/output pinning for static archive assembly.
    # outputChecks.allowedReferences and the validated candidate's
    # zero-store-reference gate independently reject any leaked store path.
    *_nix_sites(
        "packages/buzz/native/onnxruntime.nix",
        r"""substituteInPlace cmake/onnxruntime.cmake --replace-fail "/usr/bin/ar" "${cctools}/bin/ar" --replace-fail "/usr/bin/ld" "${ld64}/bin/ld" --replace-fail "/usr/bin/libtool" "${cctools.libtool}/bin/libtool" --replace-fail 'set(STATIC_FRAMEWORK_OUTPUT_DIR ''${CMAKE_CURRENT_BINARY_DIR}/''${CMAKE_BUILD_TYPE}-''${CMAKE_OSX_SYSROOT})' 'set(STATIC_FRAMEWORK_OUTPUT_DIR ''${CMAKE_CURRENT_BINARY_DIR}/buzz-static-framework-output)'""",
        # Restore the upstream empty runtime fallback that nixpkgs' inherited
        # postPatch pins to the package output; the reference gates stay final.
        r'''substituteInPlace onnxruntime/core/platform/posix/env.cc --replace-fail "$out/lib/" ""''',
        # Restore upstream's @rpath install name after the same inherited
        # postPatch rewrites it to the package output.
        r'''substituteInPlace cmake/onnxruntime.cmake --replace-fail "INSTALL_NAME_DIR $out/lib" "INSTALL_NAME_DIR @rpath"''',
    ),
    *_nix_sites(
        "packages/codex/default.nix",
        r"""substituteInPlace "${target}/src/tools/js_repl/mod.rs" --replace-fail '../../../../node-version.txt' '../../../node-version.txt'""",
    ),
    *_nix_sites(
        "packages/emdash/default.nix",
        r'''substituteInPlace ${appDir}/src/main/utils/userEnv.ts --replace-fail " -ilc 'env'" " -lc 'env'"''',
        r'''substituteInPlace node_modules/debug/src/common.js --replace-fail "require('ms')" "require('../../../out/main/ms-shim.cjs')"''',
        r'''substituteInPlace "$out/bin/emdash" --replace-fail "#!/usr/bin/env bash" "#!${stdenv.shell}" --replace-fail "@out@" "$out"''',
        r'''substituteInPlace "$out/bin/emdash" --replace-fail "#!/usr/bin/env bash" "#!${stdenv.shell}" --replace-fail "@out@" "$out"''',
    ),
    *_nix_sites(
        "packages/goose-desktop/default.nix",
        r'''substituteInPlace desktop/src/updates.ts --replace-fail "export const UPDATES_ENABLED = true;" "export const UPDATES_ENABLED = false;"''',
    ),
    *_nix_sites(
        "packages/mole-app/default.nix",
        r'''substituteInPlace "$out/bin/mole" --replace-fail 'SCRIPT_DIR="$(cd "$(dirname "''${BASH_SOURCE[0]}")" && pwd)"' "SCRIPT_DIR='$out/libexec/mole'"''',
    ),
    # These exact anchors must interpolate the realized derivation output path;
    # a static patch cannot know `$out`, while --replace-fail keeps drift fail-closed.
    *_nix_sites(
        "packages/paseo/onnxruntime-source.nix",
        r'''substituteInPlace onnxruntime/core/platform/env.h --replace-fail "GetRuntimePath() const { return PathString(); }" "GetRuntimePath() const { return PathString(\"$out/lib/\"); }"''',
        r'''substituteInPlace cmake/onnxruntime.cmake --replace-fail "INSTALL_NAME_DIR @rpath" "INSTALL_NAME_DIR $out/lib"''',
    ),
    *_nix_sites(
        "packages/scratch/default.nix",
        r"""substituteInPlace "$out/nix-support/setup-hook" --replace-fail '"x86_64-unknown-linux-gnu"' '"${rustTarget}"' --replace-fail 'target/x86_64-unknown-linux-gnu' 'target/${rustTarget}' --replace-fail 'CC_X86_64_UNKNOWN_LINUX_GNU' 'CC_${rustTargetEnv}' --replace-fail 'CXX_X86_64_UNKNOWN_LINUX_GNU' 'CXX_${rustTargetEnv}' --replace-fail 'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER' 'CARGO_TARGET_${rustTargetEnv}_LINKER'""",
    ),
    *_nix_sites(
        "packages/superset/default.nix",
        r"""substituteInPlace package.json --replace-fail '"postinstall": "./scripts/postinstall.sh"' '"postinstall": ""'""",
    ),
    *_nix_sites(
        "packages/zed-editor-nightly/default.nix",
        r'''substituteInPlace "$crateRoot/src/assets.rs" --replace-fail '#[folder = "../../assets"]' '#[folder = "workspace-assets"]' --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};' --replace-fail ".filter_map(|p| {" ".filter_map(|p: std::borrow::Cow<'static, str>| {"''',
        r"""substituteInPlace "$crateRoot/src/main.rs" --replace-fail 'include_bytes!("../../../script/uninstall.sh")' 'include_bytes!("../uninstall.sh")'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'""",
        r"""substituteInPlace "$crateRoot/src/filter_languages.rs" --replace-fail '#[folder = "../grammars/src/"]' '#[folder = "workspace-language-configs-src/"]'""",
        r"""substituteInPlace "$crateRoot/src/filter_languages.rs" --replace-fail '#[folder = "../languages/src/"]' '#[folder = "workspace-language-configs-src/"]'""",
        r"""substituteInPlace "$crateRoot/src/filter_languages.rs" --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../grammars/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'""",
        r"""substituteInPlace "$crateRoot/src/filter_languages.rs" --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../languages/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")' --replace-fail 'println!("cargo:rerun-if-changed=../zed/Cargo.toml");' 'println!("cargo:rerun-if-changed=./zed-Cargo.toml");'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'PathBuf::from("../extension_api/wit")' 'PathBuf::from("workspace-extension-api-wit")'""",
        r"""substituteInPlace "$path" --replace-fail 'path: "../extension_api/wit/' 'path: "workspace-extension-api-wit/'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail '    let mut path = std::path::PathBuf::from(&cargo_manifest_dir);' '    println!("cargo:rustc-env=ZED_REPO_DIR={}", cargo_manifest_dir);""",
        r"""substituteInPlace "$crateRoot/src/prompt_store.rs" --replace-fail 'include_str!("../../git_ui/src/commit_message_prompt.txt")' 'include_str!("../commit_message_prompt.txt")'""",
        r"""substituteInPlace "$crateRoot/src/lib.rs" --replace-fail 'include_str!("../../zed/RELEASE_CHANNEL")' 'include_str!("../RELEASE_CHANNEL")'""",
        r"""substituteInPlace "$crateRoot/build.rs" --replace-fail 'include_str!("../zed/Cargo.toml")' 'include_str!("./zed-Cargo.toml")'""",
        r"""substituteInPlace "$crateRoot/src/settings.rs" --replace-fail '#[folder = "../../assets"]' '#[folder = "workspace-assets"]' --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};'""",
        r"""substituteInPlace src/lib.rs --replace-fail 'concat!("../", std::env!("CARGO_PKG_README"))' '"../README.md"'""",
    ),
)
_ALLOWED_PYTHON_AD_HOC_REWRITE_SITES: Final = (
    # pnpm records a patch hash in multiple lockfile sections. The normalizer
    # derives the replacement from the patched file and validates the exact
    # old-hash occurrence count before rewriting the generated lockfile.
    *_python_sites(
        "packages/bb/normalize_pnpm_patch_hashes.py",
        r"""normalized.replace(old_hash, new_hash)""",
    ),
    *_python_sites(
        "packages/codex/patch_allocator_weak_linkage.py",
        r"""original.replace(_WEAK_LINKAGE_ATTR, '')""",
    ),
    *_python_sites(
        "packages/gitbutler/normalize_cargo_nix.py",
        r"""_GITBUTLER_TAURI_PACKAGE_PREFIX.sub(replace_package, text, count=1)""",
        r"""_GIX_TRACE_REGISTRY_DEPENDENCY.sub(replace_dependency, text, count=1)""",
        r"""_GIX_TRACE_REGISTRY_PACKAGE.sub(replace_package, text, count=1)""",
        r"""_GIX_VALIDATE_REGISTRY_DEPENDENCY.sub(replace_dependency, text, count=1)""",
        r"""_GIX_VALIDATE_REGISTRY_PACKAGE.sub(replace_package, text, count=1)""",
        r"""dependency.replace(package_id_line, f'{package_id_line}{indent}  features = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" ];\n', 1)""",
        r"""dependency.replace(package_id_line, f'{package_id_line}{indent}  features = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" ];\n', 1)""",
        r"""package.replace('        resolvedDefaultFeatures = [ "default" ];', f'        resolvedDefaultFeatures = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" "default" ];', 1)""",
        r"""package.replace('        resolvedDefaultFeatures = [ "default" ];', f'        resolvedDefaultFeatures = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" "default" ];', 1).replace('      resolvedDefaultFeatures = [ "default" ];', f'      resolvedDefaultFeatures = [ "{_REGISTRY_SOURCE_DISAMBIGUATOR}" "default" ];', 1)""",
        r"""package.replace(closing, insertion + closing, 1)""",
        r"""package.replace(dependencies_match.group(0), dependencies_match.group(0) + dependency, 1)""",
        r"""package.replace(features_match.group(0), f'{features_match.group(0)}{source_line}', 1)""",
    ),
)

_NIX_SUBSTITUTE_AUDIT = NixSubstituteAudit(_ALLOWED_NIX_SUBSTITUTE_SITES)
_PYTHON_REWRITE_AUDIT = PythonRewriteAudit(
    _ALLOWED_PYTHON_AD_HOC_REWRITE_SITES,
)


def _format_site_delta(
    actual: tuple[NixSubstituteSite | PythonRewriteSite, ...],
    allowed: tuple[NixSubstituteSite | PythonRewriteSite, ...],
) -> str:
    actual_counts = Counter(actual)
    allowed_counts = Counter(allowed)
    unexpected = actual_counts - allowed_counts
    missing = allowed_counts - actual_counts
    return f"Unexpected: {unexpected}\nMissing: {missing}"


def test_package_overlay_substitute_in_place_sites_are_baselined() -> None:
    """Require new Nix source rewrites to be explicit migration debt."""
    actual = _NIX_SUBSTITUTE_AUDIT.current_sites()

    assert Counter(actual) == Counter(_NIX_SUBSTITUTE_AUDIT.allowed_sites), (
        _format_site_delta(actual, _NIX_SUBSTITUTE_AUDIT.allowed_sites)
    )


def test_paseo_only_retains_realized_output_path_substitutions() -> None:
    """Keep Paseo's narrow output-path debt separate from other migrations."""
    actual = tuple(
        site
        for site in _NIX_SUBSTITUTE_AUDIT.current_sites()
        if site[0].startswith("packages/paseo/")
    )
    allowed = tuple(
        site
        for site in _NIX_SUBSTITUTE_AUDIT.allowed_sites
        if site[0].startswith("packages/paseo/")
    )

    assert Counter(actual) == Counter(allowed), _format_site_delta(actual, allowed)


def test_package_overlay_python_ad_hoc_rewrite_sites_are_baselined() -> None:
    """Require new Python ad hoc rewrites to use codemod helpers or be baselined."""
    actual = _PYTHON_REWRITE_AUDIT.current_sites()

    assert Counter(actual) == Counter(_PYTHON_REWRITE_AUDIT.allowed_sites), (
        _format_site_delta(actual, _PYTHON_REWRITE_AUDIT.allowed_sites)
    )


def test_python_rewrite_audit_excludes_sibling_updater_tests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Only sibling updater test modules are excluded from production auditing."""
    source = """def rewrite(target, payload):
    target.write_text(payload.replace("old", "new"))
"""
    package = tmp_path / "packages" / "demo"
    package.mkdir(parents=True)
    (package / "updater.py").write_text(source, encoding="utf-8")
    (package / "updater_test.py").write_text(source, encoding="utf-8")
    nested = package / "nested"
    nested.mkdir()
    (nested / "updater_test.py").write_text(source, encoding="utf-8")
    monkeypatch.setattr(packaging_source_policy, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(
        packaging_source_policy,
        "iter_target_paths",
        lambda *_args, **_kwargs: tuple(sorted(package.rglob("*.py"))),
    )

    actual = PythonRewriteAudit(allowed_sites=()).current_sites()

    assert actual == (
        *_python_sites(
            "packages/demo/nested/updater_test.py",
            "payload.replace('old', 'new')",
        ),
        *_python_sites(
            "packages/demo/updater.py",
            "payload.replace('old', 'new')",
        ),
    )
