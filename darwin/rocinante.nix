{ inputs, outputs, ... }:
with outputs.lib;
with inputs;
let
  user = "george";
in
mkSystem {
  system = "aarch64-darwin";
  hostname = "rocinante";
  users = [ user ];
  homeModules = [ "${modulesPath}/home/macbook-pro-m1-16in.nix" ];
  systemModules = [
    "${modulesPath}/darwin/display-management.nix"
    nix-homebrew.darwinModules.nix-homebrew
    "${modulesPath}/darwin/homebrew.nix"
    { nix-homebrew = { inherit user; }; }
    "${modulesPath}/darwin/brew-apps.nix"
  ];
}
