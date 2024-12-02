{
  inputs,
  pkgs,
  lib,
  hostPlatform,
  ...
}:
let
  inherit (builtins) elemAt split;
in
{
  # Configuration for Nix itself
  nix =
    let
      flakeInputs = lib.filterAttrs (_: lib.isType "flake") inputs;
    in
    {
      # Perform automatic garbage collection
      gc =
        {
          automatic = true;
        }
        // {
          darwin.interval = {
            Hour = 9;
            Minute = 30;
          };
          linux.dates = "09:30";
        }
        .${elemAt (split "-" hostPlatform) 2};

      settings = {
        # Enable nix command and flakes
        experimental-features = [
          "nix-command"
          "flakes"
        ];
      };

      channel.enable = false;

      # Auto-upgrade nix command
      package = pkgs.nixVersions.latest;

      # Pin Nixpkgs to flake spec
      registry = lib.mapAttrs (_: flake: { inherit flake; }) flakeInputs;
      nixPath = lib.mapAttrsToList (n: _: "${n}=flake:${n}") flakeInputs;
    };

  # Configuration for Nixpkgs
  # Set host platform and allow unfree and insecure software
  # https://nixos.wiki/wiki/Unfree_Software
  nixpkgs = {
    inherit hostPlatform;
    config = {
      allowUnfree = true;
      allowInsecure = true;
    };
  };

  # Common system configuration
  environment = {
    # For Zsh completion
    # https://nix-community.github.io/home-manager/options.html#opt-programs.zsh.enableCompletion
    pathsToLink = [ "/share/zsh" ];

    # System-wide packages. Only put things here if they're universally needed
    # (unlikely in most cases)
    systemPackages = [ ];
  };

  # Install documentation from packages
  documentation = {
    doc.enable = true;
    info.enable = true;
    man.enable = true;
  };

  programs = {
    # Create /etc/zshrc that loads the Nix environment
    zsh.enable = true;
  };
}
