{
  inputs,
  outputs,
  pkgsFor,
  ...
}:
with outputs.lib;
with inputs;
let
  user = "george";
  system = "aarch64-darwin";
  inherit (builtins) baseNameOf;
  inherit (pkgsFor.${system}.lib) removeSuffix;
in
mkSystem {
  inherit system;
  hostname = removeSuffix ".nix" (baseNameOf ./.);
  users = [ user ];
  homeModules = [ "${modulesPath}/home/macbook-pro-16in.nix" ];
  systemModules = [
    "${modulesPath}/darwin/display-management.nix"
    nix-homebrew.darwinModules.nix-homebrew
    "${modulesPath}/darwin/homebrew.nix"
    { nix-homebrew = { inherit user; }; }
    "${modulesPath}/darwin/george/brew-apps.nix"
    "${modulesPath}/darwin/george/dock-apps.nix"
  ];
}
