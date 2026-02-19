{
  config,
  lib,
  pkgs,
  slib,
  src,
  system,
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
    # External modules (nixvim, sops-nix, stylix) are imported via lib.mkHomeModules
    fonts.fontconfig.enable = true;
    home = {
      homeDirectory = lib.mkForce "${slib.homeDirBase system}/${username}";
      stateVersion = lib.removeSuffix "\n" (builtins.readFile "${src}/NIXOS_VERSION");
    };
    nix = {
      package = lib.mkForce pkgs.nixVersions.git;
      checkConfig = true;
    };
  };
}
