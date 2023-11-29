{ pkgs, hostPlatform, ... }: {
  # Configuration for Nix itself
  nix = {
    # Pin registry to system Nixpkgs
    #registry.nixpkgs.flake = pkgs;

    settings = {
      # Enable nix command and flakes
      experimental-features = [ "nix-command" "flakes" ];

      # Perform builds in a sandboxed environment
      sandbox = true;
    };

    # Auto-upgrade nix command
    package = pkgs.nixUnstable;
  };

  # Configuration for Nixpkgs
  # Set host platform and allow unfree software
  # https://nixos.wiki/wiki/Unfree_Software
  nixpkgs = { inherit hostPlatform; config.allowUnfree = true; };

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
