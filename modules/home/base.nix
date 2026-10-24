{
  config,
  lib,
  pkgs,
  slib,
  src,
  username,
  ...
}:
let
  inherit (lib) mkOption types;
in
{
  options.nixcfg.flakePath = mkOption {
    type = types.str;
    default = "${config.xdg.configHome}/nixcfg";
    description = "Absolute path to the nixcfg flake directory.";
  };

  config = {
    # External modules (nixvim and stylix) are imported via lib.mkHomeModules.
    fonts.fontconfig.enable = true;
    home = {
      homeDirectory = lib.mkForce "${slib.homeDirBase pkgs.stdenv.hostPlatform.system}/${username}";
      stateVersion = lib.removeSuffix "\n" (builtins.readFile "${src}/NIXOS_VERSION");
    };
    nix.checkConfig = true;
  };
}
