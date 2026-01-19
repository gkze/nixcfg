{ pkgs, ... }:
let
  flake = builtins.getFlake (toString ../.);
  inherit (pkgs.stdenv.hostPlatform) system;
  username = "george";
in
{
  imports = [
    ../modules/home/base.nix
    ../modules/home/${flake.outputs.lib.kernel system}.nix
    ./george/configuration.nix
  ];

  home.username = username;

  _module.args = {
    inherit (flake) inputs outputs;
    slib = flake.outputs.lib;
    src = ../.;
    inherit system username;
    userMeta = import ./george/meta.nix;
  };
}
