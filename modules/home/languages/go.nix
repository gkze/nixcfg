{ config, lib, ... }:
let
  mkPathModule = import ./_path-module.nix { inherit config lib; };
in
mkPathModule {
  optionPath = [
    "nixcfg"
    "languages"
    "go"
  ];
  enableDescription = "Go toolchain";
  pathOptionName = "binPath";
  pathDefault = "${config.home.homeDirectory}/go/bin";
  pathDescription = "Path to add to PATH for Go-installed binaries.";
  extraConfig = {
    programs.go.enable = true;
  };
}
