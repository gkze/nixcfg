"""Policy checks for package and overlay source modifications."""

from __future__ import annotations

from typing import Final

from lib.codemods.packaging_source_policy import (
    NixSubstituteAudit,
    NixSubstituteSite,
    PythonRewriteAudit,
    PythonRewriteSite,
)

# Baselines are not approval of these mechanisms. They make existing ad hoc source
# edits visible so new ones fail until they move to .patch files, lib.codemods, or
# are intentionally recorded as migration debt.
_ALLOWED_NIX_SUBSTITUTE_SITES: Final[tuple[NixSubstituteSite, ...]] = (
    (
        "overlays/gemini-cli/default.nix",
        18,
        "substituteInPlace packages/a2a-server/package.json packages/cli/package.json "
        'package-lock.json --replace-fail \'"tar": "7.5.8"\' \'"tar": "7.5.11"\'',
    ),
    (
        "overlays/gemini-cli/default.nix",
        23,
        "substituteInPlace packages/cli/package.json package-lock.json --replace-fail "
        '\'"clipboardy": "5.2.0"\' \'"clipboardy": "5.2.1"\'',
    ),
    (
        "overlays/gemini-cli/default.nix",
        76,
        'substituteInPlace packages/core/src/tools/ripGrep.ts --replace-fail "const systemRg '
        "= resolveExecutable('rg');\" \"const systemRg = '${rg}' || "
        "resolveExecutable('rg');\"",
    ),
    (
        "overlays/gemini-cli/default.nix",
        79,
        'substituteInPlace packages/core/src/tools/ripGrep.ts --replace-fail "if '
        "(isTrustedSystemPath(realPath)) {\" \"if (realPath === '${rg}' || "
        'isTrustedSystemPath(realPath)) {"',
    ),
    (
        "overlays/gemini-cli/default.nix",
        89,
        'substituteInPlace scripts/build.js --replace-fail "npm run build --workspaces" "npm '
        'run build --workspace=@google/gemini-cli-devtools && npm run build --workspaces"',
    ),
    (
        "overlays/github-desktop/default.nix",
        60,
        'substituteInPlace "$node_addon_api_header" --replace-fail \'static const '
        "napi_typedarray_type unknown_array_type = static_cast<napi_typedarray_type>(-1);' "
        "'static const napi_typedarray_type unknown_array_type = "
        "static_cast<napi_typedarray_type>(0);'",
    ),
    (
        "overlays/gogcli/default.nix",
        10,
        'substituteInPlace go.mod --replace-fail "go 1.26.2" "go ${final.go.version}"',
    ),
    (
        "overlays/vim-plugin-overrides.nix",
        33,
        "substituteInPlace lua/codesnap/module.lua --replace-fail '${moduleLuaOld}' "
        "'${moduleLuaNew}'",
    ),
    (
        "overlays/vim-plugin-overrides.nix",
        36,
        "substituteInPlace lua/codesnap/fetch.lua --replace-fail '${fetchLuaOld}' "
        "'${fetchLuaNew}'",
    ),
    (
        "overlays/vim-plugin-overrides.nix",
        40,
        "substituteInPlace lua/codesnap/init.lua --replace-fail "
        "'string.match(static.config.save_path, \"%.(.+)$\")' 'string.match(save_path, "
        '"%.(.+)$")\'',
    ),
    (
        "overlays/vim-plugin-overrides.nix",
        44,
        "substituteInPlace lua/codesnap/init.lua --replace-fail 'if matched_extension ~= "
        "\"png\" and matched_extension ~= nil then' 'if matched_extension ~= nil and "
        'matched_extension ~= "png" and matched_extension ~= "svg" and matched_extension ~= '
        '"html" then\' --replace-fail \'error("The extension of save_path should be .png", '
        "0)' 'error(\"The extension of save_path should be .png, .svg, or .html\", 0)'",
    ),
    (
        "overlays/vim-plugin-overrides.nix",
        49,
        "substituteInPlace lua/codesnap/init.lua --replace-fail "
        "'require(\"generator\").save_snapshot(config)' '${saveCallNew}'",
    ),
    (
        "overlays/vim-plugin-overrides.nix",
        54,
        "substituteInPlace lua/codesnap/init.lua --replace-fail 'config.save_path' "
        "'save_path'",
    ),
    (
        "overlays/vim-plugin-overrides.nix",
        58,
        "substituteInPlace lua/codesnap/utils/table.lua --replace-fail 'if t1[k] == nil and "
        "v ~= nil then' 'if t1[k] == nil and v ~= nil and v ~= \"none\" then'",
    ),
    (
        "packages/codex/default.nix",
        77,
        'substituteInPlace "$out/core/src/tools/js_repl/mod.rs" --replace-fail '
        "'../../../../node-version.txt' '../../../node-version.txt'",
    ),
    (
        "packages/emdash/default.nix",
        133,
        'substituteInPlace ${appDir}/src/main/utils/userEnv.ts --replace-fail " -ilc '
        "'env'\" \" -lc 'env'\"",
    ),
    (
        "packages/emdash/default.nix",
        189,
        "substituteInPlace node_modules/debug/src/common.js --replace-fail \"require('ms')\" "
        "\"require('../../../out/main/ms-shim.cjs')\"",
    ),
    (
        "packages/emdash/default.nix",
        279,
        'substituteInPlace "$out/bin/emdash" --replace-fail "#!/usr/bin/env bash" '
        '"#!${stdenv.shell}" --replace-fail "@out@" "$out"',
    ),
    (
        "packages/emdash/default.nix",
        306,
        'substituteInPlace "$out/bin/emdash" --replace-fail "#!/usr/bin/env bash" '
        '"#!${stdenv.shell}" --replace-fail "@out@" "$out"',
    ),
    (
        "packages/goose-desktop/default.nix",
        131,
        'substituteInPlace desktop/src/updates.ts --replace-fail "export const '
        'UPDATES_ENABLED = true;" "export const UPDATES_ENABLED = false;"',
    ),
    (
        "packages/mole-app/default.nix",
        40,
        'substituteInPlace "$out/bin/mole" --replace-fail \'SCRIPT_DIR="$(cd "$(dirname '
        '"\'\'${BASH_SOURCE[0]}")" && pwd)"\' "SCRIPT_DIR=\'$out/libexec/mole\'"',
    ),
    (
        "packages/scratch/default.nix",
        36,
        'substituteInPlace "$out/nix-support/setup-hook" --replace-fail '
        "'\"x86_64-unknown-linux-gnu\"' '\"${rustTarget}\"' --replace-fail "
        "'target/x86_64-unknown-linux-gnu' 'target/${rustTarget}' --replace-fail "
        "'CC_X86_64_UNKNOWN_LINUX_GNU' 'CC_${rustTargetEnv}' --replace-fail "
        "'CXX_X86_64_UNKNOWN_LINUX_GNU' 'CXX_${rustTargetEnv}' --replace-fail "
        "'CARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER' "
        "'CARGO_TARGET_${rustTargetEnv}_LINKER'",
    ),
    (
        "packages/t3code/_shared.nix",
        92,
        'substituteInPlace pnpm-workspace.yaml --replace-fail "  - infra/*" ""',
    ),
    (
        "packages/superset/default.nix",
        278,
        'substituteInPlace package.json --replace-fail \'"postinstall": '
        '"./scripts/postinstall.sh"\' \'"postinstall": ""\'',
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        103,
        'substituteInPlace "$workspaceRoot/crates/release_channel/src/lib.rs" --replace-fail '
        "'include_str!(\"../../zed/RELEASE_CHANNEL\")' "
        "'include_str!(\"../RELEASE_CHANNEL\")'",
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        106,
        'substituteInPlace "$workspaceRoot/crates/assets/src/assets.rs" --replace-fail '
        "'#[folder = \"../../assets\"]' '#[folder = \"workspace-assets\"]' --replace-fail "
        "'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};' --replace-fail "
        '".filter_map(|p| {" ".filter_map(|p: std::borrow::Cow<\'static, str>| {"',
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        111,
        'substituteInPlace "$workspaceRoot/crates/settings/src/settings.rs" --replace-fail '
        "'#[folder = \"../../assets\"]' '#[folder = \"workspace-assets\"]' --replace-fail "
        "'use rust_embed::RustEmbed;' 'use rust_embed::{Embed, RustEmbed};'",
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        115,
        'substituteInPlace "$workspaceRoot/crates/prompt_store/src/prompt_store.rs" '
        "--replace-fail 'include_str!(\"../../git_ui/src/commit_message_prompt.txt\")' "
        "'include_str!(\"../commit_message_prompt.txt\")'",
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        118,
        'substituteInPlace "$workspaceRoot/crates/extension_host/build.rs" --replace-fail '
        "'PathBuf::from(\"../extension_api/wit\")' "
        "'PathBuf::from(\"workspace-extension-api-wit\")'",
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        122,
        "substituteInPlace \"$path\" --replace-fail 'path: \"../extension_api/wit/' 'path: "
        "\"workspace-extension-api-wit/'",
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        127,
        'substituteInPlace "$workspaceRoot/crates/remote_server/build.rs" --replace-fail '
        "'include_str!(\"../zed/Cargo.toml\")' 'include_str!(\"./zed-Cargo.toml\")'",
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        132,
        'substituteInPlace "$workspaceRoot/crates/edit_prediction_cli/build.rs" '
        "--replace-fail 'std::fs::read_to_string(\"../zed/Cargo.toml\")' "
        "'std::fs::read_to_string(\"./zed-Cargo.toml\")'",
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        137,
        'substituteInPlace "$workspaceRoot/crates/eval/build.rs" --replace-fail '
        "'std::fs::read_to_string(\"../zed/Cargo.toml\")' "
        "'std::fs::read_to_string(\"./zed-Cargo.toml\")'",
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        142,
        'substituteInPlace "$workspaceRoot/crates/eval_cli/build.rs" --replace-fail '
        "'std::fs::read_to_string(\"../zed/Cargo.toml\")' "
        "'std::fs::read_to_string(\"./zed-Cargo.toml\")' --replace-fail "
        "'println!(\"cargo:rerun-if-changed=../zed/Cargo.toml\");' "
        "'println!(\"cargo:rerun-if-changed=./zed-Cargo.toml\");'",
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        149,
        "substituteInPlace "
        '"$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" --replace-fail '
        "'#[folder = \"../grammars/src/\"]' '#[folder = "
        '"workspace-language-configs-src/"]\'',
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        152,
        "substituteInPlace "
        '"$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" --replace-fail '
        "'#[folder = \"../languages/src/\"]' '#[folder = "
        '"workspace-language-configs-src/"]\'',
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        157,
        "substituteInPlace "
        '"$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" --replace-fail '
        '\'concat!(env!("CARGO_MANIFEST_DIR"), "/../grammars/src")\' '
        '\'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")\'',
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        160,
        "substituteInPlace "
        '"$workspaceRoot/crates/edit_prediction_cli/src/filter_languages.rs" --replace-fail '
        '\'concat!(env!("CARGO_MANIFEST_DIR"), "/../languages/src")\' '
        '\'concat!(env!("CARGO_MANIFEST_DIR"), "/workspace-language-configs-src")\'',
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        165,
        'substituteInPlace "$workspaceRoot/crates/cli/src/main.rs" --replace-fail '
        "'include_bytes!(\"../../../script/uninstall.sh\")' "
        "'include_bytes!(\"../uninstall.sh\")'",
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        168,
        'substituteInPlace "$workspaceRoot/crates/inspector_ui/build.rs" --replace-fail '
        "'    let mut path = std::path::PathBuf::from(&cargo_manifest_dir);' '    "
        'println!("cargo:rustc-env=ZED_REPO_DIR={}", cargo_manifest_dir);',
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        174,
        'substituteInPlace "$workspaceRoot/crates/gpui_macos/build.rs" --replace-fail '
        "'        gpui::GPUI_MANIFEST_DIR.into()' '        "
        'PathBuf::from(env::var("CARGO_MANIFEST_DIR").unwrap()).join("workspace-gpui")\'',
    ),
    (
        "packages/zed-editor-nightly/default.nix",
        348,
        'substituteInPlace src/lib.rs --replace-fail \'concat!("../", '
        'std::env!("CARGO_PKG_README"))\' \'"../README.md"\'',
    ),
)
_ALLOWED_PYTHON_AD_HOC_REWRITE_SITES: Final[tuple[PythonRewriteSite, ...]] = (
    ("packages/gitbutler/normalize_cargo_nix.py", 102, 18, 106, 9, "replace"),
    ("packages/gitbutler/normalize_cargo_nix.py", 107, 15, 111, 9, "replace"),
    ("packages/gitbutler/normalize_cargo_nix.py", 107, 15, 115, 9, "replace"),
    ("packages/gitbutler/normalize_cargo_nix.py", 117, 11, 117, 74, "sub"),
    ("packages/gitbutler/normalize_cargo_nix.py", 123, 15, 127, 9, "replace"),
    ("packages/gitbutler/normalize_cargo_nix.py", 129, 11, 129, 80, "sub"),
    ("packages/gitbutler/normalize_cargo_nix.py", 148, 15, 148, 63, "replace"),
    ("packages/gitbutler/normalize_cargo_nix.py", 150, 11, 150, 77, "sub"),
    ("packages/gitbutler/normalize_cargo_nix.py", 159, 15, 163, 9, "replace"),
    ("packages/gitbutler/normalize_cargo_nix.py", 165, 11, 165, 83, "sub"),
    ("packages/gitbutler/normalize_cargo_nix.py", 188, 15, 192, 9, "replace"),
    ("packages/gitbutler/normalize_cargo_nix.py", 194, 11, 194, 78, "sub"),
)
_NIX_SUBSTITUTE_AUDIT = NixSubstituteAudit(_ALLOWED_NIX_SUBSTITUTE_SITES)
_PYTHON_REWRITE_AUDIT = PythonRewriteAudit(_ALLOWED_PYTHON_AD_HOC_REWRITE_SITES)


def _format_sites(sites: tuple[NixSubstituteSite | PythonRewriteSite, ...]) -> str:
    return "\n".join(str(site) for site in sites)


def test_package_overlay_substitute_in_place_sites_are_baselined() -> None:
    """Require new Nix source rewrites to be explicit migration debt."""
    actual = _NIX_SUBSTITUTE_AUDIT.current_sites()

    assert actual == _NIX_SUBSTITUTE_AUDIT.allowed_sites, _format_sites(actual)


def test_package_overlay_python_ad_hoc_rewrite_sites_are_baselined() -> None:
    """Require new Python ad hoc rewrites to use codemod helpers or be baselined."""
    actual = _PYTHON_REWRITE_AUDIT.current_sites()

    assert actual == _PYTHON_REWRITE_AUDIT.allowed_sites, _format_sites(actual)
