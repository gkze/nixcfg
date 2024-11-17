{ pkgs, ... }:
{
  imports = [ ./_base.nix ];

  home.packages = with pkgs; [
    curl
    git
  ];
}
