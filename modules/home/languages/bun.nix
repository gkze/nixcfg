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
  # `bun i -g` under the isolated linker installs into
  # ~/.bun/install/global/node_modules but skips the shims in ~/.bun/bin, while
  # it maintains node_modules/.bin for both linkers. Kept after binPath so the
  # real bun binary wins over the npm `bun` package's own bin shim.
  extraPaths = [ "$HOME/.bun/install/global/node_modules/.bin" ];
  extraConfig = {
    programs.bun.enable = true;
  };
}
