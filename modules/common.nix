{
  config,
  hostname ? null,
  inputs ? { },
  lib,
  pkgs,
  slib ? null,
  system ? pkgs.stdenv.hostPlatform.system,
  ...
}:
let
  inherit (lib)
    mkDefault
    mkIf
    mkOption
    types
    ;

  cfg = config.nixcfg.common;

  kernel =
    if slib != null then
      slib.kernel system
    else if lib.hasSuffix "-darwin" system then
      "darwin"
    else if lib.hasSuffix "-linux" system then
      "linux"
    else
      throw "modules/common.nix: unsupported system '${system}'";

  flakeInputs = lib.filterAttrs (_: lib.isType "flake") inputs;

  storeHeadroomSettings =
    if cfg.nix.minFreeStoreBytes == null && cfg.nix.maxFreeStoreBytes == null then
      { }
    else if cfg.nix.minFreeStoreBytes == null || cfg.nix.maxFreeStoreBytes == null then
      throw ''
        nixcfg.common.nix.minFreeStoreBytes and maxFreeStoreBytes must be configured together
      ''
    else if cfg.nix.maxFreeStoreBytes <= cfg.nix.minFreeStoreBytes then
      throw "nixcfg.common.nix.maxFreeStoreBytes must exceed minFreeStoreBytes"
    else
      {
        min-free = cfg.nix.minFreeStoreBytes;
        max-free = cfg.nix.maxFreeStoreBytes;
      };
in
{
  options.nixcfg.common = {
    hostname = mkOption {
      type = types.nullOr types.str;
      default = hostname;
      description = "Hostname value to apply via networking.hostName.";
    };

    nix = {
      minFreeStoreBytes = mkOption {
        type = types.nullOr types.ints.positive;
        default = null;
        description = ''
          Optional free store capacity at which Nix starts automatic garbage collection.
          Configure this together with maxFreeStoreBytes.
        '';
      };

      maxFreeStoreBytes = mkOption {
        type = types.nullOr types.ints.positive;
        default = null;
        description = ''
          Optional free store capacity Nix targets after pressure-triggered garbage
          collection. Configure this together with minFreeStoreBytes.
        '';
      };

      substituters = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Additional binary cache URLs configured in nix.settings.substituters.";
      };

      trustedPublicKeys = mkOption {
        type = types.listOf types.str;
        default = [ ];
        description = "Additional cache keys configured in nix.settings.trusted-public-keys.";
      };
    };
  };

  config = {
    networking.hostName = mkIf (cfg.hostname != null) (mkDefault cfg.hostname);

    nix = {
      gc = {
        automatic = true;
        options = "--delete-older-than 3d";
      }
      // {
        darwin.interval = {
          Hour = 9;
          Minute = 30;
        };
        linux.dates = "09:30";
      }
      .${kernel};
      optimise = {
        automatic = true;
      }
      // {
        darwin.interval = {
          Weekday = 7;
          Hour = 4;
          Minute = 15;
        };
        linux.dates = "Sun 04:15";
      }
      .${kernel};
      settings = {
        experimental-features = [
          "nix-command"
          "flakes"
        ];
        keep-derivations = true;
        keep-outputs = true;
      }
      // storeHeadroomSettings
      // lib.optionalAttrs (cfg.nix.substituters != [ ]) {
        inherit (cfg.nix) substituters;
      }
      // lib.optionalAttrs (cfg.nix.trustedPublicKeys != [ ]) {
        trusted-public-keys = cfg.nix.trustedPublicKeys;
      };
      channel.enable = false;
      package = pkgs.nixVersions.git;
      registry = lib.mapAttrs (_: flake: { inherit flake; }) flakeInputs;
      nixPath = lib.mapAttrsToList (n: _: "${n}=flake:${n}") flakeInputs;
    };

    nixpkgs = {
      hostPlatform = system;
      config = {
        allowUnfree = true;
        # Per-package insecure overrides should be used instead of global allowInsecure
        # Note: allowInsecurePredicate is set in flake.nix at the flakelight level
      };
    };

    environment.pathsToLink = [ "/share/zsh" ];

    documentation = {
      doc.enable = true;
      info.enable = true;
      man.enable = true;
    };

    programs.zsh.enable = true;
  };
}
