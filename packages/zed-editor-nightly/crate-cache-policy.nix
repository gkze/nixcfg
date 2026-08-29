{ lib }:
let
  # Exact compile-time consumers in the locked upstream source. In particular,
  # windows_resources reads the value only when invoked by Windows consumer
  # build scripts, not while compiling its own library derivation.
  commitShaConsumers = [
    "cli"
    "edit_prediction_cli"
    "eval_cli"
    "remote_server"
    "zed"
  ];
  updateExplanationConsumers = [
    "auto_update"
    "cli"
  ];
  releaseVersionConsumers = [ "cli" ];
  fontConfigConsumers = [ "zed" ];
  livekitWebrtcConsumers = [ "webrtc-sys" ];
  protocConsumers = [
    "livekit_api"
    "proto"
  ];
  bindgenConsumers = [ "media" ];
  pkgConfigConsumers = [ "zed" ];
  lldConsumers = [ "zed" ];
  xcodebuildConsumers = [
    "gpui_apple"
    "media"
    "ui"
  ];
  systemLibraryConsumers = [ "zed" ];
  zstdPkgConfigConsumers = [ "zstd-sys" ];
in
{
  inherit
    bindgenConsumers
    commitShaConsumers
    fontConfigConsumers
    lldConsumers
    livekitWebrtcConsumers
    pkgConfigConsumers
    protocConsumers
    releaseVersionConsumers
    systemLibraryConsumers
    updateExplanationConsumers
    xcodebuildConsumers
    zstdPkgConfigConsumers
    ;

  forCrate =
    {
      commitSha,
      crateName,
      fontConfig,
      livekitWebrtc,
      protoc,
      releaseVersion,
      updateExplanation,
    }:
    lib.optionalAttrs (builtins.elem crateName fontConfigConsumers) {
      FONTCONFIG_FILE = fontConfig;
    }
    // lib.optionalAttrs (builtins.elem crateName livekitWebrtcConsumers) {
      LK_CUSTOM_WEBRTC = livekitWebrtc;
    }
    // lib.optionalAttrs (builtins.elem crateName protocConsumers) {
      PROTOC = protoc;
    }
    // lib.optionalAttrs (builtins.elem crateName updateExplanationConsumers) {
      ZED_UPDATE_EXPLANATION = updateExplanation;
    }
    // lib.optionalAttrs (builtins.elem crateName releaseVersionConsumers) {
      RELEASE_VERSION = releaseVersion;
    }
    // lib.optionalAttrs (builtins.elem crateName commitShaConsumers) {
      ZED_COMMIT_SHA = commitSha;
    }
    // lib.optionalAttrs (builtins.elem crateName zstdPkgConfigConsumers) {
      ZSTD_SYS_USE_PKG_CONFIG = true;
    };

  preparedSourceName = crateName: "zed-editor-nightly-${crateName}-src";
}
