{ inputs, outputs, ... }:
{
  neovimLuaCompat = import ./neovimLuaCompat.nix;

  default =
    final: prev:
    let
      inherit (prev.stdenv.hostPlatform) system;
      slib = outputs.lib;
      inherit (slib) sources;

      fragArgs = {
        inherit
          inputs
          outputs
          final
          prev
          system
          slib
          sources
          ;
      };

      fragments = import ./_lib/fragments.nix {
        inherit fragArgs;
        overlayDir = ./.;
      };

      helpers = import ./_lib/helpers.nix fragArgs;

      tinyOverlays = {
        chatgpt = final.mkSourceOverride "chatgpt" prev.chatgpt;
        code-cursor = final.mkSourceOverride "code-cursor" prev.code-cursor;
        google-chrome = final.mkSourceOverride "google-chrome" prev.google-chrome;
        worktrunk = inputs.worktrunk.packages.${system}.default;
        zed-editor-nightly = inputs.zed.packages.${system}.default;
        jetbrains = prev.jetbrains // {
          datagrip = final.mkSourceOverride "datagrip" prev.jetbrains.datagrip;
        };
      };
    in
    fragments // helpers // tinyOverlays;
}
