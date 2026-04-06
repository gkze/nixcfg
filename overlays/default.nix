{ inputs, outputs, ... }:
{
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

      withManagedMacApp =
        package: bundleName:
        package.overrideAttrs (old: {
          passthru = (old.passthru or { }) // {
            macApp = {
              inherit bundleName;
              bundleRelPath = "Applications/${bundleName}";
              installMode = "copy";
            };
          };
        });

      tinyOverlays = {
        chatgpt = withManagedMacApp (final.mkSourceOverride "chatgpt" prev.chatgpt) "ChatGPT.app";
        code-cursor = withManagedMacApp (final.mkSourceOverride "code-cursor" prev.code-cursor) "Cursor.app";
        commander = final.callPackage ../packages/commander { selfSource = sources.commander; };
        inherit (prev) flake-edit;
        google-chrome = final.mkSourceOverride "google-chrome" prev.google-chrome;
        inherit (inputs.googleworkspace-cli.packages.${system}) gws;
        worktrunk = inputs.worktrunk.packages.${system}.default;
        zed-editor-nightly =
          if prev.stdenv.hostPlatform.isDarwin then
            final.callPackage ../packages/zed-editor-nightly { }
          else
            inputs.zed.packages.${system}.default;
        jetbrains = prev.jetbrains // {
          datagrip = withManagedMacApp (final.mkSourceOverride "datagrip" prev.jetbrains.datagrip) "DataGrip.app";
        };
      };
    in
    fragments // helpers // tinyOverlays;
}
