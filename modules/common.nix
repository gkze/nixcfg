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
        substituters = [
          "https://gkze.cachix.org"
          "https://cache.nixos.org/"
        ];
        trusted-public-keys = [
          "gkze.cachix.org-1:vO2wq3fAFvRL1TA7R02JnU/R5iKGhoHMLGYbnzPRJjI="
          "cache.nixos.org-1:6NCHdD59X431o0gWypbMrAURkbJ16ZPMQFGspcDShjY="
        ];
      };
      channel.enable = false;
      package = pkgs.nixVersions.latest;
      registry = lib.mapAttrs (_: flake: { inherit flake; }) flakeInputs;
      nixPath = lib.mapAttrsToList (n: _: "${n}=flake:${n}") flakeInputs;
      # linux-builder = {
      #   enable = true;
      #   systems = [
      #     "x86_64-linux"
      #     "aarch64-linux"
      #   ];
      #   config.boot.binfmt.emulatedSystems = [ "x86_64-linux" ];
      # };
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
