{ config, lib, ... }:
let
  mkPathModule = import ./_path-module.nix { inherit config lib; };
in
mkPathModule {
  optionPath = [
    "nixcfg"
    "languages"
    "bun"
  ];
  enableDescription = "Bun JavaScript runtime";
  pathOptionName = "binPath";
  pathDefault = "$HOME/.bun/bin";
  pathDescription = "Path to add to PATH for bun-installed executables.";
  extraConfig = {
    programs.bun.enable = true;
  };
}
