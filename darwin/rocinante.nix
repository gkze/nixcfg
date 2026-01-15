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
  homeModules = [ "${modulesPath}/home/macbook-pro-16in.nix" ];
  systemModules = [
    "${modulesPath}/darwin/display-management.nix"
    nix-homebrew.darwinModules.nix-homebrew
    "${modulesPath}/darwin/homebrew.nix"
    { nix-homebrew = { inherit user; }; }
    "${modulesPath}/darwin/${user}/shell.nix"
    "${modulesPath}/darwin/${user}/brew-apps.nix"
    "${modulesPath}/darwin/${user}/dock-apps.nix"
    # To bootstrap: temporarily replace with { nix.linux-builder.enable = true; }
    nix-rosetta-builder.darwinModules.default
  ];
}
