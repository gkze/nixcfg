"""Policy checks for package and overlay source modifications."""

from __future__ import annotations

from collections import Counter
from typing import Final

from lib.codemods.packaging_source_policy import (
    NixSubstituteAudit,
    NixSubstituteSite,
    PythonRewriteAudit,
    PythonRewriteSite,
)


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
    *_nix_sites(
        "overlays/vim-plugin-overrides.nix",
        r"""substituteInPlace lua/codesnap/module.lua --replace-fail '${moduleLuaOld}' '${moduleLuaNew}'""",
        r"""substituteInPlace lua/codesnap/fetch.lua --replace-fail '${fetchLuaOld}' '${fetchLuaNew}'""",
        r"""substituteInPlace lua/codesnap/init.lua --replace-fail 'string.match(static.config.save_path, "%.(.+)$")' 'string.match(save_path, "%.(.+)$")'""",
        r"""substituteInPlace lua/codesnap/init.lua --replace-fail 'if matched_extension ~= "png" and matched_extension ~= nil then' 'if matched_extension ~= nil and matched_extension ~= "png" and matched_extension ~= "svg" and matched_extension ~= "html" then' --replace-fail 'error("The extension of save_path should be .png", 0)' 'error("The extension of save_path should be .png, .svg, or .html", 0)'""",
        r"""substituteInPlace lua/codesnap/init.lua --replace-fail 'require("generator").save_snapshot(config)' '${saveCallNew}'""",
        r"""substituteInPlace lua/codesnap/init.lua --replace-fail 'config.save_path' 'save_path'""",
        r"""substituteInPlace lua/codesnap/utils/table.lua --replace-fail 'if t1[k] == nil and v ~= nil then' 'if t1[k] == nil and v ~= nil and v ~= "none" then'""",
    ),
    *_nix_sites(
        "packages/codex/default.nix",
        r"""substituteInPlace "$out/core/src/tools/js_repl/mod.rs" --replace-fail '../../../../node-version.txt' '../../../node-version.txt'""",
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
        r"""substituteInPlace "$workspaceRoot/crates/release_channel/src/lib.rs" --replace-fail 'include_str!("../../zed/RELEASE_CHANNEL")' 'include_str!("../RELEASE_CHANNEL")'""",
        r'''substituteInPlace "$workspaceRoot/crates/assets/src/assets.rs" --replace-fail '#[folder = "../../assets"]' '#[folder = "workspace-assets"]' --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};' --replace-fail ".filter_map(|p| {" ".filter_map(|p: std::borrow::Cow<'static, str>| {"''',
        r"""substituteInPlace "$workspaceRoot/crates/settings/src/settings.rs" --replace-fail '#[folder = "../../assets"]' '#[folder = "workspace-assets"]' --replace-fail 'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};'""",
        r"""substituteInPlace "$workspaceRoot/crates/prompt_store/src/prompt_store.rs" --replace-fail 'include_str!("../../git_ui/src/commit_message_prompt.txt")' 'include_str!("../commit_message_prompt.txt")'""",
        r"""substituteInPlace "$workspaceRoot/crates/extension_host/build.rs" --replace-fail 'PathBuf::from("../extension_api/wit")' 'PathBuf::from("workspace-extension-api-wit")'""",
        r"""substituteInPlace "$path" --replace-fail 'path: "../extension_api/wit/' 'path: "workspace-extension-api-wit/'""",
        r"""substituteInPlace "$workspaceRoot/crates/remote_server/build.rs" --replace-fail 'include_str!("../zed/Cargo.toml")' 'include_str!("./zed-Cargo.toml")'""",
        r"""substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/build.rs" --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'""",
        r"""substituteInPlace "$workspaceRoot/crates/eval/build.rs" --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")'""",
        r"""substituteInPlace "$workspaceRoot/crates/eval_cli/build.rs" --replace-fail 'std::fs::read_to_string("../zed/Cargo.toml")' 'std::fs::read_to_string("./zed-Cargo.toml")' --replace-fail 'println!("cargo:rerun-if-changed=../zed/Cargo.toml");' 'println!("cargo:rerun-if-changed=./zed-Cargo.toml");'""",
        r"""substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" --replace-fail '#[folder = "../grammars/src/"]' '#[folder = "workspace-language-configs-src/"]'""",
        r"""substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" --replace-fail '#[folder = "../languages/src/"]' '#[folder = "workspace-language-configs-src/"]'""",
        r"""substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../grammars/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'""",
        r"""substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" --replace-fail 'concat!(env!("CARGO_MANIFEST_DIR"), "/../languages/src")' 'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")'""",
        r"""substituteInPlace "$workspaceRoot/crates/cli/src/main.rs" --replace-fail 'include_bytes!("../../../script/uninstall.sh")' 'include_bytes!("../uninstall.sh")'""",
        r"""substituteInPlace "$workspaceRoot/crates/inspector_ui/build.rs" --replace-fail '    let mut path = std::path::PathBuf::from(&cargo_manifest_dir);' '    println!("cargo:rustc-env=ZED_REPO_DIR={}", cargo_manifest_dir);""",
        r"""substituteInPlace "$workspaceRoot/crates/gpui_macos/build.rs" --replace-fail '        gpui::GPUI_MANIFEST_DIR.into()' '        PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("workspace-gpui")'""",
        r"""substituteInPlace src/lib.rs --replace-fail 'concat!("../", std::env!("CARGO_PKG_README"))' '"../README.md"'""",
    ),
)
_ALLOWED_PYTHON_AD_HOC_REWRITE_SITES: Final = (
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


def test_package_overlay_python_ad_hoc_rewrite_sites_are_baselined() -> None:
    """Require new Python ad hoc rewrites to use codemod helpers or be baselined."""
    actual = _PYTHON_REWRITE_AUDIT.current_sites()

    assert Counter(actual) == Counter(_PYTHON_REWRITE_AUDIT.allowed_sites), (
        _format_site_delta(actual, _PYTHON_REWRITE_AUDIT.allowed_sites)
    )
