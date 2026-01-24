{ pkgs, ... }:
let
  # Absolute path required since this file is symlinked into the Nix store
  flake = builtins.getFlake "git+file://${builtins.getEnv "HOME"}/.config/nixcfg";
  inherit (flake) inputs outputs;
  inherit (pkgs.stdenv.hostPlatform) system;
  slib = flake.outputs.lib;
  src = flake.outPath;
  username = "george";
  userMeta = import "${src}/home/${username}/meta.nix";
in
{
  imports = slib.mkHomeModules { inherit system username; };
  home.username = username;
  _module.args = {
    inherit
      inputs
      outputs
      pkgs
      slib
      src
      system
      username
      userMeta
      ;
  };
}
