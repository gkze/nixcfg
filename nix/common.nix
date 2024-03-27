{ pkgs, hostPlatform, inputs, ... }:
let inherit (builtins) elemAt split; in {
  # Configuration for Nix itself
  nix = {
    # Perform automatic garbage collection
    gc = { automatic = true; } // {
      darwin.interval = { Hour = 09; Minute = 30; };
      linux.dates = "09:30";
    }.${elemAt (split "-" hostPlatform) 2};

    settings = {
      # Enable nix command and flakes
      experimental-features = [ "nix-command" "flakes" ];

      # Perform builds in a sandboxed environment
      sandbox = false;
    };

    # Auto-upgrade nix command
    package = pkgs.nixUnstable;

    # Pin Nixpkgs to flake spec
    registry.nixpkgs.flake = inputs.nixpkgs;
  };

  # Configuration for Nixpkgs
  # Set host platform and allow unfree and insecure software
  # https://nixos.wiki/wiki/Unfree_Software
  nixpkgs = {
    inherit hostPlatform;
    config = { allowUnfree = true; allowInsecure = true; };
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
  documentation = { doc.enable = true; info.enable = true; man.enable = true; };

  # Create /etc/zshrc that loads the Nix environment
  programs.zsh.enable = true;
}
