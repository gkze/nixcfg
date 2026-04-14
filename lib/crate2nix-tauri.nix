{ lib }:
let
  tauriPluginEnvCrateNames = [
    "tauri-plugin-clipboard-manager"
    "tauri-plugin-decorum"
    "tauri-plugin-deep-link"
    "tauri-plugin-dialog"
    "tauri-plugin-fs"
    "tauri-plugin-http"
    "tauri-plugin-notification"
    "tauri-plugin-opener"
    "tauri-plugin-os"
    "tauri-plugin-process"
    "tauri-plugin-shell"
    "tauri-plugin-single-instance"
    "tauri-plugin-store"
    "tauri-plugin-updater"
    "tauri-plugin-window-state"
  ];

  mkCrate2nixTauriEnvOverride =
    { pkgs }:
    attrs: {
      nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ pkgs.python3 ];

      postFixup = (attrs.postFixup or "") + ''
        export TAURI_ENV_OUT="$out/env"
        export TAURI_ENV_LIB="$lib/env"
        export TAURI_ENV_METADATA_DIR="$lib/lib/${attrs.crateName or "crate"}.env"
        ${pkgs.python3}/bin/python3 ${./crate2nix_tauri_env_rewrite.py}
      '';
    };

  mkCrate2nixTauriUtilsOverride =
    { pkgs }:
    attrs: {
      nativeBuildInputs = (attrs.nativeBuildInputs or [ ]) ++ [ pkgs.python3 ];

      postPatch = (attrs.postPatch or "") + ''
        ${pkgs.python3}/bin/python3 ${./crate2nix_tauri_patch_acl_build.py}
      '';
    };

  mkCrate2nixTauriOverrides =
    {
      pkgs,
      pluginCrates ? tauriPluginEnvCrateNames,
      patchTauriUtils ? true,
    }:
    let
      tauriEnvOverride = mkCrate2nixTauriEnvOverride { inherit pkgs; };
    in
    {
      tauri = tauriEnvOverride;
    }
    // lib.genAttrs pluginCrates (_: tauriEnvOverride)
    // lib.optionalAttrs patchTauriUtils {
      tauri-utils = mkCrate2nixTauriUtilsOverride { inherit pkgs; };
    };
in
{
  inherit
    mkCrate2nixTauriEnvOverride
    mkCrate2nixTauriOverrides
    mkCrate2nixTauriUtilsOverride
    tauriPluginEnvCrateNames
    ;
}
