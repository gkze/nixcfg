{
  config,
  inputs ? { },
  lib,
  primaryUser ? null,
  ...
}:
let
  inherit (lib)
    mkEnableOption
    mkIf
    mkOption
    optionalAttrs
    types
    ;

  defaultTaps =
    (optionalAttrs (builtins.hasAttr "homebrew-core" inputs) {
      "homebrew/homebrew-core" = inputs."homebrew-core";
    })
    // (optionalAttrs (builtins.hasAttr "homebrew-cask" inputs) {
      "homebrew/homebrew-cask" = inputs."homebrew-cask";
    })
    // (optionalAttrs (builtins.hasAttr "pantsbuild-tap" inputs) {
      "pantsbuild/homebrew-tap" = inputs."pantsbuild-tap";
    });

  cfg = config.nixcfg.darwin.homebrew;
  resolvedUser = if cfg.user != null then cfg.user else primaryUser;
in
{
  options.nixcfg.darwin.homebrew = {
    enable = mkEnableOption "managed nix-homebrew configuration" // {
      default = true;
    };

    user = mkOption {
      type = types.nullOr types.str;
      default = primaryUser;
      description = "User that owns the Homebrew installation.";
    };

    enableRosetta = mkOption {
      type = types.bool;
      default = true;
      description = "Enable Rosetta support in nix-homebrew on Apple Silicon.";
    };

    taps = mkOption {
      type = types.attrsOf types.anything;
      default = defaultTaps;
      description = "Tap attrset passed directly to nix-homebrew.taps.";
    };

    mutableTaps = mkOption {
      type = types.bool;
      default = false;
      description = "Whether Homebrew taps may be modified outside nix-homebrew.";
    };
  };

  config = mkIf cfg.enable {
    assertions = [
      {
        assertion = resolvedUser != null;
        message = "nixcfg.darwin.homebrew.user must be set (or provide primaryUser via lib.mkSystem/lib.mkDarwinHost).";
      }
    ];

    nix-homebrew = {
      enable = true;
      inherit (cfg)
        enableRosetta
        mutableTaps
        taps
        ;
      user = resolvedUser;
    };
  };
}
