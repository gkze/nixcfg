{
  inputs,
  outputs,
  lib,
  ...
}:
with outputs.lib;
with inputs;
let
  user = "george";
  inherit (builtins) baseNameOf;
  inherit (lib) removeSuffix;
in
mkSystem {
  system = "aarch64-darwin";
  hostname = removeSuffix ".nix" (baseNameOf ./.);
  users = [ user ];
  homeModules = [ "${modulesPath}/home/town.nix" ];
  systemModules = [
    "${modulesPath}/darwin/display-management.nix"
    nix-homebrew.darwinModules.nix-homebrew
    "${modulesPath}/darwin/homebrew.nix"
    { nix-homebrew = { inherit user; }; }
    "${modulesPath}/darwin/${user}/shell.nix"
    "${modulesPath}/darwin/${user}/brew-apps.nix"
    "${modulesPath}/darwin/town.nix"
    "${modulesPath}/darwin/${user}/town-dock-apps.nix"
  ];
}
