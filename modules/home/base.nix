{
  inputs,
  lib,
  pkgs,
  slib,
  src,
  system,
  username,
  ...
}:
{
  imports = with inputs; [
    nixvim.homeManagerModules.nixvim
    stylix.homeManagerModules.stylix
  ];
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
