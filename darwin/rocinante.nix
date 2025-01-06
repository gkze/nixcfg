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
    nix-homebrew.darwinModules.nix-homebrew
    "${modulesPath}/darwin/homebrew.nix"
    {
      nix-homebrew = { inherit user; };
      homebrew.masApps = {
        "AdGuard for Safari" = 1440147259;
        "Apple Configurator" = 1037126344;
        "JSONPeep" = 1458969831;
        "Shazam: Identify Songs" = 897118787;
        "Twitter" = 1482454543;
        "Xcode" = 497799835;
      };
    }
    "${modulesPath}/darwin/window-management.nix"
  ];
}
