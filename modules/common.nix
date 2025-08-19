{
  hostname,
  inputs,
  lib,
  pkgs,
  slib,
  system,
  ...
}:
{
  networking.hostName = hostname;

  nix =
    let
      flakeInputs = lib.filterAttrs (_: lib.isType "flake") inputs;
    in
    {
      gc = {
        automatic = true;
      }
      // {
        darwin.interval = {
          Hour = 9;
          Minute = 30;
        };
        linux.dates = "09:30";
      }
      .${slib.kernel system};
      settings = {
        experimental-features = [
          "nix-command"
          "flakes"
        ];
      };
      channel.enable = false;
      package = pkgs.nixVersions.latest;
      registry = lib.mapAttrs (_: flake: { inherit flake; }) flakeInputs;
      nixPath = lib.mapAttrsToList (n: _: "${n}=flake:${n}") flakeInputs;
    };

  nixpkgs = {
    hostPlatform = system;
    config = {
      allowUnfree = true;
      allowInsecure = true;
    };
  };

  environment.pathsToLink = [ "/share/zsh" ];

  documentation = {
    doc.enable = true;
    info.enable = true;
    man.enable = true;
  };

  programs.zsh.enable = true;
}
