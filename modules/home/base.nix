{
  lib,
  pkgs,
  slib,
  src,
  system,
  username,
  ...
}:
{
  # External modules (nixvim, sops-nix, stylix) are imported via lib.mkHomeModules
  fonts.fontconfig.enable = true;
  home = {
    homeDirectory = lib.mkForce "${slib.homeDirBase system}/${username}";
    stateVersion = lib.removeSuffix "\n" (builtins.readFile "${src}/NIXOS_VERSION");
  };
  nix = {
    package = lib.mkForce pkgs.nixVersions.latest;
    checkConfig = true;
  };
}
