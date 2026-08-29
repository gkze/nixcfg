{ lib }:
let
  versionConsumers = [
    "but"
    "but-update"
  ];
  channelConsumers = [
    "but"
    "but-api"
    "but-path"
    "but-update"
    "gitbutler-tauri"
  ];
in
{
  inherit channelConsumers versionConsumers;

  forCrate =
    {
      channel,
      crateName,
      version,
    }:
    lib.optionalAttrs (builtins.elem crateName channelConsumers) {
      CHANNEL = channel;
    }
    // lib.optionalAttrs (builtins.elem crateName versionConsumers) {
      VERSION = version;
    };

  # Keep the output basename identical to the crate directory. GitButler's
  # Tauri build script validates CARGO_MANIFEST_DIR's basename.
  patchedSourceName = crateName: crateName;
}
