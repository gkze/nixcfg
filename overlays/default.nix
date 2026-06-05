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
        appcleaner = withManagedMacApp prev.appcleaner "AppCleaner.app";
        betterdisplay = withManagedMacApp prev.betterdisplay "BetterDisplay.app";
        chatgpt = withManagedMacApp (final.mkSourceOverride "chatgpt" prev.chatgpt) "ChatGPT.app";
        code-cursor = withManagedMacApp (final.mkSourceOverride "code-cursor" prev.code-cursor) "Cursor.app";
        commander = withManagedMacApp (final.callPackage ../packages/commander {
          selfSource = sources.commander;
        }) "Commander.app";
        cyberduck = withManagedMacApp prev.cyberduck "Cyberduck.app";
        dbeaver-bin = withManagedMacApp prev.dbeaver-bin "dbeaver.app";
        discord = withManagedMacApp prev.discord "Discord.app";
        element-desktop = withManagedMacApp prev.element-desktop "Element.app";
        inherit (prev) flake-edit;
        google-chrome = withManagedMacApp (final.mkSourceOverride "google-chrome" prev.google-chrome) "Google Chrome.app";
        hoppscotch = withManagedMacApp prev.hoppscotch "Hoppscotch.app";
        iina = withManagedMacApp prev.iina "IINA.app";
        inherit (inputs.googleworkspace-cli.packages.${system}) gws;
        notion-app = withManagedMacApp prev.notion-app "Notion.app";
        orbstack = withManagedMacApp (final.mkSourceOverride "orbstack" prev.orbstack) "OrbStack.app";
        postman = withManagedMacApp prev.postman "Postman.app";
        rectangle = withManagedMacApp prev.rectangle "Rectangle.app";
        slack = withManagedMacApp prev.slack "Slack.app";
        sloth-app = withManagedMacApp prev.sloth-app "Sloth.app";
        spacedrive = withManagedMacApp prev.spacedrive "Spacedrive.app";
        zed-editor-nightly =
          if prev.stdenv.hostPlatform.isDarwin then
            withManagedMacApp (final.callPackage ../packages/zed-editor-nightly { }) "Zed Nightly.app"
          else
            inputs.zed.packages.${system}.default;
        jetbrains = prev.jetbrains // {
          datagrip = withManagedMacApp (final.mkSourceOverride "datagrip" prev.jetbrains.datagrip) "DataGrip.app";
        };
      };
    in
    fragments // helpers // tinyOverlays;
}
